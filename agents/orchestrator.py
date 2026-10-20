"""Agent unique de Monika."""

from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from config import build_persona_prompt
from core import action_log
from agents.runtime import run_react_loop
from core.serial_executor import SerialExecutor
from core.settings import settings
from memory.manager import memory_manager, prompt_section
from tools.registry import AVAILABLE_TOOLS, TOOLS_SCHEMA, TOOL_DEFS_BY_NAME
from agents.guides import GUIDES
import agents.background  # noqa: F401 - registers the background sub-agent runner

MAX_TURNS = 100

_AGENT_THREAD = SerialExecutor("monika-agent")

_TOOLS_NOTE = (
    "## Outils\n"
    "Tu disposes d'outils (système, fichiers, e-mails, calendrier, Discord, recherche web, météo, "
    "Spotify, rappels et tâches planifiées...). Utilise-les directement dès que c'est pertinent. "
    "Ne parle pas de tes outils dans tes réponses, sauf si on te demande ce que tu sais faire."
)

# Situational contexts passed through process_user_message(context=...).
# Shared so that two near-identical phrasings do not drift apart over time.

SCHEDULED_TASK_CONTEXT = (
    "Contexte : l'échéance d'une tâche planifiée précédemment vient d'arriver. "
    "Aucun utilisateur n'est présent dans cette conversation pour répondre. "
    "Exécute l'instruction ci-dessous MAINTENANT, directement avec les outils "
    "nécessaires pour l'accomplir (ex: send_discord_message, email_control, "
    "get_weather...). La planification est déjà faite : il s'agit maintenant de "
    "l'exécuter réellement."
)

DISCORD_CONTEXT = (
    "Contexte : l'utilisateur te parle par Discord (message privé ou salon), probablement loin de son "
    "ordinateur. Ta réponse lui est envoyée automatiquement sur Discord : n'appelle donc PAS "
    "send_discord_message pour lui répondre. Reste concis ; le Markdown Discord (**gras**, `code`, "
    "listes) est accepté, évite les tableaux. Les messages Discord sont limités à 2000 caractères "
    "(le surplus est envoyé en plusieurs messages)."
)

_JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def _now() -> datetime:
    try:
        return datetime.now(ZoneInfo(settings.TIMEZONE))
    except Exception:  # unknown timezone or no tz database: use the machine's local time
        return datetime.now().astimezone()


def _environment_block(now: datetime) -> str:
    """Date, timezone and system: without them the model invents dates ("tomorrow", reminders, calendar).

    Only the date is here (it changes once a day, which keeps the LLM server's prefix cache valid); the exact
    time is added to each user message.
    """
    date_fr = f"{_JOURS[now.weekday()]} {now.day} {_MOIS[now.month - 1]} {now.year}"
    return (
        "## Contexte\n"
        f"- Date du jour : {date_fr}.\n"
        f"- Fuseau horaire : {settings.TIMEZONE}. L'heure exacte est indiquée à la fin de chaque message de l'utilisateur.\n"
        f"- Système : Linux. Dossier personnel : {Path.home()}."
    )


def build_system_prompt(voice: bool, context: str = "") -> str:
    parts = [
        build_persona_prompt(voice=voice),
        _environment_block(_now()),
        _TOOLS_NOTE,
        prompt_section().strip(),
    ]
    parts.extend(GUIDES)
    if context.strip():
        parts.append(context.strip())
    return "\n\n".join(part for part in parts if part)


def process_user_message(
    messages: list,
    max_turns: int = MAX_TURNS,
    interactive: bool = True,
    context: str = "",
    voice: bool = False,
    on_wait: Optional[Callable[[str], None]] = None,
    on_text: Optional[Callable[[str], None]] = None,
) -> str:
    """Process the user's message with the single agent (see `_process_user_message`).

    Every turn (keyboard, scheduled tasks, Discord) goes through one dedicated thread: see core/serial_executor.py.
    `on_wait(phrase)` is called (from another thread) when a tool flagged `lent` takes a while: the caller says
    the phrase on its channel (voice, terminal, Discord...). `on_text(delta)` receives the reply as it is generated
    (streaming TTS).
    """
    return _AGENT_THREAD.run(_process_user_message, messages, max_turns, interactive, context, voice, on_wait, on_text)


def _process_user_message(
    messages: list,
    max_turns: int = MAX_TURNS,
    interactive: bool = True,
    context: str = "",
    voice: bool = False,
    on_wait: Optional[Callable[[str], None]] = None,
    on_text: Optional[Callable[[str], None]] = None,
) -> str:
    """Process the user's message with the single agent.

    `context` lets the caller (scheduled task, call...) add situational instructions to the system prompt.
    `voice` picks the reply style (spoken and concise, or a fuller written answer).
    """
    # Reasoning from previous turns must not be sent back to the model.
    for msg in messages:
        msg.pop("reasoning_content", None)

    for i in range(len(messages) - 1, 0, -1):
        if messages[i].get("role") == "system":
            del messages[i]

    user_message = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    user_text = user_message.get("content") if user_message else None
    if not isinstance(user_text, str):
        user_text = ""  # content missing or multimodal: leave it alone

    system_content = build_system_prompt(voice, context)

    if not messages or messages[0].get("role") != "system":
        messages.insert(0, {"role": "system", "content": system_content})
    else:
        messages[0]["content"] = system_content

    # Additions valid for THIS TURN only, removed afterwards: exact time and quick memory.
    # Putting them in the system prompt would change it every turn (invalidating the LLM server's prefix cache
    # for the whole history), and they would pile up in the conversation if they stayed.
    turn_addendum = ""
    if user_text:
        turn_addendum = f"\n\n[Heure actuelle : {_now():%H:%M}]" + memory_manager.build_context(user_text)
        user_message["content"] = user_text + turn_addendum
    action_log.set_main_task(user_text or context or "tâche sans texte")
    try:
        reply = run_react_loop(
            messages,
            # All tools are exposed. Both structures are the registry's own (not copies): a tool created during
            # the turn by create_custom_tool is usable immediately.
            lambda: TOOLS_SCHEMA,
            AVAILABLE_TOOLS,
            max_turns=max_turns,
            tool_defs=TOOL_DEFS_BY_NAME,
            on_wait=on_wait,
            on_text=on_text,
        )
    finally:
        action_log.set_main_task(None)
        if turn_addendum:
            user_message["content"] = user_text

    # Automatic capture (in the background): real exchanges with the user only. A scheduled task looping every
    # hour would flood the memory, and an error message has nothing worth remembering.
    if interactive and user_text and reply and not reply.startswith("⚠️"):
        memory_manager.remember_turn(user_text, reply)
    return reply
