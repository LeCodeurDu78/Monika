"""ReAct agent loop: think, call tools, observe, repeat, then answer."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol

from agent.context import (
    ContextManager,
    ToolOutputStore,
    estimate_tokens,
    message_tokens,
)
from agent.filler import FillerState
from agent.llm import Completion, LLMError, TextDelta
from agent.loop_detect import LoopDetector
from agent.prompt import build_system_prompt
from agent.sessions import SessionsError, trim_history
from character.models import AvatarCue
from memory.capture import MemoryCapture
from memory.notes import NoteMemory
from monika.config import Settings
from monika.i18n import tr
from security.untrusted import wrap_tool_output
from tools.registry import ToolRegistry

if TYPE_CHECKING:
    from agent.recipes import RecipeKeeper, RecipeStore
    from agent.reflection import BetweenTurns
    from agent.sessions import SessionRecorder
    from agent.surface import SurfaceReflex
    from character.service import CharacterService

log = logging.getLogger(__name__)


class _LLM(Protocol):
    def stream(self, messages: list[dict[str, Any]], tools: Any = None) -> Any: ...


@dataclass(frozen=True)
class ToolStart:
    """A tool call is about to run."""

    name: str
    arguments: str
    slow: bool


@dataclass(frozen=True)
class ToolEnd:
    """A tool call finished (errors included)."""

    name: str
    ok: bool
    duration_ms: float
    preview: str


@dataclass(frozen=True)
class Filler:
    """A waiting phrase to say out loud because a slow tool is still running."""

    text: str


@dataclass(frozen=True)
class Notice:
    """A message from Monika's machinery to the user, not from the model."""

    text: str


@dataclass(frozen=True)
class TurnEnd:
    """Always the last event of a turn."""

    reason: Literal["done", "limit", "loop", "empty"]
    steps: int


AgentEvent = TextDelta | ToolStart | ToolEnd | Filler | Notice | AvatarCue | TurnEnd


class Agent:
    """Owns the conversation history and runs one user turn at a time."""

    def __init__(
        self,
        settings: Settings,
        llm: _LLM,
        registry: ToolRegistry,
        store: ToolOutputStore | None = None,
        rng: random.Random | None = None,
        voice: bool = False,
        memory: NoteMemory | None = None,
        capture: MemoryCapture | None = None,
        rag: Any | None = None,
        character: CharacterService | None = None,
        sessions: SessionRecorder | None = None,
        recipes: RecipeStore | None = None,
        recipe_keeper: RecipeKeeper | None = None,
        morning: Any | None = None,
        surface: SurfaceReflex | None = None,
        reflection: BetweenTurns | None = None,
    ) -> None:
        self._s = settings
        self._llm = llm
        self.registry = registry
        self._store = store
        self._rng = rng
        self._voice = voice
        self.context = ContextManager(settings, llm)
        self.memory = memory
        self.capture = capture
        self.rag = rag
        self.character = character
        self._memory_block = ""
        self._character_block = ""
        self._journal_block = ""
        self._morning_block = ""  # 1-2 thoughts written by the nightly review, this day only
        self.sessions = (
            sessions  # Persistent conversation log; None when the store could not open
        )
        self.recipes = recipes  # Learned procedures, injected as suggestions
        self.recipes_keeper = recipe_keeper  # Writes a draft after a long, clean turn
        self.morning = morning  # MorningNotes: read once at the first message of the day
        self.surface = surface  # Quick first line, before any tool runs
        self.reflection = reflection  # Advice prepared between turns
        self._spoken = ""  # The surface line, included in the final reply
        self._reflection_block = ""
        self._recipe_block = ""
        self._recipes_used: list[str] = []
        self.turn_running = (
            False  # True while a turn streams: the history must not be swapped then
        )
        self._memory_warned = False
        self.history: list[dict[str, Any]] = []
        self.memory_ok: bool | None = (
            None  # Last memory recall: True, False, or None when memory is off
        )

    def clear(self) -> None:
        self.history.clear()
        self.context.clear()
        if self.sessions is not None:
            self.sessions.close_session()  # The next turn opens a new session on disk

    def resume_session(self, session_id: str) -> str:
        """Replace the conversation with a stored one; returns the line to show to the user."""
        if self.sessions is None:
            raise SessionsError(tr("loop.session_disabled"))
        if self.turn_running:
            raise SessionsError(tr("loop.session_busy"))
        loaded = self.sessions.store.load(session_id)
        if loaded is None:
            raise SessionsError(tr("loop.session_missing", id=session_id))
        now = datetime.now().astimezone()
        available = (
            self._s.context_budget_tokens
            - self._base_tokens(now)
            - estimate_tokens(loaded.summary)
        )
        history = list(loaded.history)
        dropped = trim_history(history, max(4_000, available))
        self.history = history
        self.context.summary = loaded.summary
        self.sessions.adopt(loaded.id)
        parts = [
            tr(
                "loop.session_resumed",
                id=loaded.id,
                turns=loaded.turns,
                messages=len(history),
            )
        ]
        if loaded.summary:
            parts.append(tr("loop.session_summary"))
        if dropped:
            parts.append(tr("loop.session_dropped", count=dropped))
        return " ; ".join(parts) + "."

    def _system_text(self, now: datetime, with_summary: bool = True) -> str:
        return build_system_prompt(
            now=now,
            has_tools=bool(self.registry.tools()),
            has_result_reader=self.registry.get("lire_resultat") is not None,
            summary=self.context.summary if with_summary else "",
            user_name=self._s.user_name,
            voice=self._voice,
            memory_block=self._memory_block,
            persona_block=self._s.persona_text,
            has_documents=self.registry.get("documents_chercher") is not None,
            character_block=self._character_block,
            journal_block=self._journal_block,
            morning_block=self._morning_block,
            recipe_block=self._recipe_block,
            already_said=self._spoken,
            reflection_block=self._reflection_block,
        )

    def _base_tokens(self, now: datetime) -> int:
        """Tokens that every request pays: system prompt (without summary) and the tool schemas sent."""
        schemas = self.registry.schemas()
        return estimate_tokens(self._system_text(now, with_summary=False)) + (
            estimate_tokens(json.dumps(schemas, ensure_ascii=False)) if schemas else 0
        )

    def tool_costs(self) -> list[tuple[str, str, int]]:
        """(name, source, estimated tokens) of every tool schema, heaviest first."""
        costs = [
            (
                t.name,
                t.source,
                estimate_tokens(json.dumps(t.schema(), ensure_ascii=False)),
            )
            for t in self.registry.tools()
        ]
        return sorted(costs, key=lambda c: c[2], reverse=True)

    def stats(self) -> dict[str, int]:
        """Estimated context usage, for /contexte."""
        now = datetime.now().astimezone()
        tools = self.tool_costs()
        return {
            "tokens": self.context.total(self._base_tokens(now), self.history),
            "budget": self._s.context_budget_tokens,
            "window": self._s.context_window_tokens,
            "messages": len(self.history),
            "summary_tokens": estimate_tokens(self.context.summary),
            "prompt_tokens": estimate_tokens(
                self._system_text(now, with_summary=False)
            ),
            "tools_tokens": sum(c[2] for c in tools),
            "tools_count": len(tools),
            "history_tokens": sum(message_tokens(m) for m in self.history),
        }

    async def run_turn(
        self, user_text: str, *, auto: bool = False
    ) -> AsyncIterator[AgentEvent]:
        """Stream events for one user message; the last one is always TurnEnd.

        `auto` marks a turn Monika started herself (briefing, scheduled reminder): its text is a
        prompt she prepared, not the user's words, so it is neither remembered nor turned into a
        recipe.
        """
        start = len(self.history)
        self.history.append({"role": "user", "content": user_text})
        self.turn_running = True
        if self.sessions is not None:
            self.sessions.begin(user_text)
        now = datetime.now().astimezone()
        self.context.new_turn()
        self._memory_block = ""
        self._character_block = ""
        self._journal_block = (
            self.character.journal_block() if self.character is not None else ""
        )
        self._morning_block = ""
        if self.morning is not None:
            self._morning_block = self.morning.take()  # once per day, then consumed
        self._spoken = ""
        self._reflection_block = (
            self.reflection.take() if self.reflection is not None else ""
        )
        self._recipe_block, self._recipes_used = "", []
        if self.recipes is not None:
            block, self._recipes_used = self.recipes.prompt_block(
                user_text, self._s.recipes_inject_max
            )
            self._recipe_block = "\n\n".join(
                part for part in (block, self.recipes.pending_text()) if part
            )
        tool_calls = 0
        tool_errors = 0
        reply_text = ""
        turn = (
            self.character.start_turn(user_text) if self.character is not None else None
        )
        if turn is not None:
            turn.begin()  # Reads the tone while the memory recall runs
        fillers = FillerState(self._s.filler_max_per_turn, self._rng)
        detector = LoopDetector(self._s.loop_warn_after, self._s.loop_stop_after)
        actions: list[str] = []
        steps, reason = 0, "limit"
        finished = (
            False  # Once the turn is complete, closing the generator must not roll back
        )
        try:
            # Surface first, tools after: a small model says one line while nothing is verified,
            # so its prompt forbids claiming any result. A absent or slow surface changes nothing.
            if self.surface is not None and not auto:
                line = await self.surface.line(user_text, voice=self._voice)
                if line:
                    self._spoken = line
                    yield TextDelta(line + "\n\n")
            self._memory_block, warning = await self._recall(user_text)
            if warning:
                yield Notice(warning)
            if turn is not None:
                for cue in await turn.ready():
                    yield cue
                self._character_block = turn.prompt_block()
            while steps < self._s.max_steps:
                steps += 1
                fit = await self.context.fit(
                    self.history, start, self._base_tokens(now)
                )
                start -= fit.removed
                for text in fit.notices:
                    yield Notice(text)
                system = {"role": "system", "content": self._system_text(now)}
                completion: Completion | None = None
                async for item in self._llm.stream(
                    [system, *self.history], self.registry.schemas() or None
                ):
                    if isinstance(item, TextDelta):
                        yield item
                        if turn is not None:
                            for cue in turn.feed(item.text):
                                yield cue
                    else:
                        completion = item
                assert completion is not None
                if not completion.tool_calls:
                    if completion.content.strip():
                        reply_text = self._with_spoken(completion.content)
                        message = completion.as_message()
                        message["content"] = reply_text
                        self.history.append(message)
                        self._submit_capture(user_text, reply_text, auto=auto)
                        finished = True
                        if turn is not None:
                            turn.commit(reply_text)
                            for cue in turn.flush():
                                yield cue
                        yield TurnEnd("done", steps)
                    else:
                        if not actions:
                            del self.history[start:]
                        yield Notice(tr("loop.empty_answer"))
                        finished = True
                        yield TurnEnd("empty", steps)
                    return

                self.history.append(completion.as_message())
                stop = False
                for call in completion.tool_calls:
                    if stop:
                        self.history.append(self._tool_message(call.id, tr("loop.skipped")))
                        continue
                    tool = self.registry.get(call.name)
                    slow = bool(tool and tool.slow)
                    yield ToolStart(call.name, call.arguments, slow)
                    task = asyncio.create_task(
                        self.registry.call(call.name, call.arguments)
                    )
                    wait: float | None = self._s.filler_delay_s if slow else None
                    try:
                        while True:
                            done, _ = await asyncio.wait({task}, timeout=wait)
                            if done:
                                break
                            phrase = fillers.next_phrase(
                                tool.filler_variants if tool else None
                            )
                            if phrase:
                                yield Filler(phrase)
                            wait = self._s.filler_repeat_s
                    except asyncio.CancelledError:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        raise
                    finally:
                        if not task.done():
                            task.cancel()
                    result = task.result()
                    tool_calls += 1
                    if result.is_error:
                        tool_errors += 1
                    preview = " ".join(result.text.split())[:200]
                    yield ToolEnd(
                        call.name, not result.is_error, result.duration_ms, preview
                    )
                    if self.sessions is not None:
                        self.sessions.tool(
                            call.name,
                            call.arguments,
                            not result.is_error,
                            result.duration_ms,
                            result.text,
                        )
                    actions.append(
                        f"{call.name}({self._short(call.arguments)}) → "
                        f"{'erreur' if result.is_error else 'ok'}"
                    )
                    shown = (
                        self._store.limit(call.name, result.text)
                        if self._store
                        else result.text
                    )
                    content = wrap_tool_output(
                        call.name, shown, result.is_error, tool.source if tool else ""
                    )
                    verdict = detector.record(
                        call.name, self._args_key(call.arguments), result.text
                    )
                    if verdict == "warn":
                        content += "\n" + tr("loop.warning")
                    elif verdict == "stop":
                        content += "\n" + tr("loop.stopped")
                        stop = True
                    self.history.append(self._tool_message(call.id, content))
                if stop:
                    reason = "loop"
                    break
            else:
                reason = "limit"

            yield Notice(
                tr("loop.limit_notice")
                if reason == "limit"
                else tr("loop.loop_notice")
            )
            system = {"role": "system", "content": self._system_text(now)}
            bilan = {
                "role": "user",
                "content": tr(
                    "loop.bilan_prompt",
                    why=tr("loop.bilan_limit" if reason == "limit" else "loop.bilan_loop"),
                ),
            }
            final: Completion | None = None
            async for item in self._llm.stream([system, *self.history, bilan], None):
                if isinstance(item, TextDelta):
                    yield item
                else:
                    final = item
            reply_text = self._with_spoken(
                (final.content if final else "") or tr("loop.empty_bilan")
            )
            self.history.append({"role": "assistant", "content": reply_text})
            self._submit_capture(user_text, reply_text, auto=auto)
            finished = True
            if turn is not None:
                turn.commit(self.history[-1]["content"])
            yield TurnEnd(reason, steps)
        except (LLMError, asyncio.CancelledError, GeneratorExit) as exc:
            if not finished:
                self._rollback(start, user_text, actions, exc)
            raise
        finally:
            if turn is not None:
                turn.close()  # An aborted turn leaves no trace: the background tone reading is cancelled
            if (
                self.sessions is not None
            ):  # Runs on every exit: done, step limit, error, Ctrl+C, close
                self.sessions.finish(
                    self.history, self.context.summary, start, complete=finished
                )
            self._finish_reflection(finished, user_text, reply_text, auto=auto)
            self._finish_recipes(
                finished,
                tool_errors,
                tool_calls,
                user_text,
                actions,
                reply_text,
                auto=auto,
            )
            self.turn_running = False

    async def _recall(self, query: str) -> tuple[str, str | None]:
        """Fetch relevant memories for this message; a failure never blocks the turn."""
        if self.memory is None:
            return "", None
        reason = ""
        try:
            async with asyncio.timeout(self._s.memory_recall_timeout_s):
                block = await self.memory.recall(query)
            self.memory_ok = True
            return block, None
        except TimeoutError:
            reason = tr("loop.memory_timeout", seconds=self._s.memory_recall_timeout_s)
        except Exception as exc:  # Any backend failure: continue without memory
            reason = f"{type(exc).__name__}: {exc}"[:200]
        self.memory_ok = False
        if self._memory_warned:
            return "", None
        self._memory_warned = True
        return "", tr("loop.memory_unavailable", reason=reason)

    def _with_spoken(self, text: str) -> str:
        """The full assistant turn: the surface line, then the deep answer."""
        text = text.strip()
        if not self._spoken:
            return text
        return f"{self._spoken}\n\n{text}" if text else self._spoken

    def _finish_reflection(
        self, finished: bool, user_text: str, reply: str, auto: bool = False
    ) -> None:
        """Hand a clean turn to the reflection queue; automatic turns are skipped (no loop)."""
        if not finished or auto or not reply.strip() or self.reflection is None:
            return
        self.reflection.submit(user_text, reply)

    def _submit_capture(self, user_text: str, reply: str, auto: bool = False) -> None:
        """Hand the finished exchange to the background capture (never tool output)."""
        if self.capture is not None and not auto and reply and reply.strip():
            self.capture.submit(user_text, reply)

    def _finish_recipes(
        self,
        finished: bool,
        tool_errors: int,
        tool_calls: int,
        user_text: str,
        actions: list[str],
        reply: str,
        auto: bool = False,
    ) -> None:
        """Count whether the suggested recipes worked, then propose one after a clean turn."""
        if self.recipes is None:
            return
        try:
            ok = finished and tool_errors == 0
            if self._recipes_used:
                self.recipes.record_use(self._recipes_used, ok)
                self._recipes_used = []
            keeper = self.recipes_keeper
            if (
                ok
                and not auto
                and reply.strip()
                and tool_calls >= self._s.recipes_min_tools
                and keeper is not None
            ):
                keeper.consider(user_text, actions, reply)
        except Exception as exc:  # noqa: BLE001 - optional layer: never break a turn
            log.warning(
                "recettes : suivi du tour impossible (%s: %s)",
                type(exc).__name__,
                exc,
            )

    async def aclose(self) -> int:
        """Finish pending background captures and journal notes; return how many could not be saved."""
        left = await self.capture.drain() if self.capture is not None else 0
        if self.reflection is not None:
            left += await self.reflection.drain()
        if self.recipes_keeper is not None:
            left += await self.recipes_keeper.drain()
        if self.character is not None:
            left += await self.character.aclose()
        return left

    def _rollback(
        self, start: int, user_text: str, actions: list[str], exc: BaseException
    ) -> None:
        """Drop the unfinished turn but remember what was already done."""
        del self.history[start:]
        if not actions:
            return
        why = (
            tr("loop.rollback_error", actions="; ".join(actions[-10:]))
            if isinstance(exc, LLMError)
            else tr("loop.rollback_interrupted", actions="; ".join(actions[-10:]))
        )
        self.history += [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": why},
        ]

    @staticmethod
    def _tool_message(call_id: str, content: str) -> dict[str, Any]:
        return {"role": "tool", "tool_call_id": call_id, "content": content}

    @staticmethod
    def _args_key(raw: str) -> str:
        try:
            return json.dumps(json.loads(raw), sort_keys=True, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            return raw

    @staticmethod
    def _short(raw: str, limit: int = 80) -> str:
        flat = " ".join(str(raw).split())
        return flat if len(flat) <= limit else flat[: limit - 1] + "…"
