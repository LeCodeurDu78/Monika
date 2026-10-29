"""VoiceListener: microphone -> voice activity -> end of sentence -> transcript."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import numpy as np

from monika.config import Settings
from voice.audio import AudioSource
from voice.endpoint import Endpointer
from voice.errors import AudioError
from voice.stt import Transcriber, Transcript
from voice.vad import FrameVAD

StateCallback = Callable[[str], None]  # "ecoute" | "parole" | "transcription"
FrameCallback = Callable[[float, float], None]  # (speech probability, RMS level 0..1)


class VoiceListener:
    """Keeps the microphone open and only hands frames to the detector while Monika is listening."""

    def __init__(self, source: AudioSource, vad: FrameVAD, transcriber: Transcriber,
                 settings: Settings) -> None:
        self._source = source
        self._vad = vad
        self._transcriber = transcriber
        self._s = settings
        self._endpointer = self._new_endpointer()
        self._queue: asyncio.Queue = asyncio.Queue()
        self._active = False
        self._active_since = 0.0
        self._pump_task: asyncio.Task | None = None
        self._error: AudioError | None = None
        self.dropped: list[str] = []  # Reasons of transcripts rejected as noise or hallucinations

    @property
    def transcriber(self) -> Transcriber:
        """The speech recogniser, for read-only status (health grid)."""
        return self._transcriber

    def _new_endpointer(self) -> Endpointer:
        s = self._s
        return Endpointer(s.vad_speech_threshold, None, s.vad_min_speech_ms, s.vad_end_silence_ms,
                          s.vad_pre_roll_ms, s.vad_max_utterance_s)

    async def start(self) -> None:
        await self._source.start()
        self._pump_task = asyncio.get_running_loop().create_task(self._pump())

    async def close(self) -> None:
        if self._pump_task is not None:
            self._pump_task.cancel()
            await asyncio.gather(self._pump_task, return_exceptions=True)
        await self._source.close()
        self._transcriber.close()

    async def _pump(self) -> None:
        """Always read the microphone so it never backs up; keep frames only while listening."""
        loop = asyncio.get_running_loop()
        try:
            while True:
                frame = await self._source.read_frame()
                if self._active and loop.time() >= self._active_since:
                    self._queue.put_nowait(frame)
        except AudioError as exc:
            self._error = exc
            self._queue.put_nowait(exc)

    def _activate(self) -> None:
        """Start from a clean slate: drop stale audio (including Monika's own voice)."""
        self._queue = asyncio.Queue()
        self._vad.reset()
        self._endpointer.reset()
        self._active_since = asyncio.get_running_loop().time() + self._s.voice_resume_delay_ms / 1000
        self._active = True

    async def listen(self, timeout: float | None = None, on_state: StateCallback | None = None,
                     on_frame: FrameCallback | None = None) -> Transcript | None:
        """Wait for one complete sentence and return its transcript (None on timeout)."""
        if self._error is not None:
            raise self._error
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
        notify = on_state or (lambda _state: None)
        speaking = False
        try:
            self._activate()
            notify("ecoute")
            while True:
                remaining = None if deadline is None else deadline - loop.time()
                if remaining is not None and remaining <= 0:
                    return None
                try:
                    item = await asyncio.wait_for(self._queue.get(), remaining)
                except TimeoutError:
                    return None
                if isinstance(item, Exception):
                    raise item
                probability = self._vad.probability(item)
                if on_frame is not None:
                    on_frame(probability, float(np.sqrt(np.mean((item.astype(np.float32) / 32768.0) ** 2))))
                utterance = self._endpointer.feed(item, probability)
                if self._endpointer.in_speech and not speaking:
                    speaking = True
                    notify("parole")
                if utterance is None:
                    continue
                speaking = False
                self._active = False  # Deaf while transcribing
                notify("transcription")
                transcript = await self._transcriber.transcribe(utterance.audio)
                if transcript.filtered:
                    self.dropped.append(transcript.filtered)
                    self._activate()
                    notify("ecoute")
                    continue
                return transcript
        finally:
            self._active = False
