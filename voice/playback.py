"""Audio output through sounddevice (PortAudio), reporting the playback level for the avatar."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from collections.abc import Callable

import numpy as np

from voice.backend import BackendUnavailable, device_arg, extra_settings, sounddevice
from voice.errors import PlaybackError

log = logging.getLogger(__name__)

LevelCallback = Callable[
    [float], None
]  # Loudness 0..1 of the sound being heard right now
LEVEL_WINDOW_MS = 20


def levels_of(pcm: np.ndarray, window_samples: int) -> list[float]:
    """RMS loudness (0..1) of each window of int16 samples; the last window may be shorter."""
    out = []
    for start in range(0, len(pcm), window_samples):
        part = pcm[start : start + window_samples].astype(np.float32) / 32768.0
        out.append(float(np.sqrt(np.mean(part * part))) if part.size else 0.0)
    return out


class SoundDeviceSink:
    """One playback stream: open, write PCM chunks, then close (waits for the sound to finish)."""

    def __init__(
        self,
        sample_rate: int = 24000,
        device: str = "",
        latency_ms: int = 100,
        on_level: LevelCallback | None = None,
    ) -> None:
        self._rate = max(1, int(sample_rate))
        self._device = device
        self._latency = max(0.0, latency_ms / 1000)
        self._on_level = on_level
        self._window = max(1, self._rate * LEVEL_WINDOW_MS // 1000)
        self._max_buffer = self._rate * 2 * 2  # two seconds of int16 mono
        self._stream = None
        self._buffer = bytearray()
        self._lock = threading.Condition()
        self._closed = True
        self._aborted = False
        self._failed = False
        self._emitter: asyncio.Task | None = None
        self._timeline: deque[tuple[float, float]] = deque()
        self._wake = asyncio.Event()
        self._clock_end = (
            0.0  # Loop time at which everything written so far has been played
        )

    @property
    def is_open(self) -> bool:
        return self._stream is not None

    async def open(self) -> None:
        if self._stream is not None:
            return
        try:
            sd = sounddevice()
        except BackendUnavailable as exc:
            raise PlaybackError(str(exc)) from exc
        with self._lock:
            self._buffer.clear()
            self._closed = False
            self._aborted = False
            self._failed = False
            self._lock.notify_all()
        self._timeline.clear()
        self._clock_end = 0.0
        try:
            self._stream = sd.RawOutputStream(
                samplerate=self._rate,
                blocksize=0,
                device=device_arg(self._device),
                channels=1,
                dtype="int16",
                latency=self._latency,
                extra_settings=extra_settings(sd),
                callback=self._output,
                finished_callback=self._finished,
            )
            self._stream.start()
        except Exception as exc:
            stream, self._stream = self._stream, None
            self._close_stream(stream, abort=True)
            with self._lock:
                self._closed = True
                self._aborted = True
                self._lock.notify_all()
            raise PlaybackError(self._error_text(exc)) from exc
        self._latency = max(
            0.0, float(getattr(self._stream, "latency", self._latency) or self._latency)
        )
        if self._on_level is not None:
            self._emitter = asyncio.get_running_loop().create_task(self._emit_levels())

    def _output(self, outdata, frames: int, time_info, status) -> None:
        if status:
            log.debug("audio output status: %s", status)
        need = frames * 2  # int16 mono
        with self._lock:
            chunk = bytes(self._buffer[:need])
            del self._buffer[:need]
            self._lock.notify_all()
        if len(chunk) < need:  # underrun: keep the stream going with silence
            chunk += b"\x00" * (need - len(chunk))
        try:
            outdata[:] = chunk
        except Exception:  # the stream is closing
            log.debug("could not fill the audio output buffer", exc_info=True)

    def _finished(self) -> None:
        # A normal close() stops the stream; an unexpected stop must fail the next write.
        if not self._closed:
            self._failed = True
            log.warning("the audio output stream stopped unexpectedly")

    async def write(self, pcm: np.ndarray) -> None:
        """Send int16 mono samples; waits when too much audio is already queued."""
        if self._stream is None or self._failed:
            raise PlaybackError("La sortie audio n'est pas ouverte.")
        if pcm.size == 0:
            return
        data = np.ascontiguousarray(pcm, dtype=np.int16).tobytes()
        self._schedule_levels(pcm)
        try:
            await asyncio.get_running_loop().run_in_executor(None, self._append, data)
        except PlaybackError:
            raise
        except Exception as exc:
            raise PlaybackError(self._error_text(exc)) from exc

    def _append(self, data: bytes) -> None:
        with self._lock:
            while (
                len(self._buffer) > self._max_buffer
                and not self._aborted
                and not self._closed
            ):
                self._lock.wait(0.1)
            if self._aborted or self._closed:
                raise PlaybackError("La sortie audio a été interrompue.")
            self._buffer.extend(data)
            self._lock.notify_all()

    def _schedule_levels(self, pcm: np.ndarray) -> None:
        """Queue the loudness windows at the moment the matching sound reaches the speakers."""
        if self._on_level is None:
            return
        now = asyncio.get_running_loop().time()
        start = max(
            self._clock_end, now + self._latency
        )  # After an underrun, the sound starts late
        for i, level in enumerate(levels_of(pcm, self._window)):
            self._timeline.append((start + i * LEVEL_WINDOW_MS / 1000, level))
        self._clock_end = start + len(pcm) / self._rate
        self._wake.set()

    async def _emit_levels(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            if not self._timeline:
                self._wake.clear()
                await self._wake.wait()
                continue
            due, level = self._timeline.popleft()
            if due > loop.time():
                await asyncio.sleep(due - loop.time())
            self._notify(level)

    def _notify(self, level: float) -> None:
        if self._on_level is None:
            return
        try:
            self._on_level(level)
        except Exception:  # A broken listener must never cut the voice
            log.exception("on_level callback failed")

    async def close(self) -> None:
        """Finish the sound already sent, then release the stream."""
        stream = self._stream
        if stream is None:
            return
        remaining = max(0.0, self._clock_end - asyncio.get_running_loop().time())
        if remaining > 0:
            await asyncio.sleep(min(remaining, 30.0))
        await asyncio.get_running_loop().run_in_executor(
            None, self._close_stream, stream, False
        )
        await self._release()

    async def stop(self) -> None:
        """Cut the sound immediately."""
        stream = self._stream
        if stream is None:
            return
        with self._lock:
            self._aborted = True
            self._closed = True
            self._buffer.clear()
            self._lock.notify_all()
        await asyncio.get_running_loop().run_in_executor(
            None, self._close_stream, stream, True
        )
        await self._release()

    async def _release(self) -> None:
        self._stream = None
        with self._lock:
            self._closed = True
            self._aborted = True
            self._buffer.clear()
            self._lock.notify_all()
        if self._emitter is not None:
            self._emitter.cancel()
            await asyncio.gather(self._emitter, return_exceptions=True)
            self._emitter = None
        self._timeline.clear()
        self._notify(0.0)

    def _error_text(self, exc: Exception) -> str:
        where = f" « {self._device} »" if self._device else ""
        return (
            f"La sortie audio{where} ne peut pas être ouverte : {exc}. "
            "Vérifie AUDIO_OUTPUT_DEVICE (liste : `python -m sounddevice`)."
        )

    @staticmethod
    def _close_stream(stream, abort: bool) -> None:
        try:
            stream.abort() if abort else stream.stop()
        except Exception:  # already stopped or unplugged
            log.debug("could not stop the audio output stream", exc_info=True)
        try:
            stream.close()
        except Exception:
            log.debug("could not close the audio output stream", exc_info=True)
