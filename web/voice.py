"""Voice in the browser: the page's microphone in, the answer's voice out, on its WebSocket.

The page captures the microphone (`getUserMedia`, echo cancellation), a small AudioWorklet turns it
into 16 kHz PCM and sends it here as binary frames. Those frames feed the very same `VoiceListener`
as the terminal's `parec` stream, so the VAD, the endpointer and Whisper are unchanged: only the
source of the audio differs.

When the page has the voice on, the shared `Speaker` writes to this page (WebAudio) instead of
pacat, which is what makes the conversation hands-free in the browser. Turning it off hands the
voice back to the terminal's output.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import numpy as np

from monika.config import Settings
from monika.paths import Paths
from voice.audio import FRAME_BYTES, FRAME_SAMPLES
from voice.listener import VoiceListener
from voice.playback import levels_of
from voice.stt import Transcriber
from voice.vad import SileroStreamingVAD

log = logging.getLogger(__name__)

LEVEL_WINDOW_MS = 20
MAX_QUEUED_FRAMES = 50  # ~1.6 s: more than that and the microphone is ahead of us anyway


class WebAudioSource:
    """Microphone frames pushed in by the page, handed to the listener as fixed 512-sample frames."""

    def __init__(self, max_frames: int = MAX_QUEUED_FRAMES) -> None:
        self._frames: asyncio.Queue[bytes] = asyncio.Queue(max_frames)
        self._leftover = b""
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    async def start(self) -> None:
        self._open = True

    async def close(self) -> None:
        self._open = False
        while not self._frames.empty():
            self._frames.get_nowait()
        self._leftover = b""

    def feed(self, data: bytes) -> None:
        """One chunk from the page (any length); nothing is kept while the voice is off."""
        if not self._open or not data:
            return
        self._leftover += data
        while len(self._leftover) >= FRAME_BYTES:
            frame, self._leftover = self._leftover[:FRAME_BYTES], self._leftover[FRAME_BYTES:]
            if self._frames.full():  # a slow reader loses the oldest audio, never blocks the page
                self._frames.get_nowait()
            self._frames.put_nowait(frame)

    async def read_frame(self) -> np.ndarray:
        data = await self._frames.get()
        return np.frombuffer(data, dtype=np.int16)


class WebAudioSink:
    """Plays the voice in the page: PCM out on the WebSocket, timed on the server's clock.

    `close()` waits until the last sample would have been heard, which is what makes the
    conversation half-duplex: Monika only starts listening again once she has finished speaking.
    """

    def __init__(self, send_json: Callable[[dict[str, Any]], Awaitable[None]],
                 send_bytes: Callable[[bytes], Awaitable[None]], sample_rate: int = 24000,
                 on_level: Callable[[float], None] | None = None) -> None:
        self._send_json = send_json
        self._send_bytes = send_bytes
        self._rate = max(1, int(sample_rate))
        self._on_level = on_level
        self._window = max(1, self._rate * LEVEL_WINDOW_MS // 1000)
        self._open = False
        self._written = 0  # samples already sent, for the playback clock
        self._start = 0.0

    @property
    def is_open(self) -> bool:
        return self._open

    async def open(self) -> None:
        self._open = True
        self._written = 0
        self._start = asyncio.get_running_loop().time()
        await self._send_quietly({"type": "audio", "event": "start", "rate": self._rate})

    async def write(self, pcm: np.ndarray) -> None:
        pcm = np.asarray(pcm, dtype=np.int16)
        if not self._open or not len(pcm):
            return
        try:
            await self._send_bytes(pcm.tobytes())
        except Exception:  # the page went away mid-sentence: the sound is lost, the turn is not
            log.debug("page absente, audio de la voix perdu", exc_info=True)
            self._open = False
            return
        self._levels(pcm)  # the mouth moves with the sound, not with the sending
        self._written += len(pcm)

    def _levels(self, pcm: np.ndarray) -> None:
        if self._on_level is None:
            return
        loop = asyncio.get_running_loop()
        ahead = max(0.0, self._start + self._written / self._rate - loop.time())
        for index, level in enumerate(levels_of(pcm, self._window)):
            at = ahead + index * (self._window / self._rate)
            try:
                loop.call_later(at, self._on_level, level)
            except RuntimeError:  # the loop is closing: nothing to animate
                return

    async def close(self) -> None:
        if not self._open:
            return
        loop = asyncio.get_running_loop()
        remaining = self._start + self._written / self._rate - loop.time()
        if remaining > 0:
            await asyncio.sleep(min(remaining, 30.0))
        self._open = False
        await self._send_quietly({"type": "audio", "event": "end"})

    async def stop(self) -> None:
        self._open = False
        await self._send_quietly({"type": "audio", "event": "stop"})  # the page cuts what it plays

    async def _send_quietly(self, message: dict[str, Any]) -> None:
        try:
            await self._send_json(message)
        except Exception:  # a closed page is not an error of the voice
            log.debug("page absente, commande audio ignorée", exc_info=True)


class PageVoice:
    """One browser conversation: microphone, transcription, and the voice routed back to that page."""

    def __init__(self, settings: Settings, paths: Paths, hub: Any,
                 send_json: Callable[[dict[str, Any]], Awaitable[None]],
                 send_bytes: Callable[[bytes], Awaitable[None]],
                 targets: dict[str, Any] | None = None,
                 listener_factory: Callable[[WebAudioSource], Any] | None = None) -> None:
        self._s = settings
        self._listener_factory = listener_factory  # tests inject one: Silero + Whisper are heavy
        self._paths = paths
        self._hub = hub
        self._send_json = send_json
        self._send_bytes = send_bytes
        # The conversation exists only once the page is up: the launcher fills these two keys later
        # ("speaker": the shared Speaker, "ask": async (text) -> None, one whole turn).
        self._targets = targets if targets is not None else {}
        self._source = WebAudioSource()
        self._transcriber = Transcriber(settings, paths)
        self._listener: VoiceListener | None = None
        self._sink: WebAudioSink | None = None
        self._task: asyncio.Task | None = None
        self.on = False

    @property
    def _speaker(self) -> Any:
        return self._targets.get("speaker")

    @property
    def _ask(self) -> Callable[[str], Awaitable[None]] | None:
        return self._targets.get("ask")

    # --- what the WebSocket calls --------------------------------------------------------------

    async def toggle(self, on: bool) -> None:
        if on:
            await self._start()
        else:
            await self._stop()

    def feed(self, data: bytes) -> None:
        self._source.feed(data)

    async def close(self) -> None:
        """The page went away: stop the microphone and give the voice back to the terminal."""
        await self._stop(announce=False)

    # --- internals -----------------------------------------------------------------------------

    async def _say(self, event: str, text: str = "") -> None:
        message: dict[str, Any] = {"type": "voice", "event": event}
        if text:
            message["text"] = text
        try:
            await self._send_json(message)
        except Exception:  # the page closed: there is nobody left to tell
            log.debug("page absente, message de voix ignoré", exc_info=True)

    async def _start(self) -> None:
        if self.on:
            await self._say("on")
            return
        if self._ask is None:
            await self._say("error", "La conversation n'est pas encore prête : réessaie dans un instant.")
            return
        self.on = True  # A second click while loading must not open a second microphone
        try:
            self._listener = (self._listener_factory(self._source) if self._listener_factory is not None
                              else VoiceListener(self._source, SileroStreamingVAD(),
                                                 self._transcriber, self._s))
            await self._listener.start()
        except Exception as exc:  # noqa: BLE001 - missing model, no onnxruntime…
            self.on = False
            self._listener = None
            log.warning("voix de la page : %s", exc)
            await self._say("error", f"Reconnaissance vocale indisponible : {exc}")
            return
        if self._speaker is not None:
            rate = int(getattr(self._speaker.engine, "sample_rate", 24000) or 24000)
            self._sink = WebAudioSink(self._send_json, self._send_bytes, rate,
                                      on_level=self._hub.on_level if self._hub is not None else None)
            if self._speaker.busy:  # her voice would otherwise be split between the two outputs
                await self._speaker.cancel()
            self._speaker.use_sink(self._sink)
        self._task = asyncio.get_running_loop().create_task(self._loop())
        if self._hub is not None:
            self._hub.set_state("listening")
        await self._say("on")

    async def _stop(self, announce: bool = True) -> None:
        self.on = False
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self._listener is not None:
            try:
                await self._listener.close()
            except Exception:  # noqa: BLE001 - closing a broken microphone is best effort
                pass
            self._listener = None
        if self._speaker is not None and self._sink is not None:
            self._speaker.use_sink(None)  # back to pacat
            await self._sink.stop()
            self._sink = None
        if self._hub is not None:
            self._hub.set_state("idle")
        if announce:
            await self._say("off")

    async def _loop(self) -> None:
        """Listen, transcribe, answer, wait for the answer to be heard, listen again."""
        ask = self._targets.get("ask")
        speaker = self._targets.get("speaker")
        assert self._listener is not None and ask is not None
        try:
            while True:
                transcript = await self._listener.listen(on_state=self._state)
                text = (transcript.text or "").strip() if transcript is not None else ""
                if not text:
                    continue
                await self._say("text", text)
                if speaker is not None and speaker.busy:
                    await speaker.wait_idle()  # half duplex: no listening over her own voice
                await ask(text)
                if speaker is not None:
                    await speaker.wait_idle()
                if self._hub is not None and self.on:
                    self._hub.set_state("listening")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the page must hear about it, not the exception
            log.warning("écoute de la page interrompue (%s: %s)", type(exc).__name__, exc)
            await self._stop(announce=False)
            await self._say("error", f"Écoute arrêtée : {exc}")

    def _state(self, state: str) -> None:
        if self._hub is not None and self.on:
            self._hub.set_state("listening" if state in ("ecoute", "parole") else "thinking")
