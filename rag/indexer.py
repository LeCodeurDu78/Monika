"""Incremental indexing of the home folder.

A scan walks the configured folders and compares (modification date, size) with the state stored in SQLite:
only new or modified files are re-read (and if the content is identical despite a new date, they are simply
skipped thanks to the hash). Files that disappeared or are now excluded are removed from the index. A scan
never runs twice in parallel.
"""

import hashlib
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from qdrant_client import models

from core.db import db_path, get_connection, init_table
from core.settings import settings
from core.watcher import start_watcher
from rag import sparse
from rag.chunking import extract_chunks
from rag.models import get_embedder, get_reranker
from rag.store import DENSE, SPARSE, get_store, point_id

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    mtime_ns INTEGER NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT,
    n_chunks INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ok',
    indexed_at REAL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""
_DB_FILE = "rag_index.db"
_scan_lock = threading.Lock()
_schema_ready = False

PROGRESS: dict = {"state": "idle", "done": 0, "total": 0, "phase": ""}


@contextmanager
def _db():
    global _schema_ready
    path = db_path(_DB_FILE)
    if not _schema_ready:
        init_table(path, _SCHEMA)
        _schema_ready = True
    conn = get_connection(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _log(message: str) -> None:
    print(f"📚 [RAG] {message}")


# --- Exploration ---------------------------------------------------------------------------------


def _extensions() -> set[str]:
    return {e.lower() if e.startswith(".") else f".{e.lower()}" for e in settings.RAG_EXTENSIONS}


def walk_files(roots: list[Path]) -> Iterator[tuple[str, os.stat_result]]:
    """Walk the folders applying the exclusions; does not follow symbolic links."""
    exts = _extensions()
    excluded = {d.lower() for d in settings.RAG_EXCLUDE_DIRS}
    app_dir = os.path.abspath(str(settings.APP_DIR))
    max_text = int(settings.RAG_MAX_FILE_MB * 1024 * 1024)
    max_binary = int(settings.RAG_MAX_BINARY_MB * 1024 * 1024)
    stack = [os.path.abspath(str(r)) for r in roots if Path(r).is_dir()]
    while stack:
        current = stack.pop()
        if current == app_dir or os.path.exists(os.path.join(current, ".ragignore")):
            continue
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    name = entry.name
                    if name.startswith(".") and not settings.RAG_INCLUDE_HIDDEN:
                        continue
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if name.lower() not in excluded and not name.endswith(".egg-info"):
                                stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            ext = os.path.splitext(name)[1].lower()
                            if ext not in exts:
                                continue
                            st = entry.stat(follow_symlinks=False)
                            limit = max_binary if ext in (".pdf", ".docx") else max_text
                            if 0 < st.st_size <= limit:
                                yield entry.path, st
                    except OSError:
                        continue
        except OSError:
            continue  # dossier illisible (permissions)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _ancestors(path: str) -> list[str]:
    parts = Path(path).parents
    return [str(p) for p in parts if str(p) != os.sep] + [path]


# --- Indexing one file ---------------------------------------------------------------------


def _index_file(path: str, st: os.stat_result, known: Optional[tuple]) -> str:
    """Return 'indexed', 'unchanged', 'skipped' or raise an exception (the caller records the error)."""
    store = get_store()
    digest = _sha256(path)
    old_chunks = known[3] if known else 0
    if known and known[2] == digest:
        with _db() as conn:
            conn.execute("UPDATE files SET mtime_ns=?, size=? WHERE path=?", (st.st_mtime_ns, st.st_size, path))
        return "unchanged"

    chunks = extract_chunks(Path(path))
    if not chunks:
        if old_chunks:
            store.delete_path(path)
        _save_state(path, st, digest, 0, "skipped")
        return "skipped"

    name = os.path.basename(path)
    folder = os.path.basename(os.path.dirname(path))
    headers = [f"{folder}/{name} — {c.location}" for c in chunks]
    texts_for_embedding = [f"{h}\n{c.text}" for h, c in zip(headers, chunks)]
    vectors = get_embedder().embed_passages(texts_for_embedding)
    ancestors = _ancestors(path)
    points = [
        models.PointStruct(
            id=point_id(path, i),
            vector={DENSE: vectors[i], SPARSE: sparse.encode_document(texts_for_embedding[i])},
            payload={
                "path": path,
                "name": name,
                "ext": os.path.splitext(name)[1].lower(),
                "location": chunk.location,
                "header": headers[i],
                "text": chunk.text,
                "chunk_index": i,
                "mtime": st.st_mtime,
                "ancestors": ancestors,
            },
        )
        for i, chunk in enumerate(chunks)
    ]
    store.upsert(points)  # identifiers are deterministic, so old chunks are overwritten
    if old_chunks > len(points):
        store.delete_ids(point_id(path, i) for i in range(len(points), old_chunks))
    _save_state(path, st, digest, len(points), "ok")
    return "indexed"


def _save_state(path: str, st: os.stat_result, digest: Optional[str], n_chunks: int, status: str) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO files (path, mtime_ns, size, sha256, n_chunks, status, indexed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (path, st.st_mtime_ns, st.st_size, digest, n_chunks, status, time.time()),
        )


# --- Scan ----------------------------------------------------------------------------------------


def scan(stop_event: Optional[threading.Event] = None) -> Optional[dict]:
    """One indexing pass. Return statistics, or None if a scan is already running."""
    if not settings.RAG_ENABLED or not _scan_lock.acquire(blocking=False):
        return None
    started = time.time()
    stats = {"indexed": 0, "unchanged": 0, "skipped": 0, "errors": 0, "deleted": 0, "complete": False}
    try:
        PROGRESS.update(state="scanning", phase="exploration", done=0, total=0)
        roots = [Path(r).expanduser() for r in settings.RAG_ROOTS]
        with _db() as conn:
            known = {row[0]: row[1:] for row in conn.execute(
                "SELECT path, mtime_ns, size, sha256, n_chunks FROM files")}

        seen: set[str] = set()
        todo: list[tuple[str, os.stat_result]] = []
        for path, st in walk_files(roots):
            if stop_event is not None and stop_event.is_set():
                return stats
            seen.add(path)
            prev = known.get(path)
            if prev is None or prev[0] != st.st_mtime_ns or prev[1] != st.st_size:
                todo.append((path, st))

        PROGRESS.update(phase="indexation", total=len(todo))
        if todo:
            _log(f"{len(todo)} fichier(s) à indexer sur {len(seen)} trouvés.")
        shown_errors = 0
        for n, (path, st) in enumerate(todo, 1):
            if stop_event is not None and stop_event.is_set():
                return stats
            try:
                prev = known.get(path)
                outcome = _index_file(path, st, prev)
                stats[outcome] += 1
            except Exception as exc:
                stats["errors"] += 1
                try:  # recorded: no new attempt until the file changes
                    _save_state(path, st, None, 0, "error")
                except Exception:
                    pass
                if shown_errors < 5:
                    shown_errors += 1
                    _log(f"⚠️ {path} : {type(exc).__name__}: {exc}")
            PROGRESS["done"] = n
            if n % 200 == 0:
                _log(f"{n}/{len(todo)} fichiers traités...")
            if settings.RAG_FILE_PAUSE_SECONDS:
                time.sleep(settings.RAG_FILE_PAUSE_SECONDS)

        # Deletions: files that disappeared or are now excluded. Safeguards: never on an interrupted scan,
        # never if nothing was seen (unmounted disk, wrong path), and only under the accessible roots.
        live_roots = [str(r.resolve()) for r in roots if r.is_dir()]
        if seen and live_roots:
            stale = [
                p for p in known
                if p not in seen and any(p.startswith(r.rstrip(os.sep) + os.sep) for r in live_roots)
            ]
            store = get_store()
            for p in stale:
                store.delete_path(p)
            if stale:
                with _db() as conn:
                    conn.executemany("DELETE FROM files WHERE path=?", [(p,) for p in stale])
            stats["deleted"] = len(stale)

        stats["complete"] = True
        with _db() as conn:
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('last_full_scan', ?)", (str(time.time()),))
        if stats["indexed"] or stats["deleted"] or stats["errors"]:
            _log(
                f"Scan terminé en {time.time() - started:.0f}s : {stats['indexed']} indexé(s), "
                f"{stats['deleted']} supprimé(s), {stats['errors']} erreur(s)."
            )
        return stats
    finally:
        PROGRESS.update(state="idle", phase="")
        _scan_lock.release()


def status_line() -> str:
    """Status sentence shown by the search tool (empty if the index is complete and up to date)."""
    if PROGRESS["state"] == "scanning":
        if PROGRESS["phase"] == "indexation" and PROGRESS["total"]:
            return f"Indexation en cours : {PROGRESS['done']}/{PROGRESS['total']} fichiers traités ; les résultats sont partiels."
        return "Exploration des dossiers en cours ; les résultats sont partiels."
    try:
        with _db() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key='last_full_scan'").fetchone()
    except Exception:
        row = None
    if row is None:
        return "La première indexation n'est pas terminée ; les résultats sont partiels ou absents."
    return ""


def stats_summary() -> dict:
    with _db() as conn:
        rows = conn.execute("SELECT status, COUNT(*), COALESCE(SUM(n_chunks),0) FROM files GROUP BY status").fetchall()
    return {status: {"files": n, "chunks": c} for status, n, c in rows}


def reset_state() -> None:
    with _db() as conn:
        conn.execute("DELETE FROM files")
        conn.execute("DELETE FROM meta")


# --- Integration with Monika ------------------------------------------------------------------------


def start_rag_indexing() -> threading.Event:
    """Start the periodic scan in the background; the first pass happens shortly after startup."""
    stop_event: threading.Event

    def _tick() -> None:
        try:
            scan(stop_event)
        except Exception as exc:  # a failed scan must never bring the watcher down
            _log(f"⚠️ Scan impossible : {type(exc).__name__}: {exc}")

    stop_event = start_watcher(settings.RAG_SCAN_INTERVAL_SECONDS, _tick)

    def _first_pass() -> None:
        if stop_event.wait(settings.RAG_SCAN_INITIAL_DELAY_SECONDS):
            return
        try:
            get_embedder()
            if settings.RAG_RERANK_ENABLED:
                get_reranker()
        finally:
            _tick()

    threading.Thread(target=_first_pass, daemon=True).start()
    return stop_event
