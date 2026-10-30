"""Tool registry: one place to declare, list and call every tool."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from security.audit import AuditLog

Handler = Callable[[dict[str, Any]], Awaitable[str]]


class ToolFailure(Exception):
    """Expected tool failure; its message is sent back to the model as text."""


@dataclass
class Tool:
    """A callable tool exposed to the model through function calling."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Handler
    slow: bool = False
    filler_variants: list[str] = field(default_factory=list)
    source: str = "interne"  # Origin shown in listings (internal or MCP server name)

    def schema(self) -> dict[str, Any]:
        """Return the OpenAI function-calling definition."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class ToolResult:
    """Outcome of a call; errors are plain text so the model can fix its call."""

    name: str
    text: str
    is_error: bool = False
    duration_ms: float = 0.0


def safe_name(raw: str) -> str:
    """Make a name valid for OpenAI function calling (letters, digits, _ and -)."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", raw)


class ToolRegistry:
    """Holds tools, validates calls, never raises on tool errors, and audits each call."""

    def __init__(self, audit: AuditLog | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self._audit = audit

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"outil déjà enregistré : {tool.name}")
        self._tools[tool.name] = tool

    def unique_name(self, wanted: str) -> str:
        """Return a valid, free name (max 64 chars) derived from wanted."""
        base = safe_name(wanted)[:64]
        name, n = base, 2
        while name in self._tools:
            suffix = f"_{n}"
            name, n = base[: 64 - len(suffix)] + suffix, n + 1
        return name

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    def schemas(self, names: set[str] | None = None) -> list[dict[str, Any]]:
        """Function-calling definitions, all of them or only `names` (the director's subset for one turn)."""
        if names is None:
            return [t.schema() for t in self._tools.values()]
        return [t.schema() for name, t in self._tools.items() if name in names]

    async def call(
        self, name: str, raw_arguments: str | dict[str, Any] | None
    ) -> ToolResult:
        """Run a tool and return its text result or a text error."""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(name, self._unknown(name), True)
        try:
            args = self._parse_args(raw_arguments)
        except ValueError as exc:
            self._log(
                name, {"arguments_bruts": str(raw_arguments)}, "error", 0.0, str(exc)
            )
            return ToolResult(name, f"Arguments invalides pour {name} : {exc}", True)

        loop = asyncio.get_running_loop()
        start = loop.time()
        try:
            text, is_error = await tool.handler(args), False
        except ToolFailure as exc:
            text, is_error = str(exc), True
        except asyncio.CancelledError:
            self._log(name, args, "interrupted", (loop.time() - start) * 1000, None)
            raise
        except Exception as exc:  # Any handler bug becomes a message the model can read
            text, is_error = (
                f"Erreur interne de l'outil {name} : {type(exc).__name__}: {exc}",
                True,
            )
        elapsed = (loop.time() - start) * 1000
        self._log(
            name,
            args,
            "error" if is_error else "ok",
            elapsed,
            text if is_error else None,
        )
        return ToolResult(name, text, is_error, elapsed)

    def audit_event(self, event: str, data: dict[str, Any] | None = None) -> None:
        """Log a non-tool event (e.g. a sensitive-word level) in the same trail, secrets masked."""
        if self._audit is not None:
            self._audit.record(event, data or {}, "ok", 0.0)

    def _log(
        self,
        name: str,
        args: dict[str, Any],
        status: str,
        ms: float,
        detail: str | None,
    ) -> None:
        if self._audit:
            self._audit.record(name, args, status, ms, detail)

    def _unknown(self, name: str) -> str:
        names = ", ".join(sorted(self._tools)) or "aucun"
        return f"Outil inconnu : {name}. Outils disponibles : {names}."

    @staticmethod
    def _parse_args(raw: str | dict[str, Any] | None) -> dict[str, Any]:
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return {}
        if isinstance(raw, dict):
            return raw
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON incorrect ({exc.msg}, position {exc.pos})") from exc
        if not isinstance(value, dict):
            raise ValueError("les arguments doivent être un objet JSON")
        return value
