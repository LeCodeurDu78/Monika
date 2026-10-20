"""Voice calls on Discord: Monika joins a voice channel, listens to you and answers out loud.

    you speak -> Discord audio (48 kHz stereo) -> 16 kHz mono -> VAD segmenter -> Whisper
    -> agent turn (streamed) -> sentence-level TTS -> 48 kHz stereo PCM -> Discord

Ways to start a call:
  * you DM her "/appel" (or ask her to call you: tool `discord_call`);
  * automatically when you enter a voice channel (DISCORD_CALL_AUTOJOIN);
  * Monika "calls you": a bot cannot ring a phone, so she sends you a DM with the channel link, joins the
    channel and greets you when you arrive (DISCORD_CALL_CHANNEL_ID is used when you are not in a channel yet).

Receiving voice needs the third-party `discord-ext-voice-recv` package (discord.py itself only sends audio).
Speech recognition and synthesis are the same local models as voice mode, so this needs the voice stack.
"""

import asyncio
import queue
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from core.settings import settings
from agents.turn import run_streamed_turn
from voice.segmenter import Segmenter
from voice.streaming_tts import SpeechStream

try:  # discord.py is optional until calls are enabled
    import discord

    _SourceBase = discord.AudioSource
except ImportError:  # pragma: no cover
    discord = None
    _SourceBase = object

CALL_CONTEXT = (
    "Contexte : tu es en appel vocal Discord avec l'utilisateur ; ce que tu écris est dit à voix haute. "
    "Réponds comme à l'oral : une ou deux phrases, sans Markdown, sans liste, sans émojis."
)
_HANGUP_PATTERN = re.compile(r"\b(raccroche|raccrocher)\b", re.IGNORECASE)

FRAME_BYTES = 3840  # 20 ms of 48 kHz stereo 16-bit PCM
_GRACE_FRAMES = 12  # keep playing silence this long when the buffer runs dry, so sentence gaps do not stop playback
_VAD_FRAME_MS = 30
_VAD_FRAME_SAMPLES = 16000 * _VAD_FRAME_MS // 1000


class CallUnavailable(Exception):
    pass


# --- audio conversion --------------------------------------------------------------------------
class Downsampler:
    """Stereo 48 kHz s16le PCM -> mono 16 kHz float32, with a low-pass filter whose state survives across chunks."""

    TAPS = 33

    def __init__(self) -> None:
        n = np.arange(self.TAPS) - (self.TAPS - 1) / 2
        kernel = np.sinc(2 * (7000 / 48000) * n) * np.hamming(self.TAPS)
        self._kernel = (kernel / kernel.sum()).astype(np.float32)
        self._tail = np.zeros(self.TAPS - 1, dtype=np.float32)
        self._phase = 0

    def process(self, pcm: bytes) -> np.ndarray:
        usable = len(pcm) - len(pcm) % 4
        if usable <= 0:
            return np.zeros(0, dtype=np.float32)
        samples = np.frombuffer(pcm[:usable], dtype="<i2").astype(np.float32) / 32768.0
        mono = samples.reshape(-1, 2).mean(axis=1)
        buffer = np.concatenate([self._tail, mono])
        filtered = np.convolve(buffer, self._kernel, mode="valid")  # same length as `mono`
        self._tail = buffer[-(self.TAPS - 1) :]
        out = filtered[self._phase :: 3]
        self._phase = (self._phase - len(filtered)) % 3
        return out


def to_discord_pcm(audio: np.ndarray, sample_rate: int) -> bytes:
    """Mono float32 audio at any rate -> 48 kHz stereo s16le bytes (linear interpolation is fine for speech)."""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size == 0:
        return b""
    if sample_rate != 48000:
        target = max(1, int(round(audio.size * 48000 / sample_rate)))
        audio = np.interp(np.linspace(0, audio.size - 1, target), np.arange(audio.size), audio)
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    return np.repeat(pcm, 2).tobytes()


class PCMStreamSource(_SourceBase):
    """Audio source fed by push(); playback ends by itself shortly after the buffer runs dry."""

    def __init__(self, on_data: Optional[Callable[[], None]] = None) -> None:
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._on_data = on_data  # asks the owner to (re)start playback when audio arrives
        self._dry_frames = 0
        self.ended = True  # True once read() returned b"" (playback over) until new data arrives

    def push(self, pcm: bytes) -> None:
        with self._lock:
            self._buffer += pcm
            self._dry_frames = 0
        if self._on_data is not None:
            self._on_data()

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()

    def pending(self) -> int:
        with self._lock:
            return len(self._buffer)

    def read(self) -> bytes:
        with self._lock:
            if self._buffer:
                self._dry_frames = 0
                chunk = bytes(self._buffer[:FRAME_BYTES])
                del self._buffer[:FRAME_BYTES]
                return chunk.ljust(FRAME_BYTES, b"\x00")
            self._dry_frames += 1
            if self._dry_frames <= _GRACE_FRAMES:
                return b"\x00" * FRAME_BYTES
            self.ended = True
            return b""

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        pass


def _energy_vad(frame: bytes) -> bool:
    """Fallback voice activity detection when webrtcvad is not installed."""
    samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(np.square(samples)))) > 0.015


def _make_vad() -> Callable[[bytes], bool]:
    try:
        import webrtcvad

        vad = webrtcvad.Vad(settings.VOICE_VAD_AGGRESSIVENESS)
        return lambda frame: vad.is_speech(frame, 16000)
    except ImportError:
        return _energy_vad


# --- backends (injectable for tests) ----------------------------------------------------------------
@dataclass
class Backends:
    transcribe: Callable[..., str]
    synthesize: Callable[[str], Optional[tuple]]
    process: Callable[..., str]
    make_vad: Callable[[], Callable[[bytes], bool]] = _make_vad


def default_backends(process: Callable[..., str]) -> Backends:
    from voice import voice_tts
    from voice.voice_stt import transcribe

    return Backends(transcribe, voice_tts.synthesize, process)


class _UserPipe:
    """Audio path of one speaker: resampling, 30 ms re-framing, utterance segmentation."""

    def __init__(self, make_vad: Callable[[], Callable[[bytes], bool]]) -> None:
        self.down = Downsampler()
        self.pending = np.zeros(0, dtype=np.float32)
        self.segmenter = Segmenter(
            make_vad(),
            sample_rate=16000,
            frame_ms=_VAD_FRAME_MS,
            silence_ms=settings.VOICE_SILENCE_MS,
            min_speech_ms=settings.VOICE_MIN_SPEECH_MS,
            preroll_ms=settings.VOICE_PREROLL_MS,
            max_seconds=settings.VOICE_MAX_RECORD_SECONDS,
        )

    def feed(self, pcm: bytes):
        """Yield the finished utterance (float32) each time one ends; frames arrive 20 ms at a time."""
        self.pending = np.concatenate([self.pending, self.down.process(pcm)])
        while self.pending.size >= _VAD_FRAME_SAMPLES:
            frame, self.pending = self.pending[:_VAD_FRAME_SAMPLES], self.pending[_VAD_FRAME_SAMPLES:]
            utterance = self.segmenter.push((np.clip(frame, -1, 1) * 32767).astype(np.int16))
            if utterance is not None:
                yield utterance


# --- one call -----------------------------------------------------------------------------------
class CallSession:
    """A live call: owns the voice client, the conversation history and the worker that answers."""

    def __init__(self, voice_client, channel, allowed: set, backends: Backends, loop: asyncio.AbstractEventLoop) -> None:
        self.vc = voice_client
        self.channel = channel
        self.allowed = allowed
        self.loop = loop
        self._b = backends
        self.history = [{"role": "system", "content": ""}]  # the orchestrator fills the system prompt
        self.source = PCMStreamSource(on_data=self._ensure_playing)
        self._pipes: dict[int, _UserPipe] = {}
        self._work: "queue.Queue" = queue.Queue()
        self._stop = threading.Event()
        self.pending_greeting: Optional[str] = None
        self.on_hangup: Optional[Callable[[], None]] = None
        self._thread = threading.Thread(target=self._worker, daemon=True, name="discord-call")

    # --- audio in (called from the voice receive thread) ------------------------------------
    def attach_sink(self) -> None:
        from discord.ext import voice_recv

        self.vc.listen(voice_recv.BasicSink(self._on_packet))
        self._thread.start()

    def _on_packet(self, user, data) -> None:
        if user is None or getattr(user, "id", None) not in self.allowed or self._stop.is_set():
            return
        pipe = self._pipes.get(user.id)
        if pipe is None:
            pipe = self._pipes[user.id] = _UserPipe(self._b.make_vad)
        self.handle_pcm(pipe, data.pcm)

    def handle_pcm(self, pipe: _UserPipe, pcm: bytes) -> None:
        for utterance in pipe.feed(pcm):
            self._work.put(("utterance", utterance))

    # --- audio out ----------------------------------------------------------------------
    def _ensure_playing(self) -> None:
        self.loop.call_soon_threadsafe(self._start_playback)

    def _start_playback(self) -> None:
        try:
            if self.vc.is_playing() and not self.source.ended:
                return
            if self.vc.is_playing():
                self.vc.stop()
            self.source.ended = False
            self.vc.play(self.source)
        except Exception as exc:
            print(f"⚠️ [Call] Could not start playback ({type(exc).__name__}: {exc}).")

    def _play(self, audio, sample_rate: int, text: str) -> None:
        """Hand one synthesized sentence to Discord and wait until it has been played."""
        self.source.push(to_discord_pcm(audio, sample_rate))
        while self.source.pending() > 0:
            time.sleep(0.02)
        time.sleep(0.02 * _GRACE_FRAMES / 4)  # let the last frames leave before the next sentence

    # --- worker --------------------------------------------------------------------------
    def say(self, text: str) -> None:
        self._work.put(("say", text))

    def stop(self) -> None:
        self._stop.set()
        self._work.put(("stop", None))
        self.source.clear()

    def _worker(self) -> None:
        while not self._stop.is_set():
            kind, payload = self._work.get()
            if kind == "stop":
                return
            try:
                if kind == "say":
                    self._speak_only(payload)
                else:
                    self._handle_utterance(payload)
            except Exception as exc:  # one bad utterance must not end the call
                print(f"⚠️ [Call] Utterance dropped ({type(exc).__name__}: {exc}).")

    def _new_stream(self) -> SpeechStream:
        return SpeechStream(self._b.synthesize, self._play)

    def _speak_only(self, text: str) -> None:
        stream = self._new_stream()
        stream.say(text)
        stream.finish()

    def _handle_utterance(self, audio: np.ndarray) -> None:
        text = self._b.transcribe(audio).strip()
        if not text:
            return
        if _HANGUP_PATTERN.search(text) and len(text.split()) <= 4:
            if self.on_hangup:
                self.on_hangup()
            return

        stream = self._new_stream()
        reply = run_streamed_turn(
            self.history,
            text,
            process=self._b.process,
            stream=stream,
            on_wait=stream.say,
            context=CALL_CONTEXT,
            voice=True,
        )
        print(f"📞 Monika: {reply}")


# --- manager: tracks users, starts and ends calls ------------------------------------------------
class CallManager:
    def __init__(self, listener, backends: Optional[Backends] = None) -> None:
        self._listener = listener
        self._backends = backends
        self.session: Optional[CallSession] = None
        self._user_channel: dict[int, object] = {}
        self._ring_task: Optional[asyncio.Task] = None
        self._leave_task: Optional[asyncio.Task] = None

    # --- helpers -------------------------------------------------------------------------
    @property
    def _client(self):
        return self._listener._client

    @property
    def _loop(self):
        return self._listener._loop

    @property
    def _allowed(self) -> set:
        return set(self._listener.allowed)

    def _owner_id(self) -> int:
        return self._listener.allowed[0]

    def backends(self) -> Backends:
        if self._backends is None:
            self._backends = default_backends(self._listener._process)
        return self._backends

    async def _dm(self, user_id: int, text: str) -> None:
        try:
            user = await self._client.fetch_user(user_id)
            await user.send(text, allowed_mentions=discord.AllowedMentions.none())
        except Exception as exc:
            print(f"⚠️ [Call] DM not sent ({type(exc).__name__}: {exc}).")

    # --- voice state tracking ----------------------------------------------------------------
    def scan(self) -> None:
        """Learn who is already in a voice channel (called when the client is ready)."""
        for guild in self._client.guilds:
            for channel in guild.voice_channels:
                for user_id in getattr(channel, "voice_states", {}):
                    if user_id in self._allowed:
                        self._user_channel[user_id] = channel

    async def on_voice_state_update(self, member, before, after) -> None:
        if member.id not in self._allowed:
            return
        before_channel, after_channel = getattr(before, "channel", None), getattr(after, "channel", None)
        if after_channel is not None:
            self._user_channel[member.id] = after_channel
        else:
            self._user_channel.pop(member.id, None)
        if before_channel == after_channel:
            return

        session = self.session
        if session is not None:
            if after_channel is not None and after_channel.id == session.channel.id:
                self._cancel_task("_leave_task")
                self._cancel_task("_ring_task")
                if session.pending_greeting is not None:  # the user answered the call
                    greeting, session.pending_greeting = session.pending_greeting, None
                    session.say(greeting)
            elif before_channel is not None and before_channel.id == session.channel.id and not self._allowed_in(session.channel):
                self._schedule_leave()
            return

        if after_channel is not None and settings.DISCORD_CALL_AUTOJOIN:
            wanted = settings.DISCORD_CALL_CHANNEL_ID
            if not wanted or str(after_channel.id) == str(wanted):
                await self._start_session(after_channel, greeting="Salut ! Je t'écoute.")

    def _allowed_in(self, channel) -> bool:
        return any(uid in self._allowed and chan.id == channel.id for uid, chan in self._user_channel.items())

    def _cancel_task(self, name: str) -> None:
        task = getattr(self, name)
        if task is not None:
            task.cancel()
            setattr(self, name, None)

    def _schedule_leave(self) -> None:
        self._cancel_task("_leave_task")

        async def _later() -> None:
            await asyncio.sleep(settings.DISCORD_CALL_LEAVE_AFTER_SECONDS)
            if self.session is not None and not self._allowed_in(self.session.channel):
                await self.hangup()

        self._leave_task = asyncio.ensure_future(_later())

    # --- starting and ending calls ------------------------------------------------------------------
    async def _start_session(self, channel, greeting: Optional[str], pending: bool = False) -> None:
        if self.session is not None:
            raise CallUnavailable("un appel est déjà en cours")
        try:
            from discord.ext import voice_recv
        except ImportError as exc:
            raise CallUnavailable(
                "il manque le paquet « discord-ext-voice-recv » (voir requirements.txt) pour recevoir ta voix"
            ) from exc
        voice_client = await channel.connect(cls=voice_recv.VoiceRecvClient)
        session = CallSession(voice_client, channel, self._allowed, self.backends(), self._loop)
        session.on_hangup = lambda: asyncio.run_coroutine_threadsafe(self.hangup(), self._loop)
        self.session = session
        session.attach_sink()
        if pending:
            session.pending_greeting = greeting
        elif greeting:
            session.say(greeting)

    async def _wait_for_user(self, user_id: int, greeting: str):
        """Join the configured call channel and wait there for the user.

        Return (channel, None) on success, (None, None) when no call channel is configured (or it cannot be
        found), and (None, error_text) when joining failed.
        """
        wanted = settings.DISCORD_CALL_CHANNEL_ID
        channel = self._client.get_channel(int(wanted)) if wanted else None
        if channel is None:
            return None, None
        try:
            await self._start_session(channel, greeting=greeting, pending=True)
        except CallUnavailable as exc:
            return None, f"❌ Appel impossible : {exc}."

        async def _timeout() -> None:
            await asyncio.sleep(settings.DISCORD_CALL_RING_TIMEOUT_SECONDS)
            if self.session is not None and self.session.pending_greeting is not None:
                await self.hangup()
                await self._dm(user_id, "📞 Appel manqué : tu n'as pas rejoint le salon à temps.")

        self._ring_task = asyncio.ensure_future(_timeout())
        return channel, None

    async def join_user(self, user_id: int) -> str:
        """The user asks, by DM, for a call. The reply is sent back in that same DM.

        Discord bots cannot join a private (DM) call, only a server voice channel. So: if the user is already in
        a voice channel Monika joins it; otherwise she goes to the configured call channel, gives the link here,
        and greets the user when they arrive.
        """
        channel = self._user_channel.get(user_id)
        if channel is not None:
            try:
                await self._start_session(channel, greeting="Je suis là, je t'écoute.")
            except CallUnavailable as exc:
                return f"❌ Appel impossible : {exc}."
            return f"📞 Je te rejoins dans « {channel.name} »."

        channel, error = await self._wait_for_user(user_id, "Je suis là, je t'écoute.")
        if error:
            return error
        if channel is None:
            return (
                "Je ne te vois dans aucun salon vocal et aucun salon d'appel n'est configuré "
                "(DISCORD_CALL_CHANNEL_ID) : rejoins un salon vocal puis redis « /appel »."
            )
        return f"📞 Rejoins le salon vocal <#{channel.id}> : je t'y attends et je te dis bonjour dès que tu arrives."

    async def ring(self, message: str = "") -> str:
        """Monika calls the owner: join their channel, or wait for them in the configured one (DM with the link)."""
        user_id = self._owner_id()
        greeting = message.strip() or "Salut, c'est moi. Tu m'entends ?"
        channel = self._user_channel.get(user_id)
        if channel is not None:
            try:
                await self._start_session(channel, greeting=greeting)
            except CallUnavailable as exc:
                return f"❌ Appel impossible : {exc}."
            await self._dm(user_id, f"📞 Monika te rejoint dans « {channel.name} ».")
            return f"Appel démarré : je suis dans « {channel.name} », avec l'utilisateur."

        channel, error = await self._wait_for_user(user_id, greeting)
        if error:
            return error
        if channel is None:
            return (
                "❌ L'utilisateur n'est dans aucun salon vocal et DISCORD_CALL_CHANNEL_ID n'est pas défini "
                "(ou introuvable) : impossible de l'appeler."
            )
        await self._dm(user_id, f"📞 Monika t'appelle ! Rejoins le salon vocal <#{channel.id}>.")
        return f"J'appelle l'utilisateur : message privé envoyé, j'attends dans « {channel.name} »."

    async def hangup(self) -> str:
        session, self.session = self.session, None
        self._cancel_task("_ring_task")
        self._cancel_task("_leave_task")
        if session is None:
            return "Aucun appel en cours."
        session.stop()
        try:
            session.vc.stop_listening()
        except Exception:
            pass
        try:
            await session.vc.disconnect(force=True)
        except Exception as exc:
            print(f"⚠️ [Call] Disconnect failed ({type(exc).__name__}: {exc}).")
        return "📞 J'ai raccroché."

    def status(self) -> str:
        if self.session is None:
            return "Aucun appel en cours."
        waiting = " (en attente que l'utilisateur rejoigne)" if self.session.pending_greeting is not None else ""
        return f"Appel en cours dans « {self.session.channel.name} »{waiting}."

    # --- entry point for the agent's tool (runs in the agent thread) ---------------------------------
    def run(self, action: str, message: str = "") -> str:
        if self._loop is None:
            return "❌ Discord n'est pas encore connecté."
        if action == "call_me":
            coro = self.ring(message)
        elif action == "hangup":
            coro = self.hangup()
        elif action == "status":
            return self.status()
        else:
            return "Action inconnue : utilise 'call_me', 'hangup' ou 'status'."
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=60)


_manager: Optional[CallManager] = None


def set_manager(manager: Optional[CallManager]) -> None:
    global _manager
    _manager = manager


def get_manager() -> Optional[CallManager]:
    return _manager
