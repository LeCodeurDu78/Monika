"""Voice-related errors; every message is in French and ready to display."""

from __future__ import annotations


class VoiceError(Exception):
    """Base class for audio and speech recognition failures."""


class AudioError(VoiceError):
    """The microphone cannot be used."""


class SttError(VoiceError):
    """The speech recognition model cannot be loaded or used."""


class TtsError(VoiceError):
    """The speech synthesis model or the reference voice cannot be used."""


class PlaybackError(VoiceError):
    """The audio output cannot be used."""
