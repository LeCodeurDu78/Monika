"""What the page shows (context, memory) and the edits it may make; every section fails on its own."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from monika import __version__
from monika.config import Settings
from monika.paths import Paths
from web.server import ActionError

log = logging.getLogger(__name__)

SECTION_TIMEOUT_S = 3.0


class Dashboard:
    """Collects what the page shows. Parts that do not exist yet (agent still starting) say so."""

    def __init__(self, settings: Settings, paths: Paths, hub: Any, mode: str = "texte",
                 clock: Callable[[], float] = time.time) -> None:
        self._s = settings
        self._paths = paths
        self._hub = hub
        self.mode = mode
        self.agent: Any = None
        self._clock = clock

    async def _section(self, build: Callable[[], Any]) -> Any:
        """Run one section; a failure becomes {'error': ...} instead of breaking the whole page."""
        try:
            result = build()
            if asyncio.iscoroutine(result):
                result = await asyncio.wait_for(result, SECTION_TIMEOUT_S)
            return result
        except TimeoutError:
            return {"error": f"délai de {SECTION_TIMEOUT_S:g} s dépassé"}
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}

    async def snapshot(self) -> dict[str, Any]:
        agent = self.agent
        return {
            "app": {"version": __version__, "mode": self.mode, "model": self._s.model_name,
                    "state": self._hub.state, "ready": agent is not None,
                    "fx": self._s.scene_fx_enabled},  # the page decides whether to build the effects
            "context": await self._section(lambda: agent.stats() if agent else None),
            "memory": await self._section(self._memory),
            "character": await self._section(self._character_section),
        }

    async def _memory(self) -> dict[str, Any]:
        memory = getattr(self.agent, "memory", None)
        if memory is None:
            return {"enabled": False}
        items = await memory.list_all(limit=200)
        items.sort(key=lambda i: i.created_at or "", reverse=True)
        return {"enabled": True, "facts": [{"id": i.id, "text": i.text} for i in items]}

    def _character_section(self) -> dict[str, Any]:
        character = getattr(self.agent, "character", None)
        if character is None:
            return {"enabled": False}
        return {"enabled": True, "state": character.snapshot() or None, "journal": character.journal_entries()}

    # --- edits requested from the page ---------------------------------------------------------

    def actions(self) -> dict[str, Callable[..., Any]]:
        return {"memory_update": self._memory_update, "memory_delete": self._memory_delete,
                "journal_update": self._journal_update, "journal_delete": self._journal_delete,
                "journal_clear": self._journal_clear, "impression_update": self._impression_update,
                "relation_reset": self._relation_reset}

    def _character(self) -> Any:
        character = getattr(self.agent, "character", None)
        if character is None:
            raise ActionError("Le personnage est désactivé ou indisponible.", 503)
        return character

    def _journal_owner(self) -> Any:
        character = self._character()
        if character.journal_entries() is None:
            raise ActionError("Le journal est désactivé.", 503)
        return character

    async def _journal_update(self, note_id: str, text: str) -> None:
        if not self._journal_owner().journal_edit(note_id, text):
            raise ActionError("Cette note n'existe plus.", 404)
        log.info("Note de journal %s modifiée depuis la page", note_id)
        self._hub.activity("journal")

    async def _journal_delete(self, note_id: str) -> None:
        if not self._journal_owner().journal_delete(note_id):
            raise ActionError("Cette note n'existe plus.", 404)
        log.info("Note de journal %s supprimée depuis la page", note_id)
        self._hub.activity("journal")

    async def _journal_clear(self) -> None:
        self._journal_owner().journal_clear()
        log.info("Journal effacé depuis la page")
        self._hub.activity("journal")

    async def _impression_update(self, text: str) -> None:
        if not self._journal_owner().journal_edit_impression(text):
            raise ActionError("Le journal est désactivé.", 503)
        log.info("Impression d'ensemble modifiée depuis la page")
        self._hub.activity("journal")

    async def _relation_reset(self) -> None:
        self._character().reset_relationship()
        log.info("Relation réinitialisée depuis la page")
        self._hub.activity("character")

    def _fast_memory(self) -> Any:
        memory = getattr(self.agent, "memory", None)
        if memory is None:
            raise ActionError("La mémoire rapide est désactivée ou indisponible.", 503)
        return memory

    async def _memory_update(self, memory_id: str, text: str) -> None:
        if not await self._fast_memory().update(memory_id, text):
            raise ActionError("Ce souvenir n'existe plus.", 404)
        log.info("Souvenir %s modifié depuis la page", memory_id)
        self._hub.activity("memory")

    async def _memory_delete(self, memory_id: str) -> None:
        if await self._fast_memory().delete(memory_id) is None:
            raise ActionError("Ce souvenir n'existe plus.", 404)
        log.info("Souvenir %s supprimé depuis la page", memory_id)
        self._hub.activity("memory")
