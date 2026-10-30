"""RagService: what the agent, the commands and the CLI use (search, sync, status)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from monika.config import Settings
from monika.paths import Paths
from rag.indexer import Indexer, SyncProgress
from rag.store import Hit, RagError, RagStore


@dataclass
class SearchOutcome:
    hits: list[Hit] = field(default_factory=list)
    reranked: bool = False
    warning: str = ""
    index_empty: bool = False
    filtered: int = 0  # Passages dropped for scoring below RAG_MIN_SCORE
    min_score: float | None = (
        None  # Threshold actually applied (None: no filter was applied)
    )


class RagService:
    """Hybrid search with reranking, plus a background sync job."""

    def __init__(
        self,
        store: RagStore,
        settings: Settings,
        paths: Paths,
        display_home: Path | None = None,
    ) -> None:
        self._store = store
        self._s = settings
        self._indexer = Indexer(store, settings, paths.home, display_home)
        self.progress = SyncProgress()
        self._job: asyncio.Task | None = None

    @classmethod
    def create(
        cls,
        settings: Settings,
        paths: Paths,
        display_home: Path | None = None,
        **store_kwargs,
    ) -> RagService:
        return cls(
            RagStore.create(settings, paths, **store_kwargs),
            settings,
            paths,
            display_home,
        )

    @property
    def default_top_k(self) -> int:
        return self._s.rag_top_k

    @property
    def running(self) -> bool:
        return self._job is not None and not self._job.done()

    def start_sync(self, roots: list[Path] | None = None, reset: bool = False) -> bool:
        """Launch a sync in the background; False if one is already running."""
        if self.running:
            return False
        targets = roots or self._s.rag_root_paths()

        async def job() -> None:
            if reset:
                await self._store.reset()
            await self._indexer.sync(targets, self.progress)

        self.progress.state = "analyse"
        self._job = asyncio.get_running_loop().create_task(job())
        return True

    async def wait_sync(self) -> SyncProgress:
        if self._job is not None:
            await asyncio.gather(self._job, return_exceptions=True)
        return self.progress

    async def stop_sync(self) -> None:
        if self.running:
            self._job.cancel()
            await asyncio.gather(self._job, return_exceptions=True)

    async def search(self, query: str, top_k: int | None = None) -> SearchOutcome:
        """Hybrid (meaning + keywords) retrieval, then cross-encoder reranking."""
        top_k = top_k or self._s.rag_top_k
        counts = self._store.manifest.counts()
        if counts["indexed"] == 0:
            return SearchOutcome(index_empty=True)
        vector = await self._store.embed_query(query)
        hits = await self._store.query(
            query, vector, max(self._s.rag_candidates, top_k), self._s.rag_hybrid_alpha
        )
        outcome = SearchOutcome(hits=hits)
        if self._s.rag_rerank_enabled and hits:
            try:
                scores = await self._store.rerank(query, [h.text for h in hits])
                for hit, score in zip(hits, scores):
                    hit.score = score
                outcome.hits = sorted(hits, key=lambda h: h.score, reverse=True)
                outcome.reranked = True
                # The cut-off is in the reranker's own scale, so it only applies when reranking worked
                outcome.min_score = self._s.rag_min_score
                kept = [h for h in outcome.hits if h.score >= outcome.min_score]
                outcome.filtered = len(outcome.hits) - len(kept)
                outcome.hits = kept
            except (
                Exception
            ) as exc:  # Offline or model missing: fall back to the hybrid order
                outcome.warning = (
                    f"reranking indisponible ({type(exc).__name__}: {str(exc)[:120]}) : "
                    "classement par la recherche hybride seule, sans filtre de score"
                )
        outcome.hits = outcome.hits[:top_k]
        return outcome

    def counts(self) -> dict[str, int]:
        """Index size and last errors, for the health grid."""
        return self._store.manifest.counts()

    async def status_text(self) -> str:
        counts = self._store.manifest.counts()
        lines = [
            (
                f"Documents indexés : {counts['indexed']} fichier(s), {counts['chunks']} passages ; "
                f"{counts['skipped']} ignoré(s), {counts['error']} en erreur."
            )
        ]
        reasons = self._store.manifest.skipped_reasons(5)
        if reasons:
            lines.append(
                "Raisons d'exclusion : "
                + ", ".join(f"{n} {r}" for r, n in reasons)
                + "."
            )
        lines.append(
            "Dernière indexation : " + self.progress.summary()
            if self.progress.state != "inactif"
            else "Aucune indexation lancée dans cette session."
        )
        lines.append(
            "Dossiers surveillés : "
            + ", ".join(str(p) for p in self._s.rag_root_paths())
            + "."
        )
        return "\n".join(lines)

    async def reset(self) -> None:
        await self.stop_sync()
        await self._store.reset()

    async def close(self) -> None:
        await self.stop_sync()
        await self._store.close()


__all__ = ["RagError", "RagService", "SearchOutcome"]
