"""ReAct agent loop: think, call tools, observe, repeat, then answer."""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol

from agent.context import ContextManager, ToolOutputStore, estimate_tokens, message_tokens
from agent.director import TurnPlan, plan as plan_turn
from agent.filler import FillerState
from agent.llm import Completion, LLMError, TextDelta
from agent.loop_detect import LoopDetector
from agent.prompt import build_system_prompt, sensitive_block
from agent.sessions import SessionsError, trim_history
from character.cues import sensitive_cue
from character.models import AvatarCue
from monika.config import Settings
from memory.capture import MemoryCapture
from memory.fast import FastMemory, format_for_prompt
from security.untrusted import wrap_tool_output
from security.words import WordBank
from tools.registry import ToolRegistry

if TYPE_CHECKING:
    from agent.sessions import SessionRecorder
    from character.service import CharacterService

LOOP_WARNING = (
    "[Avertissement système : tu as déjà fait exactement cet appel avec le même résultat. "
    "Change d'approche (autres arguments, autre outil) ou conclus avec ce que tu as.]"
)
LOOP_STOPPED = "[Avertissement système : boucle détectée, la tâche est arrêtée pour faire un bilan.]"
SKIPPED = "[Appel non exécuté : la tâche a été arrêtée avant.]"
BILAN_REQUEST = {
    "limit": "tu as atteint la limite d'étapes autorisées pour cette tâche",
    "loop": "tu tournes en boucle sur les mêmes appels",
}
BILAN_PROMPT = (
    "[Consigne interne de Monika, ne vient pas de l'utilisateur] {why}. N'appelle plus d'outil. "
    "Fais maintenant un bilan honnête et concis : ce qui est terminé ET vérifié, ce qui ne l'est "
    "pas, ce qui reste à faire, et ce que tu proposes ensuite."
)


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

    def __init__(self, settings: Settings, llm: _LLM, registry: ToolRegistry,
                 store: ToolOutputStore | None = None, rng: random.Random | None = None,
                 voice: bool = False, memory: FastMemory | None = None,
                 capture: MemoryCapture | None = None, rag: Any | None = None,
                 character: CharacterService | None = None, words: WordBank | None = None,
                 sessions: SessionRecorder | None = None) -> None:
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
        self._hint_block = ""  # The director's note, this turn only (never kept in the history)
        self._tone_block = ""  # Sensitive-word tone for this message, this turn only
        self.words = words  # The user's word bank; the settings page swaps it (hot reload)
        self.sessions = sessions  # Persistent conversation log; None when SESSIONS_ENABLED is false
        self._memory_warned = False
        self.history: list[dict[str, Any]] = []
        self.last_plan: TurnPlan | None = None  # What the director decided for the last turn (/contexte)
        self.memory_ok: bool | None = None  # Last memory recall: True, False, or None when memory is off

    def clear(self) -> None:
        self.history.clear()
        self.context.clear()
        if self.sessions is not None:
            self.sessions.close_session()  # The next turn opens a new session on disk

    def resume_session(self, session_id: str) -> str:
        """Replace the conversation with a stored one; returns the line to show to the user."""
        if self.sessions is None:
            raise SessionsError("Les sessions sont désactivées (SESSIONS_ENABLED=false).")
        loaded = self.sessions.store.load(session_id)
        if loaded is None:
            raise SessionsError(f"Aucune session avec l'id {session_id}.")
        now = datetime.now().astimezone()
        available = (self._s.context_budget_tokens - self._base_tokens(now)
                     - estimate_tokens(loaded.summary))
        history = list(loaded.history)
        dropped = trim_history(history, max(4_000, available))
        self.history = history
        self.context.summary = loaded.summary
        self.sessions.adopt(loaded.id)
        parts = [f"Session {loaded.id} reprise : {loaded.turns} tour(s), {len(history)} message(s)"]
        if loaded.summary:
            parts.append("avec son résumé")
        if dropped:
            parts.append(f"{dropped} ancien(s) message(s) écarté(s) pour tenir dans le budget de contexte")
        return " ; ".join(parts) + "."

    def _system_text(self, now: datetime, with_summary: bool = True) -> str:
        return build_system_prompt(
            now=now, has_tools=bool(self.registry.tools()),
            has_result_reader=self.registry.get("lire_resultat") is not None,
            summary=self.context.summary if with_summary else "",
            user_name=self._s.user_name, voice=self._voice, memory_block=self._memory_block,
            persona_block=self._s.persona_text,
            has_documents=self.registry.get("documents_chercher") is not None,
            character_block=self._character_block, journal_block=self._journal_block,
            hint_block=self._hint_block, tone_block=self._tone_block)

    def _base_tokens(self, now: datetime, tools: set[str] | None = None) -> int:
        """Tokens that every request pays: system prompt (without summary) and the tool schemas sent."""
        schemas = self.registry.schemas(tools)
        return estimate_tokens(self._system_text(now, with_summary=False)) + (
            estimate_tokens(json.dumps(schemas, ensure_ascii=False)) if schemas else 0)

    def tool_costs(self) -> list[tuple[str, str, int]]:
        """(name, source, estimated tokens) of every tool schema, heaviest first."""
        costs = [(t.name, t.source, estimate_tokens(json.dumps(t.schema(), ensure_ascii=False)))
                 for t in self.registry.tools()]
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
            "prompt_tokens": estimate_tokens(self._system_text(now, with_summary=False)),
            "tools_tokens": sum(c[2] for c in tools),
            "tools_count": len(tools),
            "history_tokens": sum(message_tokens(m) for m in self.history),
        }

    async def run_turn(self, user_text: str) -> AsyncIterator[AgentEvent]:
        """Stream events for one user message; the last one is always TurnEnd."""
        start = len(self.history)
        self.history.append({"role": "user", "content": user_text})
        if self.sessions is not None:
            self.sessions.begin(user_text)
        now = datetime.now().astimezone()
        self.context.new_turn()
        self._memory_block = ""
        self._character_block = ""
        self._journal_block = self.character.journal_block() if self.character is not None else ""
        self._hint_block = ""
        self._tone_block = ""
        self.last_plan = None
        turn = self.character.start_turn(user_text) if self.character is not None else None
        if turn is not None:
            turn.begin()  # Reads the tone while the memory recall runs
        tools = None  # Subset of tool schemas for this turn (director); None = every tool
        plan_task = self._start_plan(user_text)
        fillers = FillerState(self._s.filler_max_per_turn, self._rng)
        detector = LoopDetector(self._s.loop_warn_after, self._s.loop_stop_after)
        actions: list[str] = []
        steps, reason = 0, "limit"
        finished = False  # Once the turn is complete, closing the generator must not roll back
        try:
            tier = self._sensitive_tier(user_text)
            if tier is not None:
                self._tone_block = sensitive_block(tier)
                if tier in ("bad", "severe"):
                    # Level only: the word itself never reaches the trail, nor the page
                    self.registry.audit_event("sensitive", {"tier": tier})
                cue = sensitive_cue(tier)
                if cue is not None:
                    yield cue  # Before anything else: the reaction is immediate
            self._memory_block, warning = await self._recall(user_text)
            if warning:
                yield Notice(warning)
            if turn is not None:
                for cue in await turn.ready():
                    yield cue
                self._character_block = turn.prompt_block()
            if plan_task is not None:
                self.last_plan = await plan_task  # Runs in parallel with the memory recall and the tone reading
                if self.last_plan is not None:
                    tools = self.last_plan.tools
                    self._hint_block = self.last_plan.hint
            while steps < self._s.max_steps:
                steps += 1
                fit = await self.context.fit(self.history, start, self._base_tokens(now, tools))
                start -= fit.removed
                for text in fit.notices:
                    yield Notice(text)
                system = {"role": "system", "content": self._system_text(now)}
                completion: Completion | None = None
                async for item in self._llm.stream([system, *self.history],
                                                   self.registry.schemas(tools) or None):
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
                        self.history.append(completion.as_message())
                        self._submit_capture(user_text, completion.content)
                        finished = True
                        if turn is not None:
                            turn.commit(completion.content)
                            for cue in turn.flush():
                                yield cue
                        yield TurnEnd("done", steps)
                    else:
                        if not actions:
                            del self.history[start:]
                        yield Notice("Réponse vide du modèle.")
                        finished = True
                        yield TurnEnd("empty", steps)
                    return

                self.history.append(completion.as_message())
                stop = False
                for call in completion.tool_calls:
                    if stop:
                        self.history.append(self._tool_message(call.id, SKIPPED))
                        continue
                    tool = self.registry.get(call.name)
                    slow = bool(tool and tool.slow)
                    yield ToolStart(call.name, call.arguments, slow)
                    task = asyncio.create_task(self.registry.call(call.name, call.arguments))
                    wait: float | None = self._s.filler_delay_s if slow else None
                    try:
                        while True:
                            done, _ = await asyncio.wait({task}, timeout=wait)
                            if done:
                                break
                            phrase = fillers.next_phrase(tool.filler_variants if tool else None)
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
                    preview = " ".join(result.text.split())[:200]
                    yield ToolEnd(call.name, not result.is_error, result.duration_ms, preview)
                    if self.sessions is not None:
                        self.sessions.tool(call.name, call.arguments, not result.is_error,
                                           result.duration_ms, result.text)
                    actions.append(f"{call.name}({self._short(call.arguments)}) → "
                                   f"{'erreur' if result.is_error else 'ok'}")
                    shown = self._store.limit(call.name, result.text) if self._store else result.text
                    content = wrap_tool_output(call.name, shown, result.is_error,
                                               tool.source if tool else "")
                    verdict = detector.record(call.name, self._args_key(call.arguments), result.text)
                    if verdict == "warn":
                        content += "\n" + LOOP_WARNING
                    elif verdict == "stop":
                        content += "\n" + LOOP_STOPPED
                        stop = True
                    self.history.append(self._tool_message(call.id, content))
                if stop:
                    reason = "loop"
                    break
            else:
                reason = "limit"

            yield Notice("Limite d'étapes atteinte : je fais un bilan." if reason == "limit"
                         else "Boucle détectée : je m'arrête et je fais un bilan.")
            system = {"role": "system", "content": self._system_text(now)}
            bilan = {"role": "user", "content": BILAN_PROMPT.format(why=BILAN_REQUEST[reason])}
            final: Completion | None = None
            async for item in self._llm.stream([system, *self.history, bilan], None):
                if isinstance(item, TextDelta):
                    yield item
                else:
                    final = item
            self.history.append({"role": "assistant",
                                 "content": (final.content if final else "") or "(bilan vide)"})
            self._submit_capture(user_text, self.history[-1]["content"])
            finished = True
            if turn is not None:
                turn.commit(self.history[-1]["content"])
            yield TurnEnd(reason, steps)
        except (LLMError, asyncio.CancelledError, GeneratorExit) as exc:
            if not finished:
                self._rollback(start, user_text, actions, exc)
            raise
        finally:
            self._hint_block = ""
            self._tone_block = ""
            if plan_task is not None and not plan_task.done():
                plan_task.cancel()
            if turn is not None:
                turn.close()  # An aborted turn leaves no trace: the background tone reading is cancelled
            if self.sessions is not None:  # Runs on every exit: done, step limit, error, Ctrl+C, close
                self.sessions.finish(self.history, self.context.summary, start, complete=finished)

    def _sensitive_tier(self, text: str) -> str | None:
        """The level this message hits (or None). Deterministic, no model call, never buys a turn."""
        if self.words is None or not self._s.sensitive_words_enabled:
            return None
        try:
            return self.words.match(text)
        except Exception:  # noqa: BLE001 - a broken bank must never break a turn
            return None

    def _start_plan(self, user_text: str) -> asyncio.Task[TurnPlan | None] | None:
        """Start the director in the background: None when the role is off or there is nothing to plan."""
        if not self._s.director_enabled:
            return None
        known = {tool.name for tool in self.registry.tools()}
        if not known:
            return None
        return asyncio.get_running_loop().create_task(
            plan_turn(user_text, known, self._llm, self._s.director_timeout_s, self._s.director_llm_model))

    async def _recall(self, query: str) -> tuple[str, str | None]:
        """Fetch relevant memories for this message; a failure never blocks the turn."""
        if self.memory is None:
            return "", None
        reason = ""
        try:
            async with asyncio.timeout(self._s.memory_recall_timeout_s):
                items = await self.memory.recall(query)
            self.memory_ok = True
            return format_for_prompt(items), None
        except TimeoutError:
            reason = f"délai de {self._s.memory_recall_timeout_s:g} s dépassé"
        except Exception as exc:  # Any backend failure: continue without memory
            reason = f"{type(exc).__name__}: {exc}"[:200]
        self.memory_ok = False
        if self._memory_warned:
            return "", None
        self._memory_warned = True
        return "", f"Mémoire rapide indisponible ({reason}) : je continue sans."

    def _submit_capture(self, user_text: str, reply: str) -> None:
        """Hand the finished exchange to the background capture (never tool output)."""
        if self.capture is not None and reply and reply.strip():
            self.capture.submit(user_text, reply)

    async def aclose(self) -> int:
        """Finish pending background captures and journal notes; return how many could not be saved."""
        left = await self.capture.drain() if self.capture is not None else 0
        if self.character is not None:
            left += await self.character.aclose()
        return left

    def _rollback(self, start: int, user_text: str, actions: list[str], exc: BaseException) -> None:
        """Drop the unfinished turn but remember what was already done."""
        del self.history[start:]
        if not actions:
            return
        why = "par une erreur du LLM" if isinstance(exc, LLMError) else "avant la fin"
        done = "; ".join(actions[-10:])
        self.history += [
            {"role": "user", "content": user_text},
            {"role": "assistant",
             "content": f"[Tour interrompu {why}. Actions déjà exécutées : {done}]"},
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
