"""SQLite record of what has been indexed, so syncing only touches changed files."""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileRow:
    path: str
    file_id: str
    mtime_ns: int
    size: int
    sha256: str
    chunks: int
    status: str  # indexed | skipped | error
    detail: str = ""
    indexed_at: float = 0.0


class Manifest:
    """Thread-safe file table plus a fingerprint of the settings the index was built with."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, file_id TEXT, mtime_ns INTEGER,"
                " size INTEGER, sha256 TEXT, chunks INTEGER, status TEXT, detail TEXT, indexed_at REAL)")
            self._db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        path.chmod(0o600)

    def fingerprint(self) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key='fingerprint'").fetchone()
        return row[0] if row else None

    def set_fingerprint(self, value: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO meta VALUES ('fingerprint', ?)", (value,))

    def get(self, path: str) -> FileRow | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM files WHERE path=?", (path,)).fetchone()
        return FileRow(*row) if row else None

    def all(self) -> dict[str, FileRow]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM files").fetchall()
        return {r[0]: FileRow(*r) for r in rows}

    def upsert(self, row: FileRow) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?,?)", (
                row.path, row.file_id, row.mtime_ns, row.size, row.sha256, row.chunks, row.status,
                row.detail, row.indexed_at or time.time()))

    def delete(self, path: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM files WHERE path=?", (path,))

    def counts(self) -> dict[str, int]:
        """Return files per status plus the total number of indexed passages."""
        with self._lock:
            rows = self._db.execute("SELECT status, COUNT(*), SUM(chunks) FROM files GROUP BY status").fetchall()
        out = {"indexed": 0, "skipped": 0, "error": 0, "chunks": 0}
        for status, n, chunks in rows:
            out[status] = n
            if status == "indexed":
                out["chunks"] += chunks or 0
        return out

    def skipped_reasons(self, limit: int = 8) -> list[tuple[str, int]]:
        with self._lock:
            return self._db.execute(
                "SELECT detail, COUNT(*) c FROM files WHERE status='skipped' GROUP BY detail "
                "ORDER BY c DESC LIMIT ?", (limit,)).fetchall()

    def clear(self) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM files")
            self._db.execute("DELETE FROM meta")

    def close(self) -> None:
        with self._lock:
            self._db.close()
