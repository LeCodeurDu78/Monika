"""Keyword side of the hybrid search: hashed BM25-style sparse vectors, no model needed."""

from __future__ import annotations

import re
import unicodedata
import zlib

# Typical chunk length in tokens, used as the BM25 average document length
AVG_DOC_LEN = 120.0
K1, B = 1.2, 0.75

_WORD = re.compile(r"[A-Za-z0-9_]+")
_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
STOPWORDS = frozenset(
    "le la les un une des du de et en au aux ce ces cet cette son sa ses leur leurs mon ma mes "
    "ton ta tes que qui quoi dont où ou mais donc or ni car ne pas plus se sur sous dans par pour "
    "avec sans est sont été être avoir a ai as ont il elle ils elles on nous vous je tu me te lui "
    "the a an and or of to in on at is are was were be been it its this that these those for with "
    "as by from not but if then so".split()
)


def _strip_accents(text: str) -> str:
    """Drop accents, keeping case, so 'Résumé' matches 'resume'."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _fold(text: str) -> str:
    return text.lower()


def tokenize(text: str) -> list[str]:
    """Words plus the parts of snake_case / camelCase identifiers (useful for code)."""
    tokens: list[str] = []
    for word in _WORD.findall(_strip_accents(text)):
        parts = [p for chunk in word.split("_") for p in _CAMEL.findall(chunk)]
        pieces = [_fold(word)] + ([_fold(p) for p in parts] if len(parts) > 1 else [])
        for piece in pieces:
            if 2 <= len(piece) <= 40 and piece not in STOPWORDS:
                tokens.append(piece)
    return tokens


def _bucket(token: str) -> int:
    return zlib.crc32(token.encode("utf-8")) & 0xFFFFFFFF


def encode_documents(texts: list[str]) -> tuple[list[list[int]], list[list[float]]]:
    """BM25 term-frequency weights; Qdrant's IDF modifier supplies the rest at query time."""
    all_indices: list[list[int]] = []
    all_values: list[list[float]] = []
    for text in texts:
        tokens = tokenize(text)
        counts: dict[int, int] = {}
        for token in tokens:
            counts[_bucket(token)] = counts.get(_bucket(token), 0) + 1
        norm = K1 * (1 - B + B * max(len(tokens), 1) / AVG_DOC_LEN)
        indices = sorted(counts)
        all_indices.append(indices)
        all_values.append([counts[i] * (K1 + 1) / (counts[i] + norm) for i in indices])
    return all_indices, all_values


def encode_queries(texts: list[str]) -> tuple[list[list[int]], list[list[float]]]:
    """One unit weight per distinct query term."""
    all_indices: list[list[int]] = []
    for text in texts:
        all_indices.append(sorted({_bucket(t) for t in tokenize(text)}))
    return all_indices, [[1.0] * len(ix) for ix in all_indices]
