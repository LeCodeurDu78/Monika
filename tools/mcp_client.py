"""MCP client: connects to the configured servers and exposes their tools in the registry."""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from tools.registry import Tool, ToolFailure, ToolRegistry, safe_name

_UNRESOLVED_VAR = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")


class McpConfigError(Exception):
    """Invalid mcp_servers.json; the message is in French."""


class McpServerConfig(BaseModel):
    """One entry of mcp_servers.json (stdio command or streamable HTTP url)."""

    model_config = ConfigDict(extra="forbid")

    command: str | None = None
    args: list[str] = []
    env: dict[str, str] = {}
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = {}
    enabled: bool = True
    start_timeout_s: float | None = None  # Overrides MCP_START_TIMEOUT_S for this server
    slow: bool = False
    slow_tools: list[str] | dict[str, list[str]] = []
    # Every tool schema is sent to the model with each request: keep only what is useful.
    enabled_tools: list[str] = []   # Non-empty: only these tools (names as the server gives them)
    disabled_tools: list[str] = []  # These tools are never exposed (wins over enabled_tools)

    @model_validator(mode="after")
    def _one_transport(self) -> McpServerConfig:
        if bool(self.command) == bool(self.url):
            raise ValueError("il faut exactement un des champs « command » ou « url »")
        return self

    def is_slow(self, tool: str) -> bool:
        return self.slow or tool in self.slow_tools

    def keeps(self, tool: str) -> bool:
        """Whether the tool is exposed to the model."""
        if tool in self.disabled_tools:
            return False
        return not self.enabled_tools or tool in self.enabled_tools

    def fillers(self, tool: str) -> list[str]:
        if isinstance(self.slow_tools, dict):
            return list(self.slow_tools.get(tool, []))
        return []


def load_mcp_config(path: Path) -> dict[str, McpServerConfig]:
    """Read the servers file; a missing file simply means no MCP servers."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise McpConfigError(f"Impossible de lire {path} : {exc}") from exc
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        raise McpConfigError(f"{path} doit contenir un objet « mcpServers ».")
    out: dict[str, McpServerConfig] = {}
    for name, raw in servers.items():
        try:
            out[name] = McpServerConfig.model_validate(raw)
        except ValidationError as exc:
            reason = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'serveur'} : {e['msg']}"
                               for e in exc.errors())
            raise McpConfigError(f"Serveur MCP « {name} » invalide dans {path} — {reason}") from exc
    return out


def expand(value: str, env: Mapping[str, str] | None = None) -> str:
    """Expand $VAR / ${VAR} from env (default: the process environment); raise if one is undefined."""
    source = os.environ if env is None else env

    def lookup(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in source:
            raise McpConfigError(f"variable d'environnement non définie : {name}")
        return source[name]

    return _UNRESOLVED_VAR.sub(lookup, value)


def describe(exc: BaseException) -> str:
    """Return a short readable reason, unwrapping exception groups."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    if isinstance(exc, FileNotFoundError):
        return f"commande introuvable ({exc.filename or exc})"
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


def render_result(result: Any) -> tuple[str, bool]:
    """Turn an MCP call result into (text, is_error); binary content is never faked."""
    if not isinstance(result, types.CallToolResult):
        return ("L'outil demande une saisie interactive, ce qui n'est pas pris en charge.", True)
    parts: list[str] = []
    for block in result.content:
        kind = getattr(block, "type", "")
        if kind == "text":
            parts.append(block.text)
        elif kind in ("image", "audio"):
            parts.append(f"[{kind} {block.mime_type} reçu mais non transmis au modèle]")
        elif kind == "resource":
            res = block.resource
            parts.append(res.text if hasattr(res, "text") else f"[ressource binaire {res.uri}]")
        elif kind == "resource_link":
            parts.append(f"[lien de ressource : {block.uri}]")
    text = "\n".join(parts).strip()
    if not text and result.structured_content:
        text = json.dumps(result.structured_content, ensure_ascii=False)
    return text, bool(result.is_error)


@dataclass
class ServerStatus:
    """Connection outcome for one server, shown to the user."""

    name: str
    ok: bool
    detail: str
    tools: list[str] = field(default_factory=list)


class McpManager:
    """Owns the MCP sessions; use as `async with` in the task that runs the agent."""

    def __init__(self, servers: dict[str, McpServerConfig], registry: ToolRegistry,
                 log_dir: Path, start_timeout: float = 30.0, call_timeout: float = 120.0,
                 extra_env: Mapping[str, str] | None = None) -> None:
        self._env = {**(extra_env or {}), **os.environ}  # The real environment wins over the extra values
        self._servers = servers
        self._registry = registry
        self._log_dir = log_dir
        self._start_timeout = start_timeout
        self._call_timeout = call_timeout
        self._stack = AsyncExitStack()
        self.statuses: list[ServerStatus] = []

    async def __aenter__(self) -> McpManager:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self._stack.aclose()

    async def start(self) -> None:
        """Connect to every enabled server; one failing server never blocks the others."""
        for name, cfg in self._servers.items():
            if not cfg.enabled:
                self.statuses.append(ServerStatus(name, False, "désactivé dans la configuration"))
                continue
            try:
                tool_names, skipped, unknown = await self._connect(name, cfg)
            except TimeoutError:
                limit = cfg.start_timeout_s or self._start_timeout
                self.statuses.append(ServerStatus(name, False, f"pas de réponse après {limit:g} s"))
            except Exception as exc:  # Report in French and continue with the next server
                self.statuses.append(ServerStatus(name, False, describe(exc)))
            else:
                detail = f"{len(tool_names)} outil(s)"
                if skipped:
                    detail += f", {skipped} écarté(s) par la configuration"
                if unknown:
                    detail += f" (noms inconnus dans la configuration : {', '.join(unknown)})"
                self.statuses.append(ServerStatus(name, True, detail, tool_names))

    async def _connect(self, name: str, cfg: McpServerConfig) -> tuple[list[str], int, list[str]]:
        local = AsyncExitStack()
        try:
            async with asyncio.timeout(cfg.start_timeout_s or self._start_timeout):
                streams = await self._open_transport(name, cfg, local)
                session = await local.enter_async_context(ClientSession(streams[0], streams[1]))
                await session.initialize()
                tools = await self._list_tools(session)
        except BaseException:
            await local.aclose()
            raise
        self._stack.push_async_exit(local)
        names = []
        skipped = 0
        for t in tools:
            if not cfg.keeps(t.name):
                skipped += 1
                continue
            registered = self._registry.unique_name(f"{safe_name(name)}__{t.name}")
            self._registry.register(Tool(
                name=registered,
                description=(t.description or t.title or t.name).strip(),
                parameters=self._schema(t.input_schema),
                handler=self._make_handler(session, name, t.name),
                slow=cfg.is_slow(t.name),
                filler_variants=cfg.fillers(t.name),
                source=f"MCP:{name}",
            ))
            names.append(registered)
        available = {t.name for t in tools}
        unknown = [n for n in [*cfg.enabled_tools, *cfg.disabled_tools] if n not in available]
        return names, skipped, unknown

    async def _open_transport(self, name: str, cfg: McpServerConfig, stack: AsyncExitStack) -> Any:
        if cfg.command:
            params = StdioServerParameters(
                command=expand(cfg.command, self._env),
                args=[expand(a, self._env) for a in cfg.args],
                env={k: expand(v, self._env) for k, v in cfg.env.items()} or None,
                cwd=expand(cfg.cwd, self._env) if cfg.cwd else None,
            )
            self._log_dir.mkdir(parents=True, exist_ok=True)
            # Server stderr goes to a log file so it never garbles the chat
            errlog = stack.enter_context(
                open(self._log_dir / f"mcp-{safe_name(name)}.log", "a", encoding="utf-8"))
            return await stack.enter_async_context(stdio_client(params, errlog=errlog))
        client = create_mcp_http_client(headers={k: expand(v, self._env) for k, v in cfg.headers.items()} or None)
        await stack.enter_async_context(client)
        return await stack.enter_async_context(
            streamable_http_client(expand(cfg.url or "", self._env), http_client=client))

    @staticmethod
    async def _list_tools(session: ClientSession) -> list[types.Tool]:
        tools: list[types.Tool] = []
        result = await session.list_tools()
        tools.extend(result.tools)
        while result.next_cursor:
            result = await session.list_tools(params=types.PaginatedRequestParams(cursor=result.next_cursor))
            tools.extend(result.tools)
        return tools

    @staticmethod
    def _schema(schema: dict[str, Any] | None) -> dict[str, Any]:
        # OpenAI function calling needs an object schema
        schema = dict(schema or {})
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        return schema

    def _make_handler(self, session: ClientSession, server: str, tool: str):
        async def handler(args: dict[str, Any]) -> str:
            try:
                async with asyncio.timeout(self._call_timeout):
                    result = await session.call_tool(tool, args)
            except TimeoutError:
                raise ToolFailure(
                    f"L'outil {tool} (serveur {server}) n'a pas répondu en {self._call_timeout:g} s."
                ) from None
            except Exception as exc:
                raise ToolFailure(f"Le serveur MCP {server} a échoué : {describe(exc)}") from exc
            text, is_error = render_result(result)
            if is_error:
                raise ToolFailure(text or f"L'outil {tool} a renvoyé une erreur sans message.")
            return text or "(l'outil n'a rien renvoyé)"
        return handler
