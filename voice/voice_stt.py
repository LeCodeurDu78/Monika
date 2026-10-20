"""Reconnaissance vocale locale via faster-whisper (CTranslate2)."""

import threading

import numpy as np
from faster_whisper import WhisperModel
from core.settings import settings

STT_MODEL_SIZE = settings.VOICE_STT_MODEL
STT_DEVICE = settings.VOICE_STT_DEVICE
STT_COMPUTE_TYPE = settings.VOICE_STT_COMPUTE_TYPE
STT_LANGUAGE = settings.VOICE_STT_LANGUAGE

_model = None
_model_lock = threading.Lock()  # voice mode and Discord calls may ask for the model at the same time


def _get_model() -> WhisperModel:
    global _model
    with _model_lock:
        if _model is None:
            print(f"Chargement du modèle Whisper '{STT_MODEL_SIZE}' ({STT_DEVICE}/{STT_COMPUTE_TYPE})...")
            _model = WhisperModel(STT_MODEL_SIZE, device=STT_DEVICE, compute_type=STT_COMPUTE_TYPE)
        return _model


def transcribe(audio_float32: np.ndarray, sample_rate: int = 16000) -> str:
    """Transcribe a mono float32 [-1, 1] array."""
    if audio_float32.size == 0:
        return ""
    if sample_rate != 16000:
        raise ValueError(
            f"transcribe() attend du 16000 Hz, reçu {sample_rate} Hz. "
            "Rééchantillonne avant d'appeler cette fonction."
        )

    segments, _info = _get_model().transcribe(
        audio_float32,
        language=STT_LANGUAGE,
        vad_filter=True,
        beam_size=5,
    )
    return " ".join(segment.text.strip() for segment in segments).strip()


def transcribe_file(path: str) -> str:
    """Transcribe an audio file (ogg, opus, mp3, wav, m4a...); faster-whisper decodes it itself (PyAV)."""
    segments, _info = _get_model().transcribe(
        str(path),
        language=STT_LANGUAGE,
        vad_filter=True,
        beam_size=5,
    )
    return " ".join(segment.text.strip() for segment in segments).strip()
