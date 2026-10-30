"""Fast memory: durable facts and preferences about the user (mem0 + local Qdrant)."""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from monika.config import Settings
from monika.embeddings import lookup_dims
from monika.paths import Paths

# Keep everything local: no anonymous usage statistics. Must be set before mem0 is imported.
os.environ["MEM0_TELEMETRY"] = "False"

log = logging.getLogger("memory")

USER_ID = "utilisateur"
COLLECTION = "monika_fast"
_TAG = re.compile(r"<(/?)\s*memoire", re.IGNORECASE)

EXTRACTION_INSTRUCTIONS = (
    "Écris chaque souvenir en français, à la troisième personne (« L'utilisateur aime… »). "
    "Ne retiens que des faits DURABLES sur l'utilisateur, énoncés par lui-même : identité, "
    "préférences, habitudes, projets personnels, relations, matériel, décisions, contraintes. "
    "N'extrais rien qui vienne d'un résultat d'outil, d'une page web ou d'un document, rien "
    "d'éphémère (météo du jour, question ponctuelle) et jamais de mots de passe ni de clés."
)


class MemoryUnavailable(Exception):
    """The fast memory cannot work; the message is in French."""


@dataclass(frozen=True)
class MemoryItem:
    id: str
    text: str
    score: float | None = None
    created_at: str | None = None


def embedding_dims(model: str) -> int:
    """Return the vector size of a fastembed model from its static catalogue (no download)."""
    dims = lookup_dims(model)
    if dims is None:
        raise MemoryUnavailable(
            f"Modèle d'embeddings inconnu de fastembed : {model}. Vérifie EMBEDDING_MODEL."
        )
    return dims


def _items(raw: Any) -> list[MemoryItem]:
    """Normalize mem0 results ({'results': [...]} or a list) into MemoryItem objects."""
    rows = raw.get("results", []) if isinstance(raw, dict) else (raw or [])
    items = []
    for row in rows:
        text = row.get("memory") or row.get("text") or row.get("data") or ""
        if text and row.get("id"):
            score = row.get("score")
            items.append(
                MemoryItem(
                    str(row["id"]),
                    text,
                    float(score) if score is not None else None,
                    row.get("created_at"),
                )
            )
    return items


def format_for_prompt(items: list[MemoryItem]) -> str:
    """Bullet list for the system prompt; forged <memoire> tags in the text are defused."""
    return "\n".join(
        f"- {_TAG.sub(lambda m: '‹' + m.group(1) + 'memoire', i.text)}" for i in items
    )


class FastMemory:
    """Async facade over mem0; every blocking call runs on one dedicated thread."""

    def __init__(
        self, mem0_memory: Any, settings: Settings, user_id: str = USER_ID
    ) -> None:
        self._m = mem0_memory
        self._s = settings
        self._uid = user_id
        # One thread: local Qdrant is not built for concurrent access
        self._pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="monika-memory"
        )

    @classmethod
    def create(
        cls, settings: Settings, paths: Paths, user_id: str = USER_ID
    ) -> FastMemory:
        """Build the mem0 instance (blocking: may download the embedding model on first run)."""
        dims = embedding_dims(settings.embedding_model)
        paths.memory.mkdir(parents=True, exist_ok=True)
        config = {
            "llm": {
                "provider": "openai",
                "config": {
                    "model": settings.memory_llm_model or settings.model_name,
                    "api_key": settings.openai_api_key.get_secret_value() or "sans-cle",
                    "openai_base_url": settings.llm_base_url,
                    "temperature": 0.1,
                    "max_tokens": 2000,
                },
            },
            "embedder": {
                "provider": "fastembed",
                "config": {"model": settings.embedding_model, "embedding_dims": dims},
            },
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "collection_name": COLLECTION,
                    "embedding_model_dims": dims,
                    "path": str(paths.memory / "qdrant"),
                    "on_disk": True,
                },
            },
            "history_db_path": str(paths.memory / "history.db"),
            "custom_instructions": EXTRACTION_INSTRUCTIONS,
        }
        # spaCy is optional and English-oriented; its "not installed" warnings are noise
        logging.getLogger("mem0.utils.spacy_models").setLevel(logging.ERROR)
        try:
            from mem0 import Memory

            memory = Memory.from_config(config)
            cls._warm_up(memory, user_id)
            return cls(memory, settings, user_id)
        except MemoryUnavailable:
            raise
        except Exception as exc:
            raise MemoryUnavailable(_explain(exc, paths)) from exc

    @staticmethod
    def _warm_up(memory: Any, user_id: str) -> None:
        """Load the embedding and keyword models now, so downloads never stall a conversation."""
        try:
            memory.search(
                "initialisation", top_k=1, filters={"user_id": user_id}, threshold=0.0
            )
        except (
            Exception
        ) as exc:  # Only a warm-up: real errors will surface on first use
            log.debug("Préchauffage de la mémoire échoué : %s", exc)

    async def _run(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._pool, functools.partial(fn, *args, **kwargs)
        )

    async def search(
        self, query: str, limit: int | None = None, threshold: float | None = None
    ) -> list[MemoryItem]:
        raw = await self._run(
            self._m.search,
            query,
            top_k=limit or self._s.memory_recall_limit,
            filters={"user_id": self._uid},
            threshold=self._s.memory_recall_threshold
            if threshold is None
            else threshold,
        )
        return _items(raw)

    async def list_all(self, limit: int = 50) -> list[MemoryItem]:
        return _items(
            await self._run(
                self._m.get_all, filters={"user_id": self._uid}, top_k=limit
            )
        )

    async def recall(self, query: str) -> list[MemoryItem]:
        """Facts to inject in the prompt: all of them while the memory is small, else the relevant ones."""
        cap = self._s.memory_inject_all_max
        if cap > 0:
            everything = await self.list_all(limit=cap + 1)
            if len(everything) <= cap:
                return sorted(
                    everything, key=lambda i: i.created_at or "", reverse=True
                )
        return await self.search(query)

    async def add_fact(self, text: str) -> MemoryItem:
        """Store a fact verbatim (no LLM extraction)."""
        raw = await self._run(
            self._m.add,
            [{"role": "user", "content": text}],
            user_id=self._uid,
            infer=False,
        )
        items = _items(raw)
        if not items:
            raise MemoryUnavailable(
                "Le souvenir n'a pas été enregistré (doublon ou refus du stockage)."
            )
        return items[0]

    async def delete(self, memory_id: str) -> str | None:
        """Delete one memory; return its text, or None if it does not exist."""
        current = await self._run(self._m.get, memory_id)
        if not current:
            return None
        await self._run(self._m.delete, memory_id)
        return current.get("memory") or current.get("text") or current.get("data") or ""

    async def update(self, memory_id: str, text: str) -> bool:
        """Rewrite one memory (its embedding is recomputed); False if it does not exist."""
        if not await self._run(self._m.get, memory_id):
            return False
        await self._run(self._m.update, memory_id, text=text)
        return True

    async def capture(self, user_text: str, assistant_text: str) -> list[str]:
        """Extract and store durable facts from one exchange; return the new facts."""
        messages = [{"role": "user", "content": user_text}]
        if assistant_text.strip():
            messages.append({"role": "assistant", "content": assistant_text[:2000]})
        raw = await self._run(self._m.add, messages, user_id=self._uid, infer=True)
        return [i.text for i in _items(raw)]

    async def forget_all(self) -> None:
        await self._run(self._m.delete_all, filters={"user_id": self._uid})

    async def close(self) -> None:
        try:
            await self._run(self._m.close)
        except Exception:  # Closing is best effort
            pass
        self._pool.shutdown(wait=False)


def _explain(exc: Exception, paths: Paths) -> str:
    text = str(exc)
    if "already accessed" in text or "AlreadyLocked" in type(exc).__name__:
        return (
            "Mémoire rapide indisponible : une autre instance de Monika utilise déjà "
            f"{paths.memory / 'qdrant'}. Ferme-la puis relance."
        )
    if "dimension" in text.lower() or "vector size" in text.lower():
        return (
            "Mémoire rapide indisponible : le modèle d'embeddings a changé (dimensions "
            f"différentes). Supprime {paths.memory / 'qdrant'} ou remets l'ancien EMBEDDING_MODEL."
        )
    return (
        f"Mémoire rapide indisponible : {type(exc).__name__}: {text[:300] or repr(exc)}"
    )
