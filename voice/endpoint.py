"""End-of-sentence detection: turns per-frame speech probabilities into complete utterances."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import numpy as np

from voice.audio import FRAME_SAMPLES, SAMPLE_RATE

FRAME_MS = FRAME_SAMPLES * 1000 / SAMPLE_RATE  # 32 ms
KEEP_TAIL_MS = 150  # A little trailing silence stays, so the last word is not clipped


@dataclass(frozen=True)
class Utterance:
    audio: np.ndarray  # float32, 16 kHz mono
    duration_s: float
    truncated: bool = False  # True when cut at the maximum length instead of a pause


class Endpointer:
    """Hysteresis state machine: speech starts after a few confident frames and ends after a pause."""

    def __init__(self, speech_threshold: float = 0.5, silence_threshold: float | None = None,
                 min_speech_ms: int = 250, end_silence_ms: int = 700, pre_roll_ms: int = 300,
                 max_utterance_s: float = 30.0, onset_frames: int = 3) -> None:
        self._speech = speech_threshold
        # Between the two thresholds a frame neither starts nor ends anything
        self._silence = speech_threshold - 0.15 if silence_threshold is None else silence_threshold
        self._min_speech_frames = math.ceil(min_speech_ms / FRAME_MS)
        self._end_frames = math.ceil(end_silence_ms / FRAME_MS)
        self._max_frames = math.ceil(max_utterance_s * 1000 / FRAME_MS)
        self._onset_needed = onset_frames
        self._pre_roll_frames = math.ceil(pre_roll_ms / FRAME_MS)
        self._keep_tail = math.ceil(KEEP_TAIL_MS / FRAME_MS)
        self.reset()

    def reset(self) -> None:
        self._pre_roll: deque[np.ndarray] = deque(maxlen=self._pre_roll_frames + self._onset_needed)
        self._onset = 0
        self._in_speech = False
        self._frames: list[np.ndarray] = []
        self._speech_frames = 0
        self._silence_run = 0

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def feed(self, frame: np.ndarray, probability: float) -> Utterance | None:
        """Consume one frame; returns the utterance when a sentence just ended."""
        if not self._in_speech:
            self._pre_roll.append(frame)
            self._onset = self._onset + 1 if probability >= self._speech else 0
            if self._onset >= self._onset_needed:
                self._in_speech = True
                self._frames = list(self._pre_roll)  # Includes the audio just before the onset
                self._speech_frames = self._onset
                self._silence_run = 0
            return None
        self._frames.append(frame)
        if probability >= self._speech:
            self._speech_frames += 1
        self._silence_run = 0 if probability >= self._silence else self._silence_run + 1
        if self._silence_run >= self._end_frames:
            return self._finish(truncated=False)
        if len(self._frames) >= self._max_frames:
            return self._finish(truncated=True)
        return None

    def _finish(self, truncated: bool) -> Utterance | None:
        frames, speech_frames, silence = self._frames, self._speech_frames, self._silence_run
        self.reset()
        if speech_frames < self._min_speech_frames:
            return None  # A click or cough, not a sentence
        frames = frames[: len(frames) - max(0, silence - self._keep_tail)]
        audio = np.concatenate(frames).astype(np.float32) / 32768.0
        return Utterance(audio, len(audio) / SAMPLE_RATE, truncated)
