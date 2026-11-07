"""Filesystem layout under the Monika home directory (~/.config/monika, %APPDATA%\\monika)."""

from __future__ import annotations

import os
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
    nightly: Path
    briefing: Path
    mcp_config: Path
    custom_tools: Path
    custom_tools_index: Path

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
            nightly=home / "nightly",
            briefing=home / "briefing",
            mcp_config=settings.mcp_servers_file or home / "mcp_servers.json",
            custom_tools=home / "mcp_custom",
            custom_tools_index=home / "custom_tools.json",
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
    def memory_notes(self) -> Path:
        """Le carnet personnel (JSON, 0600) : mes notes, les sujets et les épisodes retenus."""
        return self.memory / "notes.json"

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

    @property
    def recipes_file(self) -> Path:
        """Recettes apprises (JSON, 0600) : brouillons, versions gardées et usages."""
        return self.home / "recipes.json"

    @property
    def tasks_file(self) -> Path:
        """Scheduled tasks (JSON, 0600): reminders and recurring jobs."""
        return self.home / "tasks.json"

    @property
    def nightly_state(self) -> Path:
        """The last night handled and its outcome (JSON, 0600)."""
        return self.nightly / "state.json"

    @property
    def nightly_propositions(self) -> Path:
        """Memory consolidation proposals waiting for a yes or no (JSON, 0600)."""
        return self.nightly / "propositions.json"

    @property
    def morning_notes(self) -> Path:
        """1-2 thoughts for tomorrow, read once at the first message of the day (JSON, 0600)."""
        return self.nightly / "matin.json"

    @property
    def nightly_phase3(self) -> Path:
        """Optional phase 3 timeline (JSON); NIGHTLY_PHASE3_FILE overrides it."""
        return self.nightly / "phase3.json"

    @property
    def briefing_state(self) -> Path:
        """Le dernier matin traité et son résultat (JSON, 0600)."""
        return self.briefing / "state.json"

    @property
    def instance_lock(self) -> Path:
        """One Monika per data folder: memory and documents cannot be opened twice."""
        return self.home / "instance.lock"

    def ensure(self) -> None:
        """Create all directories; the home itself is private to the user."""
        self.home.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":  # Windows has no Unix modes; chmod there would only toggle read-only
            self.home.chmod(0o700)
        for path in (
            self.memory,
            self.voice,
            self.rag,
            self.character,
            self.logs,
            self.tool_results,
            self.sessions,
            self.nightly,
            self.briefing,
            self.custom_tools,
        ):
            path.mkdir(parents=True, exist_ok=True)
