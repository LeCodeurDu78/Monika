"""CharacterService: the single door between the agent loop and Monika's inner life.

One `CharacterTurn` per user message:
    turn = service.start_turn(text)        # starts reading the tone in the background
    cues = await turn.ready()              # the reaction (awaited after the memory recall, which runs in parallel)
    system prompt += turn.prompt_block()   # relationship and mood for this reply
    for each streamed text piece: cues = turn.feed(piece)
    turn.commit()                          # ONLY when the turn completed: emotions and favorability are saved
    turn.close()                           # always (cancels the background reading if the turn was aborted)

No method raises: this layer is optional, so a failure is logged and the conversation carries on without it.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from character.appraisal import Appraiser, Complete, LlmAppraiser
from character.core import CharacterCore, TurnOutcome
from character.cues import ReplyCues, level_index, reaction_cues
from character.journal import Journal
from character.journal_keeper import JournalKeeper
from character.models import Appraisal, AvatarCue
from character.tone import render_block

log = logging.getLogger(__name__)
T = TypeVar("T")
Clock = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now().astimezone()


def _safe(default: Callable[[], Any]) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator: never let an exception of the character layer reach the conversation."""
    def wrap(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def inner(*args: Any, **kwargs: Any) -> T:
            try:
                return func(*args, **kwargs)
            except Exception as err:  # noqa: BLE001 - deliberate: optional layer
                log.warning("character.%s failed: %s: %s", func.__name__, type(err).__name__, err)
                return default()
        return inner
    return wrap


class CharacterService:
    def __init__(self, core: CharacterCore, appraiser: Appraiser, clock: Clock = _now,
                 monotonic: Callable[[], float] = time.monotonic, journal: Journal | None = None,
                 keeper: JournalKeeper | None = None, journal_max_chars: int = 1800) -> None:
        self.core = core
        self._appraiser = appraiser
        self._clock = clock
        self._monotonic = monotonic
        self.journal = journal
        self._keeper = keeper
        self._journal_max_chars = journal_max_chars
        self.on_change: Callable[[dict[str, Any]], None] | None = None   # e.g. hub.set_character

    def start_turn(self, user_text: str) -> CharacterTurn:
        return CharacterTurn(self, user_text)

    @_safe(dict)
    def snapshot(self) -> dict[str, Any]:
        return self.core.snapshot(self._clock())

    @_safe(lambda: None)
    def reset_relationship(self) -> None:
        self.core.reset_relationship()
        self._notify()

    def _notify(self) -> None:
        if self.on_change is not None:
            self.on_change(self.core.snapshot(self._clock()))

    # ------------------------------------------------------------------------------------ journal
    @_safe(str)
    def journal_block(self) -> str:
        """The journal section of the system prompt ('' when there is no journal or nothing in it yet)."""
        if self.journal is None:
            return ""
        return self.journal.block(self._clock(), self._journal_max_chars)

    @_safe(lambda: None)
    def journal_entries(self) -> dict[str, Any] | None:
        """What the user may read: impression, threads and every note with its id (None when journaling is off)."""
        if self.journal is None:
            return None
        return self.journal.data.to_dict()

    @_safe(lambda: False)
    def journal_delete(self, note_id: str) -> bool:
        return self.journal is not None and self.journal.delete_note(note_id)

    @_safe(lambda: False)
    def journal_edit(self, note_id: str, text: str) -> bool:
        return self.journal is not None and self.journal.edit_note(note_id, text)

    @_safe(lambda: False)
    def journal_edit_impression(self, text: str) -> bool:
        if self.journal is None:
            return False
        self.journal.replace_impression(text)
        return True

    @_safe(lambda: None)
    def journal_clear(self) -> None:
        if self.journal is not None:
            self.journal.clear()

    @_safe(lambda: None)
    def journal_flush(self) -> None:
        """Session end: queue a note about the last exchanges (written in the background)."""
        if self._keeper is not None:
            self._keeper.flush()

    @property
    def journal_writing(self) -> bool:
        """True while notes are being written (shutdown tells the user)."""
        return self._keeper is not None and self._keeper.pending > 0

    async def aclose(self) -> int:
        """Shutdown: write about the last exchanges, wait for pending notes. Returns how many could not be written."""
        if self._keeper is None:
            return 0
        try:
            self._keeper.flush()
            return await self._keeper.drain()
        except Exception as err:  # noqa: BLE001
            log.warning("journal shutdown failed: %s: %s", type(err).__name__, err)
            return 0

    def _observe(self, user_text: str, reply: str, level: str) -> None:
        if self._keeper is not None and reply.strip():
            self._keeper.observe(user_text, reply, level)


class CharacterTurn:
    def __init__(self, service: CharacterService, user_text: str) -> None:
        self._service = service
        self._text = user_text
        self._task: asyncio.Task[list[AvatarCue]] | None = None
        self._appraisal: Appraisal | None = None
        self._block = ""
        self._reply: ReplyCues | None = None
        self._committed = False
        self._begin_cues: list[AvatarCue] = []

    # ------------------------------------------------------------------------------------ start
    @_safe(lambda: None)
    def begin(self) -> None:
        """Start reading the tone in the background (needs a running event loop)."""
        self._task = asyncio.get_running_loop().create_task(self._begin())

    async def _begin(self) -> list[AvatarCue]:
        s = self._service
        appraisal = await s._appraiser.appraise(self._text)
        snapshot = s.core.preview(appraisal, s._clock())
        self._appraisal = appraisal
        self._block = render_block(snapshot)
        cues = reaction_cues(appraisal, snapshot)
        self._begin_cues = cues
        self._reply = ReplyCues(level=level_index(snapshot), played={c.clip for c in cues if c.clip},
                                clock=s._monotonic)
        return cues

    async def ready(self) -> list[AvatarCue]:
        """Wait for the tone reading; [] (and no mood block) if it failed. Cancellation is not swallowed."""
        if self._task is None:
            return []
        try:
            return await self._task
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            log.warning("character appraisal failed: %s: %s", type(err).__name__, err)
            return []

    # ------------------------------------------------------------------------------------ during
    def prompt_block(self) -> str:
        return self._block

    @_safe(list)
    def feed(self, text: str) -> list[AvatarCue]:
        return self._reply.feed(text) if self._reply is not None else []

    @_safe(list)
    def flush(self) -> list[AvatarCue]:
        return self._reply.flush() if self._reply is not None else []

    # ------------------------------------------------------------------------------------ end
    @_safe(lambda: None)
    def commit(self, reply: str = "") -> TurnOutcome | None:
        """Make this turn real: emotions and favorability change and are saved, and the exchange is offered to the
        journal. At most once, never if the tone could not be read. `reply` is Monika's own final answer."""
        if self._committed or self._appraisal is None:
            return None
        self._committed = True
        outcome = self._service.core.apply(self._appraisal, self._service._clock())
        self._service._notify()
        self._service._observe(self._text, reply, outcome.level)
        return outcome

    def close(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()


# ---------------------------------------------------------------------------------------- assembly

def llm_complete(llm: Any, model: str = "", temperature: float = 0.0) -> Complete:
    """Adapt Monika's LLMClient to the (system, user) -> text function the appraiser and the journal need."""
    async def complete(system: str, user: str) -> str:
        answer = await llm.ask([{"role": "system", "content": system}, {"role": "user", "content": user}],
                               model=model or None, temperature=temperature)
        return answer.content
    return complete


def create_service(state_path: Path, complete: Complete | None = None, timeout_s: float = 2.0,
                   clock: Clock = _now, journal_path: Path | None = None, journal_complete: Complete | None = None,
                   journal_every: int = 8, journal_max_chars: int = 1800,
                   mask: Callable[[str], str] = lambda text: text) -> CharacterService:
    """Load the saved state and build the service.

    `complete=None` means lexicon-only tone reading. With a `journal_path` the journal is read and injected; new
    notes are only written when `journal_complete` (a model) is given.
    """
    core = CharacterCore.load(state_path)
    appraiser = Appraiser(LlmAppraiser(complete) if complete else None, timeout_s)
    journal = Journal.load(journal_path) if journal_path is not None else None
    keeper = (JournalKeeper(journal, journal_complete, every_turns=journal_every, mask=mask, clock=clock)
              if journal is not None and journal_complete is not None else None)
    return CharacterService(core, appraiser, clock, journal=journal, keeper=keeper, journal_max_chars=journal_max_chars)


__all__ = ["CharacterService", "CharacterTurn", "create_service", "llm_complete"]
