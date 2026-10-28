"""Persistence of the character state: one small JSON file, written atomically, readable only by its owner."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
VERSION = 1


class CharacterStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any] | None:
        """The saved data, or None when there is none or it is unusable (a damaged file is set aside, not deleted)."""
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as err:
            log.warning("character state unreadable: %s", err)
            return None
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("not an object")
            if data.get("version") != VERSION:
                raise ValueError(f"unknown version {data.get('version')!r}")
            return data
        except ValueError as err:
            aside = self.path.with_name(f"{self.path.name}.corrupt-{int(time.time())}")
            try:
                os.replace(self.path, aside)
                log.warning("character state damaged (%s); kept as %s and starting fresh", err, aside.name)
            except OSError:
                log.warning("character state damaged (%s); starting fresh", err)
            return None

    def save(self, data: dict[str, Any]) -> None:
        """Write to a temporary file in the same folder, then replace: a crash never leaves half a file."""
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        payload = json.dumps({"version": VERSION, **data}, ensure_ascii=False, indent=2)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)
