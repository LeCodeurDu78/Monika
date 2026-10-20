"""Morning briefing: when it is due, and a small state file so it is delivered once a day.

The content itself is produced by the agent (see BRIEFING_GUIDE in tools/guides.py), so asking for
"mon briefing" by voice, on Discord or in the terminal gives the same result as the automatic one.
"""

import json
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from core.settings import settings

REQUEST = "Fais mon briefing du matin."


def now() -> datetime:
    try:
        return datetime.now(ZoneInfo(settings.TIMEZONE))
    except Exception:  # unknown timezone or no tz database: use the machine's local time
        return datetime.now().astimezone()


def _parse_time(value: str) -> tuple[int, int]:
    try:
        hour, minute = (int(part) for part in value.strip().split(":"))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except ValueError:
        pass
    return 8, 0


def _state_path():
    return settings.APP_DIR / "briefing_state.json"


def last_delivered() -> Optional[str]:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8")).get("last")
    except (OSError, ValueError, AttributeError):
        return None


def mark_delivered(moment: datetime) -> None:
    try:
        _state_path().write_text(json.dumps({"last": moment.date().isoformat()}), encoding="utf-8")
    except OSError:
        pass


def is_due(moment: datetime) -> bool:
    """True once per day, from BRIEFING_TIME until BRIEFING_CATCHUP_HOURS later (covers a late start)."""
    if not settings.BRIEFING_ENABLED:
        return False
    hour, minute = _parse_time(settings.BRIEFING_TIME)
    scheduled = moment.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if moment < scheduled or moment - scheduled > timedelta(hours=settings.BRIEFING_CATCHUP_HOURS):
        return False
    return last_delivered() != moment.date().isoformat()


def context() -> str:
    """Situational note for the automatic run: nobody is typing, and which city to use."""
    note = "Contexte : c'est l'heure du briefing du matin automatique ; l'utilisateur ne l'a pas demandé explicitement."
    if settings.BRIEFING_CITY:
        note += f" Ville pour la météo : {settings.BRIEFING_CITY}."
    return note
