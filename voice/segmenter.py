"""Split a continuous microphone stream into utterances.

Frame based and free of any audio device: it is fed fixed-size int16 frames and returns a finished
utterance (float32, 16 kHz) when the speaker stops. The same class serves the local microphone and the
audio received from a Discord call.
"""

from collections import deque
from typing import Callable, Optional

import numpy as np


class Segmenter:
    def __init__(
        self,
        is_speech: Callable[[bytes], bool],
        sample_rate: int = 16000,
        frame_ms: int = 30,
        silence_ms: int = 900,
        min_speech_ms: int = 250,
        preroll_ms: int = 300,
        max_seconds: float = 20.0,
    ) -> None:
        self._is_speech = is_speech
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self._silence_frames = max(1, silence_ms // frame_ms)
        self._min_speech_frames = max(1, min_speech_ms // frame_ms)
        self._max_frames = int(max_seconds * 1000 / frame_ms)
        self._preroll: deque = deque(maxlen=max(0, preroll_ms // frame_ms))
        self._reset()

    def _reset(self) -> None:
        self._frames: list[np.ndarray] = []
        self._in_speech = False
        self._speech_frames = 0
        self._silent_run = 0

    def _voiced(self, frame: np.ndarray) -> bool:
        return bool(self._is_speech(frame.tobytes()))

    def push(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """Feed one frame; return the finished utterance as float32 [-1, 1] when it just ended."""
        voiced = self._voiced(frame)
        if not self._in_speech:
            if not voiced:
                self._preroll.append(frame.copy())
                return None
            self._in_speech = True
            self._frames = list(self._preroll) + [frame.copy()]
            self._preroll.clear()
            self._speech_frames = 1
            self._silent_run = 0
            return None

        self._frames.append(frame.copy())
        if voiced:
            self._speech_frames += 1
            self._silent_run = 0
        else:
            self._silent_run += 1

        if self._silent_run >= self._silence_frames or len(self._frames) >= self._max_frames:
            long_enough = self._speech_frames >= self._min_speech_frames
            audio = np.concatenate(self._frames).astype(np.float32) / 32768.0 if long_enough else None
            self._reset()
            return audio
        return None
