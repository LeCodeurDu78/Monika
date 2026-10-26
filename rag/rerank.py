"""Cross-encoder reranking (fastembed, CPU): reads query and passage together for a finer ranking."""

from __future__ import annotations

from typing import Protocol


class Reranker(Protocol):
    def score(self, query: str, texts: list[str]) -> list[float]: ...


class FastembedReranker:
    """Lazy wrapper around fastembed's TextCrossEncoder (the model downloads on first use)."""

    def __init__(self, model_name: str, threads: int | None = None) -> None:
        self._name = model_name
        self._threads = threads
        self._model = None

    def score(self, query: str, texts: list[str]) -> list[float]:
        if self._model is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            self._model = TextCrossEncoder(model_name=self._name, threads=self._threads)
        return [float(s) for s in self._model.rerank(query, texts)]
