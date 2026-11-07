"""Language selection, catalogs and the English-first slash commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from monika.config import Settings
from monika.i18n import (
    COMMAND_ALIASES,
    LOCALES_DIR,
    command,
    get_language,
    normalize_language,
    set_language,
    subcommand,
    tr,
    translate,
)


@pytest.fixture(autouse=True)
def restore_language():
    yield
    set_language("fr")


def test_language_aliases_and_fallback():
    assert normalize_language("en") == "en"
    assert normalize_language("anglais") == "en"
    assert normalize_language("french") == "fr"
    assert normalize_language("français") == "fr"
    assert normalize_language("de") == "fr"
    assert set_language("english") == "en"
    assert get_language() == "en"


def test_catalogs_have_the_same_keys():
    def keys(node, prefix=""):
        found = set()
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                found |= keys(value, path)
            else:
                found.add(path)
        return found

    fr = json.loads((LOCALES_DIR / "fr.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))
    assert keys(fr) == keys(en)


def test_english_catalog_and_french_fallback():
    set_language("en")
    assert tr("cli.also_stopped", items="x") != "cli.also_stopped"
    assert translate("cli.also_stopped", language="fr", items="x") != tr("cli.also_stopped", items="x")
    assert tr("does.not.exist") == "does.not.exist"
    assert tr("cli.goodbye", default="bye") != "bye"


def test_settings_applies_language_and_follows_it_for_stt():
    settings = Settings(language="english", _env_file=None)
    assert settings.language == "en"
    assert settings.stt_language == "en"
    assert settings.stt_prompt
    assert settings.tts_prompt_lang in {"auto", "en", "zh", "ja", "ko", "yue"}


def test_slash_commands_are_english_and_keep_french_aliases():
    assert command("/help")[0] == "help"
    assert command("/aide")[0] == "help"
    assert command("/memory")[0] == "memory"
    assert command("/memoire")[0] == "memory"
    assert command("/remind in 20 minutes: water") == ("remind", "in 20 minutes: water")
    assert command("/rappel demain 8h : eau") == ("remind", "demain 8h : eau")
    assert subcommand("keep") == "keep"
    assert subcommand("garder") == "keep"
    assert COMMAND_ALIASES["rappel"] == COMMAND_ALIASES["remind"]
