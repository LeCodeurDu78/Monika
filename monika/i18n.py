"""UI language, message catalogs and slash-command aliases.

The catalogs live in ``monika/locales/<code>.json``.  French stays the default so an
existing installation behaves exactly as before; English is the first added language.
Only short, user-facing strings and model prompts belong here: log messages, comments
and internal data keys stay as they are.
"""

from __future__ import annotations

import json
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

LOCALES_DIR = Path(__file__).resolve().parent / "locales"
DEFAULT_LANGUAGE = "fr"
SUPPORTED_LANGUAGES = ("fr", "en")
LANGUAGE_NAMES = {
    "fr": "Français",
    "en": "English",
}
_ALIASES = {
    "fr": "fr",
    "francais": "fr",
    "français": "fr",
    "french": "fr",
    "en": "en",
    "anglais": "en",
    "english": "en",
}
_current = DEFAULT_LANGUAGE
_lock = threading.RLock()


def normalize_language(value: str | None, default: str = DEFAULT_LANGUAGE) -> str:
    """Return a supported code, accepting the usual French/English spellings."""
    code = str(value or "").strip().lower().replace("_", "-")
    code = _ALIASES.get(code, code.split("-", 1)[0])
    return code if code in SUPPORTED_LANGUAGES else default


def set_language(value: str | None) -> str:
    """Set the process-wide display language and return the normalized code."""
    global _current
    code = normalize_language(value)
    with _lock:
        _current = code
    return code


def get_language() -> str:
    return _current


def is_english() -> bool:
    return get_language() == "en"


@lru_cache(maxsize=None)
def _catalog(code: str) -> dict[str, Any]:
    path = LOCALES_DIR / f"{code}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _lookup(code: str, key: str) -> Any:
    node: Any = _catalog(code)
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def has(key: str, language: str | None = None) -> bool:
    return _lookup(normalize_language(language or _current), key) is not None


def translate(key: str, language: str | None = None, **values: Any) -> str:
    """Translate one key.  ``key`` itself is returned when a catalog entry is missing."""
    code = normalize_language(language or _current)
    value = _lookup(code, key)
    if value is None and code != DEFAULT_LANGUAGE:
        value = _lookup(DEFAULT_LANGUAGE, key)
    if value is None:
        return key
    text = str(value)
    if values:
        try:
            text = text.format(**values)
        except (KeyError, IndexError, ValueError):
            pass
    return text


def translate_list(key: str, language: str | None = None) -> list[str]:
    code = normalize_language(language or _current)
    value = _lookup(code, key)
    if value is None and code != DEFAULT_LANGUAGE:
        value = _lookup(DEFAULT_LANGUAGE, key)
    return [str(item) for item in value] if isinstance(value, list) else []


def tr(key: str, **values: Any) -> str:
    """Translate, with an optional French ``default`` used while a catalog is incomplete."""
    default = values.pop("default", None)
    code = normalize_language(_current)
    value = _lookup(code, key)
    if value is None and code != DEFAULT_LANGUAGE:
        value = _lookup(DEFAULT_LANGUAGE, key)
    if value is None:
        value = default if default is not None else key
    text = str(value)
    if values:
        try:
            text = text.format(**values)
        except (KeyError, IndexError, ValueError):
            pass
    return text


def tr_list(key: str) -> list[str]:
    return translate_list(key)

def tr_dict(key: str) -> dict[str, Any]:
    """Return a catalog mapping (e.g. spoken unit names); {} when it is missing."""
    code = normalize_language(_current)
    value = _lookup(code, key)
    if value is None and code != DEFAULT_LANGUAGE:
        value = _lookup(DEFAULT_LANGUAGE, key)
    return value if isinstance(value, dict) else {}


def language_name(code: str | None = None) -> str:
    return LANGUAGE_NAMES.get(normalize_language(code or _current), code or "")


# Canonical names are English in every language; the French names remain accepted.
COMMAND_ALIASES: dict[str, str] = {
    "help": "help",
    "aide": "help",
    "quit": "quit",
    "q": "quit",
    "quitter": "quit",
    "clear": "clear",
    "effacer": "clear",
    "tools": "tools",
    "outils": "tools",
    "context": "context",
    "contexte": "context",
    "memory": "memory",
    "memoire": "memory",
    "mémoire": "memory",
    "forget": "forget",
    "oublier": "forget",
    "relationship": "relationship",
    "relation": "relationship",
    "journal": "journal",
    "nightly": "nightly",
    "journal-nuit": "nightly",
    "sessions": "sessions",
    "resume": "resume",
    "reprendre": "resume",
    "search": "search",
    "chercher": "search",
    "session": "session",
    "index": "index",
    "indexer": "index",
    "documents": "documents",
    "remind": "remind",
    "rappel": "remind",
    "tasks": "tasks",
    "taches": "tasks",
    "tâches": "tasks",
    "task": "task",
    "tache": "task",
    "tâche": "task",
    "briefing": "briefing",
    "stop": "stop",
    "custom-tools": "custom-tools",
    "outils-perso": "custom-tools",
    "recipes": "recipes",
    "recettes": "recipes",
}

SUBCOMMAND_ALIASES: dict[str, str] = {
    "forget": "forget",
    "oublier": "forget",
    "keep": "keep",
    "garder": "keep",
    "refuse": "refuse",
    "refuser": "refuse",
    "show": "show",
    "voir": "show",
    "delete": "delete",
    "supprimer": "delete",
    "cancel": "cancel",
    "annuler": "cancel",
    "accept": "accept",
    "accepter": "accept",
    "disable": "disable",
    "desactiver": "disable",
    "désactiver": "disable",
    "code": "code",
    "reset": "reset",
    "reinitialiser": "reset",
    "réinitialiser": "reset",
    "clear": "clear",
    "effacer": "clear",
}


def command(line: str) -> tuple[str | None, str]:
    """Return (canonical command, rest) for a slash line; (None, line) for a message."""
    text = (line or "").strip()
    if not text.startswith("/"):
        return None, text
    head, _, rest = text[1:].partition(" ")
    return COMMAND_ALIASES.get(head.lower()), rest.strip()


def subcommand(word: str) -> str:
    return SUBCOMMAND_ALIASES.get((word or "").strip().lower(), "")


def command_names(key: str) -> list[str]:
    """Accepted spellings for one canonical command (English first)."""
    return [name for name, canonical in COMMAND_ALIASES.items() if canonical == key]
