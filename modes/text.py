"""Terminal chat mode: streamed answers, visible tool steps and waiting phrases."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from collections.abc import Awaitable, Callable

from agent.llm import LLMError, TextDelta
from agent.loop import Agent, AvatarCue, Filler, Notice, ToolEnd, ToolStart, TurnEnd
from security.masking import Masker
from voice.speech_text import SentenceSplitter

HELP = (
    "Commandes : /aide, /outils (liste des outils), /contexte (taille de la conversation), "
    "/memoire (ce que je sais de toi), /oublier ID (supprime un souvenir), "
    "/relation (où nous en sommes ; « /relation reinitialiser » repart de zéro), "
    "/journal (mes notes sur toi ; « /journal oublier ID », « /journal effacer »), "
    "/indexer [dossier] (indexe tes documents), /documents (état de l'index), "
    "/effacer (oublie la conversation en cours), /quitter (ou Ctrl+D). "
    "Ctrl+C interrompt une réponse en cours."
)


class TextSession:
    """Interactive loop: read a line, run an agent turn, print its events."""

    def __init__(self, agent: Agent, read_line: Callable[[], Awaitable[str]],
                 out=None, masker: Masker | None = None, banner: str | None = None,
                 speaker=None, speech_max_chars: int = 200, hub=None,
                 action_director=None) -> None:
        self._agent = agent
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
            if line == "/aide":
                self._write(HELP + "\n")
            elif line == "/effacer":
                self._agent.clear()
                self._write("Conversation effacée.\n")
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
            elif line.startswith("/oublier"):
                await self._forget(line[len("/oublier"):].strip())
            elif line.startswith("/"):
                self._write(f"Commande inconnue : {line}. {HELP}\n")
            else:
                await self.reply(line)
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
            self._write("[interrompu]\n\n")
            if self._speaker is not None:
                await self._speaker.cancel()  # Ctrl+C also cuts the voice
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
