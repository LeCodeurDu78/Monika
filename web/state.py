"""Shared state of the 3D companion (Monika)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, asdict

VALID_STATES = ("idle", "listening", "thinking", "speaking")


@dataclass
class AvatarState:
    state: str = "idle"
    amplitude: float = 0.0  # 0..1, drives the jaw opening while "speaking"
    text: str = ""  # last sentence spoken/heard (optional, debug display)
    updated_at: float = 0.0


_lock = threading.Lock()
_state = AvatarState(updated_at=time.time())


def set_state(state: str, amplitude: float = 0.0, text: str = "") -> None:
    """Update the companion's state. Thread-safe, callable from any thread."""
    if state not in VALID_STATES:
        raise ValueError(f"État inconnu : {state!r} (attendu parmi {VALID_STATES})")
    with _lock:
        _state.state = state
        _state.amplitude = max(0.0, min(1.0, amplitude))
        if text:
            _state.text = text
        _state.updated_at = time.time()


def set_amplitude(amplitude: float) -> None:
    """Update the amplitude only (used during audio playback for lip sync)."""
    with _lock:
        _state.amplitude = max(0.0, min(1.0, amplitude))
        _state.updated_at = time.time()


def snapshot() -> dict:
    """Return a copy of the current state, ready for JSON serialisation."""
    with _lock:
        return asdict(_state)
