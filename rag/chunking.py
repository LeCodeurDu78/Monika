"""Structure-aware document chunking: headings (Markdown/HTML/DOCX), functions and classes (Python),
pages (PDF), line blocks (other code), paragraphs (text). Each chunk keeps its position.
"""

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from core.settings import settings


@dataclass
class Chunk:
    text: str
    location: str  # « section : A > B », « page 3 », « lignes 10-42 · def foo »...


# --- Secrets -------------------------------------------------------------------------------------

_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    re.compile(r"(?i)\b(?:password|passwd|mot_?de_?passe|secret|api[_-]?key|token)\b\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"),
]
_SECRET_NAME = re.compile(r"(?i)(secret|credential|password|passwd|mdp|id_rsa|id_ed25519|\.env)")
_SECRET_EXT = {".pem", ".key", ".p12", ".pfx", ".kdbx", ".gpg", ".asc"}


def is_secret_filename(path: Path) -> bool:
    return path.suffix.lower() in _SECRET_EXT or bool(_SECRET_NAME.search(path.name))


def looks_secret(text: str) -> bool:
    return any(p.search(text) for p in _SECRET_PATTERNS)


# --- Generic chunking -------------------------------------------------------------------------


def _explode(block: str, max_chars: int) -> list[str]:
    """Split a block that is too long: by lines if it has several, else by sentences, else by cutting."""
    multiline = "\n" in block
    sep = "\n" if multiline else " "
    pieces = block.split("\n") if multiline else re.split(r"(?<=[.!?…])\s+", block)
    out: list[str] = []
    cur = ""
    for piece in pieces:
        if len(piece) > max_chars:
            if cur:
                out.append(cur)
                cur = ""
            out.extend(piece[i : i + max_chars] for i in range(0, len(piece), max_chars))
        elif cur and len(cur) + len(sep) + len(piece) > max_chars:
            out.append(cur)
            cur = piece
        else:
            cur = f"{cur}{sep}{piece}" if cur else piece
    if cur:
        out.append(cur)
    return out


def split_text(text: str, max_chars: int, overlap: int) -> list[str]:
    """Group paragraphs up to `max_chars`, with a slight overlap between chunks."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    units: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        units.extend([para] if len(para) <= max_chars else _explode(para, max_chars))
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for unit in units:
        extra = len(unit) + (2 if cur else 0)
        if cur and cur_len + extra > max_chars:
            chunks.append("\n\n".join(cur))
            carry: list[str] = []
            carried = 0
            for prev in reversed(cur):
                if carried + len(prev) > overlap:
                    break
                carry.insert(0, prev)
                carried += len(prev) + 2
            if carried + len(unit) + 2 > max_chars:
                carry, carried = [], 0
            cur = carry
            cur_len = sum(len(x) for x in cur) + 2 * max(0, len(cur) - 1)
            extra = len(unit) + (2 if cur else 0)
        cur.append(unit)
        cur_len += extra
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


def chunk_lines(text: str, location_prefix: str = "") -> list[Chunk]:
    """Line windows (code, files with no known structure), cut preferably on an empty line."""
    max_chars = settings.RAG_CHUNK_CHARS
    lines: list[str] = []
    for line in text.splitlines():
        if len(line) > max_chars:  # minified files
            lines.extend(line[i : i + max_chars] for i in range(0, len(line), max_chars))
        else:
            lines.append(line)
    out: list[Chunk] = []
    i, n = 0, len(lines)
    overlap_lines = 3
    while i < n:
        j, size, last_blank = i, 0, -1
        while j < n and (j == i or size + len(lines[j]) + 1 <= max_chars):
            if not lines[j].strip():
                last_blank = j
            size += len(lines[j]) + 1
            j += 1
        if j < n and last_blank > i + (j - i) // 3:
            j = last_blank + 1
        body = "\n".join(lines[i:j]).strip()
        if body:
            out.append(Chunk(body, f"{location_prefix}lignes {i + 1}-{j}"))
        if j >= n:
            break
        i = max(i + 1, j - overlap_lines)
    return out


def chunk_plain_text(text: str) -> list[Chunk]:
    parts = split_text(text, settings.RAG_CHUNK_CHARS, settings.RAG_CHUNK_OVERLAP_CHARS)
    return [Chunk(p, f"partie {k}/{len(parts)}") for k, p in enumerate(parts, 1)]


# --- Markdown ------------------------------------------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def chunk_markdown(text: str) -> list[Chunk]:
    sections: list[tuple[list[str], str]] = []
    stack: list[tuple[int, str]] = []
    cur_lines: list[str] = []
    cur_path: list[str] = []
    in_fence = False

    def flush() -> None:
        body = "\n".join(cur_lines).strip()
        if body:
            sections.append((list(cur_path), body))

    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        match = None if in_fence else _HEADING.match(line)
        if match:
            flush()
            level, title = len(match.group(1)), match.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            cur_path = [t for _, t in stack]
            cur_lines = [line]
        else:
            cur_lines.append(line)
    flush()

    def label(paths: list[list[str]]) -> str:
        names: list[str] = []
        for path in paths:
            name = " > ".join(path)
            if name and name not in names:
                names.append(name)
        if not names:
            return "début du document"
        return "section : " + " ; ".join(names[:3]) + (" …" if len(names) > 3 else "")

    chunks: list[Chunk] = []
    carry = ""
    carry_paths: list[list[str]] = []
    for idx, (path, body) in enumerate(sections):
        # A tiny section is attached to the next one; the position lists every merged section.
        body = f"{carry}\n\n{body}".strip() if carry else body
        paths = carry_paths + [path]
        carry, carry_paths = "", []
        if len(body) < settings.RAG_MIN_CHUNK_CHARS and idx < len(sections) - 1:
            carry, carry_paths = body, paths
            continue
        for part in split_text(body, settings.RAG_CHUNK_CHARS, settings.RAG_CHUNK_OVERLAP_CHARS):
            chunks.append(Chunk(part, label(paths)))
    return chunks


# --- Python --------------------------------------------------------------------------------------

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def chunk_python(text: str) -> list[Chunk]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return chunk_lines(text)
    lines = text.splitlines()
    max_chars = settings.RAG_CHUNK_CHARS
    spans: list[tuple[int, int, str]] = []  # (start, end, label), 1-indexed lines, inclusive

    def add(start: int, end: int, label: str) -> None:
        if end >= start and "\n".join(lines[start - 1 : end]).strip():
            spans.append((start, end, label))

    def node_start(node) -> int:
        return min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])

    def walk(nodes, lo: int, hi: int, prefix: str) -> None:
        cursor = lo
        for node in nodes:
            if not isinstance(node, _DEFS):
                continue
            start, end = node_start(node), node.end_lineno
            add(cursor, start - 1, prefix or "module")
            name = f"{prefix}.{node.name}" if prefix else node.name
            size = sum(len(line) + 1 for line in lines[start - 1 : end])
            if isinstance(node, ast.ClassDef) and size > max_chars:
                walk(node.body, start, end, name)  # big class: one entry per method
            else:
                add(start, end, f"{'class' if isinstance(node, ast.ClassDef) else 'def'} {name}")
            cursor = end + 1
        add(cursor, hi, prefix or "module")

    walk(tree.body, 1, len(lines), "")

    # Group small contiguous blocks (avoids dozens of one-line chunks).
    merged: list[list] = []
    for start, end, label in spans:
        size = sum(len(line) + 1 for line in lines[start - 1 : end])
        if merged and merged[-1][3] + size <= max_chars and merged[-1][1] + 1 == start:
            merged[-1][1] = end
            merged[-1][2].append(label)
            merged[-1][3] += size
        else:
            merged.append([start, end, [label], size])

    chunks: list[Chunk] = []
    for start, end, labels, _ in merged:
        shown = ", ".join(labels[:3]) + (" …" if len(labels) > 3 else "")
        body = "\n".join(lines[start - 1 : end])
        for part in split_text(body, max_chars, settings.RAG_CHUNK_OVERLAP_CHARS):
            chunks.append(Chunk(part, f"lignes {start}-{end} · {shown}"))
    return chunks


# --- HTML / PDF / DOCX ---------------------------------------------------------------------------


def chunk_html(text: str) -> list[Chunk]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "head"]):
        tag.decompose()
    for level in range(1, 7):
        for h in soup.find_all(f"h{level}"):
            h.replace_with(f"\n\n{'#' * level} {h.get_text(' ', strip=True)}\n\n")
    return chunk_markdown(soup.get_text("\n"))


def chunk_pdf(path: Path) -> list[Chunk]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        return []
    chunks: list[Chunk] = []
    for number, page in enumerate(reader.pages[: settings.RAG_MAX_PDF_PAGES], 1):
        try:
            page_text = page.extract_text() or ""
        except Exception:
            continue  # unreadable page: move on to the next (scanned PDFs have no text, and there is no OCR)
        for part in split_text(page_text, settings.RAG_CHUNK_CHARS, settings.RAG_CHUNK_OVERLAP_CHARS):
            chunks.append(Chunk(part, f"page {number}"))
    return chunks


def chunk_docx(path: Path) -> list[Chunk]:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = docx.Document(str(path))
    lines: list[str] = []
    items = document.iter_inner_content() if hasattr(document, "iter_inner_content") else document.paragraphs
    for item in items:
        if isinstance(item, Paragraph):
            style = (item.style.name if item.style is not None else "") or ""
            text = item.text.strip()
            if not text:
                continue
            m = re.match(r"(?i)(?:heading|titre)\s*(\d)", style)
            lines.append(f"{'#' * min(int(m.group(1)), 6)} {text}" if m else text)
        elif isinstance(item, Table):
            for row in item.rows:
                cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                if any(cells):
                    lines.append(" | ".join(cells))
        lines.append("")
    return chunk_markdown("\n".join(lines))


# --- Entry point ------------------------------------------------------------------------------

_TEXT_ONLY = {".txt", ".rst", ".org"}
_MARKDOWN = {".md", ".markdown"}
_HTML = {".html", ".htm"}


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if b"\x00" in raw[:8192]:
        return ""  # disguised binary
    return raw.decode("utf-8", errors="replace")


def extract_chunks(path: Path) -> list[Chunk]:
    """Chunk a file. Empty list: nothing usable (empty, binary, secret, scanned PDF...)."""
    ext = path.suffix.lower()
    if settings.RAG_SKIP_SECRETS and is_secret_filename(path):
        return []
    if ext == ".pdf":
        chunks = chunk_pdf(path)
    elif ext == ".docx":
        chunks = chunk_docx(path)
    else:
        text = _read_text(path)
        if not text.strip():
            return []
        if ext in _MARKDOWN:
            chunks = chunk_markdown(text)
        elif ext == ".py":
            chunks = chunk_python(text)
        elif ext in _HTML:
            chunks = chunk_html(text)
        elif ext in _TEXT_ONLY:
            chunks = chunk_plain_text(text)
        else:
            chunks = chunk_lines(text)
    chunks = [c for c in chunks if c.text.strip()]
    if settings.RAG_SKIP_SECRETS:
        chunks = [c for c in chunks if not looks_secret(c.text)]
    return chunks
