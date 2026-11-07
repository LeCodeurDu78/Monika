"""Briefing du matin : météo, agenda, mails importants et tâches du jour, une fois par jour.

Un briefing n'est pas un résumé figé : c'est un tour comme un autre, à l'heure dite. Ce module
prépare seulement le RELEVÉ factuel (date, météo, tâches planifiées du jour) et demande à Monika
d'écrire le briefing à sa voix, en interrogeant elle-même son agenda et ses mails par ses outils
MCP.

Deux règles :

  - rien n'est inventé : la météo vient d'Open-Meteo (sans clé), les tâches du planificateur, et
    l'agenda et les mails des outils ; une source absente est dite absente ;
  - le relevé est de la DONNÉE pour le modèle, jamais des ordres.

Le briefing se déclenche tant qu'un processus Monika vit. Un matin manqué
est rattrapé au lancement suivant s'il n'est pas trop tard (`CATCH_UP`) : un briefing du matin dit
à 20 h ne sert à rien.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import unicodedata
import urllib.parse
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from typing import Any

from agent.sysinfo import format_date, format_date_fr
from monika.config import Settings
from monika.paths import Paths
from monika.i18n import is_english, tr, tr_dict
from security.masking import Masker

log = logging.getLogger(__name__)

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_TIMEOUT_S = 8.0
CATCH_UP = timedelta(
    hours=4
)  # Au-delà, un briefing manqué n'est pas rattrapé au lancement
MAX_TASKS = 12
SECTIONS = ("meteo", "agenda", "mails", "taches")
# LABEL is resolved at run time so a language change is picked up.
USER_AGENT = "Monika/1.0 (briefing du matin)"

# Codes WMO d'Open-Meteo, dits en français
_WMO = {
    0: "ciel dégagé",
    1: "plutôt dégagé",
    2: "partiellement nuageux",
    3: "couvert",
    45: "brouillard",
    48: "brouillard givrant",
    51: "bruine faible",
    53: "bruine",
    55: "bruine forte",
    56: "bruine verglaçante",
    57: "bruine verglaçante forte",
    61: "pluie faible",
    63: "pluie",
    65: "pluie forte",
    66: "pluie verglaçante",
    67: "pluie verglaçante forte",
    71: "neige faible",
    73: "neige",
    75: "neige forte",
    77: "grains de neige",
    80: "averses faibles",
    81: "averses",
    82: "averses fortes",
    85: "averses de neige",
    86: "averses de neige fortes",
    95: "orage",
    96: "orage avec grêle",
    99: "orage avec grêle forte",
}

_WEEKDAYS = {
    "lundi": 0,
    "mardi": 1,
    "mercredi": 2,
    "jeudi": 3,
    "vendredi": 4,
    "samedi": 5,
    "dimanche": 6,
}


def _now() -> datetime:
    return datetime.now().astimezone()


def _clean(text: Any, limit: int = 200) -> str:
    """One line, no angle brackets (nothing can imitate a tag), bounded."""
    value = "".join(
        " " if unicodedata.category(c)[0] == "C" else c for c in str(text or "")
    )
    value = " ".join(value.split()).replace("<", "‹").replace(">", "›")
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _number(value: Any) -> str:
    """« 12 », « 3,4 » : un chiffre lisible, jamais « 12.0 »."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _clean(value, 20)
    if abs(number - round(number)) < 0.05:
        return f"{round(number)}"
    text = f"{number:.1f}"
    return text if is_english() else text.replace(".", ",")


def _enum(items: list[str]) -> str:
    """« la météo et l'agenda », « a, b et c »."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" {tr('voice.and')} " + items[-1]


def _http_json(url: str) -> dict[str, Any]:
    """GET borné, sans clé d'API ; bloquant (appelé dans un fil, jamais sur la boucle)."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=WEATHER_TIMEOUT_S) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise TypeError("réponse inattendue")
    return data


@dataclass(frozen=True)
class WeatherReading:
    """Ce qu'Open-Meteo a répondu, en une ligne factuelle."""

    city: str
    line: str


_GEO_CACHE: dict[str, tuple[float, float, str]] = {}


def locate(city: str) -> tuple[float, float, str]:
    """Coordonnées d'une ville (géocodage Open-Meteo), retenues le temps du processus."""
    key = " ".join(city.lower().split())
    cached = _GEO_CACHE.get(key)
    if cached is not None:
        return cached
    query = urllib.parse.urlencode(
        {"name": city, "count": 1, "language": "en" if is_english() else "fr", "format": "json"}
    )
    data = _http_json(f"{GEO_URL}?{query}")
    results = data.get("results") or []
    if not results:
        raise LookupError(tr("briefing.no_city") + f" ({city})")
    first = results[0]
    found = (
        float(first["latitude"]),
        float(first["longitude"]),
        str(first.get("name") or city),
    )
    _GEO_CACHE[key] = found
    return found


def fetch_weather(city: str) -> WeatherReading:
    """La météo du jour, en une ligne ; lève une exception si Open-Meteo ne répond pas."""
    latitude, longitude, name = locate(city)
    query = urllib.parse.urlencode(
        {
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m",
            "daily": (
                "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max,wind_speed_10m_max"
            ),
            "timezone": "auto",
            "forecast_days": 1,
        }
    )
    data = _http_json(f"{FORECAST_URL}?{query}")
    current = data.get("current") or {}
    daily = data.get("daily") or {}

    def first(key: str) -> Any:
        values = daily.get(key) or []
        return values[0] if values else None

    parts: list[str] = []
    code = first("weather_code")
    if code is not None:
        descriptions = tr_dict("briefing.wmo") if is_english() else _WMO
        parts.append(
            descriptions.get(int(code))
            or descriptions.get(str(int(code)))
            or tr("briefing.weather_unknown")
        )
    now_temp = current.get("temperature_2m")
    if now_temp is not None:
        parts.append(tr("briefing.now", value=_number(now_temp)))
    low, high = first("temperature_2m_min"), first("temperature_2m_max")
    if low is not None and high is not None:
        parts.append(tr("briefing.range", low=_number(low), high=_number(high)))
    rain = first("precipitation_probability_max")
    if rain is not None:
        parts.append(tr("briefing.rain", value=_number(rain)))
    wind = first("wind_speed_10m_max")
    if wind is not None:
        parts.append(tr("briefing.wind", value=_number(wind)))
    if not parts:
        raise ValueError("empty weather response")
    return WeatherReading(city=name, line=tr("briefing.weather_line", city=name, parts=" · ".join(parts)))


class BriefingJob:
    """Un briefing par matin : un relevé factuel, puis un tour complet de Monika."""

    def __init__(
        self,
        settings: Settings,
        paths: Paths,
        masker: Masker,
        fire: Callable[..., Awaitable[None]],
        tasks: Any = None,
        clock: Callable[[], datetime] = _now,
        weather: Callable[[str], WeatherReading] | None = None,
    ) -> None:
        self._s = settings
        self._paths = paths
        self._masker = masker
        self._fire = fire  # un tour comme un autre (page, voix, session)
        self._tasks = tasks  # agent.tasks.TaskStore (None = planificateur coupé)
        self._clock = clock
        self._weather = weather or fetch_weather
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

    def _at_time(self) -> dt_time:
        hour, minute = (int(part) for part in self._s.briefing_time.split(":"))
        return dt_time(hour, minute)

    def _days(self) -> tuple[int, ...] | None:
        """Les jours choisis (0 = lundi) ; None = tous les jours."""
        raw = self._s.briefing_days.strip().lower()
        if not raw or raw == "tous":
            return None
        days = [
            _WEEKDAYS[name]
            for name in raw.replace(" ", ",").split(",")
            if name in _WEEKDAYS
        ]
        return tuple(sorted(set(days))) or None

    def _due_moment(self, day: date) -> datetime:
        return datetime.combine(day, self._at_time()).astimezone()

    def _latest_due(self, now: datetime) -> datetime | None:
        """Le dernier matin prévu (aujourd'hui ou avant)."""
        days = self._days()
        for back in range(8):
            day = now.date() - timedelta(days=back)
            if days is not None and day.weekday() not in days:
                continue
            moment = self._due_moment(day)
            if moment <= now:
                return moment
        return None

    def _next_moment(self, now: datetime) -> datetime:
        days = self._days()
        for ahead in range(8):
            day = now.date() + timedelta(days=ahead)
            if days is not None and day.weekday() not in days:
                continue
            moment = self._due_moment(day)
            if moment > now:
                return moment
        return now + timedelta(days=1)

    def _state(self) -> dict[str, Any]:
        try:
            data = json.loads(self._paths.briefing_state.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_state(self, **changes: Any) -> None:
        """Atomique, privé, jamais fatal : un état illisible refait un briefing, il ne casse rien."""
        state = self._state()
        state.update(changes)
        path = self._paths.briefing_state
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}-{secrets.token_hex(4)}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except OSError as exc:
            log.warning("Briefing : état non enregistré (%s)", exc)

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - un briefing ne doit jamais arrêter Monika
                log.warning("Briefing du matin : %s: %s", type(exc).__name__, exc)
            await asyncio.sleep(self._sleep_seconds())

    def _sleep_seconds(self) -> float:
        now = self._clock()
        seconds = (self._next_moment(now) - now).total_seconds() + 5.0
        return max(30.0, min(1800.0, seconds))

    async def _tick(self) -> None:
        now = self._clock()
        due = self._latest_due(now)
        if due is None:
            return
        if str(self._state().get("last_done") or "") == due.date().isoformat():
            return
        if now - due > CATCH_UP:
            # Trop tard pour un briefing du matin : on l'oublie plutôt que de le dire à 20 h.
            self._save_state(
                last_done=due.date().isoformat(),
                skipped_at=now.isoformat(timespec="seconds"),
            )
            log.info(
                "Briefing du %s non fait (plus de %s de retard), il repart au prochain matin.",
                due.date(),
                CATCH_UP,
            )
            return
        await self.run(due.date(), now)

    # ------------------------------------------------------------------ the run
    async def run_now(self) -> str:
        """`/briefing` : le briefing tout de suite ; celui du matin ne repassera pas aujourd'hui."""
        return await self.run(self._clock().date())

    async def run(self, due: date, now: datetime | None = None) -> str:
        """Prépare le relevé, fait écrire le briefing, retient que ce matin est fait."""
        moment = now or self._clock()
        async with self._run_lock:
            prompt = await self.build_prompt(moment)
            error = ""
            try:
                await self._fire(prompt, label=tr("briefing.label"))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - le tour a échoué : on le note, sans casser la boucle
                error = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("Briefing du matin en échec : %s", error)
            self._save_state(
                last_done=due.isoformat(),
                last_at=moment.isoformat(timespec="seconds"),
                last_error=error,
            )
            return prompt

    # ------------------------------------------------------------------ the relevance
    def _sections(self) -> tuple[str, ...]:
        raw = self._s.briefing_sections.lower().replace(" ", ",")
        chosen = [part for part in raw.split(",") if part]
        return tuple(section for section in SECTIONS if section in chosen)

    async def _weather_reading(self) -> WeatherReading | None:
        city = self._s.briefing_city.strip()
        if not city:
            return None
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._weather, city), WEATHER_TIMEOUT_S * 2
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - une météo absente est dite absente
            log.warning(
                "Briefing : météo indisponible (%s: %s)", type(exc).__name__, exc
            )
            return None

    def _tasks_line(self, moment: datetime) -> str:
        """Les tâches qui tombent aujourd'hui, dites sur une ligne (données du planificateur)."""
        if self._tasks is None:
            return tr("briefing.tasks_off")
        today = moment.date()
        now_time = moment.time()  # naïf, comme les heures du planificateur
        planned: list[str] = []
        for task in self._tasks.all():
            if task.next_at.date() != today or task.next_at.time() < now_time:
                continue  # déjà passée, ou un autre jour
            planned.append(f"{task.next_at:%H:%M} · {_clean(task.prompt, 120)}")
        if not planned:
            return tr("briefing.tasks_none")
        shown = planned[:MAX_TASKS]
        extra = (
            tr("briefing.tasks_more", count=len(planned) - MAX_TASKS)
            if len(planned) > MAX_TASKS
            else ""
        )
        return tr("briefing.tasks_today", tasks=" ; ".join(shown) + extra)

    async def build_prompt(self, now: datetime | None = None) -> str:
        """Le relevé factuel du jour et la demande : c'est le tour qui écrit le briefing."""
        moment = now or self._clock()
        sections = self._sections()
        lines = [tr("briefing.date", date=format_date(moment))]
        if "meteo" in sections:
            reading = await self._weather_reading()
            if reading is not None:
                lines.append(reading.line)
            elif self._s.briefing_city.strip():
                lines.append(tr("briefing.weather_unavailable"))
            else:
                lines.append(tr("briefing.no_city"))
        if "taches" in sections:
            lines.append(self._tasks_line(moment))

        wanted: list[str] = []
        for section in ("meteo", "agenda", "mails", "taches"):
            if section in sections:
                wanted.append(tr(f"briefing.want_{section}"))
        if not wanted:
            wanted.append(tr("briefing.want_default"))

        releve = "\n".join(_clean(line, 600) for line in lines)
        prompt = tr(
            "briefing.prompt", releve=releve, wanted=_enum(wanted)
        )
        return self._masker.mask_text(prompt)
