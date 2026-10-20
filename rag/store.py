"""Qdrant access: one collection with a dense vector and a sparse vector (BM25, server-side IDF)."""

import os
import threading
import uuid
from pathlib import Path
from typing import Iterable, Optional

from qdrant_client import QdrantClient, models

from core.settings import settings

DENSE = "dense"
SPARSE = "bm25"
_LOCAL_MODE_WARN_POINTS = 20_000
_UPSERT_BATCH = 128


def point_id(path: str, index: int) -> str:
    """Deterministic identifier: re-indexing a file overwrites its old chunks instead of duplicating them."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path}#{index}"))


def normalize_prefix(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path)).rstrip(os.sep) or os.sep


class VectorStore:
    def __init__(self) -> None:
        self._client: Optional[QdrantClient] = None
        self._lock = threading.RLock()  # Qdrant's local mode is not thread-safe
        self._ready = False
        self._warned_local = False

    @property
    def is_local(self) -> bool:
        return not settings.RAG_QDRANT_URL

    def _get_client(self) -> QdrantClient:
        if self._client is None:
            if settings.RAG_QDRANT_URL:
                self._client = QdrantClient(
                    url=settings.RAG_QDRANT_URL, api_key=settings.RAG_QDRANT_API_KEY, timeout=60
                )
            else:
                path = settings.RAG_QDRANT_PATH or (settings.APP_DIR / "rag" / "qdrant")
                Path(path).mkdir(parents=True, exist_ok=True)
                self._client = QdrantClient(path=str(path))
        return self._client

    def ensure_collection(self) -> None:
        with self._lock:
            if self._ready:
                return
            client = self._get_client()
            name = settings.RAG_COLLECTION
            if not client.collection_exists(name):
                client.create_collection(
                    collection_name=name,
                    vectors_config={
                        DENSE: models.VectorParams(
                            size=settings.RAG_EMBED_DIMS, distance=models.Distance.COSINE, on_disk=True
                        )
                    },
                    sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
                )
                if not self.is_local:  # payload indexes do not exist in local mode
                    for field in ("path", "ancestors"):
                        client.create_payload_index(name, field, models.PayloadSchemaType.KEYWORD)
            else:
                params = client.get_collection(name).config.params.vectors
                size = params[DENSE].size if isinstance(params, dict) and DENSE in params else None
                if size is not None and size != settings.RAG_EMBED_DIMS:
                    raise RuntimeError(
                        f"La collection '{name}' a des vecteurs de dimension {size}, mais RAG_EMBED_DIMS="
                        f"{settings.RAG_EMBED_DIMS}. Change de modèle = nouvelle collection : supprime-la "
                        f"(python -m rag reset) ou change RAG_COLLECTION."
                    )
            self._ready = True

    def reset(self) -> None:
        with self._lock:
            client = self._get_client()
            if client.collection_exists(settings.RAG_COLLECTION):
                client.delete_collection(settings.RAG_COLLECTION)
            self._ready = False

    def upsert(self, points: list[models.PointStruct]) -> None:
        with self._lock:
            self.ensure_collection()
            client = self._get_client()
            for i in range(0, len(points), _UPSERT_BATCH):
                client.upsert(settings.RAG_COLLECTION, points=points[i : i + _UPSERT_BATCH])
            self._warn_if_local_is_large(client)

    def delete_path(self, path: str) -> None:
        with self._lock:
            self.ensure_collection()
            self._get_client().delete(
                settings.RAG_COLLECTION,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[models.FieldCondition(key="path", match=models.MatchValue(value=path))]
                    )
                ),
            )

    def delete_ids(self, ids: Iterable[str]) -> None:
        ids = list(ids)
        if not ids:
            return
        with self._lock:
            self.ensure_collection()
            self._get_client().delete(
                settings.RAG_COLLECTION, points_selector=models.PointIdsList(points=ids)
            )

    def count(self) -> int:
        with self._lock:
            self.ensure_collection()
            return self._get_client().count(settings.RAG_COLLECTION, exact=False).count

    def search(
        self,
        dense: list[float],
        sparse: Optional[models.SparseVector],
        limit: int,
        prefix: Optional[str] = None,
    ) -> list[models.ScoredPoint]:
        """Hybrid search: dense + BM25, fused by RRF (or dense only if the query has no term)."""
        flt = None
        if prefix:
            flt = models.Filter(
                must=[models.FieldCondition(key="ancestors", match=models.MatchValue(value=normalize_prefix(prefix)))]
            )
        with self._lock:
            self.ensure_collection()
            client = self._get_client()
            if sparse is None:
                result = client.query_points(
                    settings.RAG_COLLECTION, query=dense, using=DENSE, limit=limit,
                    query_filter=flt, with_payload=True,
                )
            else:
                # The filter must be repeated in each prefetch: it does not propagate from the final query.
                result = client.query_points(
                    settings.RAG_COLLECTION,
                    prefetch=[
                        models.Prefetch(query=dense, using=DENSE, limit=limit, filter=flt),
                        models.Prefetch(query=sparse, using=SPARSE, limit=limit, filter=flt),
                    ],
                    query=models.FusionQuery(fusion=models.Fusion.RRF),
                    limit=limit,
                    with_payload=True,
                )
        return list(result.points)

    def _warn_if_local_is_large(self, client: QdrantClient) -> None:
        if not self.is_local or self._warned_local:
            return
        try:
            total = client.count(settings.RAG_COLLECTION).count
        except Exception:
            return
        if total > _LOCAL_MODE_WARN_POINTS:
            self._warned_local = True
            print(
                f"⚠️ [RAG] {total} extraits en mode Qdrant local (recherche brute en mémoire, de plus en plus "
                "lente). Passe à un serveur : docker compose -f dev_animations/docker-compose.qdrant.yml up -d "
                "puis RAG_QDRANT_URL=http://localhost:6333."
            )


_store: Optional[VectorStore] = None
_store_lock = threading.Lock()


def get_store() -> VectorStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = VectorStore()
        return _store
