"""Embedding (e5) and reranking (cross-encoder) models, loaded on demand."""

import threading
from typing import Optional

from core.settings import settings


def _resolve_device(requested: str) -> str:
    if requested.startswith("cuda"):
        try:
            import torch

            if torch.cuda.is_available():
                return requested
        except ImportError:
            pass
        print(f"⚠️ [RAG] CUDA indisponible, bascule sur le CPU (demandé : {requested}).")
        return "cpu"
    return requested


class Embedder:
    """multilingual-e5: "passage: " for documents, "query: " for queries (mandatory)."""

    def __init__(self) -> None:
        self._model = None
        self._lock = threading.Lock()

    def _ensure(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            device = _resolve_device(settings.RAG_EMBED_DEVICE)
            print(f"📚 [RAG] Chargement de {settings.RAG_EMBED_MODEL} ({device})...")
            model = SentenceTransformer(settings.RAG_EMBED_MODEL, device=device)
            model.max_seq_length = 512
            if device.startswith("cuda"):
                model.half()
            self._model = model
        return self._model

    def _encode(self, prefix: str, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        with self._lock:  # one encoding at a time (indexing and queries share the model)
            model = self._ensure()
            vectors = model.encode(
                [prefix + t for t in texts],
                batch_size=settings.RAG_EMBED_BATCH,
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
        return vectors.tolist()

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return self._encode("passage: ", texts)

    def embed_query(self, text: str) -> list[float]:
        return self._encode("query: ", [text])[0]


class Reranker:
    """Cross-encoder: scores each (query, chunk) pair. `score` returns None if unavailable."""

    def __init__(self) -> None:
        self._model = None
        self._failed = False
        self._lock = threading.Lock()

    def _ensure(self):
        if self._model is None and not self._failed:
            try:
                from sentence_transformers import CrossEncoder

                device = _resolve_device(settings.RAG_RERANK_DEVICE)
                print(f"📚 [RAG] Chargement de {settings.RAG_RERANK_MODEL} ({device})...")
                model = CrossEncoder(settings.RAG_RERANK_MODEL, device=device, max_length=512)
                if device.startswith("cuda"):
                    model.model.half()
                self._model = model
            except Exception as exc:  # model not found, offline...: fall back to the fusion order
                self._failed = True
                print(f"⚠️ [RAG] Reranker indisponible ({type(exc).__name__}: {exc}). Ordre de la fusion conservé.")
        return self._model

    def score(self, query: str, documents: list[str]) -> Optional[list[float]]:
        if not documents:
            return []
        with self._lock:
            model = self._ensure()
            if model is None:
                return None
            pairs = [(query, d) for d in documents]
            kwargs = {"batch_size": settings.RAG_EMBED_BATCH, "show_progress_bar": False}
            try:  # scores as probabilities (0..1) so that the RAG_RERANK_MIN_SCORE threshold makes sense
                import torch

                scores = model.predict(pairs, activation_fn=torch.nn.Sigmoid(), **kwargs)
            except TypeError:  # signature differs by version: the default of a 1-label cross-encoder is already a sigmoid
                scores = model.predict(pairs, **kwargs)
        return [float(s) for s in scores]


_embedder: Optional[Embedder] = None
_reranker: Optional[Reranker] = None
_singleton_lock = threading.Lock()


def get_embedder() -> Embedder:
    global _embedder
    with _singleton_lock:
        if _embedder is None:
            _embedder = Embedder()
        return _embedder


def get_reranker() -> Reranker:
    global _reranker
    with _singleton_lock:
        if _reranker is None:
            _reranker = Reranker()
        return _reranker
