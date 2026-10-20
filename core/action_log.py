"""Journal of what Monika does: every tool call, its outcome, and what is running right now.

It feeds the dashboard (dashboard/) and doubles as the failure log: a failed call is stored like any other,
with status "error", so recurring failures can be counted per tool.

Privacy: this is a local file (`APP_DIR/databases/actions.db`) that contains what the tools were asked to do.
Arguments and results are shortened, values that look like secrets are masked, and rows older than
ACTION_LOG_RETENTION_DAYS are deleted at startup. Nothing here ever raises into the agent: logging must not
be able to break a tool call.
"""

import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from core.db import db_path, get_connection
from core.settings import settings

DB_PATH = db_path("actions.db")

OK, ERROR, BLOCKED, CANCELLED = "ok", "error", "blocked", "cancelled"

_ARGS_MAX_CHARS = 300
_PREVIEW_MAX_CHARS = 300
_SECRET_KEY = re.compile(r"pass(word|wd)?|pwd|token|secret|api_?key|authorization|credential", re.IGNORECASE)
# Free-text fields of these tools can hold anything, including a password typed into a web form.
_MASKED_FIELDS = {"browser_control": ("text", "value")}

_CREATE_SQL = """
    CREATE TABLE IF NOT EXISTS actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        source TEXT NOT NULL,
        tool TEXT NOT NULL,
        args TEXT NOT NULL,
        status TEXT NOT NULL,
        duration_ms INTEGER NOT NULL,
        preview TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_actions_ts ON actions (ts);
    CREATE INDEX IF NOT EXISTS idx_actions_status ON actions (status, ts);
"""

_init_lock = threading.Lock()
_initialised = False
_write_lock = threading.Lock()

_in_flight: dict[int, dict] = {}
_in_flight_lock = threading.Lock()
_next_token = 1
_main_task: Optional[dict] = None


# --- helpers ----------------------------------------------------------------------------
def _short(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def sanitize_args(tool: str, args: Any) -> str:
    """Arguments as short JSON text, secrets masked."""
    if not isinstance(args, dict):
        return _short(args, _ARGS_MAX_CHARS)
    masked = _MASKED_FIELDS.get(tool, ())
    safe = {key: ("[masqué]" if (_SECRET_KEY.search(str(key)) or key in masked) else value) for key, value in args.items()}
    return _short(json.dumps(safe, ensure_ascii=False, default=str), _ARGS_MAX_CHARS)


def classify(result: str) -> str:
    """Outcome of a tool call, from its result text (tools report failures as text, not exceptions)."""
    text = (result or "").lstrip()
    if text.startswith("⚠️ Boucle détectée"):
        return BLOCKED
    if text.startswith(("❌", "⚠️", "Erreur")):
        return ERROR
    return OK


def current_source() -> str:
    """Who is acting: the main agent thread or a background sub-agent."""
    return "fond" if threading.current_thread().name.startswith("monika-bg") else "agent"


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """A connection that is committed and CLOSED on exit (sqlite3's own `with` only commits)."""
    conn = get_connection(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_db() -> None:
    global _initialised
    if _initialised:
        return
    with _init_lock:
        if _initialised:
            return
        with _connect() as conn:
            conn.executescript(_CREATE_SQL)
            cutoff = time.time() - max(1, settings.ACTION_LOG_RETENTION_DAYS) * 86400
            conn.execute("DELETE FROM actions WHERE ts < ?", (cutoff,))
        _initialised = True


# --- writing -----------------------------------------------------------------------------
def record(
    source: str, tool: str, args: Any, status: str, duration_ms: int = 0, preview: str = "", *, ts: Optional[float] = None
) -> None:
    """Store one finished action. Never raises."""
    if not settings.ACTION_LOG_ENABLED:
        return
    try:
        _ensure_db()
        with _write_lock, _connect() as conn:
            conn.execute(
                "INSERT INTO actions (ts, source, tool, args, status, duration_ms, preview) VALUES (?,?,?,?,?,?,?)",
                (
                    ts or time.time(),
                    source,
                    tool,
                    sanitize_args(tool, args),
                    status,
                    int(duration_ms),
                    _short(preview, _PREVIEW_MAX_CHARS),
                ),
            )
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ [Journal] Écriture impossible ({type(exc).__name__}: {exc}).")


def begin(tool: str, args: Any) -> int:
    """A tool call starts: it shows up as "running" until `finish` or `abort`. Returns a token."""
    global _next_token
    with _in_flight_lock:
        token = _next_token
        _next_token += 1
        _in_flight[token] = {
            "tool": tool,
            "args": sanitize_args(tool, args),
            "args_raw": args,
            "started": time.time(),
            "source": current_source(),
        }
    return token


def _pop(token: int) -> Optional[dict]:
    with _in_flight_lock:
        return _in_flight.pop(token, None)


def finish(token: int, result: str) -> None:
    """The tool call returned `result` (an error is a result too)."""
    entry = _pop(token)
    if entry is None:
        return
    record(
        entry["source"],
        entry["tool"],
        entry["args_raw"],
        classify(result),
        int((time.time() - entry["started"]) * 1000),
        result,
        ts=entry["started"],
    )


def abort(token: int) -> None:
    """The tool call was interrupted (a cancelled background job)."""
    entry = _pop(token)
    if entry is not None:
        record(
            entry["source"], entry["tool"], entry["args_raw"], CANCELLED,
            int((time.time() - entry["started"]) * 1000), "Interrompu.", ts=entry["started"],
        )


def set_main_task(label: Optional[str]) -> None:
    """What the main agent is working on (None when idle)."""
    global _main_task
    _main_task = {"label": _short(label, 200), "started": time.time()} if label else None


# --- reading -----------------------------------------------------------------------------
def main_task() -> Optional[dict]:
    return dict(_main_task) if _main_task else None


def in_flight() -> list[dict]:
    with _in_flight_lock:
        items = [
            {"tool": e["tool"], "args": e["args"], "source": e["source"], "started": e["started"]}
            for e in _in_flight.values()
        ]
    return sorted(items, key=lambda e: e["started"])


def _row(row: tuple) -> dict:
    keys = ("id", "ts", "source", "tool", "args", "status", "duration_ms", "preview")
    return dict(zip(keys, row))


def recent(
    limit: int = 50,
    before_id: Optional[int] = None,
    status: Optional[str] = None,
    tool: Optional[str] = None,
    source: Optional[str] = None,
) -> list[dict]:
    """Newest first. `status="failed"` means error or blocked. `before_id` pages backwards."""
    _ensure_db()
    where, params = [], []
    if before_id:
        where.append("id < ?")
        params.append(before_id)
    if status == "failed":
        where.append("status IN ('error', 'blocked')")
    elif status:
        where.append("status = ?")
        params.append(status)
    if tool:
        where.append("tool = ?")
        params.append(tool)
    if source:
        where.append("source = ?")
        params.append(source)
    sql = "SELECT id, ts, source, tool, args, status, duration_ms, preview FROM actions"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    with _connect() as conn:
        return [_row(r) for r in conn.execute(sql, params).fetchall()]


def failure_summary(days: int = 7) -> list[dict]:
    """Per tool over the last `days`: calls, failures and the latest failure. Only tools that failed."""
    _ensure_db()
    since = time.time() - max(1, days) * 86400
    with _connect() as conn:
        rows = conn.execute(
            """SELECT tool, COUNT(*) AS total,
                      SUM(CASE WHEN status IN ('error','blocked') THEN 1 ELSE 0 END) AS failures
               FROM actions WHERE ts >= ? GROUP BY tool HAVING failures > 0
               ORDER BY failures DESC, tool""",
            (since,),
        ).fetchall()
        summary = []
        for tool, total, failures in rows:
            last = conn.execute(
                "SELECT ts, preview FROM actions WHERE tool = ? AND status IN ('error','blocked') AND ts >= ? "
                "ORDER BY id DESC LIMIT 1",
                (tool, since),
            ).fetchone()
            summary.append(
                {"tool": tool, "total": total, "failures": failures, "last_ts": last[0], "last_error": last[1]}
            )
    return summary
