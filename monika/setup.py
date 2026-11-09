"""First-run wizard (`python -m monika --setup`).

It writes only the project's `.env` and `config/settings.json`; no network call, no model
download. The installer calls it once, and it can be rerun at any time.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from monika.config import ENV_FILE, PROJECT_ROOT, PROVIDER_PRESETS
from monika.i18n import tr

CONFIG_DIR = PROJECT_ROOT / "config"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
SETTINGS_EXAMPLE = CONFIG_DIR / "settings.example.json"


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{prompt}{suffix} : ").strip()
    except EOFError:
        return default
    return value or default


def _yes(prompt: str, default: bool = True) -> bool:
    value = _ask(f"{prompt} (o/n)", "o" if default else "n").lower()
    return value in {"o", "oui", "y", "yes"}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}-")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(text)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _set(lines: list[str], key: str, value: str) -> list[str]:
    out: list[str] = []
    replaced = False
    for line in lines:
        head = line.split("=", 1)[0].strip().upper()
        if not line.lstrip().startswith("#") and head == key:
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    return out


def run_setup() -> int:
    """Interactive setup; returns 0 on success, 1 when there is no terminal."""
    if not sys.stdin.isatty():
        print(tr("setup.tty"))
        return 1
    print(f"\n{tr('setup.title')}\n")
    env_path = ENV_FILE
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    elif (PROJECT_ROOT / ".env.example").exists():
        lines = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    language = _ask(tr("setup.language"), "fr").lower()
    language = "en" if language.startswith("en") else "fr"
    lines = _set(lines, "LANGUAGE", language)

    provider = _ask(tr("setup.provider"), "custom").lower()
    provider = provider if provider in {"custom", "openrouter", "ollama"} else "custom"
    preset = PROVIDER_PRESETS.get(provider, {})
    base = _ask(tr("setup.base_url"), preset.get("llm_base_url", "https://api.libertai.io/v1"))
    model = _ask(tr("setup.model"), preset.get("model_name", "deepseek-v4.1-flash"))
    lines = _set(lines, "LLM_PROVIDER", provider)
    lines = _set(lines, "LLM_BASE_URL", base)
    lines = _set(lines, "MODEL_NAME", model)
    if provider == "ollama":
        key = "ollama"
    else:
        key = _ask(tr("setup.key"), "" if provider == "custom" else "")
    if key:
        lines = _set(lines, "OPENAI_API_KEY", key)

    name = _ask(tr("setup.name"), "")
    if name:
        lines = _set(lines, "USER_NAME", name)
    voice = _yes(tr("setup.voice"), True)
    lines = _set(lines, "TTS_ENABLED", "true" if voice else "false")

    lines = _set(lines, "MONIKA_SETTINGS_FILE", "config/settings.json")
    _write(env_path, "\n".join(lines) + "\n")

    if not SETTINGS_FILE.exists():
        if SETTINGS_EXAMPLE.exists():
            _write(SETTINGS_FILE, SETTINGS_EXAMPLE.read_text(encoding="utf-8"))
        else:
            _write(SETTINGS_FILE, "{}\n")

    print(f"\n{tr('setup.done')}")
    print("  .venv/bin/python -m monika --config")
    print("  .venv/bin/python -m monika")
    if voice:
        print(f"\n{tr('setup.voice_hint')}")
        print("  ./scripts/install_gptsovits.sh")
    return 0
