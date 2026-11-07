"""System prompt, rebuilt at every turn from personality, method, date and system context.

Every block lives in ``monika/locales``; this module only assembles them, so switching
LANGUAGE changes the prompt without touching the agent loop.
"""

from __future__ import annotations

from datetime import datetime

from agent.sysinfo import format_date, system_context
from monika.i18n import is_english, tr


def _owner(name: str) -> str:
    """Return the possessive phrase the personality block expects."""
    name = name.strip()
    if not name:
        return tr("prompt.owner_user")
    if is_english():
        return tr("prompt.owner_name", name=name)
    return f"d'{name}" if name[0].lower() in "aeiouyhéèêàâîïôûü" else f"de {name}"


def sensitive_block(tier: str | None) -> str:
    """The tone section for this message's level (mild gets nothing)."""
    key = {"bad": "prompt.sensitive_bad", "severe": "prompt.sensitive_severe"}.get(
        tier or ""
    )
    return tr(key) if key else ""


def _section(header: str, body: str) -> str:
    return f"{header}\n{body.strip()}"


def build_system_prompt(
    now: datetime | None = None,
    has_tools: bool = False,
    has_result_reader: bool = False,
    summary: str = "",
    user_name: str = "",
    voice: bool = False,
    context_lines: list[str] | None = None,
    memory_block: str = "",
    has_documents: bool = False,
    character_block: str = "",
    journal_block: str = "",
    morning_block: str = "",
    recipe_block: str = "",
    persona_block: str = "",
    tone_block: str = "",
    already_said: str = "",
    reflection_block: str = "",
) -> str:
    """Assemble the full system prompt for one turn."""
    now = now or datetime.now().astimezone()
    parts = [
        tr("prompt.personality", owner=_owner(user_name)),
        tr("prompt.method"),
    ]
    if persona_block.strip():
        parts.append(
            _section(tr("prompt.persona_header"), persona_block)
        )
    if has_tools:
        parts.append(
            tr("prompt.data_rules")
            + ("\n" + tr("prompt.result_reader") if has_result_reader else "")
        )
    if has_documents:
        parts.append(tr("prompt.documents"))
    parts.append(tr("prompt.voice_style") if voice else tr("prompt.text_style"))
    lines = [tr("prompt.date", date=format_date(now))]
    lines += system_context() if context_lines is None else context_lines
    parts.append(_section(tr("prompt.context_header"), "\n".join(lines)))
    if memory_block.strip():
        parts.append(
            _section(
                tr("prompt.memory_header"),
                "<memoire>\n" + memory_block.strip() + "\n</memoire>",
            )
        )
    if journal_block.strip():
        parts.append(journal_block.strip())
    if morning_block.strip():
        parts.append(_section(tr("prompt.morning_header"), morning_block))
    if recipe_block.strip():
        parts.append(recipe_block.strip())
    if reflection_block.strip():
        parts.append(_section(tr("prompt.reflection_header"), reflection_block))
    if summary.strip():
        parts.append(_section(tr("prompt.summary_header"), summary))
    if already_said.strip():
        parts.append(tr("prompt.already_said", text=already_said.strip()))
    if tone_block.strip():
        parts.append(tone_block.strip())
    if character_block.strip():
        parts.append(character_block.strip())
    return "\n\n".join(parts)
