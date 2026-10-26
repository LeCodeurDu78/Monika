"""Local speech recognition (faster-whisper) and a filter for Whisper's well-known hallucinations."""

from __future__ import annotations

import asyncio
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from monika.config import Settings
from monika.paths import Paths
from voice.audio import SAMPLE_RATE
from voice.errors import SttError

MIN_AUDIO_S = 0.25

# Phrases Whisper invents on noise or silence (normalized form, see normalize_text)
HALLUCINATIONS = (
    "sous titres realises par la communaute d amara org", "sous titrage st", "sous titrage societe radio canada",
    "amara org", "sous titres par", "merci d avoir regarde", "n oubliez pas de vous abonner",
    "abonnez vous", "a bientot pour une nouvelle video", "j espere que cette video vous a plu",
    "thanks for watching", "subtitles by",
)


def normalize_text(text: str) -> str:
    """Lowercase, no accents, no punctuation: 'Au revoir, Monika !' -> 'au revoir monika'."""
    folded = "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", folded).split())


def filter_reason(text: str, avg_logprob: float, no_speech_prob: float) -> str | None:
    """Return why a transcript should be dropped (None = keep it)."""
    norm = normalize_text(text)
    if not norm:
        return "vide"
    for phrase in HALLUCINATIONS:
        if phrase in norm and len(norm) <= len(phrase) + 30:
            return "hallucination connue"
    words = norm.split()
    if len(words) >= 6 and len(set(words)) <= 2:
        return "répétition"
    if no_speech_prob > 0.7 and avg_logprob < -0.8:
        return "bruit"
    if avg_logprob < -1.6:
        return "peu fiable"
    return None


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str
    audio_s: float
    elapsed_s: float
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0
    filtered: str | None = None  # Reason the text was rejected, if it was


def _default_model_factory(settings: Settings, paths: Paths) -> Any:
    from faster_whisper import WhisperModel

    return WhisperModel(
        settings.stt_model,
        device="cpu" if settings.stt_device == "auto" else settings.stt_device,
        compute_type=settings.stt_compute_type,
        cpu_threads=settings.stt_cpu_threads or max(1, (os.cpu_count() or 2) // 2),
        download_root=str(paths.voice / "models" / "whisper"))


class Transcriber:
    """Loads the Whisper model once (lazily) and transcribes utterances on a worker thread."""

    def __init__(self, settings: Settings, paths: Paths,
                 model_factory: Callable[[Settings, Paths], Any] | None = None) -> None:
        self._s = settings
        self._paths = paths
        self._factory = model_factory or _default_model_factory
        self._model: Any = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="monika-stt")
        self._lock = asyncio.Lock()

    async def load(self) -> float:
        """Load the model (downloads it on first use); returns the loading time in seconds."""
        async with self._lock:
            if self._model is not None:
                return 0.0
            start = time.perf_counter()
            try:
                self._model = await asyncio.get_running_loop().run_in_executor(
                    self._pool, self._factory, self._s, self._paths)
            except Exception as exc:
                raise SttError(
                    f"Impossible de charger le modèle Whisper « {self._s.stt_model} » : "
                    f"{type(exc).__name__}: {str(exc)[:200]}. Le premier lancement télécharge le modèle : "
                    "vérifie ta connexion (ou le réglage STT_MODEL / STT_DEVICE).") from exc
            return time.perf_counter() - start

    async def transcribe(self, audio: np.ndarray) -> Transcript:
        await self.load()
        return await asyncio.get_running_loop().run_in_executor(self._pool, self._run, audio)

    def _run(self, audio: np.ndarray) -> Transcript:
        start = time.perf_counter()
        audio_s = len(audio) / SAMPLE_RATE
        if audio_s < MIN_AUDIO_S:
            return Transcript("", self._s.stt_language, audio_s, 0.0, filtered="trop court")
        segments, info = self._model.transcribe(
            audio, language=self._s.stt_language or None, beam_size=self._s.stt_beam_size,
            initial_prompt=self._s.stt_prompt or None,
            condition_on_previous_text=False,  # Each utterance stands alone: no drift between sentences
            vad_filter=False,  # The utterance is already segmented by our own VAD
            without_timestamps=True, temperature=[0.0, 0.2, 0.4], no_speech_threshold=0.6)
        segs = list(segments)  # The generator does the actual work
        text = " ".join(s.text.strip() for s in segs).strip()
        avg = float(np.mean([s.avg_logprob for s in segs])) if segs else -10.0
        no_speech = max((s.no_speech_prob for s in segs), default=1.0)
        return Transcript(text, getattr(info, "language", self._s.stt_language), audio_s,
                          time.perf_counter() - start, avg, float(no_speech),
                          filter_reason(text, avg, float(no_speech)))

    def close(self) -> None:
        self._pool.shutdown(wait=False)
