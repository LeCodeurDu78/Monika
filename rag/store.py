"""Document index storage: llama-index vector store on a local Qdrant folder, plus the models."""

from __future__ import annotations

import asyncio
import functools
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from monika.config import Settings
from monika.embeddings import lookup_dims
from monika.paths import Paths
from rag import lexical
from rag.chunking import Chunk, Position
from rag.manifest import Manifest
from rag.rerank import FastembedReranker, Reranker

COLLECTION = "monika_docs"
SCHEMA = "v1"
_NAMESPACE = uuid.UUID("6f0c1d1e-3a43-4a54-9f6e-0d4c1b7a9e11")


class RagError(Exception):
    """The document index cannot work; the message is in French."""


@dataclass
class Hit:
    """One retrieved passage with everything needed to cite it."""

    path: str
    display: str
    kind: str
    position: Position
    text: str
    score: float

    def citation(self) -> str:
        return f"{self.display} — {self.position.label()}"


def file_id_for(path: str) -> str:
    return uuid.uuid5(_NAMESPACE, path).hex


def build_nodes(path: str, display: str, kind: str, chunks: list[Chunk]) -> list[Any]:
    """Turn chunks into llama-index nodes whose embedded text includes the file name."""
    from llama_index.core.schema import NodeRelationship, RelatedNodeInfo, TextNode

    file_id = file_id_for(path)
    nodes = []
    for i, chunk in enumerate(chunks):
        node = TextNode(
            id_=str(uuid.uuid5(_NAMESPACE, f"{file_id}:{i}")),
            text=chunk.text,
            metadata={"file": display, "path": path, "kind": kind, "pos_kind": chunk.position.kind,
                      "pos_start": chunk.position.start, "pos_end": chunk.position.end},
            # Only the file name takes part in the embedding and keyword text; positions do not
            excluded_embed_metadata_keys=["path", "kind", "pos_kind", "pos_start", "pos_end"],
            excluded_llm_metadata_keys=["path", "kind", "pos_kind", "pos_start", "pos_end"],
        )
        node.relationships[NodeRelationship.SOURCE] = RelatedNodeInfo(node_id=file_id)
        nodes.append(node)
    return nodes


class RagStore:
    """Owns the Qdrant client, the manifest and the models; every call runs on a dedicated thread."""

    def __init__(self, settings: Settings, paths: Paths, client: Any, manifest: Manifest,
                 dims: int, embed_model: Any | None, reranker: Reranker | None) -> None:
        self._s = settings
        self._paths = paths
        self._client = client
        self.manifest = manifest
        self._dims = dims
        self._embed = embed_model
        self._reranker = reranker
        self._vs = self._build_vector_store()
        # One thread per resource: local Qdrant is not concurrent, and the ONNX models are heavy
        self._qdrant_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="monika-rag-qdrant")
        self._embed_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="monika-rag-embed")
        self._rerank_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="monika-rag-rerank")

    @staticmethod
    def fingerprint_for(settings: Settings) -> str:
        return (f"{settings.rag_embedding_model}|{settings.rag_chunk_chars}|"
                f"{settings.rag_chunk_overlap_chars}|{SCHEMA}")

    @classmethod
    def create(cls, settings: Settings, paths: Paths, embed_model: Any | None = None,
               dims: int | None = None, reranker: Reranker | None = None,
               ignore_fingerprint: bool = False) -> RagStore:
        """Open the index (blocking). Models are loaded lazily, on first use.

        ignore_fingerprint lets a reset open an index built with other settings.
        """
        paths.rag.mkdir(parents=True, exist_ok=True)
        manifest = Manifest(paths.rag_manifest)
        existing = manifest.fingerprint()
        expected = cls.fingerprint_for(settings)
        if existing and existing != expected and not ignore_fingerprint:
            manifest.close()
            raise RagError(
                "L'index a été construit avec d'autres réglages (modèle d'embeddings ou taille des "
                "passages). Reconstruis-le avec : python -m monika --rag-index --reset")
        dims = dims or lookup_dims(settings.rag_embedding_model)
        if dims is None:
            manifest.close()
            raise RagError(f"Modèle d'embeddings inconnu de fastembed : "
                           f"{settings.rag_embedding_model}. Vérifie RAG_EMBEDDING_MODEL.")
        try:
            from qdrant_client import QdrantClient

            client = QdrantClient(path=str(paths.rag_qdrant))
        except Exception as exc:
            manifest.close()
            text = str(exc)
            if "already accessed" in text:
                raise RagError("Index des documents indisponible : une autre instance de Monika "
                               f"utilise déjà {paths.rag_qdrant}.") from exc
            raise RagError(f"Index des documents indisponible : {type(exc).__name__}: {text[:300]}") from exc
        if not existing:
            manifest.set_fingerprint(expected)
        reranker = reranker or (FastembedReranker(settings.rag_reranker_model, cls._threads(settings))
                                if settings.rag_rerank_enabled else None)
        return cls(settings, paths, client, manifest, dims, embed_model, reranker)

    @staticmethod
    def _threads(settings: Settings) -> int:
        return settings.rag_threads or max(1, (os.cpu_count() or 2) // 2)

    def _build_vector_store(self) -> Any:
        from llama_index.vector_stores.qdrant import QdrantVectorStore
        from qdrant_client import models

        return QdrantVectorStore(
            collection_name=COLLECTION, client=self._client, enable_hybrid=True,
            # Own keyword encoder: no model to download, accent-insensitive, understands identifiers
            sparse_doc_fn=lexical.encode_documents, sparse_query_fn=lexical.encode_queries,
            sparse_config=models.SparseVectorParams(modifier=models.Modifier.IDF),
            dense_config=models.VectorParams(size=self._dims, distance=models.Distance.COSINE),
            index_doc_id=False,  # Payload indexes are not supported (and warn) in local mode
            batch_size=64)

    def _embedder(self) -> Any:
        if self._embed is None:
            from llama_index.embeddings.fastembed import FastEmbedEmbedding

            self._embed = FastEmbedEmbedding(
                model_name=self._s.rag_embedding_model, threads=self._threads(self._s),
                embed_batch_size=self._s.rag_embed_batch)
        return self._embed

    async def _run(self, pool: ThreadPoolExecutor, fn: Any, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.get_running_loop().run_in_executor(
            pool, functools.partial(fn, *args, **kwargs))

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return await self._run(self._embed_pool, lambda: self._embedder().get_text_embedding_batch(texts))

    async def embed_query(self, text: str) -> list[float]:
        return await self._run(self._embed_pool, lambda: self._embedder().get_query_embedding(text))

    async def add_nodes(self, nodes: list[Any]) -> None:
        await self._run(self._qdrant_pool, self._vs.add, nodes)

    async def delete_file(self, file_id: str) -> None:
        await self._run(self._qdrant_pool, self._vs.delete, ref_doc_id=file_id)

    async def query(self, text: str, vector: list[float], k: int, alpha: float) -> list[Hit]:
        from llama_index.core.vector_stores.types import VectorStoreQuery, VectorStoreQueryMode

        def run() -> Any:
            if not self._client.collection_exists(COLLECTION):
                return None
            return self._vs.query(VectorStoreQuery(
                query_embedding=vector, query_str=text, similarity_top_k=k, sparse_top_k=k,
                hybrid_top_k=k, mode=VectorStoreQueryMode.HYBRID, alpha=alpha))

        result = await self._run(self._qdrant_pool, run)
        hits = []
        for node, score in zip(getattr(result, "nodes", None) or [], getattr(result, "similarities", None) or []):
            meta = node.metadata
            hits.append(Hit(path=meta["path"], display=meta["file"], kind=meta["kind"],
                            position=Position(meta["pos_kind"], int(meta["pos_start"]), int(meta["pos_end"])),
                            text=node.get_content(), score=float(score)))
        return hits

    async def rerank(self, query: str, texts: list[str]) -> list[float]:
        if self._reranker is None:
            raise RagError("reranking désactivé")
        return await self._run(self._rerank_pool, self._reranker.score, query, texts)

    async def count_points(self) -> int:
        def run() -> int:
            return self._client.count(COLLECTION).count if self._client.collection_exists(COLLECTION) else 0
        return await self._run(self._qdrant_pool, run)

    async def reset(self) -> None:
        """Forget everything: vectors, manifest, and adopt the current settings fingerprint."""
        def run() -> None:
            if self._client.collection_exists(COLLECTION):
                self._client.delete_collection(COLLECTION)
            self._vs = self._build_vector_store()
        await self._run(self._qdrant_pool, run)
        self.manifest.clear()
        self.manifest.set_fingerprint(self.fingerprint_for(self._s))

    async def close(self) -> None:
        def run() -> None:
            try:
                self._client.close()
            except Exception:  # Closing is best effort
                pass
        await self._run(self._qdrant_pool, run)
        self.manifest.close()
        for pool in (self._qdrant_pool, self._embed_pool, self._rerank_pool):
            pool.shutdown(wait=False)
