"""Monika's quick memory (mem0): lasting facts and preferences about the user.

mem0 extracts these facts from conversations with Monika's LLM, stores them in a local Qdrant
(~/.monika/memory/qdrant) and finds them by semantic similarity in a few tens of ms.
"""

import concurrent.futures
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from core.settings import settings
from memory.common import MemoryUnavailable, describe_error, log

MEMORY_DIR = settings.APP_DIR / "memory"

# mem0 reads these environment variables AT IMPORT: set them before any `import mem0`.
os.environ.setdefault("MEM0_TELEMETRY", "False")  # personal memory: no telemetry
os.environ.setdefault("MEM0_DIR", str(MEMORY_DIR / "mem0"))  # ~/.monika rather than ~/.mem0

# On every start mem0 reports two missing optional components (spaCy: English entity extraction, useless
# here; fastembed: keyword search). This noise does not belong in Monika's terminal; real errors (ERROR level)
# are still shown.
logging.getLogger("mem0.utils.spacy_models").setLevel(logging.ERROR)
logging.getLogger("mem0.vector_stores.qdrant").setLevel(logging.ERROR)

# Instruction added to mem0's extraction prompt. Without it mem0 also keeps what the assistant says
# (weather, search results...) and writes its memories in English.
EXTRACTION_INSTRUCTIONS = (
    "Tu alimentes la mémoire personnelle d'un assistant. Ne retiens QUE des informations durables "
    "que l'UTILISATEUR donne sur lui-même : identité, proches, lieux de vie, travail ou études, "
    "goûts, habitudes, préférences (ton, format de réponse...), contraintes, projets personnels. "
    "N'extrais RIEN des réponses de l'assistant ni des résultats d'outils (météo, recherche web, "
    "e-mails, fichiers, prix...), ni des commandes ponctuelles (volume, ouvrir une application, "
    "lancer une musique...). Si le message ne contient aucune information durable sur l'utilisateur, "
    "n'extrais rien. Rédige chaque souvenir en français, à la troisième personne "
    "(« L'utilisateur ... »), de façon courte et autonome."
)


# --- embedding model download tracking ---------------------------------------------
# The model loads in a background thread: Hugging Face's progress bars get lost there (and would mess with
# the input prompt). So we watch the cache and print short lines instead.
DOWNLOAD_POLL_SECONDS = 3.0
DOWNLOAD_STALL_SECONDS = 30.0
_UNUSED_DIRS = ("onnx/", "openvino/")
_UNUSED_FILES = {"pytorch_model.bin", "tf_model.h5", "rust_model.ot", "flax_model.msgpack"}


def _expected_download_bytes(repo_id: str) -> Optional[int]:
    """Expected download size (weights + tokenizer), or None if the Hub does not answer."""
    try:
        from huggingface_hub import HfApi

        info = HfApi().model_info(repo_id, files_metadata=True, timeout=10)
    except Exception:
        return None
    files = {s.rfilename: (s.size or 0) for s in (info.siblings or [])}
    has_safetensors = any(name.endswith(".safetensors") for name in files)
    total = 0
    for name, size in files.items():
        if name.startswith(_UNUSED_DIRS) or (has_safetensors and name in _UNUSED_FILES):
            continue  # sentence-transformers downloads neither the ONNX/OpenVINO formats nor duplicates
        total += size
    return total or None


def _repo_cache_bytes(repo_id: str) -> int:
    """Bytes already present in the Hugging Face cache for this model (.incomplete files included)."""
    from huggingface_hub import constants

    blobs = Path(constants.HF_HUB_CACHE) / ("models--" + repo_id.replace("/", "--")) / "blobs"
    total = 0
    try:
        for path in blobs.iterdir():
            try:
                total += path.stat().st_size
            except OSError:
                pass
    except OSError:
        return 0
    return total


def _watch_model_download(repo_id: str, stop: threading.Event) -> None:
    """Print the download progress of `repo_id` until `stop` is set.

    Silent if the model is already cached. Also reports a download that stopped progressing.
    """
    expected = _expected_download_bytes(repo_id)
    start = _repo_cache_bytes(repo_id)
    if expected and start >= expected:
        return  # already cached: nothing to show

    mb = 1024 * 1024
    last, last_time, last_growth = start, time.monotonic(), time.monotonic()
    begun = False
    while not stop.wait(DOWNLOAD_POLL_SECONDS):
        now, clock = _repo_cache_bytes(repo_id), time.monotonic()
        if now > last:
            speed = (now - last) / max(clock - last_time, 0.001) / mb
            if expected:
                percent = min(100, int(now * 100 / expected))
                log(
                    f"⬇️ [Mémoire] Modèle d'embedding : {now / mb:.0f}/{expected / mb:.0f} Mo "
                    f"({percent} %) — {speed:.1f} Mo/s"
                )
            else:
                log(f"⬇️ [Mémoire] Modèle d'embedding : {now / mb:.0f} Mo — {speed:.1f} Mo/s")
            begun, last_growth = True, clock
        elif begun and clock - last_growth >= DOWNLOAD_STALL_SECONDS:
            log(
                f"⏸️ [Mémoire] Le téléchargement du modèle ne progresse plus depuis "
                f"{int(clock - last_growth)} s ({now / mb:.0f} Mo reçus)."
            )
            last_growth = clock  # next reminder in DOWNLOAD_STALL_SECONDS
        last, last_time = now, clock

    if begun:
        log("✅ [Mémoire] Modèle d'embedding téléchargé.")


def _embedder_config() -> dict[str, Any]:
    """mem0 embedder configuration for the chosen provider (see MEM0_EMBEDDER_* in settings)."""
    provider = settings.MEM0_EMBEDDER_PROVIDER.strip().lower()
    base_url = settings.MEM0_EMBEDDER_BASE_URL
    config: dict[str, Any] = {
        "model": settings.MEM0_EMBEDDER_MODEL,
        "embedding_dims": settings.MEM0_EMBEDDER_DIMS,
    }

    if provider == "huggingface":
        if base_url:  # serveur TEI compatible OpenAI
            config["huggingface_base_url"] = base_url
        else:  # local model (sentence-transformers): CPU by default so it does not fight the voice for the GPU
            config["model_kwargs"] = {"device": settings.MEM0_EMBEDDER_DEVICE}
    elif provider == "openai":  # tout endpoint /v1/embeddings compatible OpenAI
        config["openai_base_url"] = base_url or settings.LLM_BASE_URL
        config["api_key"] = settings.MEM0_EMBEDDER_API_KEY or settings.OPENAI_API_KEY
    elif provider == "ollama":
        config["ollama_base_url"] = base_url

    return {"provider": provider, "config": {k: v for k, v in config.items() if v is not None}}


def build_config() -> dict[str, Any]:
    """Complete mem0 configuration: Monika's LLM, multilingual embedder, persistent local Qdrant."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    llm_config: dict[str, Any] = {
        "model": settings.MEM0_LLM_MODEL or settings.MODEL_NAME,
        "temperature": 0.1,
        "max_tokens": 4096,  # margin for models that "think" before answering
        "api_key": settings.OPENAI_API_KEY,
        "openai_base_url": settings.LLM_BASE_URL,
    }

    return {
        "llm": {
            "provider": "openai",  # OpenAI-compatible endpoint, like Monika's main LLM
            "config": {k: v for k, v in llm_config.items() if v is not None},
        },
        "embedder": _embedder_config(),
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": "monika_user_facts",
                "path": str(MEMORY_DIR / "qdrant"),
                # mem0 does not infer it from the embedder: must match MEM0_EMBEDDER_DIMS.
                "embedding_model_dims": settings.MEM0_EMBEDDER_DIMS,
            },
        },
        "history_db_path": str(MEMORY_DIR / "mem0_history.db"),
        "custom_instructions": EXTRACTION_INSTRUCTIONS,
    }


def _results(response: Any) -> list[dict]:
    """Extract the list of results from a mem0 response ({'results': [...]} or a plain list)."""
    if isinstance(response, dict):
        response = response.get("results", [])
    return list(response or [])


class Mem0Memory:
    """Thread-safe wrapper around `mem0.Memory`.

    - Initialisation (embedding model load, Qdrant open) happens in the background.
    - Reads are never blocked by a write in progress (a write calls the LLM: several seconds); only writes are
      serialised with each other.
    """

    def __init__(self) -> None:
        self._memory: Any = None
        self._error: Optional[str] = None
        self._ready = threading.Event()
        self._started = False
        self._start_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._reads = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="mem0-read")

    # --- cycle de vie ----------------------------------------------------------------
    def start(self) -> None:
        """Start initialisation in the background (no effect if already started)."""
        with self._start_lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._init, daemon=True, name="mem0-init").start()

    def _init(self) -> None:
        # A local embedding model (huggingface without a server) downloads on first launch: show it.
        stop_watch = threading.Event()
        if settings.MEM0_EMBEDDER_PROVIDER.strip().lower() == "huggingface" and not settings.MEM0_EMBEDDER_BASE_URL:
            threading.Thread(
                target=_watch_model_download,
                args=(settings.MEM0_EMBEDDER_MODEL, stop_watch),
                daemon=True,
                name="mem0-download-watch",
            ).start()
        try:
            from mem0 import Memory  # late import: mem0ai is an optional dependency

            memory = Memory.from_config(build_config())
            # Warm-up: forces the lazily loaded components to initialise, and reveals a dimension mismatch right away
            # rather than on the user's first message.
            memory.search("démarrage", top_k=1, filters={"user_id": settings.MEM0_USER_ID})
            total = len(_results(memory.get_all(filters={"user_id": settings.MEM0_USER_ID}, top_k=10_000)))
            self._memory = memory
            log(f"🧠 [Mémoire] mem0 prêt ({total} souvenir(s) sur l'utilisateur).")
        except Exception as exc:
            self._error = describe_error(exc)
            if "dimension" in str(exc).lower():
                self._error += (
                    " — si tu as changé de modèle d'embedding, supprime "
                    f"{MEMORY_DIR / 'qdrant'} (les souvenirs mem0 seront réextraits au fil des conversations)."
                )
            log(f"⚠️ [Mémoire] mem0 indisponible : {self._error}")
        finally:
            stop_watch.set()
            self._ready.set()

    def _require(self, wait: float) -> Any:
        """Return the mem0 instance, waiting at most `wait` seconds for it to be ready."""
        self.start()
        if not self._ready.wait(wait):
            raise MemoryUnavailable(
                "mem0 s'initialise encore (au premier lancement : téléchargement du modèle d'embedding)."
            )
        if self._memory is None:
            raise MemoryUnavailable(f"mem0 indisponible : {self._error}")
        return self._memory

    # --- operations --------------------------------------------------------------------
    def search(
        self,
        query: str,
        limit: Optional[int] = None,
        wait: float = 0.0,
        timeout: Optional[float] = None,
    ) -> list[dict]:
        """Memories closest to `query`: [{'id', 'memory', 'score', ...}], most relevant first."""
        memory = self._require(wait)
        future = self._reads.submit(
            memory.search,
            query,
            top_k=limit or settings.MEM0_TOP_K,
            threshold=settings.MEM0_MIN_SCORE,
            filters={"user_id": settings.MEM0_USER_ID},
        )
        try:
            return _results(future.result(timeout=timeout))
        except concurrent.futures.TimeoutError:
            raise MemoryUnavailable("la recherche mem0 a dépassé le délai imparti.") from None

    def list_all(self, limit: int = 50, wait: float = 0.0) -> list[dict]:
        """Every known memory (at most `limit`)."""
        memory = self._require(wait)
        return _results(memory.get_all(filters={"user_id": settings.MEM0_USER_ID}, top_k=limit))

    def add(self, messages: Any, wait: float = 0.0) -> list[dict]:
        """Hand a conversation (or a text) to mem0, which extracts the facts worth keeping.

        Returns the memories created or updated: [{'id', 'memory', 'event'}].
        """
        memory = self._require(wait)
        with self._write_lock:
            response = memory.add(messages, user_id=settings.MEM0_USER_ID)
        return _results(response)

    def update(self, memory_id: str, text: str, wait: float = 0.0) -> None:
        """Rewrite one memory in place (the dashboard's "correct" button)."""
        memory = self._require(wait)
        with self._write_lock:
            memory.update(memory_id, data=text)

    def delete(self, memory_id: str, wait: float = 0.0) -> None:
        memory = self._require(wait)
        with self._write_lock:
            memory.delete(memory_id)