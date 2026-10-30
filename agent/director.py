"""Director role: one short planning call before the ReAct loop.

It does two things and nothing else: it narrows the tool schemas sent with this turn's requests, and it
writes one short hint that goes into the system prompt for this turn only (never into the history).

Both are optional. A failure, a timeout or an unusable answer means "no plan", and the turn then runs
exactly as without it — the director may lighten a turn, never break it. Tool names are filtered against
the registry, and a plan that keeps no known tool falls back to every tool: an omitted tool cannot be
recovered during the turn, so the safe side is the full list.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

MAX_HINT_CHARS = 300
MAX_TOOLS = 8
_TAGS = re.compile(r"[<>]")
_SPACES = re.compile(r"\s+")

SYSTEM_PROMPT = """\
Tu es le planificateur d'un tour de conversation. On te donne le message de l'utilisateur et les noms des \
outils disponibles. Tu ne réponds jamais à l'utilisateur.

Réponds STRICTEMENT en JSON, sans texte autour :
{{"tools": ["nom", …], "hint": "…"}}
- "tools" : seulement des noms EXACTEMENT pris dans la liste, au plus {max_tools}, ceux qui servent à ce \
message. Mets [] si aucun ne sert ou si tu hésites : tous les outils resteront alors disponibles.
- "hint" : une phrase en français de {max_hint} caractères au plus, une piste concrète pour ce tour ; "" \
si tu n'as rien d'utile à dire.

Outils disponibles : {tools}"""


@dataclass(frozen=True)
class TurnPlan:
    """What the director decided for one turn; `tools=None` means every tool."""

    tools: set[str] | None
    hint: str = ""


def clean_hint(raw: Any) -> str:
    """One line, no angle-bracket tags (it is injected into a prompt), capped. Never raises."""
    if not isinstance(raw, str):
        return ""
    text = _SPACES.sub(" ", _TAGS.sub(" ", raw)).strip()
    return text[:MAX_HINT_CHARS].strip()


def parse_plan(raw: Any, known: set[str]) -> TurnPlan | None:
    """Strict parser: unknown tools are dropped, the hint is cleaned. Anything unusable returns None."""
    if not isinstance(raw, str) or "{" not in raw:
        return None
    start, end = raw.find("{"), raw.rfind("}")
    if end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    tools: set[str] | None = None
    picked = data.get("tools")
    if isinstance(picked, list):
        names = {name for name in picked if isinstance(name, str) and name in known}
        # [] (or only unknown names) means "no restriction"; a long list is a bad answer, not a plan
        tools = names or None
        if tools is not None and len(tools) > MAX_TOOLS:
            return None
    return TurnPlan(tools, clean_hint(data.get("hint")))


async def plan(
    user_text: str, known: set[str], llm: Any, timeout_s: float = 1.5, model: str = ""
) -> TurnPlan | None:
    """Ask for one plan; None on timeout, error or unusable answer. Never raises (cancellation aside)."""
    if not known:
        return None
    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT.format(
                tools=", ".join(sorted(known)),
                max_tools=MAX_TOOLS,
                max_hint=MAX_HINT_CHARS,
            ),
        },
        {"role": "user", "content": user_text[:2000]},
    ]
    try:
        async with asyncio.timeout(timeout_s):
            answer = await llm.ask(messages, model=model or None, temperature=0.0)
    except TimeoutError:
        return None
    except Exception:  # noqa: BLE001 - optional role: any failure means "no plan"
        return None
    return parse_plan(getattr(answer, "content", ""), known)
