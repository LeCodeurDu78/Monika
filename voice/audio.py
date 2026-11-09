"""Microphone capture through sounddevice (PortAudio), at 16 kHz mono."""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

import numpy as np

from voice.backend import BackendUnavailable, device_arg, extra_settings, sounddevice
from voice.errors import AudioError

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_SAMPLES = 512  # 32 ms, the window size the voice activity model expects
FRAME_BYTES = FRAME_SAMPLES * 2  # 16-bit samples
MAX_QUEUED_FRAMES = 50  # ~1.6 s: more than that and the microphone is ahead of us anyway


class AudioSource(Protocol):
    async def start(self) -> None: ...
    async def read_frame(self) -> np.ndarray: ...
    async def close(self) -> None: ...


class SoundDeviceSource:
    """Reads fixed-size int16 frames from the default or selected input device."""

    def __init__(self, device: str = "", sample_rate: int = SAMPLE_RATE) -> None:
        self._device = device
        self._rate = max(1, int(sample_rate))
        self._stream = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUED_FRAMES)
        self._pending = bytearray()
        self._closed = False
        self._error: AudioError | None = None

    async def start(self) -> None:
        try:
            sd = sounddevice()
        except BackendUnavailable as exc:
            raise AudioError(str(exc)) from exc
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=MAX_QUEUED_FRAMES)
        self._pending.clear()
        self._closed = False
        self._error = None
        try:
            self._stream = sd.RawInputStream(
                samplerate=self._rate,
                blocksize=FRAME_SAMPLES,
                device=device_arg(self._device),
                channels=1,
                dtype="int16",
                latency="low",
                extra_settings=extra_settings(sd),
                callback=self._received,
                finished_callback=self._finished,
            )
            self._stream.start()
        except Exception as exc:
            stream, self._stream = self._stream, None
            self._close_stream(stream)
            raise AudioError(self._error_text(exc)) from exc

    def _received(self, indata, frames: int, time_info, status) -> None:
        if status:
            log.debug("microphone status: %s", status)
        loop = self._loop
        if loop is None or self._closed:
            return
        try:
            loop.call_soon_threadsafe(self._offer, bytes(indata))
        except RuntimeError:  # the event loop is gone
            pass

    def _finished(self) -> None:
        loop = self._loop
        if loop is None or self._closed:
            return
        try:
            loop.call_soon_threadsafe(self._fail, "Le micro s'est arrêté.")
        except RuntimeError:
            pass

    def _offer(self, data: bytes) -> None:
        if self._closed or self._error is not None or not data:
            return
        self._pending.extend(data)
        while len(self._pending) >= FRAME_BYTES:
            frame = np.frombuffer(self._pending[:FRAME_BYTES], dtype=np.int16)
            del self._pending[:FRAME_BYTES]
            if self._queue.full():  # a slow reader loses the oldest audio
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            self._queue.put_nowait(frame)

    def _fail(self, message: str) -> None:
        if self._closed:
            return
        self._error = AudioError(message)
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait(self._error)

    async def read_frame(self) -> np.ndarray:
        if self._stream is None and self._error is None:
            raise AudioError("Le micro n'est pas ouvert.")
        item = await self._queue.get()
        if isinstance(item, AudioError):
            raise item
        if self._error is not None:
            raise self._error
        return item

    async def close(self) -> None:
        self._closed = True
        self._loop = None
        stream, self._stream = self._stream, None
        if stream is not None:
            await asyncio.get_running_loop().run_in_executor(
                None, self._close_stream, stream
            )

    def _error_text(self, exc: Exception) -> str:
        where = f" « {self._device} »" if self._device else ""
        return (
            f"Le micro{where} ne peut pas être ouvert : {exc}. "
            "Vérifie AUDIO_INPUT_DEVICE (liste : `python -m sounddevice`)."
        )

    @staticmethod
    def _close_stream(stream) -> None:
        for action in (stream.stop, stream.close):
            try:
                action()
            except Exception:  # already stopped or unplugged: nothing left to do
                log.debug("could not close the microphone stream", exc_info=True)
