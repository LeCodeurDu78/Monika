"""Split documents into passages while keeping exact positions for citations."""

from __future__ import annotations

from dataclasses import dataclass

from rag.loaders import Loaded


@dataclass(frozen=True)
class Position:
    kind: str  # lines | page | paragraphs
    start: int
    end: int

    def label(self) -> str:
        if self.kind == "page":
            return f"page {self.start}"
        word = "ligne" if self.kind == "lines" else "paragraphe"
        if self.start == self.end:
            return f"{word} {self.start}"
        return f"{word}s {self.start}–{self.end}"


@dataclass(frozen=True)
class Chunk:
    text: str
    position: Position


def chunk_lines(lines: list[str], size: int, overlap: int, kind: str = "lines",
                number: int | None = None) -> list[Chunk]:
    """Greedy line-based chunks; prefers cutting at a blank line; consecutive chunks overlap.

    With `number` set, every chunk is positioned on that page instead of on line numbers.
    """
    chunks: list[Chunk] = []
    buf: list[tuple[int, str]] = []  # (0-based line index, text)
    fresh = False  # True when buf holds lines not yet emitted

    def position(items: list[tuple[int, str]]) -> Position:
        if number is not None:
            return Position("page", number, number)
        return Position(kind, items[0][0] + 1, items[-1][0] + 1)

    def emit(items: list[tuple[int, str]]) -> None:
        text = "\n".join(t for _, t in items).strip()
        if text:
            chunks.append(Chunk(text, position(items)))

    def length(items: list[tuple[int, str]]) -> int:
        return sum(len(t) + 1 for _, t in items)

    for idx, line in enumerate(lines):
        if len(line) > size:  # One huge line: cut it in fixed windows on the same position
            if fresh:
                emit(buf)
            buf, fresh = [], False
            for start in range(0, len(line), size - overlap):
                emit([(idx, line[start:start + size])])
            continue
        buf.append((idx, line))
        fresh = True
        if length(buf) < size:
            continue
        cut = len(buf)
        blanks = [k for k in range(len(buf) // 2, len(buf) - 1) if not buf[k][1].strip()]
        if blanks:  # Cut at the last blank line of the window to keep paragraphs/functions whole
            cut = blanks[-1] + 1
        emit(buf[:cut])
        keep: list[tuple[int, str]] = []
        for item in reversed(buf[:cut]):
            if length(keep) >= overlap:
                break
            keep.insert(0, item)
        keep = keep[1:] if len(keep) == cut else keep  # Always make progress
        buf = keep + buf[cut:]
        fresh = len(buf) > len(keep)
    if fresh:
        emit(buf)
    return chunks


def chunk_paragraphs(paragraphs: list[str], size: int, overlap: int) -> list[Chunk]:
    """Greedy paragraph chunks (Word); a short last paragraph is repeated as overlap."""
    chunks: list[Chunk] = []
    buf: list[tuple[int, str]] = []
    fresh = False

    def emit(items: list[tuple[int, str]]) -> None:
        chunks.append(Chunk("\n".join(t for _, t in items),
                            Position("paragraphs", items[0][0] + 1, items[-1][0] + 1)))

    for idx, para in enumerate(paragraphs):
        if len(para) > size:
            if fresh:
                emit(buf)
            buf, fresh = [], False
            for start in range(0, len(para), size - overlap):
                chunks.append(Chunk(para[start:start + size], Position("paragraphs", idx + 1, idx + 1)))
            continue
        if buf and sum(len(t) + 1 for _, t in buf) + len(para) > size:
            emit(buf)
            last = buf[-1]
            buf = [last] if len(last[1]) <= overlap else []
            fresh = False
        buf.append((idx, para))
        fresh = True
    if fresh:
        emit(buf)
    return chunks


def chunk_document(doc: Loaded, size: int, overlap: int) -> list[Chunk]:
    """Chunk any loaded document with the strategy that suits its kind."""
    if doc.kind == "pdf":
        return [c for number, text in doc.pages
                for c in chunk_lines(text.splitlines(), size, overlap, number=number)]
    if doc.kind == "docx":
        return chunk_paragraphs(doc.paragraphs, size, overlap)
    return chunk_lines(doc.lines, size, overlap)
