"""Filesystem layout under the Monika home directory (~/.config/monika)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from monika.config import Settings


@dataclass(frozen=True)
class Paths:
    """Every directory where Monika stores data."""

    home: Path
    memory: Path
    voice: Path
    rag: Path
    character: Path
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
            character=home / "character",
            logs=home / "logs",
            tool_results=home / "tool_results",
            sessions=home / "sessions",
            mcp_config=settings.mcp_servers_file or home / "mcp_servers.json",
        )

    @property
    def settings_file(self) -> Path:
        """Written by the settings page; the .env keeps priority over it."""
        return self.home / "settings.json"

    @property
    def words_file(self) -> Path:
        """The user's own sensitive-word bank (empty at first; edited from the settings page)."""
        return self.home / "words.json"

    @property
    def character_state(self) -> Path:
        return self.character / "state.json"

    @property
    def character_journal(self) -> Path:
        return self.character / "journal.json"

    @property
    def rag_qdrant(self) -> Path:
        return self.rag / "qdrant"

    @property
    def rag_manifest(self) -> Path:
        return self.rag / "manifest.db"

    @property
    def audit_log(self) -> Path:
        return self.logs / "audit.jsonl"

    @property
    def sessions_db(self) -> Path:
        """The conversation log (SQLite + FTS5), in 0600."""
        return self.sessions / "sessions.db"

    def ensure(self) -> None:
        """Create all directories; the home itself is private to the user."""
        self.home.mkdir(parents=True, exist_ok=True)
        self.home.chmod(0o700)
        for path in (self.memory, self.voice, self.rag, self.character, self.logs,
                     self.tool_results, self.sessions):
            path.mkdir(parents=True, exist_ok=True)
