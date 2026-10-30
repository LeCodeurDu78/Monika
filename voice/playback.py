"""Audio output through pacat (PulseAudio/PipeWire), reporting the playback level for the avatar."""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections import deque
from collections.abc import Callable

import numpy as np

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


class PacatSink:
    """One playback stream: open, write PCM chunks, then close (waits for the sound to finish)."""

    def __init__(
        self,
        sample_rate: int = 24000,
        device: str = "",
        command: str = "pacat",
        latency_ms: int = 100,
        on_level: LevelCallback | None = None,
    ) -> None:
        self._rate = sample_rate
        self._device = device
        self._command = command
        self._latency = latency_ms / 1000
        self._on_level = on_level
        self._window = max(1, sample_rate * LEVEL_WINDOW_MS // 1000)
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr = b""
        self._drain: asyncio.Task | None = None
        self._emitter: asyncio.Task | None = None
        self._timeline: deque[tuple[float, float]] = deque()
        self._wake = asyncio.Event()
        self._clock_end = (
            0.0  # Loop time at which everything written so far has been played
        )

    @property
    def is_open(self) -> bool:
        return self._proc is not None

    async def open(self) -> None:
        exe = shutil.which(self._command)
        if exe is None:
            raise PlaybackError(
                f"{self._command} est introuvable : installe libpulse "
                "(sudo pacman -S libpulse) puis vérifie que `pactl info` répond."
            )
        args = [
            exe,
            "--playback",
            "--raw",
            "--format=s16le",
            f"--rate={self._rate}",
            "--channels=1",
            f"--latency-msec={int(self._latency * 1000)}",
            "--client-name=Monika",
            "--stream-name=Voix",
        ]
        if self._device:
            args.append(f"--device={self._device}")
        self._proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        self._stderr = b""
        self._timeline.clear()
        self._clock_end = 0.0
        loop = asyncio.get_running_loop()
        self._drain = loop.create_task(self._drain_stderr(self._proc))
        if self._on_level is not None:
            self._emitter = loop.create_task(self._emit_levels())

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        # Keep the last messages for diagnostics and never let the pipe fill up
        assert proc.stderr is not None
        while chunk := await proc.stderr.read(1024):
            self._stderr = (self._stderr + chunk)[-2000:]

    def _failure(self) -> PlaybackError:
        reason = self._stderr.decode("utf-8", "replace").strip().splitlines()
        where = f"sortie « {self._device} »" if self._device else "sortie par défaut"
        return PlaybackError(
            f"La sortie audio ne répond plus ({where}) : {reason[-1] if reason else 'pacat s’est arrêté'}. "
            "Vérifie la sortie avec `pactl list short sinks` (réglage AUDIO_OUTPUT_DEVICE)."
        )

    async def write(self, pcm: np.ndarray) -> None:
        """Send int16 mono samples; waits when the audio server has enough to play."""
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise PlaybackError("La sortie audio n'est pas ouverte.")
        if pcm.size == 0:
            return
        self._schedule_levels(pcm)
        try:
            proc.stdin.write(pcm.astype(np.int16).tobytes())
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            await asyncio.sleep(0.05)  # Let the stderr reader catch the last message
            raise self._failure() from None

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
        proc = self._proc
        if proc is None:
            return
        if proc.stdin is not None and not proc.stdin.is_closing():
            try:
                proc.stdin.close()
            except OSError:
                pass
        remaining = max(0.0, self._clock_end - asyncio.get_running_loop().time())
        try:
            await asyncio.wait_for(proc.wait(), remaining + 5)
        except TimeoutError:
            proc.kill()
            await proc.wait()
        await self._release()

    async def stop(self) -> None:
        """Cut the sound immediately."""
        proc = self._proc
        if proc is None:
            return
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        await self._release()

    async def _release(self) -> None:
        self._proc = None
        for task in (self._emitter, self._drain):
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self._emitter = self._drain = None
        self._timeline.clear()
        self._notify(0.0)
