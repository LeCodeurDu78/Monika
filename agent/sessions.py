"""Conversation sessions on disk: SQLite + FTS5, searchable, resumable, purged.

One row per session (dates, mode, title, summary of the older turns, and a JSON snapshot of the
conversation as the model saw it), one row per turn, one per tool call. The snapshot is what makes
`/reprendre` exact: tool calls and their results are restored as they were, without rebuilding them
from the turn log.

Everything is masked **before** it is written (masking the serialized JSON would miss the escaped
copies of a secret). Every method here swallows its own failures and logs them: a broken database
must never break a turn.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from agent.context import message_tokens
from security.masking import Masker

log = logging.getLogger(__name__)

TITLE_CHARS = 70
HISTORY_PREVIEW_CHARS = (
    4000  # what a tool result keeps in the turn log (the snapshot keeps it all)
)
STATUSES = ("open", "complete", "interrupted")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    mode        TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    summary     TEXT NOT NULL DEFAULT '',
    history     TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,
    at          TEXT NOT NULL,
    user_text   TEXT NOT NULL,
    reply       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'open'
);
CREATE INDEX IF NOT EXISTS turns_session ON turns(session_id, idx);
CREATE TABLE IF NOT EXISTS tool_calls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id     INTEGER NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    at          TEXT NOT NULL,
    name        TEXT NOT NULL,
    arguments   TEXT NOT NULL DEFAULT '',
    result      TEXT NOT NULL DEFAULT '',
    ok          INTEGER NOT NULL DEFAULT 1,
    duration_ms REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS tool_calls_turn ON tool_calls(turn_id);
CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
    user_text, reply, content='turns', content_rowid='id',
    tokenize="unicode61 remove_diacritics 2"
);
CREATE TRIGGER IF NOT EXISTS turns_ai AFTER INSERT ON turns BEGIN
    INSERT INTO turns_fts(rowid, user_text, reply) VALUES (new.id, new.user_text, new.reply);
END;
CREATE TRIGGER IF NOT EXISTS turns_ad AFTER DELETE ON turns BEGIN
    INSERT INTO turns_fts(turns_fts, rowid, user_text, reply)
    VALUES ('delete', old.id, old.user_text, old.reply);
END;
CREATE TRIGGER IF NOT EXISTS turns_au AFTER UPDATE ON turns BEGIN
    INSERT INTO turns_fts(turns_fts, rowid, user_text, reply)
    VALUES ('delete', old.id, old.user_text, old.reply);
    INSERT INTO turns_fts(rowid, user_text, reply) VALUES (new.id, new.user_text, new.reply);
END;
"""


class SessionsError(Exception):
    """Sessions are unusable; the message is in French and ready to display."""


@dataclass(frozen=True)
class SessionInfo:
    """One line of `/sessions`."""

    id: str
    started_at: str
    updated_at: str
    mode: str
    title: str
    turns: int
    has_summary: bool


@dataclass(frozen=True)
class Hit:
    """One line of `/chercher`."""

    session_id: str
    at: str
    excerpt: str


@dataclass
class Loaded:
    """What `/reprendre` needs."""

    id: str
    started_at: str
    mode: str
    title: str
    summary: str
    history: list[dict[str, Any]] = field(default_factory=list)
    turns: int = 0


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _one_line(text: str, limit: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def trim_history(history: list[dict[str, Any]], max_tokens: int) -> int:
    """Drop the oldest whole turns until the history fits; returns how many messages went away.

    A cut always happens on a user message, so a tool call never loses its result: the history keeps
    starting on a turn boundary.
    """
    total = sum(message_tokens(m) for m in history)
    if total <= max_tokens:
        return 0
    cut = 0
    while total > max_tokens:
        following = next(
            (
                i
                for i in range(cut + 1, len(history))
                if history[i].get("role") == "user"
            ),
            None,
        )
        if following is None:
            break
        total -= sum(message_tokens(m) for m in history[cut:following])
        cut = following
    if cut:
        del history[:cut]
    return cut


class SessionStore:
    """The SQLite side: one file, private, with a full-text index over the turns."""

    def __init__(self, path: Path, masker: Masker, keep_days: int = 30) -> None:
        self.path = path
        self.keep_days = keep_days
        self._masker = masker
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.parent.chmod(
                0o700
            )  # A data folder is private, like the home it lives in
            if not path.exists():  # Created here so it is private from its first byte
                os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
            self._conn = sqlite3.connect(str(path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            self._private()
            # A turn still open was interrupted by a crash or a `kill`: say so.
            self._conn.execute(
                "UPDATE turns SET status = 'interrupted' WHERE status = 'open'"
            )
            self._conn.commit()
        except (OSError, sqlite3.Error) as exc:
            raise SessionsError(
                f"Sessions indisponibles ({type(exc).__name__}: {exc})."
            ) from exc

    def _private(self) -> None:
        """0600 for the database and its WAL companions."""
        for suffix in ("", "-wal", "-shm"):
            candidate = self.path.with_name(self.path.name + suffix)
            try:
                if candidate.exists():
                    candidate.chmod(0o600)
            except OSError:
                continue

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------------ writing

    def start(self, mode: str = "") -> str:
        """Open a session and return its short id (random, not guessable)."""
        session_id = secrets.token_hex(4)
        stamp = _now()
        with self._conn:
            self._conn.execute(
                "INSERT INTO sessions (id, started_at, updated_at, mode, title, summary, history) "
                "VALUES (?, ?, ?, ?, '', '', '[]')",
                (session_id, stamp, stamp, mode),
            )
        return session_id

    def begin_turn(self, session_id: str, user_text: str) -> int:
        """Write the user message of a turn; returns its row id."""
        clean = self._masker.mask_text(str(user_text))
        with self._conn:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(idx), 0) + 1 FROM turns WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            index = int(row[0])
            cursor = self._conn.execute(
                "INSERT INTO turns (session_id, idx, at, user_text, reply, status) "
                "VALUES (?, ?, ?, ?, '', 'open')",
                (session_id, index, _now(), clean),
            )
            if index == 1:  # the first message names the session
                self._conn.execute(
                    "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                    (_one_line(clean, TITLE_CHARS), _now(), session_id),
                )
            else:
                self._conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?",
                    (_now(), session_id),
                )
        return int(cursor.lastrowid)

    def tool_call(
        self,
        turn_id: int,
        name: str,
        arguments: str,
        ok: bool,
        duration_ms: float,
        result: str,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO tool_calls (turn_id, at, name, arguments, result, ok, duration_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    turn_id,
                    _now(),
                    self._masker.mask_text(str(name)),
                    self._masker.mask_text(_one_line(arguments, 2000)),
                    self._masker.mask_text(str(result)[:HISTORY_PREVIEW_CHARS]),
                    1 if ok else 0,
                    float(duration_ms or 0),
                ),
            )

    def end_turn(self, turn_id: int, reply: str, status: str = "complete") -> None:
        """Close a turn: `complete`, or `interrupted` when it never produced its answer."""
        if status not in STATUSES or status == "open":
            status = "complete"
        with self._conn:
            self._conn.execute(
                "UPDATE turns SET reply = ?, status = ? WHERE id = ?",
                (self._masker.mask_text(str(reply)), status, turn_id),
            )

    def save(
        self, session_id: str, history: list[dict[str, Any]], summary: str = ""
    ) -> None:
        """Store the conversation as the model sees it, and the summary of the older turns."""
        with self._conn:
            self._conn.execute(
                "UPDATE sessions SET history = ?, summary = ?, updated_at = ? WHERE id = ?",
                (
                    json.dumps(self._mask_history(history), ensure_ascii=False),
                    self._masker.mask_text(str(summary or "")),
                    _now(),
                    session_id,
                ),
            )

    def _mask_history(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Mask every string inside the messages, keeping the structure intact."""
        out: list[dict[str, Any]] = []
        for message in history:
            copy = dict(message)
            if isinstance(copy.get("content"), str):
                copy["content"] = self._masker.mask_text(copy["content"])
            if isinstance(copy.get("tool_calls"), list):
                copy["tool_calls"] = [
                    self._mask_call(call) for call in copy["tool_calls"]
                ]
            out.append(copy)
        return out

    def _mask_call(self, call: Any) -> Any:
        """A tool call as the model wrote it, with its name and arguments masked."""
        if not isinstance(call, dict):
            return call
        function = dict(call.get("function") or {})
        function["name"] = self._masker.mask_text(str(function.get("name", "")))
        function["arguments"] = self._masker.mask_text(
            str(function.get("arguments", ""))
        )
        return {**call, "function": function}

    # ------------------------------------------------------------------ reading

    def list(self, limit: int = 15) -> list[SessionInfo]:
        rows = self._conn.execute(
            "SELECT s.id, s.started_at, s.updated_at, s.mode, s.title, "
            "       (s.summary != '') AS has_summary, "
            "       (SELECT COUNT(*) FROM turns t WHERE t.session_id = s.id) AS turns "
            "FROM sessions s ORDER BY s.updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            SessionInfo(
                r["id"],
                r["started_at"],
                r["updated_at"],
                r["mode"],
                r["title"],
                int(r["turns"]),
                bool(r["has_summary"]),
            )
            for r in rows
        ]

    def search(self, query: str, limit: int = 15) -> list[Hit]:
        """Full-text search over what was said (never over tool results, which stay out of the index)."""
        words = re.findall(r"\w+", query or "", re.UNICODE)
        if not words:
            return []
        expression = " ".join(
            f'"{w}"' for w in words[:12]
        )  # quoted: user text cannot inject operators
        rows = self._conn.execute(
            "SELECT t.session_id, t.at, snippet(turns_fts, -1, '«', '»', '…', 14) AS excerpt "
            "FROM turns_fts JOIN turns t ON t.id = turns_fts.rowid "
            "WHERE turns_fts MATCH ? ORDER BY rank LIMIT ?",
            (expression, limit),
        ).fetchall()
        return [Hit(r["session_id"], r["at"], r["excerpt"]) for r in rows]

    def load(self, session_id: str) -> Loaded | None:
        row = self._conn.execute(
            "SELECT id, started_at, mode, title, summary, history FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            history = json.loads(row["history"] or "[]")
        except ValueError as exc:
            raise SessionsError(
                f"L'historique de la session {session_id} est illisible "
                f"({exc.msg if hasattr(exc, 'msg') else exc})."
            ) from exc
        if not isinstance(history, list):
            raise SessionsError(
                f"L'historique de la session {session_id} n'est pas une conversation."
            )
        turns = self._conn.execute(
            "SELECT COUNT(*) FROM turns WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
        return Loaded(
            row["id"],
            row["started_at"],
            row["mode"],
            row["title"],
            row["summary"],
            history,
            int(turns),
        )

    def turn_statuses(self, session_id: str) -> list[str]:
        """Statuses in order: used by `/sessions` and the tests to show an interrupted turn."""
        return [
            r[0]
            for r in self._conn.execute(
                "SELECT status FROM turns WHERE session_id = ? ORDER BY idx",
                (session_id,),
            )
        ]

    # ------------------------------------------------------------------ housekeeping

    def forget(self, session_id: str) -> bool:
        """Delete a session with its turns and tool calls (ON DELETE CASCADE)."""
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM sessions WHERE id = ?", (session_id,)
            )
        return cursor.rowcount > 0

    def purge(self, keep_days: int | None = None) -> int:
        """Delete the sessions at least keep_days old; returns how many went away."""
        days = self.keep_days if keep_days is None else keep_days
        cutoff = (datetime.now().astimezone() - timedelta(days=days)).isoformat(
            timespec="seconds"
        )
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM sessions WHERE updated_at <= ?", (cutoff,)
            )
        return max(0, cursor.rowcount)

    def counts(self) -> dict[str, int]:
        sessions = self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        turns = self._conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        return {"sessions": int(sessions), "turns": int(turns)}


class SessionRecorder:
    """The agent's hooks: one session at a time, one turn at a time. Never raises."""

    def __init__(self, store: SessionStore, mode: str = "texte") -> None:
        self.store = store
        self.mode = mode
        self.session_id: str | None = None
        self._turn_id: int | None = None
        self._warned = False

    # -- what the loop calls -------------------------------------------------
    def begin(self, user_text: str) -> None:
        def action() -> None:
            if self.session_id is None:
                self.session_id = self.store.start(self.mode)
            self._turn_id = self.store.begin_turn(self.session_id, user_text)

        self._guard(action)

    def tool(
        self, name: str, arguments: str, ok: bool, duration_ms: float, result: str
    ) -> None:
        if self._turn_id is None:
            return
        self._guard(
            lambda: self.store.tool_call(
                self._turn_id, name, arguments, ok, duration_ms, result
            )
        )

    def finish(
        self, history: list[dict[str, Any]], summary: str, start: int, complete: bool
    ) -> None:
        """Close the turn, then store the conversation and its summary."""

        def action() -> None:
            reply = ""
            for message in history[start:]:
                if message.get("role") == "assistant" and isinstance(
                    message.get("content"), str
                ):
                    reply = message["content"]
            if self._turn_id is not None:
                self.store.end_turn(
                    self._turn_id, reply, "complete" if complete else "interrupted"
                )
            if self.session_id is not None:
                self.store.save(self.session_id, history, summary)
            self._turn_id = None

        self._guard(action)

    def close_session(self) -> None:
        """The in-memory conversation was cleared: the next turn opens a new session."""
        self.session_id = None
        self._turn_id = None

    def adopt(self, session_id: str) -> None:
        """Continue an existing session (`/reprendre`)."""
        self.session_id = session_id
        self._turn_id = None

    def is_current(self, session_id: str) -> bool:
        return self.session_id == session_id

    def close(self) -> None:
        self.store.close()

    def _guard(self, action) -> None:
        """Any storage failure is logged once and forgotten: the conversation goes on."""
        try:
            action()
        except Exception as exc:  # noqa: BLE001 - sessions must never break a turn
            if not self._warned:
                self._warned = True
                log.warning(
                    "Sessions : écriture impossible (%s: %s)", type(exc).__name__, exc
                )
