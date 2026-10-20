"""Drive the avatar while Monika speaks."""

from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np

from web import state as avatar_state

# Window size used to compute the amplitude envelope.
_HOP_SECONDS = 0.05


def _compute_envelope(audio: np.ndarray, sample_rate: int, hop_seconds: float = _HOP_SECONDS) -> np.ndarray:
    """RMS over sliding, non-overlapping windows, roughly normalised to [0, 1]."""
    hop = max(1, int(sample_rate * hop_seconds))
    n_hops = max(1, int(np.ceil(len(audio) / hop)))
    envelope = np.zeros(n_hops, dtype=np.float32)

    for i in range(n_hops):
        chunk = audio[i * hop : (i + 1) * hop]
        if chunk.size == 0:
            continue
        envelope[i] = float(np.sqrt(np.mean(np.square(chunk))))

    peak = float(envelope.max()) if envelope.size else 0.0
    if peak > 1e-6:
        envelope = np.sqrt(envelope / peak)
    return envelope


def speak_with_avatar(audio: np.ndarray, sample_rate: int, play_audio: Callable[[np.ndarray, int], None], text: str = "") -> None:
    """Play `audio` (as `play_audio` alone would) while driving the avatar."""
    if audio.size == 0:
        return

    envelope = _compute_envelope(audio, sample_rate)
    duration = len(audio) / float(sample_rate)
    hop_seconds = duration / len(envelope) if len(envelope) else _HOP_SECONDS

    avatar_state.set_state("speaking", amplitude=0.0, text=text)

    playback_thread = threading.Thread(target=play_audio, args=(audio, sample_rate), daemon=True)
    start = time.monotonic()
    playback_thread.start()

    try:
        for value in envelope:
            avatar_state.set_amplitude(float(value))
            target = start + hop_seconds
            remaining = target - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            start = target
    finally:
        playback_thread.join()
        avatar_state.set_state("idle", amplitude=0.0)
