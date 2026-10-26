"""Embedding model catalogue lookups shared by the memory and the document index."""

from __future__ import annotations


def lookup_dims(model: str) -> int | None:
    """Return a fastembed model's vector size from its static catalogue (no download)."""
    from fastembed import TextEmbedding

    for entry in TextEmbedding.list_supported_models():
        if entry["model"].lower() == model.lower():
            return int(entry["dim"])
    return None
