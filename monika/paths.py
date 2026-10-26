"""Filesystem layout under the Monika home directory (~/.config/monika)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config import Settings


@dataclass(frozen=True)
class Paths:
    """Every directory where Monika stores data."""

    home: Path
    memory: Path
    voice: Path
    rag: Path
    logs: Path
    tool_results: Path
    sessions: Path
    mcp_config: Path

    @classmethod
    def from_settings(cls, settings: Settings) -> Paths:
        home = settings.monika_home
        return cls(
            home=home,
            memory=home / "memory",
            voice=home / "voice",
            rag=home / "rag",
            logs=home / "logs",
            tool_results=home / "tool_results",
            sessions=home / "sessions",
            mcp_config=settings.mcp_servers_file or home / "mcp_servers.json",
        )

    @property
    def rag_qdrant(self) -> Path:
        return self.rag / "qdrant"

    @property
    def rag_manifest(self) -> Path:
        return self.rag / "manifest.db"

    @property
    def audit_log(self) -> Path:
        return self.logs / "audit.jsonl"

    def ensure(self) -> None:
        """Create all directories; the home itself is private to the user."""
        self.home.mkdir(parents=True, exist_ok=True)
        self.home.chmod(0o700)
        for path in (self.memory, self.voice, self.rag, self.logs,
                     self.tool_results, self.sessions):
            path.mkdir(parents=True, exist_ok=True)
