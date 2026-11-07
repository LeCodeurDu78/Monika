"""Terminal chat mode: streamed answers, visible tool steps and waiting phrases."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

from agent.llm import LLMError, TextDelta
from agent.loop import Agent, AvatarCue, Filler, Notice, ToolEnd, ToolStart, TurnEnd
from agent.recipes import RecipeError
from agent.sessions import SessionsError
from monika.i18n import command, subcommand, tr
from security.masking import Masker
from tools.registry import ToolFailure
from voice.speech_text import SentenceSplitter


def _help() -> str:
    return tr("help.full")


class _ChatOut:
    """What a /command displays: printed as usual, and shown in the page as a reply."""

    def __init__(self, chat, echo=None) -> None:
        self._chat = chat
        self._echo = echo
        self.parts: list[str] = []

    def write(self, text: str) -> None:
        self.parts.append(text)
        if self._echo is not None:
            self._echo.write(text)
        if self._chat is not None:
            self._chat.delta(text)

    def flush(self) -> None:
        if self._echo is not None:
            self._echo.flush()


class TextSession:
    """Interactive loop: read a line, run an agent turn, print its events."""

    def __init__(
        self,
        agent: Agent,
        read_line: Callable[[], Awaitable[str]],
        out=None,
        masker: Masker | None = None,
        banner: str | None = None,
        speaker=None,
        speech_max_chars: int = 200,
        hub=None,
        action_director=None,
        tasks=None,
        custom_tools=None,
        recipes=None,
        nightly=None,
        chat=None,
    ) -> None:
        self._agent = agent
        self._tasks = tasks
        self._custom_tools = custom_tools
        self._recipes_store = recipes  # agent.tasks.TaskStore (None = scheduled tasks disabled)
        self._nightly = nightly  # agent.nightly.NightlyJob (None = nightly review disabled)
        self._briefing = None  # agent.briefing.BriefingJob (branché après la session)
        self._briefing_tasks: set[asyncio.Task] = set()
        self._chat = (
            chat  # web.chat.ChatView: the same turn, mirrored in the page (optional)
        )
        self._turn_lock = (
            asyncio.Lock()
        )  # One turn at a time: a reminder waits for the answer
        self._hub = hub  # Live state for the web page (optional)
        self._action_director = (
            action_director  # Post-turn avatar chain (optional, cosmetic)
        )
        self._action_tasks: set[asyncio.Task] = set()
        self._speaker = speaker
        self._speech_max_chars = speech_max_chars
        if speaker is not None and speaker.on_error is None:
            speaker.on_error = self._voice_error
        self._read_line = read_line
        self._out = out or sys.stdout
        self._masker = masker or Masker()
        self._banner = banner
        self._at_line_start = True

    def _write(self, text: str) -> None:
        self._out.write(text)
        self._out.flush()
        self._at_line_start = text.endswith("\n")

    async def run(self) -> None:
        self._write(
            f"{self._banner}\n"
            if self._banner
            else f"{tr('cli.arrived')} {_help()}\n"
        )
        while True:
            try:
                line = (await self._read_line()).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            if line in ("/quitter", "/q"):
                break
            if await self.handle_command(line):
                continue
            await self.reply(line)
        await self.close()

    async def handle_command(self, line: str) -> bool:
        """Run one /command; returns False when the line is a message for Monika."""
        key, rest = command(line)
        if key is None:
            return False
        if key == "quit":
            return True  # the caller decides how to leave (the local loop breaks)
        if key == "help":
            self._write(_help() + "\n")
        elif key == "clear":
            self._agent.clear()
            self._write(tr("cli.cleared") + "\n")
        elif key == "sessions":
            self._sessions()
        elif key == "resume":
            self._resume(rest)
        elif key == "search":
            self._search(rest)
        elif key == "session":
            self._forget_session(rest)
        elif key == "tools":
            self._show_tools()
        elif key == "custom-tools":
            self._personal_tools(rest)
        elif key == "recipes":
            self._recipes_command(rest)
        elif key == "context":
            self._show_context()
        elif key == "memory":
            await self._show_memory()
        elif key == "nightly":
            await self._nightly_command(rest)
        elif key == "journal":
            self._journal(rest)
        elif key == "relationship":
            self._relation(rest)
        elif key == "index":
            self._start_indexing(rest)
        elif key == "documents":
            await self._show_documents()
        elif key == "stop":
            self._write(self._stops(await self.stop_all()) + "\n")
        elif key == "tasks":
            self._tasks_list()
        elif key == "task":
            self._task_cancel(rest)
        elif key == "briefing":
            self._briefing_now()
        elif key == "remind":
            self._remind(rest)
        elif key == "forget":
            await self._forget(rest)
        else:
            self._write(tr("cli.unknown", line=line, help=_help()) + "\n")
        return True

    async def close(self) -> None:
        """Save what is left, say goodbye: run once, when the session really ends."""
        capture = self._agent.capture
        character = self._agent.character
        if character is not None:
            character.journal_flush()
        if (capture is not None and capture.pending) or (
            character is not None and character.journal_writing
        ):
            self._write(tr("cli.saving") + "\n")
        farewell = await self._start_farewell()
        left = await self._agent.aclose()
        if left:
            self._write(tr("cli.save_left", count=left) + "\n")
        self._write(tr("cli.goodbye") + "\n")
        if farewell:
            await self._finish_speech()

    async def _start_farewell(self) -> bool:
        """Cut any ongoing speech, then say goodbye while the last captures are being saved."""
        if self._speaker is None:
            return False
        await self._speaker.cancel()
        return self._speaker.say(tr("cli.goodbye"))

    async def _finish_speech(self) -> None:
        try:
            await self._speaker.wait_idle(15)
        except asyncio.CancelledError:  # Ctrl+C while saying goodbye: just stop talking
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                task.uncancel()
            await self._speaker.cancel()

    def _voice_error(self, message: str) -> None:
        """A problem of the voice itself (the text answer is unaffected)."""
        self._newline()
        self._write(tr("cli.voice_error", error=message) + "\n")

    def _start_indexing(self, folder: str) -> None:
        rag = self._agent.rag
        if rag is None:
            self._write(tr("cli.rag_disabled") + "\n")
            return
        roots = None
        if folder:
            path = Path(folder).expanduser()
            if not path.is_dir():
                self._write(tr("cli.folder_missing", folder=folder) + "\n")
                return
            roots = [path.resolve()]
        if rag.start_sync(roots):
            self._write(tr("cli.index_started") + "\n")
        else:
            self._write(tr("cli.index_running") + "\n")

    async def _show_documents(self) -> None:
        rag = self._agent.rag
        if rag is None:
            self._write(tr("cli.documents_disabled") + "\n")
        else:
            self._write(await rag.status_text() + "\n")

    # ------------------------------------------------------------------ scheduled tasks
    def _remind(self, rest: str) -> None:
        """`/rappel QUAND : TEXTE`, without the model (the tool does the same from a sentence)."""
        if self._tasks is None:
            self._write(tr("cli.tasks_disabled") + "\n")
            return
        if " : " in rest:
            quand, texte = rest.rsplit(" : ", 1)
        elif ":" in rest:
            quand, texte = rest.rsplit(":", 1)
        else:
            quand, texte = "", ""
        if not quand.strip() or not texte.strip():
            self._write(tr("cli.remind_usage") + "\n")
            return
        from agent.tasks import TasksError, parse_when

        try:
            when = parse_when(quand)
            task = self._tasks.add(texte, when)
        except TasksError as exc:
            self._write(f"{exc}\n")
            return
        self._write(tr("cli.task_added", id=task.id, when=when.says, text=task.prompt) + "\n")

    def _tasks_list(self) -> None:
        if self._tasks is None:
            self._write(tr("cli.tasks_disabled") + "\n")
            return
        tasks = self._tasks.all()
        if not tasks:
            self._write(tr("cli.tasks_none") + "\n")
            return
        self._write(tr("cli.tasks_header", count=len(tasks)) + "\n")
        for task in tasks:
            self._write(task.describe() + "\n")
        self._write(tr("cli.task_cancel_hint") + "\n")

    def _task_cancel(self, rest: str) -> None:
        if self._tasks is None:
            self._write(tr("cli.tasks_disabled") + "\n")
            return
        first, _, tail = rest.partition(" ")
        if subcommand(first) == "cancel":
            rest = tail.strip()
        if not rest:
            self._write(tr("cli.task_cancel_usage") + "\n")
            return
        task = self._tasks.cancel(rest)
        if task is None:
            self._write(tr("cli.task_not_found", id=rest) + "\n")
        else:
            self._write(tr("cli.task_deleted", id=task.id, text=task.prompt) + "\n")

    @staticmethod
    def _date(stamp: str) -> str:
        try:
            return datetime.fromisoformat(stamp).strftime("%d/%m/%Y %H:%M")
        except (TypeError, ValueError):
            return str(stamp)[:16]

    def _sessions(self) -> None:
        sessions = self._agent.sessions
        if sessions is None:
            self._write(tr("cli.sessions_disabled") + "\n")
            return
        try:
            rows = sessions.store.list(limit=15)
        except Exception as exc:  # noqa: BLE001 - a broken log must not take the chat down
            self._write(f"[sessions] {type(exc).__name__}: {exc}\n")
            return
        if not rows:
            self._write(tr("cli.sessions_none") + "\n")
            return
        self._write(tr("cli.sessions_header") + "\n")
        for row in rows:
            mark = tr("cli.current_mark") if sessions.is_current(row.id) else ""
            summary = tr("cli.summary_mark") if row.has_summary else ""
            self._write(
                f"  [{row.id}] {self._date(row.started_at)} · {row.turns} "
                f"{tr('cli.turns', count=row.turns)}"
                f"{summary}{mark} · {row.title or tr('cli.untitled')}\n"
            )
        self._write(tr("cli.sessions_hint") + "\n")

    def _resume(self, session_id: str) -> None:
        if self._agent.sessions is None:
            self._write("Les sessions sont désactivées (SESSIONS_ENABLED=false).\n")
            return
        if not session_id:
            self._write(tr("cli.resume_usage") + "\n")
            return
        try:
            message = self._agent.resume_session(session_id)
        except SessionsError as exc:
            self._write(f"[sessions] {exc}\n")
            return
        except Exception as exc:  # noqa: BLE001
            self._write(f"[sessions] {type(exc).__name__}: {exc}\n")
            return
        self._write(message + "\n")

    def _search(self, query: str) -> None:
        sessions = self._agent.sessions
        if sessions is None:
            self._write("Les sessions sont désactivées (SESSIONS_ENABLED=false).\n")
            return
        if not query:
            self._write(tr("cli.search_usage") + "\n")
            return
        try:
            hits = sessions.store.search(query, limit=10)
        except Exception as exc:  # noqa: BLE001
            self._write(f"[sessions] {type(exc).__name__}: {exc}\n")
            return
        if not hits:
            self._write(tr("cli.search_none", query=query) + "\n")
            return
        self._write(tr("cli.search_header", count=len(hits)) + "\n")
        for hit in hits:
            self._write(f"  [{hit.session_id}] {self._date(hit.at)} · {hit.excerpt}\n")
        self._write(tr("cli.search_hint") + "\n")

    def _forget_session(self, argument: str) -> None:
        sessions = self._agent.sessions
        if sessions is None:
            self._write("Les sessions sont désactivées (SESSIONS_ENABLED=false).\n")
            return
        command, _, rest = argument.partition(" ")
        if subcommand(command) != "forget" or not rest.strip():
            self._write(tr("cli.session_forget_usage") + "\n")
            return
        session_id = rest.strip()
        try:
            removed = sessions.store.forget(session_id)
        except Exception as exc:  # noqa: BLE001
            self._write(f"[sessions] {type(exc).__name__}: {exc}\n")
            return
        if not removed:
            self._write(tr("cli.session_not_found", id=session_id) + "\n")
            return
        if sessions.is_current(session_id):
            self._agent.clear()  # the conversation being continued cannot outlive its file
        self._write(tr("cli.session_deleted", id=session_id))
        self._write("\n")

    async def _show_memory(self) -> None:
        memory = self._agent.memory
        if memory is None:
            self._write(tr("cli.memory_disabled") + "\n")
            return
        try:
            items = await memory.list_all(limit=30)
        except Exception as exc:
            self._write(tr("cli.memory_error", kind=type(exc).__name__, error=exc) + "\n")
            return
        self._write(tr("cli.memory_header") + "\n" if items else tr("cli.memory_empty") + "\n")
        for item in items:
            self._write(f"  - {item.text}  [id {item.id}]\n")
            if item.quote:
                self._write(f"      « {item.quote} »\n")
        capture = self._agent.capture
        if capture is not None:
            err = (
                " " + tr("cli.last_error", error=capture.last_error)
                if capture.failures
                else ""
            )
            self._write(
                tr(
                    "cli.memory_captures",
                    captured=capture.captured,
                    failures=capture.failures,
                    pending=capture.pending,
                    error=err,
                )
                + "\n"
            )

    async def _forget(self, memory_id: str) -> None:
        memory = self._agent.memory
        if memory is None:
            self._write(tr("cli.memory_disabled") + "\n")
        elif not memory_id:
            self._write(tr("cli.forget_usage") + "\n")
        else:
            try:
                text = await memory.delete(memory_id)
            except Exception as exc:
                self._write(f"[erreur mémoire] {type(exc).__name__}: {exc}\n")
                return
            self._write(
                tr("cli.forgotten", text=text) + "\n"
                if text is not None
                else tr("cli.no_memory_id") + "\n"
            )

    def _journal(self, argument: str) -> None:
        character = self._agent.character
        entries = character.journal_entries() if character is not None else None
        if entries is None:
            self._write(tr("cli.journal_disabled") + "\n")
            return
        command, _, rest = argument.partition(" ")
        rest = rest.strip()
        action = subcommand(command)
        if action == "clear":
            character.journal_clear()
            self._write(tr("cli.journal_cleared") + "\n")
        elif action == "forget":
            if not rest:
                self._write(tr("cli.journal_usage_forget") + "\n")
            elif character.journal_delete(rest):
                self._write(tr("cli.journal_note_deleted", id=rest) + "\n")
            else:
                self._write(tr("cli.journal_note_missing", id=rest) + "\n")
        elif command:
            self._write(tr("cli.journal_usage") + "\n")
        elif not (entries["impression"] or entries["threads"] or entries["notes"]):
            self._write(tr("cli.journal_empty") + "\n")
        else:
            if entries["impression"]:
                self._write(tr("cli.journal_impression", text=entries['impression']) + "\n")
            if entries["threads"]:
                self._write(tr("cli.journal_threads", text=" · ".join(entries["threads"])) + "\n")
            for note in entries["notes"]:
                self._write(
                    f"[{note['id']}] ({note['at'][:16].replace('T', ' ')}) {note['text']}\n"
                )

    def _relation(self, argument: str) -> None:
        character = self._agent.character
        if character is None:
            self._write(tr("cli.relation_disabled") + "\n")
            return
        if subcommand(argument) == "reset" or argument in ("reinitialiser", "réinitialiser"):
            character.reset_relationship()
            self._write(tr("cli.relation_reset") + "\n")
            return
        if argument:
            self._write(tr("cli.relation_usage") + "\n")
            return
        snap = character.snapshot()
        if not snap:
            self._write(tr("cli.relation_unavailable") + "\n")
            return
        mood = snap["dominant"] if snap["dominant"] != "neutre" else tr("character.neutral")
        self._write(
            tr(
                "cli.relation_line",
                level=snap["level_label"],
                favorability=snap["favorability"],
                mood=mood,
                messages=snap["messages"],
                days=snap["active_days"],
                streak=snap["streak"],
            )
            + "\n"
        )

    def _show_context(self) -> None:
        def fmt(n: int) -> str:
            return f"{n:,}".replace(",", " ")

        st = self._agent.stats()
        summary = (
            tr("cli.tokens", count=fmt(st['summary_tokens']))
            if st["summary_tokens"]
            else tr("cli.context_none")
        )
        self._write(
            tr(
                "cli.context_line",
                tokens=fmt(st["tokens"]),
                budget=fmt(st["budget"]),
                window=fmt(st["window"]),
                messages=st["messages"],
                summary=summary,
            )
            + "\n"
        )
        self._write(
            tr(
                "cli.context_split",
                prompt=fmt(st["prompt_tokens"]),
                tools=fmt(st["tools_tokens"]),
                count=st["tools_count"],
                history=fmt(st["history_tokens"]),
            )
            + "\n"
        )
        costs = self._agent.tool_costs()
        if costs:
            by_source: dict[str, list[int]] = {}
            for _name, source, tokens in costs:
                by_source.setdefault(source, []).append(tokens)
            parts = [
                f"{src} : {len(v)} " + tr("cli.tools_count", count=len(v)) + f", ≈ {fmt(sum(v))}"
                for src, v in sorted(by_source.items(), key=lambda kv: -sum(kv[1]))
            ]
            self._write(tr("cli.context_sources", sources=" ; ".join(parts)) + "\n")
            self._write(
                tr(
                    "cli.context_heaviest",
                    tools=", ".join(f"{n} ({fmt(t)})" for n, _s, t in costs[:5]),
                )
                + "\n"
            )
            if st["tools_tokens"] > 0.25 * st["budget"]:
                self._write(tr("cli.context_tools_heavy") + "\n")

    def _show_tools(self) -> None:
        tools = self._agent.registry.tools()
        if not tools:
            self._write(tr("cli.tools_none") + "\n")
        for t in tools:
            self._write(f"  {t.name}{tr('cli.tool_slow') if t.slow else ''} — {t.source}\n")

    def _personal_tools(self, argument: str) -> None:
        """`/outils-perso` : liste, code, désactivation et suppression des outils perso."""
        if self._custom_tools is None:
            self._write(tr("cli.custom_unavailable") + "\n")
            return
        command, _, rest = argument.partition(" ")
        rest = rest.strip()
        action = subcommand(command)
        try:
            if not command:
                self._write(self._custom_tools.status_text() + "\n")
            elif action == "code" and rest:
                self._write(self._custom_tools.show(rest) + "\n")
            elif action == "disable" and rest:
                self._write(self._custom_tools.disable(rest) + "\n")
            elif action == "delete" and rest:
                self._write(self._custom_tools.delete(rest) + "\n")
            else:
                self._write(tr("cli.custom_usage") + "\n")
        except ToolFailure as exc:
            self._write(f"{exc}\n")

    def _recipes_command(self, argument: str) -> None:
        """`/recettes` : seul l'utilisateur garde, refuse ou oublie une procédure."""
        if self._recipes_store is None:
            self._write(tr("cli.recipes_disabled") + "\n")
            return
        command, _, rest = argument.partition(" ")
        rest = rest.strip()
        action = subcommand(command)
        try:
            if not command:
                self._write(self._recipes_store.list_text() + "\n")
            elif action in ("keep", "refuse", "forget") and rest:
                call = getattr(self._recipes_store, action)
                self._write(call(rest) + "\n")
            else:
                self._write(tr("cli.recipes_usage") + "\n")
        except RecipeError as exc:
            self._write(f"{exc}\n")

    def set_briefing(self, briefing) -> None:
        """Le briefing du matin a besoin de la session pour parler : il arrive après elle."""
        self._briefing = briefing

    def _briefing_now(self) -> None:
        """`/briefing` : tout de suite, dans un tour à part (la commande tient le verrou du tour)."""
        briefing = self._briefing
        if briefing is None:
            self._write(tr("cli.briefing_disabled") + "\n")
            return
        self._write(tr("cli.briefing_wait") + "\n")

        async def run() -> None:
            try:
                await briefing.run_now()
            except Exception as exc:  # noqa: BLE001 - un briefing ne casse pas la session
                self._write(tr("cli.briefing_failed", kind=type(exc).__name__, error=exc) + "\n")

        task = asyncio.get_running_loop().create_task(run())
        self._briefing_tasks.add(task)
        task.add_done_callback(self._briefing_tasks.discard)

    async def _nightly_command(self, argument: str) -> None:
        """`/journal-nuit` : lire ou supprimer une note, accepter ou refuser une consolidation."""
        nightly = self._nightly
        if nightly is None:
            self._write(tr("cli.nightly_disabled") + "\n")
            return
        command, _, rest = argument.partition(" ")
        rest = rest.strip()
        action = subcommand(command)
        if not command:
            self._write(nightly.summary_text() + "\n")
        elif action == "show":
            if not rest:
                self._write(tr("cli.nightly_usage_show") + "\n")
                return
            text = nightly.read_note(rest)
            if text is None:
                self._write(tr("cli.nightly_no_note", date=rest) + "\n")
            else:
                self._write(text if text.endswith("\n") else text + "\n")
        elif action == "delete":
            if not rest:
                self._write(tr("cli.nightly_usage_delete") + "\n")
            elif nightly.delete_note(rest):
                self._write(tr("cli.nightly_deleted", date=rest) + "\n")
            else:
                self._write(tr("cli.nightly_no_note", date=rest) + "\n")
        elif action in ("accept", "refuse"):
            if not rest:
                self._write(tr("cli.nightly_usage_accept", action=action) + "\n")
            else:
                self._write(
                    await nightly.resolve(rest, accept=action == "accept") + "\n"
                )
        else:
            self._write(tr("cli.nightly_usage") + "\n")

    def _summary(self, raw: str, limit: int = 100) -> str:
        try:
            data = json.loads(raw)
            text = (
                ", ".join(f"{k}={v!r}" for k, v in data.items())
                if isinstance(data, dict)
                else raw
            )
        except (json.JSONDecodeError, AttributeError):
            text = raw
        text = self._masker.mask_text(" ".join(text.split()))
        return text if len(text) <= limit else text[: limit - 1] + "…"

    async def reply(
        self, user_text: str, *, source: str = "user", label: str | None = None
    ) -> None:
        """Run one turn at a time; a scheduled reminder waits for the answer in progress."""
        async with self._turn_lock:
            await self._reply(user_text, source=source, label=label)

    async def run_line(self, line: str, *, source: str = "user") -> None:
        """One line sent from the page: a /command runs here, a message is an ordinary turn.

        The page sees the same thing as the terminal: a command's answer becomes a reply in the
        conversation (the terminal keeps printing it), and a message is the usual streamed turn.
        Only /quitter makes no sense there: there is no terminal to detach.
        """
        line = (line or "").strip()
        if not line:
            return
        if not line.startswith("/"):
            await self.reply(line, source=source)
            return
        if command(line)[0] == "quit":
            if self._chat is not None:
                self._chat.begin(line, source=source)
                self._chat.end(tr("cli.quit_terminal"))
            return
        async with self._turn_lock:
            previous = self._out
            out = _ChatOut(self._chat, previous)
            if self._chat is not None:
                self._chat.begin(line, source=source)
            self._out = out
            try:
                handled = await self.handle_command(line)
            except asyncio.CancelledError:
                self._out = previous
                if self._chat is not None:
                    self._chat.end("".join(out.parts).strip() or tr("cli.stopped_short"))
                raise
            finally:
                self._out = previous
            if not handled:  # a line we do not know after all: treat it as a message
                await self._reply(line, source=source, announce=self._chat is None)
                return
            if self._chat is not None:
                self._chat.end("".join(out.parts).strip() or tr("cli.no_reply"))

    async def run_task(self, prompt: str, label: str | None = None) -> None:
        """Entry point of the scheduler in a local session: the reminder is an ordinary turn."""
        await self.reply(prompt, source="auto", label=label)

    async def stop_all(self, *, turn: bool = False) -> list[str]:
        """Stop everything running beside the keyboard; returns what was stopped, for the user.

        The memory captures already queued are not cancelled: they go on to their end. Nothing here
        raises: an interruption must always leave a usable session.
        """
        stopped = [tr("cli.answer")] if turn else []
        self._cancel_action_tasks()
        if self._speaker is not None and self._speaker.busy:
            await self._speaker.cancel()
            stopped.append(tr("cli.voice"))
        rag = getattr(self._agent, "rag", None)
        if rag is not None and rag.running:
            await rag.stop_sync()
            stopped.append(tr("cli.indexing"))
        if self._hub is not None:
            self._hub.set_state("idle")
        return stopped

    @staticmethod
    def _stops(stopped: list[str]) -> str:
        return tr("cli.stopped", items=", ".join(stopped)) if stopped else tr("cli.nothing_running")

    async def _reply(
        self,
        user_text: str,
        *,
        source: str = "user",
        announce: bool = True,
        label: str | None = None,
    ) -> None:
        """Run one turn; on failure or Ctrl+C the agent has already tidied its history."""
        self._cancel_action_tasks()  # A chain decided for the previous answer must not land in this one
        if self._chat is not None and announce:
            # `label` is what the page shows: a long prepared prompt stays hidden.
            self._chat.begin(label or user_text, source=source)
        self._write("\n")
        need_prefix = (
            True  # The "Monika ›" prefix is written only when text actually arrives
        )
        if self._speaker is not None:
            await (
                self._speaker.cancel()
            )  # A new message cuts whatever was still being said
            self._speaker.start_block()  # Grouping stays for this whole answer (TTS_RENDER=block)
        splitter = (
            SentenceSplitter(max_chars=self._speech_max_chars)
            if self._speaker
            else None
        )
        reply_parts: list[str] = []  # Monika's own text, for the action role
        if self._hub is not None:
            self._hub.set_state("thinking")
        try:
            async for event in self._agent.run_turn(user_text, auto=source != "user"):
                if splitter is not None and not isinstance(
                    event, (TextDelta, AvatarCue)
                ):
                    self._speak(
                        splitter.flush()
                    )  # Text before a tool call or the end is said now
                    self._speaker.flush_block()
                if isinstance(event, TextDelta) and self._chat is not None:
                    self._chat.delta(event.text)
                if isinstance(event, TextDelta):
                    if need_prefix:
                        self._newline()
                        self._write("Monika › ")
                        need_prefix = False
                    self._write(event.text)
                    reply_parts.append(event.text)
                    if splitter is not None:
                        self._speak(splitter.feed(event.text))
                elif isinstance(event, ToolStart):
                    self._newline()
                    self._write(f"  → {event.name}({self._summary(event.arguments)})\n")
                    need_prefix = True
                elif isinstance(event, ToolEnd):
                    mark = "✓" if event.ok else "✗"
                    detail = (
                        ""
                        if event.ok
                        else f" {self._masker.mask_text(event.preview)[:100]}"
                    )
                    self._write(f"  {mark} {event.duration_ms:.0f} ms{detail}\n")
                    if self._hub is not None:
                        self._hub.activity("tool")
                elif isinstance(event, Filler):
                    self._newline()
                    self._write(f"  … {event.text}\n")
                    need_prefix = True
                    if self._speaker is not None:
                        self._speaker.say(event.text, filler=True)
                elif isinstance(event, AvatarCue):
                    # A gesture for the avatar only: it must not interrupt the sentence being read aloud
                    if self._speaker is not None and event.emotion:
                        self._speaker.set_emotion(
                            event.emotion, event.intensity
                        )  # Tone of the voice
                    if self._hub is not None:
                        self._hub.publish("avatar", **event.payload())
                elif isinstance(event, Notice):
                    self._newline()
                    self._write(f"  [{event.text}]\n")
                    need_prefix = True
                elif isinstance(event, TurnEnd):
                    self._write("\n\n")
                    if self._hub is not None:
                        self._hub.activity("turn")
                    if self._chat is not None:
                        self._chat.end("".join(reply_parts))
                    self._spawn_action_chain(user_text, "".join(reply_parts))
        except LLMError as exc:
            self._newline()
            self._write(tr("cli.error", error=exc) + "\n\n")
            if self._chat is not None:
                self._chat.end("".join(reply_parts), error=str(exc))
        except asyncio.CancelledError:
            # Ctrl+C: asyncio.run cancels the main task; swallow it to keep the session alive
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                task.uncancel()
            self._newline()
            stopped = await self.stop_all(
                turn=True
            )  # Ctrl+C stops everything, not only the reply
            self._write(tr("cli.interrupted") + "\n\n")
            if self._chat is not None:
                self._chat.end("".join(reply_parts), error="interrompu")
            background = [s for s in stopped if s != tr("cli.answer")]
            if (
                background
            ):  # Only when something else was running: stay quiet in the common case
                self._write(tr("cli.also_stopped", items=", ".join(background)) + "\n\n")
        finally:
            if self._chat is not None and self._chat.busy:
                self._chat.end(
                    "".join(reply_parts)
                )  # An unexpected error must not leave the page busy
            if self._speaker is not None:
                self._speaker.end_block()  # Speak what is still buffered, then back to sentence mode
            if self._hub is not None:
                self._hub.set_state("idle")
                self._hub.activity("turn")

    def _cancel_action_tasks(self) -> None:
        """Drop the gestures still being decided: they belong to the previous answer, not this one."""
        for task in list(self._action_tasks):
            task.cancel()
        self._action_tasks.clear()

    def _spawn_action_chain(self, user_text: str, reply: str) -> None:
        """Let the action role pick a gesture chain for the finished reply, in the background."""
        if self._action_director is None or not reply.strip():
            return
        character = self._agent.character
        snapshot = character.snapshot() if character is not None else {}
        try:
            task = asyncio.get_running_loop().create_task(
                self._action_director.suggest(user_text, reply, snapshot)
            )
        except Exception:  # noqa: BLE001 - cosmetic role: never break the session
            return
        self._action_tasks.add(task)
        task.add_done_callback(self._action_tasks.discard)

    def _speak(self, sentences: list[str]) -> None:
        for sentence in sentences:
            self._speaker.say(sentence)

    def _newline(self) -> None:
        if not self._at_line_start:
            self._write("\n")
