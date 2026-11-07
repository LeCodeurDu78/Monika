"""Scheduled tasks: reminders and recurring jobs, kept on disk and fired by the running process.

A task is a sentence plus a moment. When the moment comes, the running process runs a full turn
with that sentence: Monika phrases the reminder herself, with her tools, and the page shows the
answer. Nothing is scheduled behind the user's back: only `/rappel` (or
the `taches_planifier` tool, when the user asks for one) can create a task.

Two forms only, written the way one says them:

    dans 20 minutes · dans 2 heures · dans 3 jours · 18h30 · demain 8h
    toutes les 2 heures · toutes les 30 minutes · tous les 3 jours
    chaque jour à 8h · tous les jours à 08:30
    chaque lundi à 9h · chaque lundi et jeudi à 9h · du lundi au vendredi à 9h

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

from monika.i18n import tr, tr_list
from security.masking import Masker

log = logging.getLogger(__name__)

POLL_S = 20.0  # How often the running process looks for due tasks
LATE_AFTER = timedelta(
    minutes=2
)  # Past this, the fired turn is told the reminder is late
MAX_DELAY = timedelta(days=365)

MINUTE = 60
HOUR = 60 * MINUTE
DAY = 24 * HOUR

_UNITS = {
    "m": MINUTE,
    "min": MINUTE,
    "mins": MINUTE,
    "minute": MINUTE,
    "minutes": MINUTE,
    "h": HOUR,
    "heure": HOUR,
    "heures": HOUR,
    "hour": HOUR,
    "hours": HOUR,
    "j": DAY,
    "jour": DAY,
    "jours": DAY,
    "day": DAY,
    "days": DAY,
}
_EVERY = re.compile(
    r"^(?:tous\s+les|toutes\s+les|chaque|every)\s+(\d{1,4})\s*([a-zé]+)\b", re.IGNORECASE
)
_DELAY = re.compile(r"^(?:dans|in)\s+(\d{1,4})\s*([a-zé]+)\b", re.IGNORECASE)
_CLOCK = re.compile(
    r"^(?:(demain)\s+)?(\d{1,2})\s*(?:h|:)\s*(\d{1,2})?$", re.IGNORECASE
)
_DAILY = re.compile(
    r"^(?:tous\s+les\s+jours|chaque\s+jour)(?:\s*,?\s*(?:à|a))?\s+"
    r"(\d{1,2})\s*(?:h|:)\s*(\d{1,2})?$",
    re.IGNORECASE,
)
_DAILY_EN = re.compile(
    r"^every\s+day(?:\s*,?\s*at)?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$",
    re.IGNORECASE,
)
_CLOCK_EN = re.compile(
    r"^(?:(tomorrow)\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)$", re.IGNORECASE
)
# Python's Monday is 0; these are the names one says
_WEEKDAYS = {
    "lundi": 0, "monday": 0,
    "mardi": 1, "tuesday": 1,
    "mercredi": 2, "wednesday": 2,
    "jeudi": 3, "thursday": 3,
    "vendredi": 4, "friday": 4,
    "samedi": 5, "saturday": 5,
    "dimanche": 6, "sunday": 6,
}
_WEEKDAY_NAMES = (
    "lundi",
    "mardi",
    "mercredi",
    "jeudi",
    "vendredi",
    "samedi",
    "dimanche",
)
_DAY_WORD = r"(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche|monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?"
_TIME_TAIL = re.compile(
    r"\s*(?:,?\s*(?:à|a))?\s*(\d{1,2})\s*(?:h|:)\s*(\d{1,2})?$", re.IGNORECASE
)
_TIME_TAIL_EN = re.compile(
    r"\s*(?:,?\s*at)?\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", re.IGNORECASE
)
_DAY_RANGE = re.compile(
    rf"^(?:du|from)\s+{_DAY_WORD}\s+(?:au|to)\s+{_DAY_WORD}$", re.IGNORECASE
)
_DAY_PREFIX = re.compile(
    r"^(?:tous\s+les|toutes\s+les|chaque|every|on)\s+(.+)$", re.IGNORECASE
)
_DAY_LIST_SEP = re.compile(r"\s*(?:,|&|\bet\b|\band\b)\s*", re.IGNORECASE)


class TasksError(Exception):
    """The task or the moment could not be understood; the message is ready to display."""


@dataclass(frozen=True)
class When:
    """A parsed moment: when it fires next, and how often afterwards (None = only once)."""

    at: datetime
    every_s: int | None
    says: str  # What to show back, in French ("demain à 08:00", "toutes les 2 heures…")
    daily_at: str | None = None  # "08:00": a fixed time, not a 24 h delay that drifts
    days: tuple[int, ...] | None = (
        None  # « chaque lundi et jeudi à 9h »: 0 = lundi, sorted
    )


@dataclass(frozen=True)
class Task:
    """One scheduled sentence."""

    id: str
    prompt: str
    next_at: datetime
    every_s: int | None
    created_at: datetime
    runs: int = 0
    daily_at: str | None = None  # "08:00": fires every day at that time
    days: tuple[int, ...] | None = (
        None  # 0 = lundi: every week, on those days, at `daily_at`
    )

    @property
    def recurring(self) -> bool:
        return self.every_s is not None or self.daily_at is not None

    def describe(self) -> str:
        """One line for `/tasks`."""
        when = f"{self.next_at:%d/%m %H:%M}"
        if self.days:
            when += f" ({tr('tasks.describe_weekly', days=_days_phrase(self.days), time=self.daily_at)})"
        elif self.daily_at is not None:
            when += f" ({tr('tasks.describe_daily', time=self.daily_at)})"
        elif self.every_s is not None:
            when += f" ({tr('tasks.describe_every', duration=_duration(self.every_s))})"
        return f"  [{self.id}] {when} · {self.prompt}"

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "next_at": self.next_at.isoformat(),
            "every_s": self.every_s,
            "created_at": self.created_at.isoformat(),
            "runs": self.runs,
            "daily_at": self.daily_at,
            "days": list(self.days) if self.days else None,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Task:
        return cls(
            id=str(data["id"]),
            prompt=str(data.get("prompt", "")),
            next_at=datetime.fromisoformat(str(data["next_at"])),
            every_s=int(data["every_s"]) if data.get("every_s") else None,
            created_at=datetime.fromisoformat(
                str(data.get("created_at", data["next_at"]))
            ),
            runs=int(data.get("runs", 0)),
            daily_at=str(data["daily_at"]) if data.get("daily_at") else None,
            days=_read_days(data),
        )


def _duration(seconds: int) -> str:
    """A duration the way one says it: « 2 heures », “2 hours”."""
    if seconds % DAY == 0:
        n, unit = seconds // DAY, "day"
    elif seconds % HOUR == 0:
        n, unit = seconds // HOUR, "hour"
    else:
        n, unit = seconds // MINUTE, "minute"
    return tr(f"tasks.duration.{unit}.{'one' if n == 1 else 'other'}", n=n)


def _unit(token: str) -> int:
    seconds = _UNITS.get(token)
    if seconds is None:
        raise TasksError(tr("tasks.unknown_unit", token=token))
    return seconds


def parse_when(text: str, now: datetime | None = None) -> When:
    """Read a moment the way the user writes it (« dans 20 minutes », « demain 8h », « toutes les 2 h »)."""
    now = now or datetime.now()
    raw = " ".join(text.strip().lower().split())
    if not raw:
        raise TasksError(tr("tasks.when_required"))

    weekly = _parse_weekly(raw, text, now)
    if weekly is not None:
        return weekly

    daily = _DAILY.match(raw) or _DAILY_EN.match(raw)
    if daily:
        hour = int(daily.group(1))
        minute = int(daily.group(2) or 0)
        ampm = daily.group(3) if daily.re is _DAILY_EN else None
        hour = _hour24(hour, ampm)
        if hour > 23 or minute > 59:
            raise TasksError(tr("tasks.invalid_time", text=text.strip()))
        at_time = f"{hour:02d}:{minute:02d}"
        at = _next_daily(at_time, now)
        return When(
            at,
            None,
            tr("tasks.daily", time=at_time, next=_human_when(at, now)),
            daily_at=at_time,
        )

    every = _EVERY.match(raw)
    if every:
        seconds = int(every.group(1)) * _unit(every.group(2))
        if seconds <= 0:
            raise TasksError(tr("tasks.interval_min"))
        if seconds > MAX_DELAY.total_seconds():
            raise TasksError(tr("tasks.interval_max"))
        at = now + timedelta(seconds=seconds)
        return When(
            at,
            seconds,
            tr("tasks.every", duration=_duration(seconds), next=_human_when(at, now)),
        )

    delay = _DELAY.match(raw)
    if delay:
        seconds = int(delay.group(1)) * _unit(delay.group(2))
        if seconds <= 0:
            raise TasksError(tr("tasks.delay_min"))
        if seconds > MAX_DELAY.total_seconds():
            raise TasksError(tr("tasks.delay_max"))
        return When(
            now + timedelta(seconds=seconds),
            None,
            _human_when(now + timedelta(seconds=seconds), now),
        )

    clock = _CLOCK.match(raw)
    english_clock = _CLOCK_EN.match(raw) if clock is None else None
    if clock or english_clock:
        tomorrow = bool((clock or english_clock).group(1))
        hour = int((clock or english_clock).group(2))
        minute = int(((clock or english_clock).group(3) or 0))
        if english_clock is not None:
            hour = _hour24(hour, english_clock.group(4))
        if hour > 23 or minute > 59:
            raise TasksError(tr("tasks.invalid_time", text=text.strip()))
        at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if not tomorrow and at <= now:
            at += timedelta(days=1)  # « 8h » quand il est 20h : c'est demain
        elif tomorrow:
            at += timedelta(days=1)
        return When(at, None, _human_when(at, now))

    raise TasksError(tr("tasks.unknown_when", text=text.strip()))


def _at_that_time(at_time: str, now: datetime) -> datetime:
    hour, minute = (int(part) for part in at_time.split(":"))
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _next_daily(at_time: str, now: datetime) -> datetime:
    """The next occurrence of that time of day: today if it has not passed yet, else tomorrow."""
    at = _at_that_time(at_time, now)
    return at if at > now else at + timedelta(days=1)


def _next_weekly(days: tuple[int, ...], at_time: str, now: datetime) -> datetime:
    """The nearest of those weekdays, at that time, strictly after now (within a week)."""
    base = _at_that_time(at_time, now)
    moments = []
    for day in days:
        at = base + timedelta(days=(day - now.weekday()) % 7)
        moments.append(at if at > now else at + timedelta(days=7))
    return min(moments)


def _read_days(data: dict[str, Any]) -> tuple[int, ...] | None:
    """Read the weekdays; older files stored a single one under `weekday`."""
    raw = data.get("days")
    if raw:
        return tuple(sorted({int(day) for day in raw}))
    if data.get("weekday") is not None:
        return (int(data["weekday"]),)
    return None


def _days_phrase(days: tuple[int, ...]) -> str:
    """ « chaque lundi », “every Monday and Thursday”, “Monday to Friday”. """
    names = tr_list("tasks.weekdays") or list(_WEEKDAY_NAMES)
    chosen = [names[day] for day in days]
    if len(days) >= 3 and all(days[i] + 1 == days[i + 1] for i in range(len(days) - 1)):
        return tr("tasks.day_range", first=chosen[0], last=chosen[-1])
    if len(chosen) == 1:
        return tr("tasks.each_day", day=chosen[0])
    return tr("tasks.day_list", days=", ".join(chosen[:-1]) + f" {tr('tasks.and')} " + chosen[-1])


def _parse_weekly(raw: str, original: str, now: datetime) -> When | None:
    """« chaque lundi à 9h », « lundi et jeudi à 9h », « du lundi au vendredi à 9h »."""
    tail = _TIME_TAIL.search(raw)
    english = False
    if tail is None:
        tail = _TIME_TAIL_EN.search(raw)
        english = tail is not None
    if tail is None:
        return None
    days = _parse_days(raw[: tail.start()])
    if not days:
        return None
    hour, minute = int(tail.group(1)), int(tail.group(2) or 0)
    if english:
        hour = _hour24(hour, tail.group(3))
    if hour > 23 or minute > 59:
        raise TasksError(tr("tasks.invalid_time", text=original.strip()))
    at_time = f"{hour:02d}:{minute:02d}"
    at = _next_weekly(days, at_time, now)
    return When(
        at,
        None,
        tr("tasks.weekly", days=_days_phrase(days), time=at_time, next=_human_when(at, now)),
        daily_at=at_time,
        days=days,
    )


def _parse_days(text: str) -> tuple[int, ...] | None:
    """Weekday numbers from a day phrase; None when it is not about weekdays at all."""
    phrase = " ".join(text.strip().strip(",").split()).lower()
    if not phrase:
        return None
    if phrase.startswith(("du ", "de ", "from ")):
        match = _DAY_RANGE.match(phrase)
        if match is None:
            return None
        start, end = _WEEKDAYS[match.group(1).lower()], _WEEKDAYS[match.group(2).lower()]
        days, day = [start], start
        while day != end:  # « du vendredi au lundi » fait le tour de la semaine
            day = (day + 1) % 7
            days.append(day)
        return tuple(sorted(days))  # same order after a reload
    prefix = _DAY_PREFIX.match(phrase)
    if prefix is None:
        return None
    days = set()
    for word in _DAY_LIST_SEP.split(prefix.group(1)):
        name = word.strip().lower()
        if name.endswith("s") and name not in _WEEKDAYS:
            name = name[:-1]
        if name not in _WEEKDAYS:
            return None
        days.add(_WEEKDAYS[name])
    return tuple(sorted(days)) or None


def _human_when(at: datetime, now: datetime | None = None) -> str:
    """ “tomorrow at 08:00”, from today's point of view. """
    now = now or datetime.now()
    names = tr_list("tasks.weekdays") or list(_WEEKDAY_NAMES)
    if at.date() == now.date():
        day = tr("tasks.today")
    elif at.date() == (now + timedelta(days=1)).date():
        day = tr("tasks.tomorrow")
    else:
        day = f"{names[at.weekday()]} {at:%d/%m}"
    return tr("tasks.at", day=day, time=f"{at:%H:%M}")


def _hour24(hour: int, ampm: str | None) -> int:
    """Convert a 12-hour clock to 24 hours; leave a 24-hour value alone."""
    if not ampm:
        return hour
    if not 1 <= hour <= 12:
        return hour
    return (hour % 12) + (12 if ampm.lower() == "pm" else 0)


class TaskStore:
    """The tasks of one data folder: a small JSON file, rewritten atomically on every change."""

    def __init__(
        self, path: Path, masker: Masker | None = None, max_tasks: int = 50
    ) -> None:
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
            log.warning(
                "Fichier de tâches illisible (%s: %s) : je repars de zéro",
                type(exc).__name__,
                exc,
            )
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
            raise TasksError(tr("tasks.missing_text"))
        if len(prompt) > 500:
            raise TasksError(tr("tasks.too_long"))
        if len(self._tasks) >= self.max_tasks:
            raise TasksError(tr("tasks.too_many", max=self.max_tasks))
        task = Task(
            id=secrets.token_hex(4),
            prompt=self._masker.mask_text(prompt),
            next_at=when.at,
            every_s=when.every_s,
            created_at=now or datetime.now(),
            daily_at=when.daily_at,
            days=when.days,
        )
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
        if (
            current.daily_at is not None
        ):  # Fixed time of day: no drift from the moment it ran
            next_at = (
                _next_weekly(current.days, current.daily_at, moment)
                if current.days
                else _next_daily(current.daily_at, moment)
            )
        elif current.every_s is not None:
            next_at = moment + timedelta(seconds=current.every_s)
        else:
            self._tasks = [t for t in self._tasks if t.id != task.id]
            self._save()
            return None
        moved = Task(
            id=current.id,
            prompt=current.prompt,
            next_at=next_at,
            every_s=current.every_s,
            created_at=current.created_at,
            runs=current.runs + 1,
            daily_at=current.daily_at,
            days=current.days,
        )
        self._tasks = sorted(
            (moved if t.id == task.id else t for t in self._tasks),
            key=lambda t: t.next_at,
        )
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
        return tr(
            "tasks.late",
            planned=f"{task.next_at:%H:%M}",
            now=f"{datetime.now():%H:%M}",
            prompt=task.prompt,
        )
    return task.prompt


class Scheduler:
    """Fires what is due, one task at a time, and reschedules the recurring ones."""

    def __init__(
        self,
        store: TaskStore,
        fire: Callable[[str], Awaitable[None]],
        poll_s: float = POLL_S,
    ) -> None:
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
                log.warning(
                    "Tâche %s en échec (%s: %s)", task.id, type(exc).__name__, exc
                )
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
