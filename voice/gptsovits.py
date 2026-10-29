"""GPT-SoVITS speech synthesis over its own HTTP API (`api_v2.py`, port 9880).

There is no gateway and no sidecar protocol here: Monika posts a synthesis request to GPT-SoVITS
itself and gets a WAV back. The emotion tags (`【happy2】` …) produced by `character/tts_emotion.py`
are resolved here: each run of text is spoken with the reference recording of its tag, taken from a
voice pack laid out like GPT-SoVITS's own:

    <pack>/character.json                  optional; "language" gives the transcript language
    <pack>/references/emotions.json        {"happy1": {"audio": "happy1.wav", "text": "…"}, …}
    <pack>/references/<clip>.wav           one reference recording per tag

A tag with no recording falls back to `neutral1` (or the pack's first tag), exactly like the voice
engines of the GPT-SoVITS family: the voice never pretends to feel something it has no clip for.

The engine is driven by `voice/speaker.py`: `synth()` is a blocking int16 PCM generator run on one
worker thread, so `Speaker` and `PacatSink` did not have to change when XTTS went away.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import socket
import time
import urllib.error
import urllib.request
import wave
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from monika.config import Settings
from monika.paths import Paths
from voice.errors import TtsError

log = logging.getLogger(__name__)

DEFAULT_TAG = "neutral1"
DEFAULT_SAMPLE_RATE = 32000  # Used until the warm-up says otherwise
CHUNK_SAMPLES = 20000        # ~0.6 s at 32 kHz: the mouth keeps moving between two chunks
WARMUP_TEXT = "Hello."       # Short and speakable in every GPT-SoVITS front-end
LOAD_TIMEOUT_S = 180.0       # The first request loads the model: give it time, it is a one-off
EMOTIONS_FILE = Path("references") / "emotions.json"

# `【tag】` as written by the emotion mapper (a tag is never part of what is spoken)
TAG_RE = re.compile(r"【\s*([^】]*?)\s*】")
# GPT-SoVITS languages; French has no front-end of its own (see language_code)
LANGUAGES = frozenset({"auto", "en", "zh", "ja", "ko", "yue"})
_NAMES = {"chinese": "zh", "english": "en", "japanese": "ja", "korean": "ko", "cantonese": "yue",
          "français": "en", "french": "en", "fr": "en"}
_LANGUAGES = {**{code: code for code in LANGUAGES}, **_NAMES}


# ------------------------------------------------------------------------------------------------ audio decoding
def wav_to_pcm(audio: bytes) -> tuple[np.ndarray, int]:
    """Decode a RIFF/WAVE blob into mono int16 samples; raises TtsError for anything else."""
    try:
        with wave.open(io.BytesIO(audio), "rb") as handle:
            channels, width, rate = handle.getnchannels(), handle.getsampwidth(), handle.getframerate()
            frames = handle.readframes(handle.getnframes())
    except (wave.Error, EOFError) as exc:
        raise TtsError("GPT-SoVITS a renvoyé un son illisible (fichier WAV attendu). Vérifie la version "
                       "d'api_v2.py lancée côté serveur.") from exc
    if width != 2:
        raise TtsError(f"GPT-SoVITS renvoie du PCM {width * 8} bits : seul le 16 bits est géré "
                       "(format WAV en 16 bits).")
    if not rate or not frames:
        raise TtsError("GPT-SoVITS a renvoyé un son vide : le texte n'était peut-être pas prononçable.")
    pcm = np.frombuffer(frames, dtype="<i2")
    if channels > 1:
        pcm = pcm.reshape(-1, channels).astype(np.int32).mean(axis=1).astype(np.int16)
    return np.ascontiguousarray(pcm.reshape(-1)), int(rate)


def resample(pcm: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """Linear resampling: a server answering at another rate must not play too fast or too slow."""
    if from_rate == to_rate or pcm.size == 0:
        return pcm
    count = max(1, int(round(pcm.size * to_rate / from_rate)))
    source = np.arange(pcm.size, dtype=np.float32)
    target = np.arange(count, dtype=np.float32) * (from_rate / to_rate)
    return np.interp(target, source, pcm.astype(np.float32)).astype(np.int16)


def chunks_of(pcm: np.ndarray, size: int = CHUNK_SAMPLES) -> Iterator[np.ndarray]:
    for start in range(0, len(pcm), size):
        piece = pcm[start:start + size]
        if piece.size:
            yield piece


# ------------------------------------------------------------------------------------------------ tagged text
def split_tagged(text: str) -> list[tuple[str | None, str]]:
    """`【a】x 【b】y` -> [("a", "x"), ("b", "y")]; text before the first tag has tag None.

    Consecutive runs with the same tag are merged: one request per reference switch, no more.
    """
    runs: list[tuple[str | None, str]] = []
    tag: str | None = None
    pos = 0
    for match in TAG_RE.finditer(text):
        chunk = " ".join(text[pos:match.start()].split())
        if chunk:
            runs.append((tag, chunk))
        tag, pos = match.group(1).strip().lower(), match.end()
    chunk = " ".join(text[pos:].split())
    if chunk:
        runs.append((tag, chunk))
    merged: list[tuple[str | None, str]] = []
    for run_tag, run_text in runs:
        if merged and merged[-1][0] == run_tag:
            merged[-1] = (run_tag, f"{merged[-1][1]} {run_text}")
        else:
            merged.append((run_tag, run_text))
    return merged


# ------------------------------------------------------------------------------------------------ voice pack
@dataclass(frozen=True)
class Reference:
    """One emotion tag and the recording that carries it."""

    tag: str
    audio: Path
    text: str = ""


@dataclass
class VoicePack:
    """A GPT-SoVITS voice folder, as far as the speech needs it."""

    folder: Path
    character_id: str
    name: str
    prompt_lang: str
    references: dict[str, Reference] = field(default_factory=dict)
    missing: tuple[str, ...] = ()          # Tags announced by emotions.json without a recording
    warnings: list[str] = field(default_factory=list)


def language_code(value: str, default: str = "zh") -> str:
    """'Chinese' / 'zh' / 'English' -> the prompt language api_v2 expects; French reads as English."""
    code = _LANGUAGES.get(str(value or "").strip().lower())
    return code if code in LANGUAGES else default


def _character_meta(folder: Path) -> dict[str, Any]:
    """character.json of a voice pack, or {} when it is absent or unreadable."""
    path = folder / "character.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def find_pack(root: Path, character_id: str = "") -> Path:
    """The voice folder to use: `root` itself, the one named by TTS_CHARACTER_ID, or the only one."""
    root = root.expanduser()
    if (root / EMOTIONS_FILE).is_file():
        return root
    if not root.is_dir():
        raise TtsError(f"Dossier de voix introuvable : {root}. Crée-le, ou corrige TTS_SPEAKERS_DIR "
                       "dans ton .env (défaut : ~/.config/monika/voice).")
    packs = sorted(p for p in root.iterdir() if p.is_dir() and (p / EMOTIONS_FILE).is_file())
    if not packs:
        raise TtsError(f"Aucun pack de voix GPT-SoVITS dans {root} : il faut un dossier contenant "
                       f"{EMOTIONS_FILE} et ses enregistrements de référence (une voix par émotion). "
                       "Voir le README, section Voix.")
    wanted = character_id.strip()
    if wanted:
        for pack in packs:
            if pack.name == wanted or str(_character_meta(pack).get("character_id") or "") == wanted:
                return pack
        names = ", ".join(pack.name for pack in packs)
        raise TtsError(f"Voix « {wanted} » introuvable dans {root} (packs présents : {names}). "
                       "Corrige TTS_CHARACTER_ID.")
    if len(packs) > 1:
        names = ", ".join(pack.name for pack in packs)
        raise TtsError(f"Plusieurs voix dans {root} ({names}) : choisis la tienne avec TTS_CHARACTER_ID.")
    return packs[0]


def read_pack(folder: Path, prompt_lang_default: str = "zh") -> VoicePack:
    """Read character.json and references/emotions.json; TtsError when there is no usable voice."""
    emo_file = folder / EMOTIONS_FILE
    try:
        table = json.loads(emo_file.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise TtsError(f"Impossible de lire {emo_file} : {type(exc).__name__}. Vérifie le pack de voix.") from exc
    except json.JSONDecodeError as exc:
        raise TtsError(f"{emo_file} n'est pas un JSON valide (ligne {exc.lineno}) : corrige-le ou "
                       "remplace le pack de voix.") from exc
    if not isinstance(table, dict):
        raise TtsError(f"{emo_file} doit contenir un objet JSON (tag -> {{audio, text}}).")
    meta = _character_meta(folder)
    pack = VoicePack(folder=folder, character_id=str(meta.get("character_id") or folder.name),
                     name=str(meta.get("name") or folder.name),
                     prompt_lang=language_code(str(meta.get("language") or ""), prompt_lang_default))
    missing: list[str] = []
    for tag, entry in table.items():
        key = str(tag).strip().lower()
        if key.startswith("_") or not isinstance(entry, dict):
            continue
        clip = str(entry.get("audio") or "").strip()
        if not clip:
            continue
        audio = (pack.folder / "references" / clip).resolve()
        if not audio.is_file():
            missing.append(key)
            continue
        pack.references[key] = Reference(key, audio, str(entry.get("text") or "").strip())
    if not pack.references:
        raise TtsError(f"Aucun enregistrement de référence utilisable dans {folder / 'references'} : "
                       "chaque tag d'emotions.json doit avoir son fichier audio, comme dans un pack "
                       "GPT-SoVITS (Speakers/<voix>/references/).")
    pack.missing = tuple(sorted(missing))
    if pack.missing:
        pack.warnings.append(
            f"{len(pack.missing)} tag(s) d'émotion sans enregistrement dans {emo_file.name} "
            f"({', '.join(pack.missing[:5])}{'…' if len(pack.missing) > 5 else ''}) : ils parleront "
            f"avec la voix de {DEFAULT_TAG}.")
    if DEFAULT_TAG not in pack.references:
        pack.warnings.append(f"Ce pack n'a pas de {DEFAULT_TAG} : les émotions sans enregistrement "
                             f"utilisent « {next(iter(pack.references))} ».")
    return pack


# ------------------------------------------------------------------------------------------------ HTTP
def http_post(url: str, payload: dict[str, Any], timeout: float) -> bytes:
    """POST one synthesis request to api_v2.py and return the WAV body; TtsError when it fails."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read()[:300].decode("utf-8", "replace").strip()
        except Exception:  # noqa: BLE001 - the body is a courtesy, never a reason to fail differently
            detail = ""
        hint = " Vérifie le texte de référence (prompt_text) et le chemin du fichier audio côté serveur."
        raise TtsError(f"GPT-SoVITS a refusé la synthèse (HTTP {exc.code})"
                       f"{' : ' + detail if detail else ''}.{hint}") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise TtsError(f"GPT-SoVITS n'a pas répondu en {timeout:.0f} s : le modèle est peut-être encore "
                           "en train de charger, ou il est occupé. Augmente TTS_TIMEOUT_S.") from exc
        raise TtsError(f"Le serveur GPT-SoVITS ({url}) ne répond pas : lance-le (api_v2.py, port 9880) "
                       f"ou corrige TTS_URL. Détail : {exc.reason}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise TtsError(f"GPT-SoVITS n'a pas répondu en {timeout:.0f} s : augmente TTS_TIMEOUT_S.") from exc
    except OSError as exc:
        raise TtsError(f"Connexion à GPT-SoVITS impossible ({type(exc).__name__}: {exc}) : vérifie TTS_URL "
                       "et que le serveur tourne.") from exc


Post = Callable[[str, dict[str, Any], float], bytes]


# ------------------------------------------------------------------------------------------------ engine
class GptSovitsEngine:
    """Speaks through the GPT-SoVITS HTTP API; same duck-typed interface as `Speaker` expects."""

    def __init__(self, settings: Settings, paths: Paths, post: Post | None = None) -> None:
        self._s = settings
        self._paths = paths
        self._post = post or http_post
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="monika-tts")
        self._lock = asyncio.Lock()
        self._loaded = False
        self.references: dict[str, Reference] = {}
        self.pack: VoicePack | None = None
        self.available_tags: set[str] | None = None  # Set once the voice pack is read
        self.character_name = ""
        self.prompt_lang = settings.tts_prompt_lang
        self.sample_rate = DEFAULT_SAMPLE_RATE
        self.load_seconds = 0.0
        self.warnings: list[str] = []
        self.latents_cached = False  # Nothing is cloned here: GPT-SoVITS owns the voice
        self.fallback_reason: str | None = None  # The server owns the device too
        self._warned_tags: set[str] = set()
        self._rate_warned = False

    @property
    def url(self) -> str:
        return f"{str(self._s.tts_url).rstrip('/')}/tts"

    @property
    def loaded(self) -> bool:
        return self._loaded

    async def run(self, fn: Callable[..., Any], *args: Any) -> Any:
        """Run a blocking call on the single synthesis thread."""
        return await asyncio.get_running_loop().run_in_executor(self._pool, fn, *args)

    async def load(self) -> float:
        """Read the voice pack and warm the server up; returns the seconds it took."""
        async with self._lock:
            if self._loaded:
                return 0.0
            start = time.perf_counter()
            self._read_pack()
            try:
                await self.run(self._warm_up)
            except TtsError:
                raise
            except Exception as exc:  # noqa: BLE001 - one clear message for the console
                raise TtsError(f"GPT-SoVITS est injoignable : {type(exc).__name__}: {str(exc)[:250]}. "
                               f"Vérifie que le serveur tourne sur {self._s.tts_url}.") from exc
            self._loaded = True
            self.load_seconds = time.perf_counter() - start
            return self.load_seconds

    def _read_pack(self) -> None:
        root = self._s.tts_speakers_dir or self._paths.voice
        self.pack = read_pack(find_pack(root, self._s.tts_character_id), self._s.tts_prompt_lang)
        self.references = self.pack.references
        self.available_tags = set(self.references)
        self.character_name = self.pack.name
        self.prompt_lang = self.pack.prompt_lang
        self.warnings.extend(self.pack.warnings)

    def _warm_up(self) -> None:
        """One short request: it loads the model server-side and tells us the real sample rate."""
        payload = self._payload(WARMUP_TEXT, self._default_reference())
        _, rate = wav_to_pcm(self._post(self.url, payload, max(self._s.tts_timeout_s, LOAD_TIMEOUT_S)))
        self.sample_rate = rate

    def _default_reference(self) -> Reference:
        if not self.references:
            raise TtsError("La voix GPT-SoVITS n'est pas chargée.")
        return self.references.get(DEFAULT_TAG) or next(iter(self.references.values()))

    def _reference(self, tag: str | None) -> Reference:
        key = (tag or DEFAULT_TAG).strip().lower()
        reference = self.references.get(key)
        if reference is not None:
            return reference
        fallback = self._default_reference()
        if key not in self._warned_tags:
            self._warned_tags.add(key)
            log.info("no reference for %r: speaking it with %r", key, fallback.tag)
        return fallback

    def _payload(self, text: str, reference: Reference) -> dict[str, Any]:
        return {
            "text": text,
            "text_lang": self._s.tts_language,
            "ref_audio_path": str(reference.audio),
            "prompt_text": reference.text,
            "prompt_lang": self.prompt_lang,
            "text_split_method": "cut5",  # Upstream's own cut: punctuation, never mid-word
            "batch_size": 1,
            "speed_factor": self._s.tts_speed,
            "streaming_mode": False,
        }

    def synth(self, text: str) -> Iterator[np.ndarray]:
        """Blocking generator of int16 chunks for one tagged sentence or block."""
        if not self._loaded:
            raise TtsError("La synthèse vocale n'est pas chargée.")
        for tag, run in split_tagged(text):
            reference = self._reference(tag)
            wav = self._post(self.url, self._payload(run, reference), self._s.tts_timeout_s)
            pcm, rate = wav_to_pcm(wav)
            if rate != self.sample_rate:
                if not self._rate_warned:
                    self._rate_warned = True
                    log.warning("GPT-SoVITS answered at %d Hz instead of %d: resampling", rate, self.sample_rate)
                pcm = resample(pcm, rate, self.sample_rate)
            yield from chunks_of(pcm)

    def close(self) -> None:
        self._pool.shutdown(wait=False)
        self._loaded = False
