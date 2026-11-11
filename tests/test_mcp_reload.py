"""An edited mcp_servers.json is reloaded, but a broken one keeps the running tools."""

from __future__ import annotations

import asyncio
import json

import pytest

from tools.mcp_client import McpConfigError, McpManager, McpServerConfig
from tools.registry import Tool, ToolRegistry


async def echo(arguments: dict) -> str:
    return "ok"


def registry_with_old_tool() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(Tool("echo", "", {}, echo, source="MCP:old"))
    return registry


async def reload_and_close(manager: McpManager) -> list:
    try:
        return await manager.reload()
    finally:
        await manager.__aexit__(None, None, None)


def test_invalid_config_keeps_the_running_tools(tmp_path) -> None:
    registry = registry_with_old_tool()
    config = tmp_path / "mcp_servers.json"
    config.write_text("{ ce n'est pas du JSON", encoding="utf-8")
    manager = McpManager({}, registry, tmp_path, config_path=config)

    with pytest.raises(McpConfigError):
        asyncio.run(reload_and_close(manager))

    assert [tool.name for tool in registry.tools()] == ["echo"]


def test_valid_config_swaps_the_tools(tmp_path) -> None:
    registry = registry_with_old_tool()
    config = tmp_path / "mcp_servers.json"
    config.write_text(
        json.dumps({"mcpServers": {"demo": {"command": "true", "enabled": False}}}),
        encoding="utf-8",
    )
    manager = McpManager(
        {"old": McpServerConfig(command="true")}, registry, tmp_path, config_path=config
    )

    statuses = asyncio.run(reload_and_close(manager))

    assert registry.tools() == []
    assert [status.name for status in statuses] == ["demo"]
