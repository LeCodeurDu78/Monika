"""Read personal files (text, code, PDF, Word) into plain text with their natural positions."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rag.filters import find_secret

TEXT_EXT = {
    ".txt", ".md", ".markdown", ".rst", ".org", ".tex", ".adoc", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".xml", ".csv", ".tsv", ".srt", ".bib",
}
CODE_EXT = {
    ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".c", ".h", ".cpp", ".cc", ".hpp", ".rs", ".go",
    ".java", ".kt", ".rb", ".php", ".sh", ".bash", ".zsh", ".fish", ".lua", ".sql", ".css",
    ".scss", ".html", ".htm", ".vue", ".svelte", ".swift", ".cs", ".pl", ".r", ".ex", ".exs",
    ".hs", ".ml", ".nix", ".cmake", ".gradle", ".zig", ".nim", ".dart", ".scala", ".clj",
    ".service", ".timer", ".install", ".desktop", ".ipynb",
}
CODE_NAMES = {"Makefile", "Dockerfile", "PKGBUILD", "CMakeLists.txt", "Justfile", "Vagrantfile"}


class SkipFile(Exception):
    """The file is not indexed; the message says why (in French)."""


@dataclass
class Loaded:
    """File content in the shape that gives the best citation position."""

    kind: str  # text | code | pdf | docx
    lines: list[str] = field(default_factory=list)  # text/code: one entry per line
    pages: list[tuple[int, str]] = field(default_factory=list)  # pdf: (page number, text)
    paragraphs: list[str] = field(default_factory=list)  # docx: non-empty paragraphs/rows

    def full_text(self) -> str:
        if self.kind == "pdf":
            return "\n".join(t for _, t in self.pages)
        if self.kind == "docx":
            return "\n".join(self.paragraphs)
        return "\n".join(self.lines)


def classify(path: Path) -> str | None:
    """Return the file kind, or None when Monika does not read this type."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext == ".docx":
        return "docx"
    if path.name in CODE_NAMES or ext in CODE_EXT:
        return "code"
    if ext in TEXT_EXT or path.name.upper() in {"README", "LICENSE", "CHANGELOG", "TODO", "NOTES"}:
        return "text"
    return None


def load(path: Path, kind: str, max_text_kb: int, max_doc_mb: int) -> Loaded:
    """Read a file or raise SkipFile with the reason."""
    size = path.stat().st_size
    if size == 0:
        raise SkipFile("fichier vide")
    if kind in ("text", "code"):
        if size > max_text_kb * 1024:
            raise SkipFile(f"trop gros ({size // 1024} Ko, limite RAG_MAX_TEXT_KB={max_text_kb})")
        loaded = _load_text(path, kind)
    else:
        if size > max_doc_mb * 1024 * 1024:
            raise SkipFile(f"trop gros ({size // (1024 * 1024)} Mo, limite RAG_MAX_DOC_MB={max_doc_mb})")
        loaded = _load_pdf(path) if kind == "pdf" else _load_docx(path)
    text = loaded.full_text()
    if not text.strip():
        raise SkipFile("aucun texte extractible" + (" (PDF scanné ? l'OCR n'est pas géré)" if kind == "pdf" else ""))
    reason = find_secret(text)
    if reason:
        raise SkipFile(f"secret détecté ({reason})")
    return loaded


def _load_text(path: Path, kind: str) -> Loaded:
    raw = path.read_bytes()
    if b"\x00" in raw[:8192]:
        raise SkipFile("fichier binaire")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    lines = text.splitlines()
    # Minified bundles: a few enormous lines are useless as passages
    if len(raw) > 50_000 and len(text) / max(len(lines), 1) > 500:
        raise SkipFile("fichier minifié")
    return Loaded(kind=kind, lines=lines)


def _load_pdf(path: Path) -> Loaded:
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted and not reader.decrypt(""):
            raise SkipFile("PDF chiffré")
        pages = []
        for number, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:  # One broken page must not lose the whole document
                text = ""
            if text.strip():
                pages.append((number, text))
    except SkipFile:
        raise
    except Exception as exc:
        raise SkipFile(f"PDF illisible ({type(exc).__name__})") from exc
    return Loaded(kind="pdf", pages=pages)


def _load_docx(path: Path) -> Loaded:
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        paragraphs: list[str] = []
        for block in Document(str(path)).iter_inner_content():
            if isinstance(block, Paragraph):
                if block.text.strip():
                    paragraphs.append(block.text.strip())
            elif isinstance(block, Table):
                for row in block.rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if cells:
                        paragraphs.append(" | ".join(cells))
    except Exception as exc:
        raise SkipFile(f"document Word illisible ({type(exc).__name__})") from exc
    return Loaded(kind="docx", paragraphs=paragraphs)
