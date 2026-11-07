"""One Monika per data folder: the lock that protects memory, documents and the notebook.

Two processes rewriting `notes.json` or opening the same Qdrant index would lose data, so the
lock is taken by the single local process (`python -m monika`) before anything is opened.

POSIX uses `fcntl.flock`; Windows has no such call and uses `msvcrt.locking` on one byte of the
same file instead. Both are released by the operating system when the process dies.
"""

from __future__ import annotations

import os
from pathlib import Path

try:  # POSIX
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]

try:  # Windows
    import msvcrt
except ImportError:  # POSIX
    msvcrt = None  # type: ignore[assignment]


class LockError(Exception):
    """Another instance owns this data folder; the message is in French and ready to display."""


class InstanceLock:
    """Exclusive, non-blocking lock on a file: a second process is refused instead of waiting."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    def acquire(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self.path, "a+", encoding="utf-8")
        except OSError as exc:
            raise LockError(
                f"Verrou d'instance inaccessible ({self.path}) : {exc}"
            ) from exc
        try:
            self._take(handle)
        except OSError as exc:
            handle.close()
            raise LockError(
                "Une autre instance Monika utilise déjà ce dossier de données (mémoire et "
                "documents ne supportent qu'un seul processus). Ferme-la, puis relance."
            ) from exc
        try:
            os.chmod(self.path, 0o600)  # a no-op on Windows: the lock, not the mode, protects it
        except OSError:
            pass
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            self._let_go(self._handle)
        except OSError:
            pass
        self._handle.close()
        self._handle = None

    @staticmethod
    def _take(handle) -> None:
        if msvcrt is not None:  # Windows: lock one byte, which must exist
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write("monika")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:  # POSIX
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _let_go(handle) -> None:
        if msvcrt is not None:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
