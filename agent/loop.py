"""ReAct agent loop: think, call tools, observe, repeat, then answer."""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from agent.context import ContextManager, ToolOutputStore, estimate_tokens
from agent.filler import FillerState
from agent.llm import Completion, LLMError, TextDelta
from agent.loop_detect import LoopDetector
from agent.prompt import build_system_prompt
from monika.config import Settings
from memory.capture import MemoryCapture
from memory.fast import FastMemory, format_for_prompt
from security.untrusted import wrap_tool_output
from tools.registry import ToolRegistry

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


AgentEvent = TextDelta | ToolStart | ToolEnd | Filler | Notice | TurnEnd


class Agent:
    """Owns the conversation history and runs one user turn at a time."""

    def __init__(self, settings: Settings, llm: _LLM, registry: ToolRegistry,
                 store: ToolOutputStore | None = None, rng: random.Random | None = None,
                 voice: bool = False, memory: FastMemory | None = None,
                 capture: MemoryCapture | None = None, rag: Any | None = None) -> None:
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
        self._memory_block = ""
        self._memory_warned = False
        self.history: list[dict[str, Any]] = []

    def clear(self) -> None:
        self.history.clear()
        self.context.clear()

    def _system_text(self, now: datetime, with_summary: bool = True) -> str:
        return build_system_prompt(
            now=now, has_tools=bool(self.registry.tools()),
            has_result_reader=self.registry.get("lire_resultat") is not None,
            summary=self.context.summary if with_summary else "",
            user_name=self._s.user_name, voice=self._voice, memory_block=self._memory_block,
            has_documents=self.registry.get("documents_chercher") is not None)

    def _base_tokens(self, now: datetime) -> int:
        """Tokens that every request pays: system prompt (without summary) and tool schemas."""
        schemas = self.registry.schemas()
        return estimate_tokens(self._system_text(now, with_summary=False)) + (
            estimate_tokens(json.dumps(schemas, ensure_ascii=False)) if schemas else 0)

    def stats(self) -> dict[str, int]:
        """Estimated context usage, for /contexte."""
        now = datetime.now().astimezone()
        return {
            "tokens": self.context.total(self._base_tokens(now), self.history),
            "budget": self._s.context_budget_tokens,
            "window": self._s.context_window_tokens,
            "messages": len(self.history),
            "summary_tokens": estimate_tokens(self.context.summary),
        }

    async def run_turn(self, user_text: str) -> AsyncIterator[AgentEvent]:
        """Stream events for one user message; the last one is always TurnEnd."""
        start = len(self.history)
        self.history.append({"role": "user", "content": user_text})
        now = datetime.now().astimezone()
        self.context.new_turn()
        self._memory_block = ""
        fillers = FillerState(self._s.filler_max_per_turn, self._rng)
        detector = LoopDetector(self._s.loop_warn_after, self._s.loop_stop_after)
        actions: list[str] = []
        steps, reason = 0, "limit"
        finished = False  # Once the turn is complete, closing the generator must not roll back
        try:
            self._memory_block, warning = await self._recall(user_text)
            if warning:
                yield Notice(warning)
            while steps < self._s.max_steps:
                steps += 1
                fit = await self.context.fit(self.history, start, self._base_tokens(now))
                start -= fit.removed
                for text in fit.notices:
                    yield Notice(text)
                system = {"role": "system", "content": self._system_text(now)}
                completion: Completion | None = None
                async for item in self._llm.stream([system, *self.history],
                                                   self.registry.schemas() or None):
                    if isinstance(item, TextDelta):
                        yield item
                    else:
                        completion = item
                assert completion is not None
                if not completion.tool_calls:
                    if completion.content.strip():
                        self.history.append(completion.as_message())
                        self._submit_capture(user_text, completion.content)
                        finished = True
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
            yield TurnEnd(reason, steps)
        except (LLMError, asyncio.CancelledError, GeneratorExit) as exc:
            if not finished:
                self._rollback(start, user_text, actions, exc)
            raise

    async def _recall(self, query: str) -> tuple[str, str | None]:
        """Fetch relevant memories for this message; a failure never blocks the turn."""
        if self.memory is None:
            return "", None
        reason = ""
        try:
            async with asyncio.timeout(self._s.memory_recall_timeout_s):
                items = await self.memory.recall(query)
            return format_for_prompt(items), None
        except TimeoutError:
            reason = f"délai de {self._s.memory_recall_timeout_s:g} s dépassé"
        except Exception as exc:  # Any backend failure: continue without memory
            reason = f"{type(exc).__name__}: {exc}"[:200]
        if self._memory_warned:
            return "", None
        self._memory_warned = True
        return "", f"Mémoire rapide indisponible ({reason}) : je continue sans."

    def _submit_capture(self, user_text: str, reply: str) -> None:
        """Hand the finished exchange to the background capture (never tool output)."""
        if self.capture is not None and reply and reply.strip():
            self.capture.submit(user_text, reply)

    async def aclose(self) -> int:
        """Finish pending background captures; return how many could not be saved."""
        return await self.capture.drain() if self.capture is not None else 0

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
