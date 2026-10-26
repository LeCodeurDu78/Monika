"""Microphone capture through parec (PulseAudio/PipeWire), already resampled to 16 kHz mono."""

from __future__ import annotations

import asyncio
import shutil
from typing import Protocol

import numpy as np

from voice.errors import AudioError

SAMPLE_RATE = 16000
FRAME_SAMPLES = 512  # 32 ms, the window size the voice activity model expects
FRAME_BYTES = FRAME_SAMPLES * 2  # 16-bit samples


class AudioSource(Protocol):
    async def start(self) -> None: ...
    async def read_frame(self) -> np.ndarray: ...
    async def close(self) -> None: ...


class ParecSource:
    """Reads fixed-size int16 frames from a parec subprocess."""

    def __init__(self, device: str = "@DEFAULT_SOURCE@", command: str = "parec") -> None:
        self._device = device
        self._command = command
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr = b""
        self._drain: asyncio.Task | None = None

    async def start(self) -> None:
        exe = shutil.which(self._command)
        if exe is None:
            raise AudioError(f"{self._command} est introuvable : installe libpulse "
                             "(sudo pacman -S libpulse) puis vérifie que `pactl info` répond.")
        self._proc = await asyncio.create_subprocess_exec(
            exe, "--format=s16le", f"--rate={SAMPLE_RATE}", "--channels=1",
            f"--device={self._device}", "--latency-msec=30",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self._drain = asyncio.get_running_loop().create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        # Keep the last messages for diagnostics and never let the pipe fill up
        assert self._proc is not None and self._proc.stderr is not None
        while chunk := await self._proc.stderr.read(1024):
            self._stderr = (self._stderr + chunk)[-2000:]

    async def read_frame(self) -> np.ndarray:
        assert self._proc is not None and self._proc.stdout is not None, "start() d'abord"
        try:
            data = await self._proc.stdout.readexactly(FRAME_BYTES)
        except asyncio.IncompleteReadError:
            await asyncio.sleep(0.05)  # Let the stderr reader catch the last message
            reason = self._stderr.decode("utf-8", "replace").strip().splitlines()
            raise AudioError(
                f"Le micro ne répond plus (source « {self._device} ») : "
                f"{reason[-1] if reason else 'parec s’est arrêté'}. "
                "Vérifie la source avec `pactl list short sources` (réglage AUDIO_INPUT_DEVICE).") from None
        return np.frombuffer(data, dtype=np.int16)

    async def close(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), 3)
            except TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        if self._drain is not None:
            self._drain.cancel()
            await asyncio.gather(self._drain, return_exceptions=True)
