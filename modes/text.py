"""Terminal chat mode: streamed answers, visible tool steps and waiting phrases."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from collections.abc import Awaitable, Callable

from agent.llm import LLMError, TextDelta
from agent.loop import Agent, AvatarCue, Filler, Notice, ToolEnd, ToolStart, TurnEnd
from agent.sessions import SessionsError
from security.masking import Masker
from voice.speech_text import SentenceSplitter

HELP = (
    "Commandes : /aide, /outils (liste des outils), /contexte (taille de la conversation), "
    "/memoire (ce que je sais de toi), /oublier ID (supprime un souvenir), "
    "/relation (où nous en sommes ; « /relation reinitialiser » repart de zéro), "
    "/journal (mes notes sur toi ; « /journal oublier ID », « /journal effacer »), "
    "/sessions (sessions enregistrées ; « /reprendre ID », « /chercher MOT », "
    "« /session oublier ID »), "
    "/indexer [dossier] (indexe tes documents), /documents (état de l'index), "
    "/rappel QUAND : TEXTE (dans 20 minutes, demain 8h, toutes les 2 heures), "
    "/taches (tâches planifiées ; « /tache annuler ID »), /stop (arrête tout ce qui tourne), "
    "/effacer (oublie la conversation en cours), /quitter (ou Ctrl+D). "
    "Ctrl+C interrompt la réponse et arrête ce qui tourne en arrière-plan."
)


class TextSession:
    """Interactive loop: read a line, run an agent turn, print its events."""

    def __init__(self, agent: Agent, read_line: Callable[[], Awaitable[str]],
                 out=None, masker: Masker | None = None, banner: str | None = None,
                 speaker=None, speech_max_chars: int = 200, hub=None,
                 action_director=None, tasks=None) -> None:
        self._agent = agent
        self._tasks = tasks  # agent.tasks.TaskStore (None = scheduled tasks disabled)
        self._turn_lock = asyncio.Lock()  # One turn at a time: a reminder waits for the answer
        self._hub = hub  # Live state for the web page (optional)
        self._action_director = action_director  # Post-turn avatar chain (optional, cosmetic)
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

    def set_output(self, out) -> None:
        """Send what the session displays somewhere else (the kernel's clients)."""
        self._out = out

    async def run(self) -> None:
        self._write(f"{self._banner}\n" if self._banner else f"Monika est là. {HELP}\n")
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
        """Run one /command; returns False when the line is a message for Monika.

        The kernel uses this to serve the terminal and the page through the same code.
        """
        if line in ("/quitter", "/q"):
            return True  # the caller decides how to leave (the local loop breaks)
        if line == "/aide":
            self._write(HELP + "\n")
        elif line == "/effacer":
            self._agent.clear()
            self._write("Conversation effacée.\n")
        elif line == "/sessions":
            self._sessions()
        elif line.startswith("/reprendre"):
            self._resume(line[len("/reprendre"):].strip())
        elif line.startswith("/chercher"):
            self._search(line[len("/chercher"):].strip())
        elif line == "/session" or line.startswith("/session "):
            self._forget_session(line[len("/session"):].strip())
        elif line == "/outils":
            self._show_tools()
        elif line == "/contexte":
            self._show_context()
        elif line == "/memoire":
            await self._show_memory()
        elif line.startswith("/journal"):
            self._journal(line[len("/journal"):].strip())
        elif line.startswith("/relation"):
            self._relation(line[len("/relation"):].strip())
        elif line.startswith("/indexer"):
            self._start_indexing(line[len("/indexer"):].strip())
        elif line == "/documents":
            await self._show_documents()
        elif line == "/stop":
            self._write(self._stops(await self.stop_all()) + "\n")
        elif line == "/taches":
            self._tasks_list()
        elif line == "/tache" or line.startswith("/tache "):
            self._task_cancel(line[len("/tache"):].strip())
        elif line.startswith("/rappel"):
            self._remind(line[len("/rappel"):].strip())
        elif line.startswith("/oublier"):
            await self._forget(line[len("/oublier"):].strip())
        elif line.startswith("/"):
            self._write(f"Commande inconnue : {line}. {HELP}\n")
        else:
            return False
        return True

    async def close(self) -> None:
        """Save what is left, say goodbye: run once, when the session really ends."""
        capture = self._agent.capture
        character = self._agent.character
        if character is not None:
            character.journal_flush()
        if (capture is not None and capture.pending) or (character is not None and character.journal_writing):
            self._write("Je termine d'enregistrer mes souvenirs…\n")
        farewell = await self._start_farewell()
        left = await self._agent.aclose()
        if left:
            self._write(f"{left} échange(s) n'ont pas pu être mémorisés.\n")
        self._write("À bientôt.\n")
        if farewell:
            await self._finish_speech()

    async def _start_farewell(self) -> bool:
        """Cut any ongoing speech, then say goodbye while the last captures are being saved."""
        if self._speaker is None:
            return False
        await self._speaker.cancel()
        return self._speaker.say("À bientôt.")

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
        self._write(f"  [voix] {message}\n")

    def _start_indexing(self, folder: str) -> None:
        rag = self._agent.rag
        if rag is None:
            self._write("Index de documents désactivé ou indisponible (voir le message au lancement).\n")
            return
        roots = None
        if folder:
            path = Path(folder).expanduser()
            if not path.is_dir():
                self._write(f"Dossier introuvable : {folder}\n")
                return
            roots = [path.resolve()]
        if rag.start_sync(roots):
            self._write("Indexation lancée en arrière-plan. /documents pour suivre l'avancement.\n")
        else:
            self._write("Une indexation est déjà en cours. /documents pour suivre l'avancement.\n")

    async def _show_documents(self) -> None:
        rag = self._agent.rag
        if rag is None:
            self._write("Index de documents désactivé ou indisponible.\n")
        else:
            self._write(await rag.status_text() + "\n")

    # ------------------------------------------------------------------ scheduled tasks
    def _remind(self, rest: str) -> None:
        """`/rappel QUAND : TEXTE`, without the model (the tool does the same from a sentence)."""
        if self._tasks is None:
            self._write("Tâches planifiées désactivées (TASKS_ENABLED=false).\n")
            return
        if " : " in rest:
            quand, texte = rest.rsplit(" : ", 1)
        elif ":" in rest:
            quand, texte = rest.rsplit(":", 1)
        else:
            quand, texte = "", ""
        if not quand.strip() or not texte.strip():
            self._write("Usage : /rappel QUAND : TEXTE — par exemple « /rappel dans 20 minutes : "
                        "boire de l'eau » ou « /rappel toutes les 2 heures : boire de l'eau ».\n")
            return
        from agent.tasks import TasksError, parse_when

        try:
            when = parse_when(quand)
            task = self._tasks.add(texte, when)
        except TasksError as exc:
            self._write(f"{exc}\n")
            return
        self._write(f"Tâche {task.id} : {when.says} — « {task.prompt} ».\n")

    def _tasks_list(self) -> None:
        if self._tasks is None:
            self._write("Tâches planifiées désactivées (TASKS_ENABLED=false).\n")
            return
        tasks = self._tasks.all()
        if not tasks:
            self._write("Aucune tâche planifiée. /rappel dans 20 minutes : boire de l'eau\n")
            return
        self._write(f"Tâches planifiées ({len(tasks)}) :\n")
        for task in tasks:
            self._write(task.describe() + "\n")
        self._write("/tache annuler ID pour en supprimer une.\n")

    def _task_cancel(self, rest: str) -> None:
        if self._tasks is None:
            self._write("Tâches planifiées désactivées (TASKS_ENABLED=false).\n")
            return
        if rest.startswith("annuler"):
            rest = rest[len("annuler"):].strip()
        if not rest:
            self._write("Usage : /tache annuler ID (les ids sont dans /taches).\n")
            return
        task = self._tasks.cancel(rest)
        if task is None:
            self._write(f"Aucune tâche avec l'id {rest}.\n")
        else:
            self._write(f"Tâche {task.id} supprimée : « {task.prompt} ».\n")

    @staticmethod
    def _date(stamp: str) -> str:
        try:
            return datetime.fromisoformat(stamp).strftime("%d/%m/%Y %H:%M")
        except (TypeError, ValueError):
            return str(stamp)[:16]

    def _sessions(self) -> None:
        sessions = self._agent.sessions
        if sessions is None:
            self._write("Les sessions sont désactivées (SESSIONS_ENABLED=false).\n")
            return
        try:
            rows = sessions.store.list(limit=15)
        except Exception as exc:  # noqa: BLE001 - a broken log must not take the chat down
            self._write(f"[sessions] {type(exc).__name__}: {exc}\n")
            return
        if not rows:
            self._write("Aucune session enregistrée pour l'instant.\n")
            return
        self._write("Sessions enregistrées, les plus récentes d'abord :\n")
        for row in rows:
            mark = " ← en cours" if sessions.is_current(row.id) else ""
            summary = ", résumé" if row.has_summary else ""
            self._write(f"  [{row.id}] {self._date(row.started_at)} · {row.turns} tour(s)"
                        f"{summary}{mark} · {row.title or '(sans titre)'}\n")
        self._write("  /reprendre ID, /session oublier ID\n")

    def _resume(self, session_id: str) -> None:
        if self._agent.sessions is None:
            self._write("Les sessions sont désactivées (SESSIONS_ENABLED=false).\n")
            return
        if not session_id:
            self._write("Usage : /reprendre ID (les ids s'affichent avec /sessions).\n")
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
            self._write("Usage : /chercher MOT (ou plusieurs mots).\n")
            return
        try:
            hits = sessions.store.search(query, limit=10)
        except Exception as exc:  # noqa: BLE001
            self._write(f"[sessions] {type(exc).__name__}: {exc}\n")
            return
        if not hits:
            self._write(f"Aucun tour ne contient « {query} ».\n")
            return
        self._write(f"{len(hits)} tour(s) trouvé(s) :\n")
        for hit in hits:
            self._write(f"  [{hit.session_id}] {self._date(hit.at)} · {hit.excerpt}\n")
        self._write("  /reprendre ID pour reprendre la conversation.\n")

    def _forget_session(self, argument: str) -> None:
        sessions = self._agent.sessions
        if sessions is None:
            self._write("Les sessions sont désactivées (SESSIONS_ENABLED=false).\n")
            return
        command, _, rest = argument.partition(" ")
        if command != "oublier" or not rest.strip():
            self._write("Usage : /session oublier ID (les ids s'affichent avec /sessions).\n")
            return
        session_id = rest.strip()
        try:
            removed = sessions.store.forget(session_id)
        except Exception as exc:  # noqa: BLE001
            self._write(f"[sessions] {type(exc).__name__}: {exc}\n")
            return
        if not removed:
            self._write(f"Aucune session avec l'id {session_id}.\n")
            return
        if sessions.is_current(session_id):
            self._agent.clear()  # the conversation being continued cannot outlive its file
        self._write(f"Session {session_id} supprimée.")
        self._write("\n")

    async def _show_memory(self) -> None:
        memory = self._agent.memory
        if memory is None:
            self._write("Mémoire rapide désactivée ou indisponible (voir le message au lancement).\n")
            return
        try:
            items = await memory.list_all(limit=30)
        except Exception as exc:
            self._write(f"[erreur mémoire] {type(exc).__name__}: {exc}\n")
            return
        self._write("Ce que je sais de toi :\n" if items else "Je ne sais encore rien de toi.\n")
        for item in items:
            self._write(f"  - {item.text}  [id {item.id}]\n")
        capture = self._agent.capture
        if capture is not None:
            err = f" Dernière erreur : {capture.last_error}" if capture.failures else ""
            self._write(f"Captures automatiques : {capture.captured} réussie(s), "
                        f"{capture.failures} échec(s), {capture.pending} en attente.{err}\n")

    async def _forget(self, memory_id: str) -> None:
        memory = self._agent.memory
        if memory is None:
            self._write("Mémoire rapide désactivée ou indisponible.\n")
        elif not memory_id:
            self._write("Usage : /oublier ID (les ids s'affichent avec /memoire).\n")
        else:
            try:
                text = await memory.delete(memory_id)
            except Exception as exc:
                self._write(f"[erreur mémoire] {type(exc).__name__}: {exc}\n")
                return
            self._write(f"Oublié : {text}\n" if text is not None else "Aucun souvenir avec cet id.\n")

    def _journal(self, argument: str) -> None:
        character = self._agent.character
        entries = character.journal_entries() if character is not None else None
        if entries is None:
            self._write("Le journal est désactivé.\n")
            return
        command, _, rest = argument.partition(" ")
        rest = rest.strip()
        if command == "effacer":
            character.journal_clear()
            self._write("Journal effacé : je repars d'une page blanche.\n")
        elif command == "oublier":
            if not rest:
                self._write("Usage : /journal oublier ID (les ids s'affichent avec /journal).\n")
            elif character.journal_delete(rest):
                self._write(f"Note {rest} supprimée.\n")
            else:
                self._write(f"Aucune note avec l'id {rest}.\n")
        elif command:
            self._write("Usage : /journal, /journal oublier ID ou /journal effacer.\n")
        elif not (entries["impression"] or entries["threads"] or entries["notes"]):
            self._write("Mon journal est encore vide : j'écris une note tous les quelques échanges.\n")
        else:
            if entries["impression"]:
                self._write(f"Impression d'ensemble : {entries['impression']}\n")
            if entries["threads"]:
                self._write("À reprendre : " + " · ".join(entries["threads"]) + "\n")
            for note in entries["notes"]:
                self._write(f"[{note['id']}] ({note['at'][:16].replace('T', ' ')}) {note['text']}\n")

    def _relation(self, argument: str) -> None:
        character = self._agent.character
        if character is None:
            self._write("Le personnage (émotions et relation) est désactivé.\n")
            return
        if argument in ("reinitialiser", "réinitialiser"):
            character.reset_relationship()
            self._write("Relation réinitialisée : on repart de zéro.\n")
            return
        if argument:
            self._write("Usage : /relation, ou /relation reinitialiser.\n")
            return
        snap = character.snapshot()
        if not snap:
            self._write("État indisponible pour le moment.\n")
            return
        mood = snap["dominant"] if snap["dominant"] != "neutre" else "égale"
        self._write(f"Relation : {snap['level_label']} (favorabilité {snap['favorability']:.2f}) · humeur : {mood} · "
                    f"{snap['messages']} messages, {snap['active_days']} jour(s) de présence, "
                    f"série en cours : {snap['streak']}.\n")

    def _show_context(self) -> None:
        def fmt(n: int) -> str:
            return f"{n:,}".replace(",", " ")

        st = self._agent.stats()
        summary = (f"environ {fmt(st['summary_tokens'])} tokens" if st["summary_tokens"] else "aucun")
        self._write(f"Contexte : environ {fmt(st['tokens'])} tokens sur un budget de {fmt(st['budget'])} "
                    f"(fenêtre du modèle : {fmt(st['window'])}), {st['messages']} messages. "
                    f"Résumé des anciens échanges : {summary}.\n")
        self._write(f"Répartition : prompt système ≈ {fmt(st['prompt_tokens'])}, "
                    f"outils ≈ {fmt(st['tools_tokens'])} ({st['tools_count']} outil(s)), "
                    f"historique ≈ {fmt(st['history_tokens'])} tokens.\n")
        costs = self._agent.tool_costs()
        if costs:
            by_source: dict[str, list[int]] = {}
            for _name, source, tokens in costs:
                by_source.setdefault(source, []).append(tokens)
            parts = [f"{src} : {len(v)} outil(s), ≈ {fmt(sum(v))}"
                     for src, v in sorted(by_source.items(), key=lambda kv: -sum(kv[1]))]
            self._write("Outils par source : " + " ; ".join(parts) + ".\n")
            self._write("Les plus lourds : " + ", ".join(f"{n} ({fmt(t)})" for n, _s, t in costs[:5])
                        + ".\n")
            if st["tools_tokens"] > 0.25 * st["budget"]:
                self._write("Les outils pèsent lourd à chaque message : limite-les avec « enabled_tools » "
                            "ou « disabled_tools » dans mcp_servers.json.\n")
        plan = self._agent.last_plan
        if plan is not None:  # What the director kept for the last turn (it runs before every turn)
            kept = "tous" if plan.tools is None else f"{len(plan.tools)} sur {st['tools_count']}"
            hint = f" · piste : {plan.hint}" if plan.hint else ""
            self._write(f"Dernier tour planifié : {kept} outil(s) envoyé(s) au modèle{hint}.\n")

    def _show_tools(self) -> None:
        tools = self._agent.registry.tools()
        if not tools:
            self._write("Aucun outil disponible (voir mcp_servers.json).\n")
        for t in tools:
            self._write(f"  {t.name}{' (lent)' if t.slow else ''} — {t.source}\n")

    def _summary(self, raw: str, limit: int = 100) -> str:
        try:
            data = json.loads(raw)
            text = ", ".join(f"{k}={v!r}" for k, v in data.items()) if isinstance(data, dict) else raw
        except (json.JSONDecodeError, AttributeError):
            text = raw
        text = self._masker.mask_text(" ".join(text.split()))
        return text if len(text) <= limit else text[: limit - 1] + "…"

    async def reply(self, user_text: str) -> None:
        """Run one turn at a time; a scheduled reminder waits for the answer in progress."""
        async with self._turn_lock:
            await self._reply(user_text)

    async def run_task(self, prompt: str) -> None:
        """Entry point of the scheduler in a local session: the reminder is an ordinary turn."""
        await self.reply(prompt)

    async def stop_all(self, *, turn: bool = False) -> list[str]:
        """Stop everything running beside the keyboard; returns what was stopped, for the user.

        The memory captures already queued are not cancelled: they go on to their end. Nothing here
        raises: an interruption must always leave a usable session.
        """
        stopped = ["réponse"] if turn else []
        self._cancel_action_tasks()
        if self._speaker is not None and self._speaker.busy:
            await self._speaker.cancel()
            stopped.append("voix")
        rag = getattr(self._agent, "rag", None)
        if rag is not None and rag.running:
            await rag.stop_sync()
            stopped.append("indexation")
        if self._hub is not None:
            self._hub.set_state("idle")
        return stopped

    @staticmethod
    def _stops(stopped: list[str]) -> str:
        return "Arrêté : " + ", ".join(stopped) + "." if stopped else "Rien ne tournait."

    async def _reply(self, user_text: str) -> None:
        """Run one turn; on failure or Ctrl+C the agent has already tidied its history."""
        self._cancel_action_tasks()  # A chain decided for the previous answer must not land in this one
        self._write("\n")
        need_prefix = True  # The "Monika ›" prefix is written only when text actually arrives
        if self._speaker is not None:
            await self._speaker.cancel()  # A new message cuts whatever was still being said
            self._speaker.start_block()  # Grouping stays for this whole answer (TTS_RENDER=block)
        splitter = SentenceSplitter(max_chars=self._speech_max_chars) if self._speaker else None
        reply_parts: list[str] = []  # Monika's own text, for the action role
        if self._hub is not None:
            self._hub.set_state("thinking")
        try:
            async for event in self._agent.run_turn(user_text):
                if splitter is not None and not isinstance(event, (TextDelta, AvatarCue)):
                    self._speak(splitter.flush())  # Text before a tool call or the end is said now
                    self._speaker.flush_block()
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
                    detail = "" if event.ok else f" {self._masker.mask_text(event.preview)[:100]}"
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
                        self._speaker.set_emotion(event.emotion, event.intensity)  # Tone of the voice
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
                    self._spawn_action_chain(user_text, "".join(reply_parts))
        except LLMError as exc:
            self._newline()
            self._write(f"[erreur] {exc}\n\n")
        except asyncio.CancelledError:
            # Ctrl+C: asyncio.run cancels the main task; swallow it to keep the session alive
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                task.uncancel()
            self._newline()
            stopped = await self.stop_all(turn=True)  # Ctrl+C stops everything, not only the reply
            self._write("[interrompu]\n\n")
            background = [s for s in stopped if s != "réponse"]
            if background:  # Only when something else was running: stay quiet in the common case
                self._write(f"  (aussi arrêté : {', '.join(background)})\n\n")
        finally:
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
                self._action_director.suggest(user_text, reply, snapshot))
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
