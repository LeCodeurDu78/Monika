"""Sub-agent that runs a long task in a worker thread (see core/background_jobs.py)."""

from typing import Any, Callable

from agents.runtime import run_react_loop
from core.background_jobs import JobCancelled, background_jobs
from core.settings import settings
from tools.registry import AVAILABLE_TOOLS, TOOLS_SCHEMA, TOOL_DEFS_BY_NAME

# Tools the sub-agent never gets:
# - browser_control: Playwright binds its objects to the thread that created them (the main agent's thread);
#   using it from a worker thread crashes. Web research goes through web_search / fetch_url instead.
# - discord_call: a voice call needs the user on the other end.
# - background_task: no tasks spawning tasks.
# - create_custom_tool / patch_existing_file: self-modification with nobody watching.
EXCLUDED_TOOLS = frozenset(
    {"browser_control", "discord_call", "background_task", "create_custom_tool", "patch_existing_file"}
)

BACKGROUND_CONTEXT = (
    "Contexte : tu exécutes une TÂCHE DE FOND, pendant que l'utilisateur continue à discuter avec toi par "
    "ailleurs. Personne ne peut répondre à tes questions dans cette conversation : n'en pose pas, fais des "
    "hypothèses raisonnables et dis-les dans ton compte rendu. N'envoie pas d'e-mail ni de message, et ne "
    "supprime rien, sauf si la demande le dit explicitement. Tu n'as pas de navigateur : pour le web, utilise "
    "web_search puis fetch_url. Ta dernière réponse est lue ou entendue par l'utilisateur à la fin : en "
    "français, 150 mots maximum, ton parlé, sans puces. Si le résultat est long (rapport, liste, code), "
    "écris-le dans un fichier avec write_file et donne le chemin dans ta réponse. N'affirme jamais avoir "
    "fait quelque chose dont tu n'as pas vu le résultat."
)


def _guarded(func: Callable[..., Any], should_stop: Callable[[], bool]) -> Callable[..., Any]:
    """Wrap a tool so that a cancelled (or timed out) job stops before its next call."""

    def call(*args: Any, **kwargs: Any) -> Any:
        if should_stop():
            raise JobCancelled()
        return func(*args, **kwargs)

    call.__name__ = getattr(func, "__name__", "tool")
    return call


def run_background_job(goal: str, should_stop: Callable[[], bool]) -> str:
    """Run `goal` with its own history and a restricted toolbox; return the final reply."""
    from agents.orchestrator import build_system_prompt  # late: orchestrator imports this module

    tools = {name: _guarded(func, should_stop) for name, func in AVAILABLE_TOOLS.items() if name not in EXCLUDED_TOOLS}
    schema = [s for s in TOOLS_SCHEMA if s["function"]["name"] in tools]

    messages = [
        {"role": "system", "content": build_system_prompt(voice=False, context=BACKGROUND_CONTEXT)},
        {"role": "user", "content": goal},
    ]
    return run_react_loop(
        messages,
        schema,
        tools,
        max_turns=settings.BACKGROUND_MAX_STEPS,
        tool_defs=TOOL_DEFS_BY_NAME,
        on_wait=None,  # nobody is waiting on this thread: no "un instant" phrases
        on_text=None,
    )


background_jobs.set_runner(run_background_job)
