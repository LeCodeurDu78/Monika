"""Smoke tests: everything imports, a tool runs, secrets are masked, the data folders exist.

No network, no model, no microphone: these catch a broken import or a broken registry in
under a second, which is what the test suite had none of.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest

from monika.config import Settings
from monika.paths import Paths
from security.masking import Masker
from tools.registry import Tool, ToolRegistry

PACKAGES = (
    "monika",
    "agent",
    "tools",
    "security",
    "memory",
    "rag",
    "character",
    "voice",
    "modes",
    "web",
)


@pytest.mark.parametrize("package", PACKAGES)
def test_package_imports(package: str) -> None:
    importlib.import_module(package)


def test_tool_registry_runs_a_tool() -> None:
    async def echo(arguments: dict) -> str:
        return f"echo {arguments['text']}"

    registry = ToolRegistry()
    registry.register(
        Tool(
            name="echo",
            description="Renvoie le texte reçu",
            parameters={"type": "object", "properties": {"text": {"type": "string"}}},
            handler=echo,
        )
    )

    assert registry.schemas()[0]["function"]["name"] == "echo"

    result = asyncio.run(registry.call("echo", '{"text": "bonjour"}'))
    assert (result.text, result.is_error) == ("echo bonjour", False)

    missing = asyncio.run(registry.call("inconnu", "{}"))
    assert missing.is_error and "Outil inconnu" in missing.text

    with pytest.raises(ValueError):
        registry.register(
            Tool(name="echo", description="doublon", parameters={}, handler=echo)
        )


def test_tool_registry_refuses_invalid_json() -> None:
    async def echo(arguments: dict) -> str:
        return "jamais appelé"

    registry = ToolRegistry()
    registry.register(Tool(name="echo", description="", parameters={}, handler=echo))

    result = asyncio.run(registry.call("echo", "{pas du json"))

    assert result.is_error and "Arguments invalides" in result.text


def test_masker_hides_secrets() -> None:
    masker = Masker(["une-cle-tres-secrete"])

    assert masker.mask_text("clé : une-cle-tres-secrete") == "clé : ***"
    assert masker.mask({"api_key": "abc123", "texte": "Bearer token-abcdefgh"}) == {
        "api_key": "***",
        "texte": "***",
    }


def test_paths_are_created(tmp_path) -> None:
    paths = Paths.from_settings(Settings(_env_file=None, monika_home=tmp_path))

    paths.ensure()

    assert paths.home == tmp_path
    for folder in (paths.memory, paths.sessions, paths.logs, paths.voice, paths.rag):
        assert folder.is_dir()
    assert paths.memory_notes.parent == paths.memory
