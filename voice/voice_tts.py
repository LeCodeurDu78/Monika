"""Local speech synthesis with XTTS v2 (Coqui TTS)."""

import re
import threading

import numpy as np
import soundfile as sf
import torch
import torchaudio

torch.backends.cudnn.enabled = False


def _load_with_soundfile(path, *args, **kwargs):
    data, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    waveform = torch.from_numpy(data.T)
    return waveform, sample_rate


torchaudio.load = _load_with_soundfile

from TTS.api import TTS

from web.lipsync import speak_with_avatar
from core.settings import settings
from voice.voice_audio import play_audio

XTTS_MODEL_NAME = settings.VOICE_XTTS_MODEL
XTTS_DEVICE = settings.VOICE_XTTS_DEVICE
XTTS_LANGUAGE = settings.VOICE_XTTS_LANGUAGE
XTTS_SPEAKER_WAV = settings.VOICE_XTTS_SPEAKER_WAV

_tts = None


def _get_tts() -> TTS:
    global _tts
    if _tts is None:
        if not XTTS_SPEAKER_WAV.exists():
            raise FileNotFoundError(
                f"Échantillon de voix de référence introuvable : {XTTS_SPEAKER_WAV}\n"
                f"Place un fichier .wav de 5 à 15 secondes (voix claire, peu de bruit) "
                f"à cet emplacement, ou change VOICE_XTTS_SPEAKER_WAV dans .env."
            )

        device = XTTS_DEVICE
        if device == "cuda" and not torch.cuda.is_available():
            print("CUDA indisponible, bascule de XTTS sur CPU (ce sera nettement plus lent).")
            device = "cpu"

        print(f"Chargement du modèle XTTS v2 '{XTTS_MODEL_NAME}' sur {device}...")
        _tts = TTS(XTTS_MODEL_NAME).to(device)
    return _tts


def _clean_for_speech(text: str) -> str:
    """Clean the text before synthesis."""
    text = re.sub(r"```.*?```", " J'ai affiché du code dans le terminal. ", text, flags=re.DOTALL)
    text = re.sub(r"[*_`#]+", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


_synth_lock = threading.Lock()  # XTTS is not re-entrant: local speech and Discord calls take turns


def synthesize(text: str):
    """Synthesize one piece of text. Returns (float32 audio, sample_rate), or None when there is nothing to say."""
    cleaned = _clean_for_speech(text)
    if not cleaned:
        return None
    tts = _get_tts()
    with _synth_lock:
        wav = tts.tts(
            text=cleaned,
            speaker_wav=str(XTTS_SPEAKER_WAV),
            language=XTTS_LANGUAGE,
        )
    if not wav:
        return None
    return np.asarray(wav, dtype=np.float32), tts.synthesizer.output_sample_rate


def play_synthesized(audio: np.ndarray, sample_rate: int, text: str = "") -> None:
    """Play synthesized audio on the speakers while driving the avatar (blocks until done or stopped)."""
    speak_with_avatar(audio, sample_rate, play_audio, text=text)


def speak(text: str) -> None:
    """Synthesize `text` and play it right away on the default speakers."""
    result = synthesize(text)
    if result is None:
        return
    audio, sample_rate = result
    play_synthesized(audio, sample_rate, text=_clean_for_speech(text))
