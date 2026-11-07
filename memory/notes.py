"""Mémoire personnelle : des notes continues, à la manière d'OpenSakura.

Un seul fichier JSON (0600) garde ce que Monika retient de l'utilisateur : ses notes,
les sujets encore ouverts, les épisodes terminés et sa lecture de la relation. Après
chaque échange, la conversation est mise en tampon ; tous les `MEMORY_CONSOLIDATE_TURNS`
tours, un « directeur » (le LLM) réécrit les notes et archive ce qui est clos.

Rien n'est cherché dans une base vectorielle : au tour suivant, le carnet entier part
dans le prompt, comme une page qu'elle relit. C'est la différence avec mem0, qui
découpait la conversation en « faits » isolés et décontextualisés, souvent faux ou
contradictoires. Ici le fil des échanges est gardé : les notes se réécrivent au lieu
de s'empiler.

Chaque sujet et chaque épisode porte sa preuve : une phrase exacte de l'utilisateur, recopiée
par le directeur puis vérifiée ici (`quote_supported`). Une entrée dont la citation ne se
retrouve pas dans les échanges n'est pas enregistrée.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.roles import REFLECTION_TEMPERATURE, memory_model
from monika.config import Settings
from monika.i18n import tr

log = logging.getLogger("memory")

USER_ID = "utilisateur"
# Une consigne de forme (« rituel à maintenir au format demandé ») n'est pas un fait : elle décrit
# comment je dois répondre, ce que mon prompt dit déjà.
_RULE = re.compile(
    r"(rituel|consigne|au format|format demand[ée]|ne pas oublier|sans titres?|"
    r"maintenir .{0,20}format|prompt syst[èe]me)",
    re.IGNORECASE,
)
_TAG = re.compile(r"<\s*/?\s*memoire\s*>", re.IGNORECASE)
_WORD = re.compile(r"[0-9a-zà-öø-ÿ]{2,}", re.IGNORECASE)

MAX_TURN_CHARS = 600  # per side, in the buffer
MAX_BUFFER = 40  # turns kept while waiting for the director
MAX_EVENTS = 20
MAX_TOPICS = 8
MAX_HISTORY = 10
MAX_NOTES_QUOTES = 6  # phrases qui justifient les notes en cours
EVIDENCE_MIN_CHARS = 12  # en dessous, une citation ne prouve rien


class MemoryUnavailable(Exception):
    """La mémoire ne peut pas fonctionner ; le message est en français."""


@dataclass(frozen=True)
class MemoryItem:
    """Une entrée du carnet, telle que la page et les outils la voient."""

    id: str
    text: str
    score: float | None = None
    created_at: str | None = None
    kind: str = ""
    quote: str = ""  # la phrase exacte de l'utilisateur qui justifie l'entrée


def _empty() -> dict[str, Any]:
    return {
        "user_id": USER_ID,
        "notes": "",
        "notes_history": [],
        "notes_quotes": [],
        "completed_events": [],
        "ongoing_topics": [],
        "strategic_guidance": {},
        "total_turns": 0,
        "buffer": [],
        "last_updated": None,
    }


def _clean_text(value: Any, limit: int = 400) -> str:
    """One line of plain text, tags defused, length capped."""
    text = _TAG.sub("", str(value or ""))
    text = " ".join(text.split())
    return text[:limit]


def looks_like_rule(text: str) -> bool:
    """Vrai pour une consigne sur ma façon de répondre : elle n'apprend rien de l'utilisateur."""
    return bool(_RULE.search(text or ""))


def _json_object(text: str) -> dict[str, Any] | None:
    """The first JSON object in a model answer, fences and chatter tolerated."""
    body = (text or "").strip()
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(body[start : end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _words(text: str) -> str:
    """Le texte réduit à ses mots : une citation se compare sans jouer sur la ponctuation."""
    return " ".join(_WORD.findall((text or "").lower()))


def quote_supported(quote: str, said: str) -> bool:
    """Vrai si la citation est une phrase réellement écrite par l'utilisateur, et assez précise."""
    needle = _words(quote)
    return len(needle) >= EVIDENCE_MIN_CHARS and needle in _words(said)


def _said_text(turns: list[dict[str, Any]]) -> str:
    """Ce que l'utilisateur a écrit dans les échanges en attente de consolidation."""
    return "\n".join(str(turn.get("user") or "") for turn in turns)


def _row_text(row: Any, limit: int = 400) -> str:
    """Le texte d'une entrée, qu'elle soit une chaîne (ancien carnet) ou un objet cité."""
    if isinstance(row, dict):
        return _clean_text(row.get("text") or row.get("event") or "", limit)
    return _clean_text(row, limit)


def _row_quote(row: Any) -> str:
    return _clean_text(row.get("quote") or "", 400) if isinstance(row, dict) else ""


def _row_at(row: Any) -> float | None:
    at = row.get("at") if isinstance(row, dict) else None
    return float(at) if isinstance(at, (int, float)) else None


def _stamp(at: float | None) -> str | None:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(at)) if at else None


class NoteMemory:
    """Le carnet : lecture, écriture et consolidation des notes personnelles."""

    def __init__(self, path: Path, settings: Settings, llm: Any = None) -> None:
        self._path = path
        self._s = settings
        self._llm = llm
        self._lock = asyncio.Lock()
        self._busy = False

    def bind_llm(self, llm: Any) -> None:
        """Le directeur a besoin du LLM, créé après la mémoire au démarrage."""
        self._llm = llm

    # --- stockage -------------------------------------------------------------------
    def _load(self) -> dict[str, Any]:
        state = _empty()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return state
        except (OSError, ValueError) as exc:
            log.warning("Carnet de mémoire illisible (%s) : je repars à vide.", exc)
            return state
        if not isinstance(data, dict):
            return state
        for key in state:
            if key in data:
                state[key] = data[key]
        for key in (
            "notes_history",
            "notes_quotes",
            "completed_events",
            "ongoing_topics",
            "buffer",
        ):
            if not isinstance(state[key], list):
                state[key] = []
        if not isinstance(state["strategic_guidance"], dict):
            state["strategic_guidance"] = {}
        if not isinstance(state["notes"], str):
            state["notes"] = str(state["notes"] or "")
        return state

    def _save(self, state: dict[str, Any]) -> None:
        """Écriture atomique et privée : un carnet à moitié écrit ne doit jamais rester."""
        state["last_updated"] = time.time()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(f"{self._path.name}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, self._path)

    async def _mutate(self, change: Callable[[dict[str, Any]], Any]) -> Any:
        async with self._lock:
            state = self._load()
            result = change(state)
            self._save(state)
            return result

    # --- lecture --------------------------------------------------------------------
    def _block(self, state: dict[str, Any]) -> str:
        """Le carnet tel qu'il entre dans le prompt : des informations, pas des ordres."""
        lines: list[str] = []
        notes = _TAG.sub("", str(state.get("notes") or "")).strip()
        if notes:  # Ce qu'un ancien directeur a gardé comme consigne ne part plus dans le prompt
            notes = "\n".join(
                line for line in notes.splitlines() if not looks_like_rule(line)
            ).strip()
        if notes:
            lines.append(f"- Mon carnet sur toi :\n{notes}")
        topics = [
            text
            for text in (
                _row_text(topic, 160) for topic in state.get("ongoing_topics") or []
            )
            if text and not looks_like_rule(text)
        ]
        if topics:
            lines.append("- Sujets encore ouverts : " + " ; ".join(topics))
        events = []
        for event in state.get("completed_events") or []:
            text = _row_text(event)
            if text and not looks_like_rule(text):
                events.append(text)
        if events:
            lines.append("- Épisodes passés : " + " / ".join(events[-3:]))
        guidance = state.get("strategic_guidance") or {}
        read = "; ".join(
            f"{key} : {_clean_text(value, 200)}"
            for key, value in guidance.items()
            if isinstance(value, str) and value.strip()
        )
        if read:
            lines.append(f"- Lecture de la relation : {read}")
        return "\n".join(lines)

    async def recall(self, query: str = "") -> str:
        """Le bloc à injecter dans le prompt (`query` n'est gardé que par compatibilité)."""
        return self._block(self._load())

    async def list_all(self, limit: int = 50) -> list[MemoryItem]:
        """Entrées du carnet, dans l'ordre où elles se lisent : notes, sujets, épisodes."""
        state = self._load()
        items: list[MemoryItem] = []
        notes = str(state.get("notes") or "").strip()
        if notes:
            quotes = [
                _clean_text(row.get("quote"), 240)
                for row in state.get("notes_quotes") or []
                if isinstance(row, dict)
            ]
            items.append(
                MemoryItem("notes", notes, kind="notes", quote=" ; ".join(quotes[:2]))
            )
        for index, topic in enumerate(state.get("ongoing_topics") or []):
            text = _row_text(topic)
            if text:
                items.append(
                    MemoryItem(
                        f"topic:{index}",
                        text,
                        created_at=_stamp(_row_at(topic)),
                        kind="sujet",
                        quote=_row_quote(topic),
                    )
                )
        for index, event in enumerate(state.get("completed_events") or []):
            text = _row_text(event)
            if text:
                items.append(
                    MemoryItem(
                        f"event:{index}",
                        text,
                        created_at=_stamp(_row_at(event)),
                        kind="événement",
                        quote=_row_quote(event),
                    )
                )
        return items[: max(1, limit)]

    async def changes_since(self, since_ts: float) -> dict[str, Any]:
        """What changed in the notebook since an epoch time (nightly review)."""
        async with self._lock:
            state = self._load()
        history = [
            entry
            for entry in state.get("notes_history") or []
            if isinstance(entry, dict) and float(entry.get("at") or 0) >= since_ts
        ]
        events = [
            text
            for text, at in (
                (_row_text(event), _row_at(event))
                for event in state.get("completed_events") or []
            )
            if text and at is not None and at >= since_ts
        ]
        topics = [
            text
            for text in (
                _row_text(topic, 160) for topic in state.get("ongoing_topics") or []
            )
            if text
        ]
        return {
            "notes": str(state.get("notes") or ""),
            "notes_rewrites": len(history),
            "topics": topics,
            "events": events,
            "total_turns": int(state.get("total_turns") or 0),
        }

    @staticmethod
    def guidance(items: list[MemoryItem]) -> str:
        return next((i.text for i in items if i.kind == "notes"), "")

    async def search(self, query: str, limit: int = 8) -> list[MemoryItem]:
        """Recherche par mots (le carnet est petit : pas besoin d'embeddings)."""
        words = {w.lower() for w in _WORD.findall(query or "")}
        if not words:
            return []
        scored: list[MemoryItem] = []
        for item in await self.list_all(limit=200):
            haystack = {w.lower() for w in _WORD.findall(item.text)}
            hits = len(words & haystack)
            if hits:
                scored.append(
                    MemoryItem(
                        item.id,
                        item.text,
                        score=hits / len(words),
                        created_at=item.created_at,
                        kind=item.kind,
                        quote=item.quote,
                    )
                )
        scored.sort(key=lambda i: i.score or 0, reverse=True)
        return scored[: max(1, limit)]

    # --- écriture -------------------------------------------------------------------
    async def add_fact(self, text: str) -> MemoryItem:
        """Ajoute une ligne aux notes (ce que l'utilisateur demande explicitement)."""
        fact = _clean_text(text, 500)
        if not fact:
            raise MemoryUnavailable(tr("memory.empty"))

        def change(state: dict[str, Any]) -> None:
            notes = str(state.get("notes") or "").strip()
            state["notes"] = (notes + f"\n- {fact}").strip()

        await self._mutate(change)
        return MemoryItem("notes", fact, kind="notes")

    async def update(self, memory_id: str, text: str) -> bool:
        """Réécrit une entrée ; False si l'id n'existe plus."""
        clean = _clean_text(text, self._s.memory_notes_max_chars)

        def change(state: dict[str, Any]) -> bool:
            if memory_id == "notes":
                old = str(state.get("notes") or "")
                if old and old != clean:
                    history = state.setdefault("notes_history", [])
                    history.append({"at": time.time(), "notes": old})
                    del history[:-MAX_HISTORY]
                    state["notes_quotes"] = []  # les preuves de l'ancien texte ne le justifient plus
                state["notes"] = clean
                return True
            kind, _, raw = memory_id.partition(":")
            if not raw.isdigit():
                return False
            index = int(raw)
            key = "ongoing_topics" if kind == "topic" else "completed_events" if kind == "event" else ""
            if not key:
                return False
            rows = state.get(key) or []
            if index >= len(rows):
                return False
            row = rows[index]
            rows[index] = {
                "text": clean,
                "quote": _row_quote(row),
                "at": _row_at(row) or time.time(),
            }
            return True

        return bool(await self._mutate(change))

    async def delete(self, memory_id: str) -> str | None:
        """Supprime une entrée ; renvoie son texte, ou None si l'id n'existe plus."""

        def change(state: dict[str, Any]) -> str | None:
            if memory_id == "notes":
                old = str(state.get("notes") or "")
                if not old:
                    return None
                state["notes"] = ""
                state["notes_quotes"] = []
                return old
            kind, _, raw = memory_id.partition(":")
            if not raw.isdigit():
                return None
            index = int(raw)
            key = "ongoing_topics" if kind == "topic" else "completed_events" if kind == "event" else ""
            if not key:
                return None
            rows = state.get(key) or []
            if index >= len(rows):
                return None
            row = rows.pop(index)
            state[key] = rows
            return _row_text(row)

        return await self._mutate(change)

    async def forget_all(self) -> None:
        """Efface le carnet entier (notes, historique, sujets, épisodes)."""

        def change(state: dict[str, Any]) -> None:
            state.clear()
            state.update(_empty())

        await self._mutate(change)

    # --- capture et consolidation ---------------------------------------------------
    async def capture(self, user_text: str, assistant_text: str) -> list[str]:
        """Met l'échange en tampon et consolide quand le tampon est plein."""
        turn = {
            "at": time.time(),
            "user": _clean_text(user_text, MAX_TURN_CHARS),
            "monika": _clean_text(assistant_text, MAX_TURN_CHARS),
        }
        if not turn["user"]:
            return []

        def change(state: dict[str, Any]) -> int:
            state["total_turns"] = int(state.get("total_turns") or 0) + 1
            buffer = state.get("buffer") or []
            buffer.append(turn)
            state["buffer"] = buffer[-MAX_BUFFER:]
            return len(state["buffer"])

        pending = await self._mutate(change)
        if pending >= self._s.memory_consolidate_turns:
            await self.consolidate()
        return []

    def _turns_text(self, buffer: list[dict[str, Any]]) -> str:
        lines = []
        for turn in buffer:
            lines.append(tr("memory.turn_you", text=turn.get('user', '')))
            if turn.get("monika"):
                lines.append(tr("memory.turn_me", text=turn['monika']))
        return "\n".join(lines)

    async def consolidate(self) -> bool:
        """Réécrit les notes avec le directeur ; rien n'est perdu en cas d'échec."""
        if self._llm is None or self._busy:
            return False
        async with self._lock:
            state = self._load()
            buffer = [t for t in (state.get("buffer") or []) if isinstance(t, dict)]
            if len(buffer) < self._s.memory_consolidate_turns:
                return False
            state["buffer"] = []  # the turns are being handled
            self._save(state)
        self._busy = True
        try:
            data = await self._ask_director(state, buffer)
        finally:
            self._busy = False
        if data is None:
            # The director failed: the turns go back to the front, nothing is lost.
            async with self._lock:
                current = self._load()
                current["buffer"] = buffer + [
                    t for t in (current.get("buffer") or []) if isinstance(t, dict)
                ]
                self._save(current)
            return False
        await self._merge(data, _said_text(buffer))
        return True

    def _state_text(self, state: dict[str, Any]) -> str:
        parts = [
            tr("memory.state_notes", notes=state.get("notes") or tr("memory.notes_none")),
            tr(
                "memory.state_topics",
                topics=json.dumps(
                    [_row_text(topic, 160) for topic in state.get("ongoing_topics") or []],
                    ensure_ascii=False,
                ),
            ),
            tr(
                "memory.state_guidance",
                guidance=json.dumps(state.get("strategic_guidance") or {}, ensure_ascii=False),
            ),
        ]
        return "\n".join(parts)

    async def _ask_director(
        self, state: dict[str, Any], buffer: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        prompt = tr("memory.director_prompt").format(
            max_chars=self._s.memory_notes_max_chars,
            state=self._state_text(state),
            turns=self._turns_text(buffer),
        )
        try:
            completion = await asyncio.wait_for(
                self._llm.ask(
                    [
                        {
                            "role": "system",
                            "content": tr("memory.system"),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=REFLECTION_TEMPERATURE,
                    model=memory_model(self._s),
                ),
                timeout=90,
            )
        except Exception as exc:  # noqa: BLE001 - memory is best effort, never fatal
            log.warning("Directeur de mémoire indisponible : %s", exc)
            return None
        data = _json_object(completion.content)
        if data is None:
            log.warning("Le directeur de mémoire n'a pas rendu de JSON exploitable.")
        return data

    async def _merge(self, data: dict[str, Any], said: str = "") -> None:
        """Fusionne la réponse du directeur ; une entrée sans citation retrouvée est écartée.

        `said` est ce que l'utilisateur a réellement écrit dans les échanges à consolider : la
        seule preuve acceptée. Le directeur propose, ce code vérifie.
        """
        dropped = 0

        def cited(raw: Any) -> list[dict[str, Any]]:
            """Les entrées acceptées, telles qu'elles seront gardées : texte, preuve, date."""
            nonlocal dropped
            kept: list[dict[str, Any]] = []
            for entry in raw if isinstance(raw, list) else []:
                if not isinstance(entry, dict):
                    dropped += 1
                    continue
                text = _clean_text(entry.get("text") or entry.get("texte") or "", 600)
                quote = _clean_text(entry.get("quote") or entry.get("preuve") or "", 400)
                if not text or looks_like_rule(text):
                    continue
                if not quote_supported(quote, said):
                    dropped += 1
                    continue
                kept.append({"text": text, "quote": quote, "at": time.time()})
            return kept

        events = cited(data.get("completed_events"))
        topics = cited(data.get("ongoing_topics"))
        note_quotes: list[dict[str, Any]] = []
        raw_quotes = data.get("notes_quotes")
        for entry in raw_quotes if isinstance(raw_quotes, list) else []:
            quote = _clean_text(entry, 400) if isinstance(entry, str) else ""
            if quote_supported(quote, said):
                note_quotes.append({"at": time.time(), "quote": quote})
            elif quote:
                dropped += 1
        if dropped:
            log.info(
                "Carnet : %d entrée(s) écartée(s), citation absente des échanges.", dropped
            )

        def change(state: dict[str, Any]) -> None:
            raw_notes = _TAG.sub("", str(data.get("notes") or "")).strip()
            notes = "\n".join(
                line for line in raw_notes.splitlines() if not looks_like_rule(line)
            ).strip()
            if notes:
                old = str(state.get("notes") or "")
                if old and old != notes:
                    history = state.setdefault("notes_history", [])
                    history.append({"at": time.time(), "notes": old})
                    del history[:-MAX_HISTORY]
                state["notes"] = notes[: self._s.memory_notes_max_chars]
                if note_quotes:
                    state["notes_quotes"] = note_quotes[-MAX_NOTES_QUOTES:]
            current = state.get("completed_events") or []
            state["completed_events"] = (current + events)[-MAX_EVENTS:]
            if topics:
                state["ongoing_topics"] = topics[:MAX_TOPICS]
            guidance = data.get("strategic_guidance")
            if isinstance(guidance, dict):
                state["strategic_guidance"] = {
                    str(key): _clean_text(value, 400)
                    for key, value in guidance.items()
                    if isinstance(value, str) and value.strip()
                }

        await self._mutate(change)

    async def close(self) -> None:
        """Rien à fermer : le carnet est un simple fichier."""
