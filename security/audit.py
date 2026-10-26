"""Append-only JSONL audit trail of every tool action."""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from security.masking import Masker


@dataclass
class AuditEntry:
    """Mutable outcome of an action, filled in by the caller."""

    status: str = "ok"
    detail: str | None = None


class AuditLog:
    """Write one JSON line per action: tool, arguments, status, duration."""

    def __init__(self, path: Path, masker: Masker, enabled: bool = True,
                 max_chars: int = 2000) -> None:
        self._path = path
        self._masker = masker
        self._enabled = enabled
        self._max_chars = max_chars
        self._lock = threading.Lock()

    def _truncate(self, obj: Any) -> Any:
        if isinstance(obj, str) and len(obj) > self._max_chars:
            return f"{obj[: self._max_chars]}… [tronqué, {len(obj)} caractères]"
        if isinstance(obj, dict):
            return {k: self._truncate(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._truncate(v) for v in obj]
        return obj

    def record(self, tool: str, arguments: dict[str, Any] | None, status: str,
               duration_ms: float, detail: str | None = None) -> None:
        if not self._enabled:
            return
        # Mask first, then truncate, so a cut never leaves half a secret
        line = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "tool": tool,
            "arguments": self._truncate(self._masker.mask(arguments or {})),
            "status": status,
            "duration_ms": round(duration_ms, 1),
        }
        if detail:
            line["detail"] = self._truncate(self._masker.mask_text(detail))
        data = json.dumps(line, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # Private file: only the owner can read the trail
            fd = os.open(self._path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(data)

    @contextmanager
    def track(self, tool: str, arguments: dict[str, Any] | None = None) -> Iterator[AuditEntry]:
        """Time an action and log it, whatever its outcome."""
        entry = AuditEntry()
        start = time.perf_counter()
        try:
            yield entry
        except Exception as exc:
            entry.status = "error"
            entry.detail = f"{type(exc).__name__}: {exc}"
            raise
        except BaseException:
            # Cancellation or Ctrl+C
            entry.status = "interrupted"
            raise
        finally:
            self.record(tool, arguments, entry.status,
                        (time.perf_counter() - start) * 1000, entry.detail)
