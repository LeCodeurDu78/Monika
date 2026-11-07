"""Rôle réflexion entre les tours : un conseil, parfois une prise de parole.

Après un tour réussi, le dernier échange part dans une file de fond. Le modèle de réflexion en
tire un conseil court, ajouté au prompt du tour suivant (comme une note interne, jamais comme une
parole de l'utilisateur). Avec `REFLECTION_SPEAK=true`, il peut aussi proposer une intention de
prise de parole : c'est le tour automatique habituel qui la dit, en son personnage, donc avec les
outils et le bilan des autres tours.

Rien n'est attendu pendant une réponse : la file ne ralentit jamais le tour en cours.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agent.llm import LLMError
from agent.roles import reflection_model
from monika.config import Settings
from monika.i18n import tr

log = logging.getLogger(__name__)

TIMEOUT_S = 30.0
MAX_TURN_CHARS = 1200
MAX_REPLY_CHARS = 1600
MAX_ADVICE = 400
MAX_SPEAK = 200
QUEUE_SIZE = 4




def _clean(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").replace("<", "‹").replace(">", "›").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _json_object(raw: str) -> dict[str, Any] | None:
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


class BetweenTurns:
    """La file de fond du rôle réflexion ; `take()` rend le conseil au tour suivant."""

    def __init__(
        self,
        settings: Settings,
        llm: Any,
        speak: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._s = settings
        self._llm = llm
        self._speak = speak
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._task: asyncio.Task[None] | None = None
        self._advice = ""

    def bind_speak(self, speak: Callable[[str], Awaitable[None]] | None) -> None:
        """Branche la prise de parole (le tour automatique de la session)."""
        self._speak = speak

    def submit(self, user_text: str, reply: str) -> None:
        """Met l'échange de côté ; une file pleine abandonne ce tour sans rien casser."""
        try:
            self._queue.put_nowait((user_text[:MAX_TURN_CHARS], reply[:MAX_REPLY_CHARS]))
        except asyncio.QueueFull:
            log.warning("réflexion : file pleine, un tour n'est pas analysé.")
            return
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run())

    def take(self) -> str:
        """Le conseil préparé, une seule fois."""
        advice, self._advice = self._advice, ""
        return advice

    async def _run(self) -> None:
        while True:
            user_text, reply = await self._queue.get()
            try:
                await self._reflect(user_text, reply)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - la réflexion ne casse jamais un tour
                log.warning("réflexion : %s: %s", type(exc).__name__, exc)
            finally:
                self._queue.task_done()

    async def _reflect(self, user_text: str, reply: str) -> None:
        messages = [
            {"role": "system", "content": tr("reflection.system")},
            {
                "role": "user",
                "content": "<dernier_echange>\n"
                f"{tr('reflection.user_label')} : {user_text}\n"
                f"{tr('reflection.monika_label')} : {reply}\n"
                "</dernier_echange>",
            },
        ]
        async with asyncio.timeout(TIMEOUT_S):
            completion = await self._llm.ask(
                messages,
                temperature=0.4,
                model=reflection_model(self._s),
            )
        data = _json_object(completion.content)
        if data is None:
            log.debug("réflexion : pas de JSON exploitable")
            return
        advice = _clean(data.get("conseil"), MAX_ADVICE)
        if advice:
            self._advice = advice
        speak = _clean(data.get("parler"), MAX_SPEAK)
        if speak and self._s.reflection_speak and self._speak is not None:
            try:
                await self._speak(speak)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("réflexion : prise de parole impossible (%s: %s)", type(exc).__name__, exc)

    async def drain(self, timeout: float = 30.0) -> int:
        """Attend la file puis arrête le worker ; rend le nombre de tours non analysés."""
        left = 0
        if self._task is not None and not self._task.done():
            try:
                await asyncio.wait_for(self._queue.join(), timeout)
            except TimeoutError:
                left = self._queue.qsize()
                log.warning("réflexion interrompue : %d tour(s) non analysé(s).", left)
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        return left
