"""XTTS v2 speech synthesis: clones the reference voice and streams the audio as PCM chunks."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import wave
import zipfile
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from monika.config import Settings
from monika.paths import Paths
from voice.errors import TtsError

AUDIO_SUFFIXES = (".wav", ".flac", ".mp3", ".ogg", ".m4a")
MIN_REFERENCE_S = 3.0  # XTTS needs a few seconds to clone a voice; 6 to 30 s clones it well
LONG_REFERENCE_S = 60.0
LICENSE_URL = "https://coqui.ai/cpml"

LICENSE_MESSAGE = (
    "XTTS v2 est publié sous la licence CPML de Coqui (usage non commercial, " + LICENSE_URL + "). "
    "Pour télécharger le modèle (environ 1,9 Go), accepte-la en ajoutant TTS_ACCEPT_LICENSE=true "
    "dans ton .env, puis relance."
)


def find_reference_voice(folder: Path) -> list[Path]:
    """Audio files placed directly in the voice folder (the models/ and cache/ subfolders are ignored)."""
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES and not p.name.startswith("."))


def audio_seconds(path: Path) -> float | None:
    """Duration of a WAV file; None for other formats, which are not decoded here."""
    if path.suffix.lower() != ".wav":
        return None
    try:
        with wave.open(str(path), "rb") as handle:
            return handle.getnframes() / float(handle.getframerate())
    except (wave.Error, EOFError, OSError):
        return None


def to_pcm16(chunk: Any) -> np.ndarray:
    """Convert a float waveform chunk (numpy or torch) to mono 16-bit samples."""
    if hasattr(chunk, "detach"):
        chunk = chunk.detach().cpu().float().numpy()
    arr = np.asarray(chunk)
    if arr.dtype == np.int16:
        return arr.reshape(-1)
    return (np.clip(arr.astype(np.float32).reshape(-1), -1.0, 1.0) * 32767.0).astype(np.int16)


def _to_numpy(tensor: Any) -> np.ndarray:
    if hasattr(tensor, "detach"):
        tensor = tensor.detach().cpu().float().numpy()
    return np.asarray(tensor, dtype=np.float32)


def _to_torch(array: np.ndarray, device: str) -> Any:
    import torch

    return torch.from_numpy(array).to(device)


def _is_out_of_memory(exc: BaseException) -> bool:
    return type(exc).__name__ == "OutOfMemoryError" or "out of memory" in str(exc).lower()


@dataclass
class XttsRuntime:
    """What the loader hands back: the model and the settings XTTS itself recommends."""

    model: Any
    device: str
    sample_rate: int = 24000
    cond_params: dict[str, Any] = field(default_factory=dict)
    gen_params: dict[str, Any] = field(default_factory=dict)
    fallback_reason: str | None = None


class TorchGpu:
    """GPU memory figures (None when torch or CUDA is not there)."""

    def info(self) -> dict[str, float] | None:
        try:
            import torch

            if not torch.cuda.is_available():
                return None
            free, total = torch.cuda.mem_get_info()
            return {"total_mb": total / 2**20, "free_mb": free / 2**20,
                    "peak_mb": torch.cuda.max_memory_allocated() / 2**20,
                    "name": torch.cuda.get_device_name(0)}  # type: ignore[dict-item]
        except Exception:
            return None

    def reset_peak(self) -> None:
        try:
            import torch

            torch.cuda.reset_peak_memory_stats()
        except Exception:
            pass


def _model_files(settings: Settings, paths: Paths) -> Path:
    """Folder holding XTTS v2 (config.json, model.pth, vocab.json...), downloaded on first use."""
    from importlib import resources

    from huggingface_hub import snapshot_download

    listing = json.loads(resources.files("TTS").joinpath(".models.json").read_text(encoding="utf-8"))
    entry = listing["tts_models"]["multilingual"]["multi-dataset"]["xtts_v2"]
    if "repo_id" not in entry:
        raise TtsError("Cette version de coqui-tts ne décrit pas XTTS v2 comme attendu : "
                       "mets-la à jour avec `pip install -U coqui-tts`.")
    options = {"cache_dir": str(paths.voice / "models" / "xtts"), "ignore_patterns": entry.get("ignore")}
    try:
        return Path(snapshot_download(entry["repo_id"], local_files_only=True, **options))
    except Exception:  # Not downloaded yet
        pass
    if not settings.tts_accept_license:
        raise TtsError(LICENSE_MESSAGE)
    try:
        return Path(snapshot_download(entry["repo_id"], **options))
    except Exception as exc:
        raise TtsError(f"Téléchargement de XTTS v2 impossible ({type(exc).__name__}: {str(exc)[:200]}). "
                       "Vérifie ta connexion puis relance.") from exc


def load_xtts(settings: Settings, paths: Paths) -> XttsRuntime:
    """Load XTTS v2 on the GPU (or CPU) with Coqui's own recommended settings."""
    try:
        import torch
    except ImportError as exc:
        raise TtsError("PyTorch n'est pas installé : installe torch et torchaudio (version CUDA pour ton GPU), "
                       "voir le README, section Voix.") from exc
    try:
        from TTS.tts.configs.xtts_config import XttsConfig
        from TTS.tts.models.xtts import Xtts
    except ImportError as exc:
        raise TtsError("coqui-tts est introuvable ou incomplet : `pip install coqui-tts[codec]` "
                       "(avec PyTorch 2.9 ou plus, il faut aussi FFmpeg : sudo pacman -S ffmpeg) "
                       f"— détail : {type(exc).__name__}: {str(exc).strip()[:200]}") from exc
    folder = _model_files(settings, paths)
    wanted = settings.tts_device
    device = "cuda" if wanted in ("auto", "cuda") and torch.cuda.is_available() else "cpu"
    if wanted == "cuda" and device == "cpu":
        raise TtsError("TTS_DEVICE=cuda mais PyTorch ne voit aucun GPU CUDA (torch.cuda.is_available() est faux) : "
                       "installe la version CUDA de torch, ou mets TTS_DEVICE=auto.")
    config = XttsConfig()
    config.load_json(str(folder / "config.json"))
    model = Xtts.init_from_config(config) if hasattr(Xtts, "init_from_config") else Xtts(config)
    model.load_checkpoint(config, checkpoint_dir=str(folder), eval=True, use_deepspeed=False)
    fallback = None
    try:
        model.to(device)
    except Exception as exc:
        if device == "cuda" and wanted == "auto" and _is_out_of_memory(exc):
            model.to("cpu")
            torch.cuda.empty_cache()
            device = "cpu"
            fallback = "pas assez de mémoire GPU pour charger XTTS v2"
        else:
            raise
    cond_names = {"max_ref_length": "max_ref_len", "gpt_cond_len": "gpt_cond_len",
                  "gpt_cond_chunk_len": "gpt_cond_chunk_len", "sound_norm_refs": "sound_norm_refs"}
    cond = {key: getattr(config, name) for key, name in cond_names.items() if hasattr(config, name)}
    gen = {name: getattr(config, name) for name in
           ("temperature", "length_penalty", "repetition_penalty", "top_k", "top_p") if hasattr(config, name)}
    return XttsRuntime(model, device, int(getattr(config.audio, "output_sample_rate", 24000)),
                       cond, gen, fallback)


Loader = Callable[[Settings, Paths], XttsRuntime]


class XttsEngine:
    """Loads XTTS once on a single worker thread and turns sentences into streams of PCM chunks."""

    def __init__(self, settings: Settings, paths: Paths, loader: Loader | None = None,
                 to_tensor: Callable[[np.ndarray, str], Any] | None = None, gpu: Any = None) -> None:
        self._s = settings
        self._paths = paths
        self._loader = loader or load_xtts
        self._to_tensor = to_tensor or _to_torch
        self.gpu = gpu if gpu is not None else TorchGpu()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="monika-tts")
        self._lock = asyncio.Lock()
        self._rt: XttsRuntime | None = None
        self._gpt: Any = None
        self._speaker: Any = None
        self.voice_files: list[Path] = []
        self.warnings: list[str] = []
        self.latents_cached = False
        self.load_seconds = 0.0
        self.sample_rate = 24000

    @property
    def loaded(self) -> bool:
        return self._rt is not None

    @property
    def device(self) -> str:
        return self._rt.device if self._rt else ""

    @property
    def fallback_reason(self) -> str | None:
        return self._rt.fallback_reason if self._rt else None

    async def run(self, fn: Callable[..., Any], *args: Any) -> Any:
        """Run a blocking call on the single synthesis thread."""
        return await asyncio.get_running_loop().run_in_executor(self._pool, fn, *args)

    async def load(self) -> float:
        """Load model and voice (downloads the model on first use); returns the loading time in seconds."""
        async with self._lock:
            if self._rt is not None:
                return 0.0
            start = time.perf_counter()
            try:
                await self.run(self._load_blocking)
            except TtsError:
                raise
            except Exception as exc:
                raise TtsError(f"Impossible de charger XTTS v2 : {type(exc).__name__}: {str(exc)[:250]}. "
                               "Si c'est un manque de mémoire GPU, mets TTS_DEVICE=cpu.") from exc
            self.load_seconds = time.perf_counter() - start
            return self.load_seconds

    def _load_blocking(self) -> None:
        files = self._check_voice()
        runtime = self._loader(self._s, self._paths)
        self._rt = runtime
        self.sample_rate = runtime.sample_rate
        try:
            self._gpt, self._speaker = self._conditioning(runtime, files)
        except Exception as exc:
            self._rt = None
            raise TtsError(f"Impossible d'analyser ta voix de référence : {type(exc).__name__}: "
                           f"{str(exc)[:250]}. Essaie un fichier .wav propre de 6 à 30 s.") from exc

    def _check_voice(self) -> list[Path]:
        folder = self._paths.voice
        files = find_reference_voice(folder)
        if not files:
            raise TtsError(f"Aucune voix de référence dans {folder} : dépose-y un enregistrement de ta voix "
                           f"({', '.join(AUDIO_SUFFIXES)}), de 6 à 30 secondes, sans bruit ni musique.")
        self.warnings = []
        for path in files:
            seconds = audio_seconds(path)
            if seconds is not None and seconds < MIN_REFERENCE_S:
                raise TtsError(f"La voix de référence {path.name} est trop courte ({seconds:.1f} s) : "
                               "il faut au moins 3 s, idéalement 6 à 30 s.")
            if seconds is not None and seconds > LONG_REFERENCE_S:
                self.warnings.append(f"{path.name} dure {seconds:.0f} s : seul le début sera utilisé "
                                     "(6 à 30 s suffisent).")
        self.voice_files = files
        return files

    def _cache_file(self, runtime: XttsRuntime, files: list[Path]) -> Path:
        parts = [f"{p.name}:{p.stat().st_size}:{p.stat().st_mtime_ns}" for p in files]
        digest = hashlib.sha256("|".join([*parts, json.dumps(runtime.cond_params, sort_keys=True,
                                                             default=str)]).encode()).hexdigest()[:16]
        return self._paths.voice / "cache" / f"latents-{digest}.npz"

    def _conditioning(self, runtime: XttsRuntime, files: list[Path]) -> tuple[Any, Any]:
        cache = self._cache_file(runtime, files)
        try:
            with np.load(cache) as data:
                gpt, speaker = data["gpt"], data["speaker"]
            self.latents_cached = True
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            self.latents_cached = False
            gpt_t, speaker_t = runtime.model.get_conditioning_latents(
                audio_path=[str(p) for p in files], **runtime.cond_params)
            gpt, speaker = _to_numpy(gpt_t), _to_numpy(speaker_t)
            cache.parent.mkdir(parents=True, exist_ok=True)
            np.savez(cache, gpt=gpt, speaker=speaker)
        return self._to_tensor(gpt, runtime.device), self._to_tensor(speaker, runtime.device)

    def synth(self, text: str) -> Iterator[np.ndarray]:
        """Blocking generator of int16 chunks for one sentence; drive it with run() from the event loop."""
        if self._rt is None:
            raise TtsError("La synthèse vocale n'est pas chargée.")
        try:
            stream = self._rt.model.inference_stream(
                text, self._s.tts_language, self._gpt, self._speaker,
                stream_chunk_size=self._s.tts_stream_chunk, speed=self._s.tts_speed,
                enable_text_splitting=False, **self._rt.gen_params)
            for chunk in stream:
                pcm = to_pcm16(chunk)
                if pcm.size:
                    yield pcm
        except TtsError:
            raise
        except Exception as exc:
            if _is_out_of_memory(exc):
                raise TtsError("Mémoire GPU insuffisante pendant la synthèse : mets TTS_DEVICE=cpu "
                               "(plus lent) ou ferme les programmes qui utilisent le GPU.") from exc
            raise TtsError(f"La synthèse a échoué : {type(exc).__name__}: {str(exc)[:250]}") from exc

    def close(self) -> None:
        self._pool.shutdown(wait=False)
        self._rt = None
