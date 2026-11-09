"""Shared sounddevice/PortAudio helpers for the microphone and the audio output."""

from __future__ import annotations

import sys
from typing import Any


class BackendUnavailable(RuntimeError):
    """sounddevice, or the PortAudio library it needs, is not installed."""


def sounddevice() -> Any:
    """Import sounddevice lazily so a missing audio backend only disables the voice."""
    try:
        import sounddevice as sd
    except (ImportError, OSError) as exc:
        raise BackendUnavailable(
            "Le moteur audio « sounddevice » est indisponible. Installe-le avec "
            "`pip install sounddevice` ; sous Linux, installe aussi PortAudio "
            "(`sudo apt install libportaudio2` ou `sudo pacman -S portaudio`)."
        ) from exc
    return sd


def extra_settings(sd: Any) -> Any:
    """Let WASAPI convert the device's mix rate on Windows instead of refusing 16 kHz."""
    if sys.platform != "win32":
        return None
    try:
        return sd.WasapiSettings(auto_convert=True)
    except (AttributeError, TypeError):
        return None


def device_arg(value: str = "") -> int | str | None:
    """A sounddevice device argument: None (default), an index, or a name substring.

    The old PulseAudio names are accepted as "default" so an existing .env keeps working.
    """
    raw = str(value or "").strip()
    if not raw or raw in {"@DEFAULT_SOURCE@", "@DEFAULT_SINK@"}:
        return None
    if raw.isdigit():
        return int(raw)
    return raw
