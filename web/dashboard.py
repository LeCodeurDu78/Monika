"""What the page shows (context, memory) and the edits it may make; every section fails on its own."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from agent.sessions import SessionsError
from monika import __version__
from monika.config import Settings
from monika.paths import Paths
from web.server import ActionError

log = logging.getLogger(__name__)

SECTION_TIMEOUT_S = 3.0
SESSION_PANEL_LIMIT = 15  # the page lists the most recent sessions, the terminal can see them all
MAX_CHAT_CHARS = 4000     # one message typed in the page


class Dashboard:
    """Collects what the page shows. Parts that do not exist yet (agent still starting) say so."""

    def __init__(self, settings: Settings, paths: Paths, hub: Any, mode: str = "texte",
                 clock: Callable[[], float] = time.time, chat: Any = None) -> None:
        self._s = settings
        self._paths = paths
        self._hub = hub
        self.mode = mode
        self.agent: Any = None
        self.chat = chat          # web.chat.ChatView: what the page shows of the conversation
        self.chat_send: Callable[[str], Any] | None = None  # bound by run_chat (kernel or local)
        self.chat_stop: Callable[[], Any] | None = None
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
                    "state": self._hub.state, "ready": agent is not None},
            "context": await self._section(lambda: agent.stats() if agent else None),
            "memory": await self._section(self._memory),
            "character": await self._section(self._character_section),
            "sessions": await self._section(self._sessions_section),
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

    async def _sessions_section(self) -> dict[str, Any] | None:
        """The recent sessions, so they can be read and deleted from the page (None: still starting)."""
        if self.agent is None:
            return None
        sessions = getattr(self.agent, "sessions", None)
        if sessions is None:
            return {"enabled": False}
        items = [{"id": row.id, "title": row.title, "mode": row.mode,
                  "at": row.updated_at, "turns": row.turns, "has_summary": row.has_summary}
                 for row in sessions.store.list(limit=SESSION_PANEL_LIMIT)]
        return {"enabled": True, "current": sessions.session_id, "items": items}

    # --- the conversation (the page is a client, like the terminal) -----------------------------

    async def _chat_send(self, text: str) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise ActionError("Le message est vide.", 400)
        if len(text) > MAX_CHAT_CHARS:
            raise ActionError(f"Message trop long ({MAX_CHAT_CHARS} caractères au plus).", 400)
        if self.agent is None or self.chat_send is None:
            raise ActionError("La conversation n'est pas encore prête.", 503)
        if self._busy() or not await self.chat_send(text):
            raise ActionError("Une réponse est déjà en cours : attends la fin, ou arrête-la.", 409)
        log.info("Message envoyé depuis la page")
        return {}

    async def _chat_stop(self) -> dict[str, Any]:
        if self.chat_stop is None:
            raise ActionError("La conversation n'est pas encore prête.", 503)
        message = await self.chat_stop()
        log.info("Arrêt demandé depuis la page")
        return {"message": message}

    # --- edits requested from the page ---------------------------------------------------------

    def actions(self) -> dict[str, Callable[..., Any]]:
        return {"memory_update": self._memory_update, "memory_delete": self._memory_delete,
                "journal_update": self._journal_update, "journal_delete": self._journal_delete,
                "journal_clear": self._journal_clear, "impression_update": self._impression_update,
                "relation_reset": self._relation_reset, "session_forget": self._session_forget,
                "session_resume": self._session_resume, "chat_send": self._chat_send,
                "chat_stop": self._chat_stop}

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

    def _recorder(self) -> Any:
        sessions = getattr(self.agent, "sessions", None)
        if sessions is None:
            raise ActionError("Les sessions sont désactivées ou indisponibles.", 503)
        return sessions

    def _busy(self) -> bool:
        """A streaming turn owns the history: it must not be cleared or swapped under its feet."""
        return bool(getattr(self.agent, "turn_running", False))

    async def _session_forget(self, session_id: str) -> None:
        sessions = self._recorder()
        if sessions.is_current(session_id) and self._busy():
            raise ActionError("Une réponse est en cours : réessaie quand elle est finie.", 409)
        if not sessions.store.forget(str(session_id)):
            raise ActionError("Cette session n'existe plus.", 404)
        if sessions.is_current(session_id):
            # The conversation being continued cannot outlive its file: start a fresh one.
            self.agent.clear()
        log.info("Session supprimée depuis la page")
        self._hub.activity("sessions")

    async def _session_resume(self, session_id: str) -> dict[str, Any]:
        """Load a stored conversation into the running agent (the terminal continues it)."""
        self._recorder()
        if self._busy():
            raise ActionError("Une réponse est en cours : réessaie quand elle est finie.", 409)
        try:
            message = self.agent.resume_session(str(session_id))
        except SessionsError as exc:
            raise ActionError(str(exc), 404) from exc
        log.info("Session reprise depuis la page")
        self._hub.activity("sessions")
        return {"message": message}

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
