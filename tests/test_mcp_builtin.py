"""Hermes and nanoMuse are built-in MCP servers: an address in the environment is enough."""

from __future__ import annotations

import json

from tools.mcp_client import builtin_servers, load_mcp_config


def test_builtin_servers_follow_the_environment() -> None:
    servers = builtin_servers(
        {"HERMES_URL": "http://127.0.0.1:8642", "HERMES_TOKEN": "secret"}
    )

    assert list(servers) == ["hermes"]
    config = servers["hermes"]
    assert config.env == {
        "HELPER_KIND": "hermes",
        "HELPER_NAME": "Hermes",
        "HELPER_URL": "http://127.0.0.1:8642",
        "HELPER_TOKEN": "secret",
    }
    assert config.args == ["-m", "bridges.helper_bridge"]
    assert config.is_slow("ask")


def test_missing_file_still_has_the_builtins(tmp_path) -> None:
    servers = load_mcp_config(
        tmp_path / "mcp_servers.json", {"NANOMUSE_URL": "http://127.0.0.1:8765"}
    )

    assert list(servers) == ["nanomuse"]
    assert servers["nanomuse"].env["HELPER_NAME"] == "nanoMuse"


def test_a_file_entry_cannot_shadow_the_builtin(tmp_path) -> None:
    config = tmp_path / "mcp_servers.json"
    config.write_text(
        json.dumps({"mcpServers": {"hermes": {"command": "true"}}}), encoding="utf-8"
    )

    servers = load_mcp_config(config, {"HERMES_URL": "http://127.0.0.1:8642"})

    assert servers["hermes"].env["HELPER_KIND"] == "hermes"
