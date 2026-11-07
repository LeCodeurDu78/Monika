"""Rôle surface : la première réplique, avant que les outils aient tourné.

Le petit modèle ne voit ni les outils ni leurs résultats. Il ne peut donc rien vérifier : sa
consigne est de ne jamais annoncer un fait, un chiffre ou un chemin, seulement le ton et ce
qu'elle s'apprête à faire. Le rôle deep reçoit ensuite cette phrase (`already_said`) pour ne pas
la répéter. Si le modèle dépasse `SURFACE_TIMEOUT_S` ou échoue, la réplique est simplement omise :
un tour ne doit jamais attendre le rôle surface.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from agent.llm import LLMError
from agent.roles import SURFACE_TEMPERATURE, surface_model
from monika.config import Settings
from monika.i18n import tr

log = logging.getLogger(__name__)

MAX_CHARS = 220


def clean(raw: str) -> str:
    """Une seule ligne, sans guillemets d'encadrement ni préfixe de dialogue."""
    line = next((part.strip() for part in str(raw or "").splitlines() if part.strip()), "")
    line = line.strip(" \"'«»“”")
    for prefix in ("Monika :", "Monika:"):
        if line.startswith(prefix):
            line = line[len(prefix) :].strip()
            break
    line = " ".join(line.split())
    return line if len(line) <= MAX_CHARS else line[: MAX_CHARS - 1].rstrip() + "…"


class SurfaceReflex:
    """Le rôle surface : `SURFACE_LLM_MODEL` vide le désactive."""

    def __init__(self, settings: Settings, llm: Any) -> None:
        self._s = settings
        self._llm = llm

    @property
    def enabled(self) -> bool:
        return bool(self._s.surface_llm_model.strip())

    async def line(self, user_text: str, *, voice: bool = False) -> str:
        """La réplique, ou "" si le rôle est coupé, lent ou en échec."""
        if not self.enabled:
            return ""
        system = tr("surface.system")
        if self._s.surface_prompt.strip():
            system += tr("surface.extra", text=self._s.surface_prompt.strip())
        if voice:
            system += "\n\n" + tr("surface.voice")
        safe = " ".join(str(user_text).split()).replace("<", "‹").replace(">", "›")
        try:
            async with asyncio.timeout(self._s.surface_timeout_s):
                completion = await self._llm.ask(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": f"<message>\n{safe}\n</message>"},
                    ],
                    temperature=SURFACE_TEMPERATURE,
                    model=surface_model(self._s),
                )
        except TimeoutError:
            log.debug("surface : délai de %s s dépassé", self._s.surface_timeout_s)
            return ""
        except asyncio.CancelledError:
            raise
        except LLMError as exc:
            log.debug("surface indisponible : %s", exc)
            return ""
        except Exception as exc:  # noqa: BLE001 - un tour ne s'arrête pas pour le rôle surface
            log.warning("surface : %s: %s", type(exc).__name__, exc)
            return ""
        return clean(completion.content)
