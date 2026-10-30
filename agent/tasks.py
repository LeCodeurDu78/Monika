"""Scheduled tasks: reminders and recurring jobs, kept on disk and fired by the running process.

A task is a sentence plus a moment. When the moment comes, the kernel (or the local chat) runs a
full turn with that sentence: Monika phrases the reminder herself, with her tools, and every
attached terminal sees the answer. Nothing is scheduled behind the user's back: only `/rappel` (or
the `taches_planifier` tool, when the user asks for one) can create a task.

Two forms only, written the way one says them:

    dans 20 minutes · dans 2 heures · dans 3 jours · 18h30 · demain 8h
    toutes les 2 heures · toutes les 30 minutes · tous les 3 jours

A task is not a systemd timer: it fires only while a Monika process is running. Whatever came due
while nothing was running is fired at the next start, saying it is late. Files are written
atomically in `0600`, and the text is masked before it touches the disk.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from security.masking import Masker

log = logging.getLogger(__name__)

POLL_S = 20.0                # How often the running process looks for due tasks
LATE_AFTER = timedelta(minutes=2)  # Past this, the fired turn is told the reminder is late
MAX_DELAY = timedelta(days=365)

MINUTE = 60
HOUR = 60 * MINUTE
DAY = 24 * HOUR

_UNITS = {"m": MINUTE, "min": MINUTE, "mins": MINUTE, "minute": MINUTE, "minutes": MINUTE,
          "h": HOUR, "heure": HOUR, "heures": HOUR,
          "j": DAY, "jour": DAY, "jours": DAY}
_EVERY = re.compile(r"^(?:tous\s+les|toutes\s+les|chaque)\s+(\d{1,4})\s*([a-zé]+)\b",
                    re.IGNORECASE)
_DELAY = re.compile(r"^dans\s+(\d{1,4})\s*([a-zé]+)\b", re.IGNORECASE)
_CLOCK = re.compile(r"^(?:(demain)\s+)?(\d{1,2})\s*(?:h|:)\s*(\d{1,2})?$", re.IGNORECASE)


class TasksError(Exception):
    """The task or the moment could not be understood; the message is ready to display."""


@dataclass(frozen=True)
class When:
    """A parsed moment: when it fires next, and how often afterwards (None = only once)."""

    at: datetime
    every_s: int | None
    says: str  # What to show back, in French ("demain à 08:00", "toutes les 2 heures…")


@dataclass(frozen=True)
class Task:
    """One scheduled sentence."""

    id: str
    prompt: str
    next_at: datetime
    every_s: int | None
    created_at: datetime
    runs: int = 0

    @property
    def recurring(self) -> bool:
        return self.every_s is not None

    def describe(self) -> str:
        """One line for `/taches`."""
        when = f"{self.next_at:%d/%m %H:%M}"
        if self.recurring:
            when += f" (toutes les {_duration(self.every_s)})"
        return f"  [{self.id}] {when} · {self.prompt}"

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "prompt": self.prompt, "next_at": self.next_at.isoformat(),
                "every_s": self.every_s, "created_at": self.created_at.isoformat(), "runs": self.runs}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Task:
        return cls(
            id=str(data["id"]),
            prompt=str(data.get("prompt", "")),
            next_at=datetime.fromisoformat(str(data["next_at"])),
            every_s=int(data["every_s"]) if data.get("every_s") else None,
            created_at=datetime.fromisoformat(str(data.get("created_at", data["next_at"]))),
            runs=int(data.get("runs", 0)),
        )


def _duration(seconds: int) -> str:
    """A duration the way one says it: « 2 heures », « 90 minutes »."""
    if seconds % DAY == 0:
        n, unit = seconds // DAY, "jour"
    elif seconds % HOUR == 0:
        n, unit = seconds // HOUR, "heure"
    else:
        n, unit = seconds // MINUTE, "minute"
    return f"{n} {unit}{'s' if n > 1 else ''}"


def _unit(token: str) -> int:
    seconds = _UNITS.get(token)
    if seconds is None:
        raise TasksError(f"Je ne comprends pas « {token} » : écris minutes, heures ou jours.")
    return seconds


def parse_when(text: str, now: datetime | None = None) -> When:
    """Read a moment the way the user writes it (« dans 20 minutes », « demain 8h », « toutes les 2 h »)."""
    now = now or datetime.now()
    raw = " ".join(text.strip().lower().split())
    if not raw:
        raise TasksError("Quand ? « dans 20 minutes », « 18h30 », « demain 8h », "
                         "« toutes les 2 heures »…")

    every = _EVERY.match(raw)
    if every:
        seconds = int(every.group(1)) * _unit(every.group(2))
        if seconds <= 0:
            raise TasksError("L'intervalle doit être d'au moins 1 minute.")
        if seconds > MAX_DELAY.total_seconds():
            raise TasksError("L'intervalle dépasse un an.")
        at = now + timedelta(seconds=seconds)
        return When(at, seconds, f"toutes les {_duration(seconds)} (prochaine fois {_human_when(at, now)})")

    delay = _DELAY.match(raw)
    if delay:
        seconds = int(delay.group(1)) * _unit(delay.group(2))
        if seconds <= 0:
            raise TasksError("Le délai doit être d'au moins 1 minute.")
        if seconds > MAX_DELAY.total_seconds():
            raise TasksError("Le délai dépasse un an.")
        return When(now + timedelta(seconds=seconds), None, _human_when(now + timedelta(seconds=seconds), now))

    clock = _CLOCK.match(raw)
    if clock:
        tomorrow = bool(clock.group(1))
        hour = int(clock.group(2))
        minute = int(clock.group(3) or 0)
        if hour > 23 or minute > 59:
            raise TasksError(f"« {text.strip()} » n'est pas une heure valide.")
        at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if not tomorrow and at <= now:
            at += timedelta(days=1)  # « 8h » quand il est 20h : c'est demain
        elif tomorrow:
            at += timedelta(days=1)
        return When(at, None, _human_when(at, now))

    raise TasksError(f"Je ne comprends pas « {text.strip()} ». Dis : « dans 20 minutes », "
                     "« 18h30 », « demain 8h », « toutes les 2 heures ».")


def _human_when(at: datetime, now: datetime | None = None) -> str:
    """« demain à 08:00 », from today's point of view."""
    now = now or datetime.now()
    if at.date() == now.date():
        day = "aujourd'hui"
    elif at.date() == (now + timedelta(days=1)).date():
        day = "demain"
    else:
        day = f"le {at:%d/%m}"
    return f"{day} à {at:%H:%M}"


class TaskStore:
    """The tasks of one data folder: a small JSON file, rewritten atomically on every change."""

    def __init__(self, path: Path, masker: Masker | None = None, max_tasks: int = 50) -> None:
        self.path = path
        self._masker = masker or Masker()
        self.max_tasks = max(1, max_tasks)
        self._tasks: list[Task] = []
        self.load()

    # ------------------------------------------------------------------ reading
    def load(self) -> list[Task]:
        """Read the file; a broken one is reported and treated as empty, never as a crash."""
        self._tasks = []
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError as exc:
            log.warning("Tâches planifiées illisibles (%s)", exc)
            return []
        try:
            data = json.loads(raw)
            for item in data.get("tasks", []):
                self._tasks.append(Task.from_json(item))
        except (ValueError, KeyError, TypeError) as exc:
            log.warning("Fichier de tâches illisible (%s: %s) : je repars de zéro", type(exc).__name__, exc)
            self._tasks = []
        self._tasks.sort(key=lambda t: t.next_at)
        return list(self._tasks)

    def all(self) -> list[Task]:
        return list(self._tasks)

    def get(self, task_id: str) -> Task | None:
        for task in self._tasks:
            if task.id == task_id:
                return task
        return None

    def due(self, now: datetime | None = None) -> list[Task]:
        moment = now or datetime.now()
        return [t for t in self._tasks if t.next_at <= moment]

    # ------------------------------------------------------------------ writing
    def add(self, prompt: str, when: When, now: datetime | None = None) -> Task:
        prompt = " ".join(prompt.split())
        if not prompt:
            raise TasksError("Il manque le texte de la tâche : « /rappel dans 20 minutes : boire de l'eau ».")
        if len(prompt) > 500:
            raise TasksError("La tâche dépasse 500 caractères : résume-la.")
        if len(self._tasks) >= self.max_tasks:
            raise TasksError(f"Trop de tâches ({self.max_tasks}) : annule-en une avec "
                             "« /tache annuler ID ».")
        task = Task(id=secrets.token_hex(4), prompt=self._masker.mask_text(prompt), next_at=when.at,
                    every_s=when.every_s, created_at=now or datetime.now())
        self._tasks.append(task)
        self._tasks.sort(key=lambda t: t.next_at)
        self._save()
        return task

    def cancel(self, task_id: str) -> Task | None:
        task = self.get(task_id)
        if task is None:
            return None
        self._tasks = [t for t in self._tasks if t.id != task_id]
        self._save()
        return task

    def finished(self, task: Task, when: datetime | None = None) -> Task | None:
        """Mark a task as run: a one-shot disappears, a recurring one gets its next moment."""
        moment = when or datetime.now()
        current = self.get(task.id)
        if current is None:
            return None
        if current.every_s is None:
            self._tasks = [t for t in self._tasks if t.id != task.id]
            self._save()
            return None
        moved = Task(id=current.id, prompt=current.prompt, next_at=moment + timedelta(seconds=current.every_s),
                     every_s=current.every_s, created_at=current.created_at, runs=current.runs + 1)
        self._tasks = sorted((moved if t.id == task.id else t for t in self._tasks),
                             key=lambda t: t.next_at)
        self._save()
        return moved

    def _save(self) -> None:
        """Atomic, private, and never fatal: a task that cannot be written must not break a turn."""
        payload = {"version": 1, "tasks": [t.to_json() for t in self._tasks]}
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("Tâches non enregistrées (%s)", exc)


def task_prompt(task: Task, late: timedelta) -> str:
    """What the model is asked when a task fires; a late one is told to say so."""
    if late > LATE_AFTER:
        return (f"(Rappel prévu à {task.next_at:%H:%M}, il est {datetime.now():%H:%M} : dis en une "
                f"phrase que tu es en retard.) {task.prompt}")
    return task.prompt


class Scheduler:
    """Fires what is due, one task at a time, and reschedules the recurring ones."""

    def __init__(self, store: TaskStore, fire: Callable[[str], Awaitable[None]],
                 poll_s: float = POLL_S) -> None:
        self.store = store
        self._fire = fire
        self._poll_s = max(1.0, poll_s)
        self._task: asyncio.Task | None = None

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

    async def tick(self) -> list[Task]:
        """Run every task that is due now; returns them (also used by the tests)."""
        fired: list[Task] = []
        now = datetime.now()
        for task in self.store.due(now):
            late = now - task.next_at
            try:
                await self._fire(task_prompt(task, late))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a failed task must not stop the others
                log.warning("Tâche %s en échec (%s: %s)", task.id, type(exc).__name__, exc)
            finally:
                self.store.finished(task)
            fired.append(task)
        return fired

    async def _loop(self) -> None:
        # First pass right away: whatever came due while nothing was running fires at startup.
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("Planificateur : %s: %s", type(exc).__name__, exc)
            await asyncio.sleep(self._poll_s)
