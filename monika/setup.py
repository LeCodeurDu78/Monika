"""First-run wizard (`python -m monika --setup`).

It writes only the project's `.env` and `config/settings.json`; no network call, no model
download. The installer calls it once, and it can be rerun at any time.

`python -m monika --setup --demo` asks the same questions but writes to a temporary folder and
shows the files it would have written: the real `.env` and `settings.json` are neither read for
their secrets nor touched.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

from monika.config import ENV_FILE, PROJECT_ROOT, PROVIDER_PRESETS
from monika.i18n import tr

CONFIG_DIR = PROJECT_ROOT / "config"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
SETTINGS_EXAMPLE = CONFIG_DIR / "settings.example.json"
SECRETS = frozenset({"API_KEY"})


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


def _has(lines: list[str], key: str) -> bool:
    return any(
        not line.lstrip().startswith("#")
        and line.split("=", 1)[0].strip().upper() == key
        for line in lines
    )


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


def _provider(value: str) -> str:
    """`custom` (OpenAI Custom) unless the answer clearly names OpenRouter or Ollama."""
    raw = " ".join(value.strip().lower().replace("/", " ").replace(",", " ").split())
    if raw.startswith("openrouter"):
        return "openrouter"
    if raw.startswith("ollama"):
        return "ollama"
    return "custom"


def _preview(lines: list[str]) -> str:
    """The `.env` as it would be written, with the key masked (demo output)."""
    out: list[str] = []
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key.strip().upper() in SECRETS and value:
            line = f"{key}={value[:4]}… ({len(value)} caractères)"
        out.append(line)
    return "\n".join(out)


def run_setup(demo: bool = False) -> int:
    """Interactive setup; returns 0 on success, 1 when there is no terminal."""
    if not sys.stdin.isatty() and not demo:
        print(tr("setup.tty"))
        return 1
    if demo:
        folder = Path(tempfile.mkdtemp(prefix="monika-setup-demo-"))
        env_path = folder / ".env"
        settings_path = folder / "config" / "settings.json"
        settings_example = folder / "config" / "settings.example.json"
        if SETTINGS_EXAMPLE.exists():
            settings_example.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SETTINGS_EXAMPLE, settings_example)
        lines: list[str] = []  # The real .env holds the real key: never read it here
        print(f"\n{tr('setup.demo_title')}")
        print(f"{tr('setup.demo_folder')} {folder}")
        if not sys.stdin.isatty():
            print(tr("setup.demo_defaults"))
    else:
        env_path, settings_path, settings_example = (
            ENV_FILE,
            SETTINGS_FILE,
            SETTINGS_EXAMPLE,
        )
        if env_path.exists():
            lines = env_path.read_text(encoding="utf-8").splitlines()
        elif (PROJECT_ROOT / ".env.example").exists():
            lines = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        else:
            lines = []
    print(f"\n{tr('setup.title')}\n")

    language = _ask(tr("setup.language"), "fr").lower()
    language = "en" if language.startswith("en") else "fr"
    lines = _set(lines, "LANGUAGE", language)

    provider = _provider(_ask(tr("setup.provider"), "custom"))
    preset = PROVIDER_PRESETS.get(provider, {})
    base = _ask(tr("setup.base_url"), preset.get("llm_base_url", "https://api.libertai.io/v1"))
    model = _ask(tr("setup.model"), preset.get("model_name", "deepseek-v4.1-flash"))
    lines = _set(lines, "LLM_PROVIDER", provider)
    lines = _set(lines, "LLM_BASE_URL", base)
    lines = _set(lines, "MODEL_NAME", model)
    key = "ollama" if provider == "ollama" else _ask(tr("setup.key"), "")
    if key:
        lines = _set(lines, "API_KEY", key)

    name = _ask(tr("setup.name"), "")
    if name:
        lines = _set(lines, "USER_NAME", name)
    voice = _yes(tr("setup.voice"), True)
    lines = _set(lines, "TTS_ENABLED", "true" if voice else "false")

    # Keep a settings file the user chose earlier (e.g. in MONIKA_HOME); only pick the project one.
    if not _has(lines, "MONIKA_SETTINGS_FILE"):
        lines = _set(lines, "MONIKA_SETTINGS_FILE", "config/settings.json")
    _write(env_path, "\n".join(lines) + "\n")

    if not settings_path.exists():
        if settings_example.exists():
            _write(settings_path, settings_example.read_text(encoding="utf-8"))
        else:
            _write(settings_path, "{}\n")

    print(f"\n{tr('setup.done')}")
    if demo:
        print(f"\n{tr('setup.demo_env')}\n{_preview(lines)}")
        print(
            f"\n{tr('setup.demo_settings')}\n"
            f"{settings_path.read_text(encoding='utf-8').strip()}"
        )
        shutil.rmtree(folder, ignore_errors=True)
        print(f"\n{tr('setup.demo_clean')}")
        return 0

    python = ".venv\\Scripts\\python.exe" if os.name == "nt" else ".venv/bin/python"
    print(f"  {python} -m monika --config")
    print(f"  {python} -m monika")
    if voice:
        print(f"\n{tr('setup.voice_hint')}")
        print("  ./scripts/install_gptsovits.sh")
    return 0
