"""Detect an agent stuck repeating the same call with the same result."""

from __future__ import annotations

import hashlib
from typing import Literal

Verdict = Literal["ok", "warn", "stop"]


class LoopDetector:
    """Count identical (tool, arguments, result) triples within one turn."""

    def __init__(self, warn_after: int = 3, stop_after: int = 5) -> None:
        self._warn = warn_after
        self._stop = stop_after
        self._counts: dict[tuple[str, str, str], int] = {}

    def record(self, name: str, args_key: str, result: str) -> Verdict:
        digest = hashlib.sha1(result.encode("utf-8", "replace")).hexdigest()
        key = (name, args_key, digest)
        self._counts[key] = count = self._counts.get(key, 0) + 1
        if count >= self._stop:
            return "stop"
        return "warn" if count >= self._warn else "ok"
