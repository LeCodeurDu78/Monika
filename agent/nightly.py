"""Veille nocturne : une note dans le coffre Obsidian, des propositions de mémoire, des pensées du matin.

La nuit, Monika relit des FAITS observés (sessions du jour, journal d'audit, mémoire, recettes,
chronologie de la phase 3 si elle existe) et en fait une note Markdown à sa voix. Rien n'est inventé :
les sections factuelles viennent des fichiers, le paragraphe d'ouverture et les pensées du lendemain
sont écrits par le modèle à partir de ce relevé seulement, puis masqués.

Deux autres sorties :
  - des PROPOSITIONS de consolidation de la mémoire (doublons, contradictions), listées dans la note
    et jamais appliquées seules : `/journal-nuit accepter ID` ou `/journal-nuit refuser ID` ;
  - `matin.json` (1 à 2 idées ou rappels), lu une seule fois au premier message du jour et présenté
    au modèle comme des pensées pouvant être fausses.

La veille est une tâche locale : elle tourne tant qu'un processus Monika vit. Une nuit manquée est
rattrapée au lancement suivant (avec la date de la nuit concernée). Tout est écrit en 0600, de façon
atomique, et le contenu du coffre reste de la DONNÉE pour le RAG comme pour le prompt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent.roles import nightly_model
from monika.config import Settings
from monika.i18n import tr
from monika.paths import Paths
from security.masking import Masker

if TYPE_CHECKING:
    from agent.recipes import RecipeStore
    from agent.sessions import SessionStore

log = logging.getLogger(__name__)

NOTE_SUFFIX = ".md"
NOTE_MARKER = "monika-nightly: true"
MAX_TURNS = 120
MAX_SESSIONS = 12
MAX_EXCERPTS = 3
EXCERPT_CHARS = 180
MAX_FAILURES = 30
MAX_PROBES = 100
MAX_PROPOSALS = 12
MAX_MEMORY_ITEMS = 24
MAX_PHASE3 = 30
MAX_MODEL_CHARS = 6000
VOICE_TIMEOUT_S = 120.0
CONTRADICTION_TIMEOUT_S = 90.0
DAY_START = dt_time(0, 0)

def _voice_system() -> str:
    return tr("nightly.voice_system")


def _contradiction_system() -> str:
    return tr("nightly.contradiction_system")

def _now() -> datetime:
    return datetime.now().astimezone()


def _clean(text: Any, limit: int) -> str:
    """One line, no angle brackets (nothing can imitate a tag), bounded."""
    value = "".join(
        " " if unicodedata.category(c)[0] == "C" else c for c in str(text or "")
    )
    value = " ".join(value.split()).replace("<", "‹").replace(">", "›")
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _normalise(text: str) -> str:
    flat = unicodedata.normalize("NFKD", str(text).lower())
    flat = "".join(c for c in flat if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", flat))


def _words(text: str) -> set[str]:
    return {w for w in _normalise(text).split() if len(w) >= 3}


def _similar(a: str, b: str) -> bool:
    """Same text, or nearly the same words (a duplicate one would merge)."""
    if not a or not b:
        return False
    if _normalise(a) == _normalise(b):
        return True
    wa, wb = _words(a), _words(b)
    if len(wa) < 3 or len(wb) < 3:
        return False
    return len(wa & wb) / len(wa | wb) >= 0.82


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _json_object(raw: str) -> dict[str, Any] | None:
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}-{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _fr_date(day: date) -> str:
    months = (
        "janvier",
        "février",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "août",
        "septembre",
        "octobre",
        "novembre",
        "décembre",
    )
    return f"{day.day} {months[day.month - 1]} {day.year}"


def _day_window(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, DAY_START).astimezone()
    return start, start + timedelta(days=1)


@dataclass
class Proposal:
    """Une consolidation proposée, jamais appliquée d'elle-même."""

    id: str
    date: str
    kind: str  # doublon | contradiction | notes
    title: str
    target: str | None = None  # identifiant à garder / réécrire
    drop: str | None = None  # identifiant à retirer (doublon)
    text: str = ""  # texte proposé (contradiction / notes réécrites)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "date": self.date,
            "kind": self.kind,
            "title": self.title,
            "target": self.target,
            "drop": self.drop,
            "text": self.text,
        }

    @classmethod
    def from_json(cls, data: Any) -> Proposal | None:
        if not isinstance(data, dict):
            return None
        ident = str(data.get("id") or "")
        if not ident or data.get("kind") not in ("doublon", "contradiction", "notes"):
            return None
        return cls(
            ident,
            str(data.get("date") or ""),
            str(data["kind"]),
            _clean(data.get("title"), 200),
            str(data["target"]) if data.get("target") else None,
            str(data["drop"]) if data.get("drop") else None,
            _clean(data.get("text"), 800),
        )


@dataclass
class Digest:
    """Le relevé factuel d'une journée, avant toute écriture."""

    day: date
    sessions: list[dict[str, Any]] = field(default_factory=list)
    sessions_enabled: bool = True
    turns: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)
    audit_enabled: bool = True
    audit_available: bool = True
    memory: dict[str, Any] | None = None
    memory_items: list[Any] = field(default_factory=list)
    recipes: list[dict[str, Any]] = field(default_factory=list)
    recipes_enabled: bool = True
    phase3: list[str] | None = None


@dataclass
class NightlyReport:
    day: str
    note_path: str | None = None
    sessions: int = 0
    turns: int = 0
    failures: int = 0
    proposals: int = 0
    morning: int = 0
    error: str = ""


class MorningNotes:
    """`matin.json` : 1-2 pensées, lues une seule fois au premier message du jour."""

    def __init__(
        self, path: Path, clock: Callable[[], datetime] = _now
    ) -> None:
        self.path = path
        self._clock = clock

    def read(self) -> dict[str, Any]:
        data = _read_json(self.path)
        return data if isinstance(data, dict) else {}

    def write(
        self,
        for_date: date,
        items: list[dict[str, str]],
        now: datetime | None = None,
    ) -> int:
        """Write 1-2 items for that day; returns how many were kept."""
        kept: list[dict[str, str]] = []
        for item in items or []:
            if isinstance(item, str):
                text, kind = item, "idée"
            elif isinstance(item, dict):
                text = item.get("text", "")
                kind = item.get("kind", "idée")
            else:
                continue
            text = _clean(text, 180)
            if not text:
                continue
            kept.append(
                {"text": text, "kind": "rappel" if kind == "rappel" else "idée"}
            )
            if len(kept) == 2:
                break
        payload = {
            "for_date": for_date.isoformat(),
            "generated_at": (now or self._clock()).isoformat(timespec="seconds"),
            "consumed_on": None,
            "items": kept,
        }
        _atomic_write(
            self.path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        )
        return len(kept)

    def take(self, now: datetime | None = None) -> str:
        """The prompt block for today, once; marks it consumed ('' when there is none)."""
        moment = now or self._clock()
        today = moment.date().isoformat()
        data = self.read()
        if data.get("for_date") != today or data.get("consumed_on") == today:
            return ""
        items = [
            item
            for item in data.get("items") or []
            if isinstance(item, dict) and _clean(item.get("text"), 180)
        ]
        if not items:
            return ""
        lines = []
        for item in items[:2]:
            kind = "rappel" if item.get("kind") == "rappel" else "idée"
            lines.append(f"- ({kind}) {_clean(item.get('text'), 180)}")
        data["consumed_on"] = today
        _atomic_write(
            self.path, json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        )
        return "\n".join(lines)


class NightlyJob:
    """Une veille par nuit : relevé, note, propositions, pensées, et le planificateur."""

    def __init__(
        self,
        settings: Settings,
        paths: Paths,
        masker: Masker,
        llm: Any = None,
        memory: Any = None,
        session_store: SessionStore | None = None,
        recipes: RecipeStore | None = None,
        character: Any = None,
        morning: MorningNotes | None = None,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._s = settings
        self._paths = paths
        self._masker = masker
        self._llm = llm
        self._memory = memory
        self._sessions = session_store
        self._recipes = recipes
        self._character = character
        self._clock = clock
        self.morning = morning or MorningNotes(paths.morning_notes, clock)
        self._task: asyncio.Task[None] | None = None
        self._run_lock = asyncio.Lock()

    # ------------------------------------------------------------------ planning
    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if not self.running:
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    def _due_moment(self, day: date) -> datetime:
        hour, minute = (int(part) for part in self._s.nightly_time.split(":"))
        return datetime.combine(day, dt_time(hour, minute)).astimezone()

    def _latest_due_day(self, now: datetime) -> date:
        """The most recent scheduled night that has come (today or yesterday)."""
        return now.date() if now >= self._due_moment(now.date()) else now.date() - timedelta(days=1)

    def _state(self) -> dict[str, Any]:
        data = _read_json(self._paths.nightly_state)
        return data if isinstance(data, dict) else {}

    def _save_state(self, **changes: Any) -> None:
        state = self._state()
        state.update(changes)
        _atomic_write(
            self._paths.nightly_state,
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        )

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the night must never take Monika down
                log.warning("veille nocturne : %s: %s", type(exc).__name__, exc)
            try:
                await asyncio.sleep(self._sleep_seconds())
            except asyncio.CancelledError:
                raise

    def _sleep_seconds(self) -> float:
        now = self._clock()
        latest = self._latest_due_day(now)
        next_due = self._due_moment(latest + timedelta(days=1))
        seconds = (next_due - now).total_seconds()
        return max(30.0, min(1800.0, seconds))

    async def _tick(self) -> None:
        now = self._clock()
        latest = self._latest_due_day(now)
        state = self._state()
        if not state.get("last_due"):
            # First launch: do not reconstruct a night we never saw; start with the next one.
            self._save_state(
                last_due=latest.isoformat(),
                started_at=now.isoformat(timespec="seconds"),
                last_error="",
            )
            return
        if str(state.get("last_due")) < latest.isoformat():
            await self._run_and_record(latest, now)

    async def _run_and_record(self, due_date: date, now: datetime) -> NightlyReport:
        try:
            report = await self.run(due_date, now)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("veille nocturne : %s: %s", type(exc).__name__, exc)
            report = NightlyReport(
                day=(due_date - timedelta(days=1)).isoformat(),
                error=f"{type(exc).__name__}: {exc}"[:300],
            )
        self._save_state(
            last_due=due_date.isoformat(),
            last_success="" if report.error else now.isoformat(timespec="seconds"),
            last_error=report.error,
        )
        return report

    # ------------------------------------------------------------------ the run
    async def run(
        self, due_date: date, now: datetime | None = None
    ) -> NightlyReport:
        """Write the note for the day before `due_date`, the proposals and tomorrow's thoughts."""
        moment = now or self._clock()
        target = due_date - timedelta(days=1)
        async with self._run_lock:
            digest = await self._digest(target)
            voice = await self._voice(digest)
            proposals = await self._proposals(digest)
            morning_items = self._morning_items(digest, voice)
            self.morning.write(due_date, morning_items, moment)
            self._store_proposals(target, proposals)
            note_path = self._write_note(target, digest, voice, proposals, morning_items)
            report = NightlyReport(
                day=target.isoformat(),
                note_path=str(note_path) if note_path else None,
                sessions=len(digest.sessions),
                turns=digest.turns,
                failures=len(digest.failures),
                proposals=len(proposals),
                morning=len(morning_items),
            )
            if note_path is None:
                report.error = (
                    "note non écrite (OBSIDIAN_VAULT absent ou note existante non générée)"
                )
            log.info(
                "Veille du %s : %d session(s), %d tour(s), %d échec(s), %d proposition(s)",
                report.day,
                report.sessions,
                report.turns,
                report.failures,
                report.proposals,
            )
            return report

    # ------------------------------------------------------------------ observed facts
    async def _digest(self, day: date) -> Digest:
        start, end = _day_window(day)
        digest = Digest(day=day)
        digest.audit_enabled = bool(self._s.audit_enabled)
        digest.recipes_enabled = bool(self._s.recipes_enabled)
        digest.sessions_enabled = self._sessions is not None

        if self._sessions is not None:
            try:
                rows = self._sessions.turns_between(
                    start.isoformat(timespec="seconds"),
                    end.isoformat(timespec="seconds"),
                    limit=MAX_TURNS,
                )
            except Exception as exc:  # noqa: BLE001 - a broken log must not lose the night
                log.warning("veille : sessions illisibles (%s)", exc)
                rows = []
            grouped: dict[str, dict[str, Any]] = {}
            for row in rows:
                group = grouped.setdefault(
                    row.session_id,
                    {
                        "id": row.session_id,
                        "title": row.title,
                        "turns": 0,
                        "at": row.at,
                        "excerpts": [],
                    },
                )
                group["turns"] += 1
                if row.user_text and len(group["excerpts"]) < MAX_EXCERPTS:
                    group["excerpts"].append(_clean(row.user_text, EXCERPT_CHARS))
                digest.turns += 1
            digest.sessions = list(grouped.values())[:MAX_SESSIONS]

        digest.failures, digest.audit_available = self._audit_failures(start, end)

        if self._memory is not None:
            try:
                digest.memory = await self._memory.changes_since(start.timestamp())
                digest.memory_items = await self._memory.list_all(limit=200)
            except Exception as exc:  # noqa: BLE001
                log.warning("veille : mémoire illisible (%s)", exc)
                digest.memory = None
                digest.memory_items = []

        if self._recipes is not None:
            try:
                digest.recipes = self._recipes.used_since(
                    start.astimezone(UTC).isoformat(timespec="seconds")
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("veille : recettes illisibles (%s)", exc)
                digest.recipes = []

        digest.phase3 = self._phase3(start, end)
        return digest

    def _audit_failures(
        self, start: datetime, end: datetime
    ) -> tuple[list[dict[str, Any]], bool]:
        path = self._paths.audit_log
        if not self._s.audit_enabled:
            return [], False
        if not path.is_file():
            return [], True
        failures: list[dict[str, Any]] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return [], False
        start_utc, end_utc = start.astimezone(UTC), end.astimezone(UTC)
        for line in lines:
            try:
                item = json.loads(line)
                stamp = datetime.fromisoformat(str(item.get("ts")))
            except (ValueError, TypeError):
                continue
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
            if not (start_utc <= stamp < end_utc):
                continue
            status = str(item.get("status") or "ok")
            if status == "ok":
                continue
            failures.append(
                {
                    "at": stamp.astimezone().strftime("%H:%M"),
                    "tool": _clean(item.get("tool"), 80),
                    "status": _clean(status, 30),
                    "detail": self._masker.mask_text(
                        _clean(item.get("detail"), 200)
                    ),
                }
            )
            if len(failures) >= MAX_FAILURES:
                break
        return failures, True

    def _phase3(self, start: datetime, end: datetime) -> list[str] | None:
        path = self._s.nightly_phase3_file or self._paths.nightly_phase3
        if not path.is_file():
            return None
        data = _read_json(path)
        if isinstance(data, dict):
            entries = data.get("entries") or data.get("chronologie") or []
        elif isinstance(data, list):
            entries = data
        else:
            return None
        kept: list[str] = []
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, str):
                text, at = entry, None
            elif isinstance(entry, dict):
                text = entry.get("text") or entry.get("texte") or ""
                at = entry.get("at")
            else:
                continue
            if at:
                try:
                    stamp = datetime.fromisoformat(str(at))
                except ValueError:
                    continue
                if stamp.tzinfo is None:
                    stamp = stamp.astimezone()
                if not (start <= stamp < end):
                    continue
            text = self._masker.mask_text(_clean(text, 300))
            if text:
                kept.append(text)
            if len(kept) >= MAX_PHASE3:
                break
        return kept

    # ------------------------------------------------------------------ prose
    def _digest_text(self, digest: Digest) -> str:
        parts: list[str] = []
        if digest.sessions:
            parts.append(f"Sessions du jour ({len(digest.sessions)}) :")
            for session in digest.sessions:
                excerpts = " / ".join(session["excerpts"]) or "(pas de texte)"
                parts.append(
                    f"- {session['at'][11:16]} · {_clean(session['title'], 90) or '(sans titre)'} "
                    f"· {session['turns']} tour(s) : {excerpts}"
                )
        else:
            parts.append("Sessions du jour : aucune session enregistrée.")
        if digest.failures:
            parts.append(f"Échecs observés dans le journal d'audit ({len(digest.failures)}) :")
            for failure in digest.failures:
                parts.append(
                    f"- {failure['at']} · {failure['tool']} · {failure['status']} : "
                    f"{failure['detail'] or '(sans détail)'}"
                )
        else:
            parts.append(
                "Journal d'audit : aucun échec observé."
                if digest.audit_enabled
                else "Journal d'audit : désactivé."
            )
        memory = digest.memory or {}
        notes = _clean(memory.get("notes"), 700)
        topics = [t for t in (memory.get("topics") or []) if isinstance(t, str)]
        events = [e for e in (memory.get("events") or []) if isinstance(e, str)]
        if notes or topics or events:
            parts.append("Mémoire :")
            if notes:
                parts.append(f"- Notes : {notes}")
            if topics:
                parts.append("- Sujets en cours : " + " ; ".join(topics[:8]))
            if events:
                parts.append("- Nouveaux épisodes : " + " / ".join(events[:6]))
        if digest.recipes:
            parts.append("Recettes utilisées :")
            for recipe in digest.recipes:
                parts.append(
                    f"- {recipe.get('titre', '')} · {recipe.get('usages', 0)} usage(s), "
                    f"{recipe.get('succes', 0)} réussite(s), {recipe.get('echecs', 0)} échec(s)"
                )
        if digest.phase3:
            parts.append("Chronologie (phase 3) :")
            parts.extend(f"- {entry}" for entry in digest.phase3)
        return self._masker.mask_text("\n".join(parts))[:MAX_MODEL_CHARS]

    async def _voice(self, digest: Digest) -> dict[str, Any] | None:
        if self._llm is None:
            return None
        try:
            completion = await asyncio.wait_for(
                self._llm.ask(
                    [
                        {"role": "system", "content": _voice_system()},
                        {
                            "role": "user",
                            "content": "<releve>\n"
                            + self._digest_text(digest)
                            + "\n</releve>",
                        },
                    ],
                    temperature=0.6,
                    model=nightly_model(self._s),
                ),
                VOICE_TIMEOUT_S,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the note is still written without prose
            log.warning("veille : paragraphe impossible (%s: %s)", type(exc).__name__, exc)
            return None
        data = _json_object(completion.content)
        if not isinstance(data, dict):
            log.warning("veille : le modèle n'a pas rendu de JSON exploitable")
            return None
        resume = self._masker.mask_text(_clean(data.get("resume"), 900))
        items: list[dict[str, str]] = []
        raw_items = data.get("demain")
        for entry in raw_items if isinstance(raw_items, list) else []:
            if isinstance(entry, str):
                text, kind = entry, "idée"
            elif isinstance(entry, dict):
                text, kind = entry.get("texte"), entry.get("genre")
            else:
                continue
            text = self._masker.mask_text(_clean(text, 180))
            if text:
                items.append(
                    {"text": text, "kind": "rappel" if kind == "rappel" else "idée"}
                )
            if len(items) == 2:
                break
        return {"resume": resume, "demain": items}

    def _morning_items(
        self, digest: Digest, voice: dict[str, Any] | None
    ) -> list[dict[str, str]]:
        items = list((voice or {}).get("demain") or [])
        if items:
            return items[:2]
        # No model: observed fallbacks only (a topic to pick up, or a tool to look at).
        memory = digest.memory or {}
        topics = [
            t for t in (memory.get("topics") or []) if isinstance(t, str) and t.strip()
        ]
        if topics:
            return [
                {"text": f"Reprendre : {_clean(topic, 150)}", "kind": "rappel"}
                for topic in topics[:2]
            ]
        if digest.failures:
            tool = digest.failures[0]["tool"] or "un outil"
            return [
                {
                    "text": f"Regarder pourquoi « {tool} » a échoué.",
                    "kind": "rappel",
                }
            ]
        return [{"text": "Revenir sur ce qu'on s'est dit hier.", "kind": "idée"}]

    # ------------------------------------------------------------------ proposals
    def _detect_duplicates(self, items: list[Any]) -> list[Proposal]:
        proposals: list[Proposal] = []
        used: set[str] = set()
        candidates = [i for i in items if getattr(i, "kind", "") in ("sujet", "événement")]
        for index, first in enumerate(candidates):
            if first.id in used:
                continue
            for second in candidates[index + 1 :]:
                if second.id in used or first.kind != second.kind:
                    continue
                if _similar(first.text, second.text):
                    used.add(first.id)
                    used.add(second.id)
                    proposals.append(
                        Proposal(
                            id="p-" + secrets.token_hex(3),
                            date="",
                            kind="doublon",
                            title=_clean(f"Doublon : {first.text}", 160),
                            target=first.id,
                            drop=second.id,
                        )
                    )
                    break
        return proposals

    def _notes_rewrite(self, items: list[Any]) -> Proposal | None:
        notes = next(
            (i for i in items if getattr(i, "id", "") == "notes"), None
        )
        if notes is None or not notes.text.strip():
            return None
        seen: set[str] = set()
        kept: list[str] = []
        removed = False
        for line in notes.text.splitlines():
            key = _normalise(line.lstrip("-* ").strip())
            if key and key in seen:
                removed = True
                continue
            if key:
                seen.add(key)
            kept.append(line.rstrip())
        if not removed:
            return None
        return Proposal(
            id="p-" + secrets.token_hex(3),
            date="",
            kind="notes",
            title="Notes : lignes en double",
            target="notes",
            text=self._masker.mask_text("\n".join(kept).strip()[:1800]),
        )

    async def _contradictions(self, items: list[Any]) -> list[Proposal]:
        if self._llm is None:
            return []
        lines = []
        for item in items[:MAX_MEMORY_ITEMS]:
            if item.id == "notes":
                continue
            lines.append(f"- [{item.id}] {_clean(item.text, 300)}")
        if not lines:
            return []
        try:
            completion = await asyncio.wait_for(
                self._llm.ask(
                    [
                        {"role": "system", "content": _contradiction_system()},
                        {
                            "role": "user",
                            "content": "<carnet>\n" + "\n".join(lines) + "\n</carnet>",
                        },
                    ],
                    temperature=0.2,
                    model=nightly_model(self._s),
                ),
                CONTRADICTION_TIMEOUT_S,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - proposals are best effort
            log.warning("veille : contradictions impossibles (%s: %s)", type(exc).__name__, exc)
            return []
        data = _json_object(completion.content)
        if not isinstance(data, dict):
            return []
        known = {item.id: item for item in items if item.id != "notes"}
        proposals: list[Proposal] = []
        for entry in data.get("contradictions") or []:
            if not isinstance(entry, dict):
                continue
            target = str(entry.get("cible") or "")
            if target not in known:
                continue
            text = self._masker.mask_text(_clean(entry.get("texte"), 400))
            if not text or _normalise(text) == _normalise(known[target].text):
                continue
            proposals.append(
                Proposal(
                    id="p-" + secrets.token_hex(3),
                    date="",
                    kind="contradiction",
                    title=_clean(f"Contradiction : {known[target].text}", 160),
                    target=target,
                    text=text,
                )
            )
            if len(proposals) == 5:
                break
        return proposals

    async def _proposals(self, digest: Digest) -> list[Proposal]:
        proposals = self._detect_duplicates(digest.memory_items)
        notes = self._notes_rewrite(digest.memory_items)
        if notes is not None:
            proposals.append(notes)
        tried = {p.target for p in proposals if p.target}
        for proposal in await self._contradictions(digest.memory_items):
            if proposal.target not in tried:
                proposals.append(proposal)
                tried.add(proposal.target)
        for proposal in proposals:
            proposal.date = digest.day.isoformat()
        return proposals[:MAX_PROPOSALS]

    def _store_proposals(self, day: date, proposals: list[Proposal]) -> None:
        stored = self.load_proposals()
        kept = [p for p in stored if p.date != day.isoformat()]
        kept.extend(proposals)
        kept = kept[-MAX_PROBES:]
        payload = {"version": 1, "items": [p.to_json() for p in kept]}
        _atomic_write(
            self._paths.nightly_propositions,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )

    def load_proposals(self) -> list[Proposal]:
        data = _read_json(self._paths.nightly_propositions)
        raw = data.get("items") if isinstance(data, dict) else None
        out: list[Proposal] = []
        for entry in raw if isinstance(raw, list) else []:
            proposal = Proposal.from_json(entry)
            if proposal is not None:
                out.append(proposal)
        return out

    def pending(self) -> list[Proposal]:
        return self.load_proposals()

    async def resolve(self, proposal_id: str, accept: bool) -> str:
        """Accept (apply) or refuse one proposal; nothing else is changed."""
        proposal = next(
            (p for p in self.load_proposals() if p.id == proposal_id), None
        )
        if proposal is None:
            return f"Aucune proposition avec l'id {proposal_id}."
        if accept:
            if self._memory is None:
                return "La mémoire est désactivée : je ne peux pas appliquer cette proposition."
            try:
                if proposal.kind == "doublon" and proposal.drop:
                    removed = await self._memory.delete(proposal.drop)
                    if removed is None:
                        return "L'entrée à retirer n'existe plus : rien n'a changé."
                elif proposal.kind in ("contradiction", "notes") and proposal.target:
                    if not await self._memory.update(proposal.target, proposal.text):
                        return "L'entrée à réécrire n'existe plus : rien n'a changé."
                else:
                    return "Cette proposition est incomplète : rien n'a changé."
            except Exception as exc:  # noqa: BLE001
                return f"Impossible d'appliquer la proposition ({type(exc).__name__}: {exc})."
        items = [p for p in self.load_proposals() if p.id != proposal_id]
        payload = {"version": 1, "items": [p.to_json() for p in items]}
        _atomic_write(
            self._paths.nightly_propositions,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )
        return (
            f"Proposition {proposal_id} appliquée."
            if accept
            else f"Proposition {proposal_id} refusée : rien n'a changé."
        )

    # ------------------------------------------------------------------ the note
    def _journal_folder(self) -> Path | None:
        vault = self._s.obsidian_vault
        if vault is None or not vault.is_dir():
            return None
        relative = Path(self._s.obsidian_journal_dir or "Monika/Journal")
        if relative.is_absolute() or ".." in relative.parts:
            return None
        return vault / relative

    def _note_path(self, day: date) -> Path | None:
        folder = self._journal_folder()
        return None if folder is None else folder / f"{day.isoformat()}{NOTE_SUFFIX}"

    def _write_note(
        self,
        day: date,
        digest: Digest,
        voice: dict[str, Any] | None,
        proposals: list[Proposal],
        morning_items: list[dict[str, str]],
    ) -> Path | None:
        path = self._note_path(day)
        if path is None:
            return None
        if not path.parent.exists():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                log.warning("veille : dossier du coffre impossible (%s)", exc)
                return None
        if path.exists():
            try:
                existing = path.read_text(encoding="utf-8")
            except OSError:
                existing = ""
            if NOTE_MARKER not in existing:
                log.warning("veille : %s existe et n'est pas une note générée, je la laisse", path)
                return None
        text = self._masker.mask_text(
            build_note(day, digest, voice, proposals, morning_items)
        )
        try:
            _atomic_write(path, text)
        except OSError as exc:
            log.warning("veille : écriture de la note impossible (%s)", exc)
            return None
        return path

    def notes(self, limit: int = 60) -> list[dict[str, Any]]:
        """Generated notes, newest first."""
        folder = self._journal_folder()
        if folder is None or not folder.is_dir():
            return []
        found: list[dict[str, Any]] = []
        for path in folder.glob(f"*{NOTE_SUFFIX}"):
            match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})", path.stem)
            if match is None:
                continue
            try:
                head = path.read_text(encoding="utf-8")[:400]
                size = path.stat().st_size
            except OSError:
                continue
            if NOTE_MARKER not in head:
                continue
            found.append({"day": match.group(1), "path": str(path), "size": size})
        found.sort(key=lambda item: item["day"], reverse=True)
        return found[: max(1, limit)]

    def read_note(self, day: str) -> str | None:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or "") is None:
            return None
        path = self._note_path(date.fromisoformat(day))
        if path is None or not path.is_file():
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        return text if NOTE_MARKER in text[:400] else None

    def delete_note(self, day: str) -> bool:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or "") is None:
            return False
        path = self._note_path(date.fromisoformat(day))
        if path is None or not path.is_file():
            return False
        try:
            if NOTE_MARKER not in path.read_text(encoding="utf-8")[:400]:
                return False
            path.unlink()
        except OSError:
            return False
        items = [p for p in self.load_proposals() if p.date != day]
        _atomic_write(
            self._paths.nightly_propositions,
            json.dumps(
                {"version": 1, "items": [p.to_json() for p in items]},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        return True

    def summary_text(self) -> str:
        state = self._state()
        pending = len(self.pending())
        notes = self.notes()
        lines = [
            f"Veille nocturne : {self._s.nightly_time} · coffre "
            f"{self._s.obsidian_vault or '(non défini)'}",
        ]
        if state.get("last_success"):
            lines.append(f"Dernière note réussie : {state['last_success'][:16]}")
        elif state.get("last_due"):
            lines.append(f"Dernière nuit traitée : {state['last_due']} (sans note)")
        if state.get("last_error"):
            lines.append(f"Dernier problème : {state['last_error']}")
        lines.append(
            f"Notes générées : {len(notes)}"
            + (f" (la plus récente : {notes[0]['day']})" if notes else "")
        )
        lines.append(f"Propositions en attente : {pending}")
        if pending:
            for proposal in self.pending()[:6]:
                lines.append(
                    f"  [{proposal.id}] {proposal.kind} · {proposal.title}"
                )
        lines.append(
            "Commandes : /journal-nuit voir DATE · /journal-nuit supprimer DATE · "
            "/journal-nuit accepter ID · /journal-nuit refuser ID"
        )
        return "\n".join(lines)


def build_note(
    day: date,
    digest: Digest,
    voice: dict[str, Any] | None,
    proposals: list[Proposal],
    morning_items: list[dict[str, str]],
) -> str:
    """The Markdown note. Stable shape: frontmatter, title, sources, proposals, tomorrow."""
    previous = day - timedelta(days=1)
    following = day + timedelta(days=1)
    resume = (voice or {}).get("resume") or (
        "Cette nuit, j'ai relu notre journée. Voici ce que j'en garde, sans rien y ajouter."
    )
    frontmatter = [
        "---",
        f"date: {day.isoformat()}",
        f"nuit: {day.isoformat()}",
        NOTE_MARKER,
        "tags: [monika, journal, nuit]",
        f"created: {_now().isoformat(timespec='seconds')}",
        f"sessions: {len(digest.sessions)}",
        f"tours: {digest.turns}",
        f"echecs: {len(digest.failures)}",
        f'liens: ["[[{previous.isoformat()}]]", "[[{following.isoformat()}]]"]',
        "---",
        "",
    ]
    lines = frontmatter + [
        f"# Nuit du {_fr_date(day)}",
        "",
        f"[[{previous.isoformat()}]] · [[{following.isoformat()}]]",
        "",
        f"*{resume}*",
        "",
        "## Sessions",
    ]
    if not digest.sessions_enabled:
        lines.append(
            "Les sessions ne sont pas enregistrées (SESSIONS_ENABLED=false) : je n'ai pas de trace de la journée."
        )
    elif not digest.sessions:
        lines.append("Aucune session enregistrée aujourd'hui.")
    else:
        for session in digest.sessions:
            title = _clean(session.get("title"), 100) or "(sans titre)"
            excerpts = " · ".join(
                f"« {_clean(excerpt, EXCERPT_CHARS)} »"
                for excerpt in session.get("excerpts") or []
            )
            lines.append(
                f"- **{str(session.get('at'))[11:16]} — {title}** "
                f"({session.get('turns', 0)} tour(s))"
                + (f" : {excerpts}" if excerpts else "")
            )

    lines += ["", "## Ce qui a raté"]
    if not digest.audit_enabled:
        lines.append("Journal d'audit désactivé (AUDIT_ENABLED=false) : rien à observer.")
    elif not digest.audit_available:
        lines.append("Journal d'audit illisible : je ne peux rien affirmer sur les échecs.")
    elif not digest.failures:
        lines.append("Rien n'a échoué dans le journal d'audit.")
    else:
        for failure in digest.failures:
            detail = f" — {failure['detail']}" if failure["detail"] else ""
            lines.append(
                f"- {failure['at']} · `{failure['tool']}` · {failure['status']}{detail}"
            )

    lines += ["", "## Mémoire"]
    memory = digest.memory
    if memory is None:
        lines.append("Mémoire désactivée ou indisponible : je n'ai rien relu.")
    else:
        rewrites = int(memory.get("notes_rewrites") or 0)
        lines.append(f"- Faits ajoutés aujourd'hui : {rewrites}.")
        topics = [t for t in (memory.get("topics") or []) if isinstance(t, str)]
        events = [e for e in (memory.get("events") or []) if isinstance(e, str)]
        if topics:
            lines.append("- Sujets encore ouverts : " + " ; ".join(topics[:8]))
        if events:
            lines.append("- Épisodes retenus aujourd'hui : " + " / ".join(events[:6]))
        if not topics and not events:
            lines.append("- Rien de nouveau retenu aujourd'hui.")

    lines += ["", "## Recettes utilisées"]
    if not digest.recipes_enabled:
        lines.append("Recettes désactivées (RECIPES_ENABLED=false).")
    elif not digest.recipes:
        lines.append("Aucune recette utilisée aujourd'hui.")
    else:
        for recipe in digest.recipes:
            lines.append(
                f"- `{_clean(recipe.get('titre'), 100)}` — {recipe.get('usages', 0)} usage(s), "
                f"{recipe.get('succes', 0)} réussite(s), {recipe.get('echecs', 0)} échec(s)"
            )

    if digest.phase3 is not None:
        lines += ["", "## Chronologie (phase 3)"]
        if digest.phase3:
            lines.extend(f"- {entry}" for entry in digest.phase3)
        else:
            lines.append("Aucun événement de la phase 3 aujourd'hui.")

    lines += ["", "## Propositions de consolidation", ""]
    if not proposals:
        lines.append("Rien à consolider ce soir.")
    else:
        lines.append(
            "Rien n'est appliqué sans ton accord. Pour chaque proposition :"
        )
        for proposal in proposals:
            command = (
                f"`/journal-nuit accepter {proposal.id}` · "
                f"`/journal-nuit refuser {proposal.id}`"
            )
            lines.append(f"- **[{proposal.kind}]** {proposal.title} — {command}")

    lines += ["", "## Pour demain"]
    if morning_items:
        for item in morning_items:
            kind = "rappel" if item.get("kind") == "rappel" else "idée"
            lines.append(f"- ({kind}) {_clean(item.get('text'), 180)}")
    else:
        lines.append("- (idée) Revenir sur ce qu'on s'est dit hier.")
    return "\n".join(lines).rstrip() + "\n"
