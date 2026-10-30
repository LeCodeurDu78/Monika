"""Favorability: a slow, persistent relationship score that shapes familiarity, warmth and complicity.

Design rules (they protect both the user and Monika's character):
  - it moves a little per message and is capped per day, so no single message (or injected text) can swing it;
  - presence counts: the first message of a day and a streak of days give a small bonus;
  - it never turns hostile: a low score makes Monika more reserved, never unkind;
  - being away is not punished harshly and never demotes by itself; being sad or having a bug is not hostility.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

from character.emotion_spec import CEILINGS
from character.models import Appraisal, clamp, finite

LEVELS: tuple[str, ...] = ("reservee", "cordiale", "complice", "proche")
LEVEL_LABELS_FR: dict[str, str] = {
    "reservee": "Réservée",
    "cordiale": "Cordiale",
    "complice": "Complice",
    "proche": "Proche",
}
THRESHOLDS: tuple[float, ...] = (
    0.0,
    0.25,
    0.50,
    0.75,
)  # favorability needed for each level
MIN_MESSAGES: tuple[int, ...] = (0, 0, 30, 100)  # ...and enough shared conversation
MIN_ACTIVE_DAYS: tuple[int, ...] = (
    0,
    0,
    4,
    14,
)  # ...and enough different days: closeness takes time
HYSTERESIS = (
    0.03  # fall this far under a threshold before losing the level (no flapping)
)

START = 0.30
FLOOR = 0.15
TONE_STEP = 0.012  # favorability per message at |valence| = 1
INSULT_FACTOR = 1.5  # unkindness weighs a little more than kindness
PRESENCE_PER_MESSAGE = 0.0015
DAILY_BONUS = 0.01
STREAK_BONUS = 0.002  # per streak day, up to 5
DAILY_GAIN_CAP = 0.06
DAILY_LOSS_CAP = 0.05
ABSENCE_GRACE_DAYS = 3
ABSENCE_RATE = 0.004  # per day beyond the grace period
ABSENCE_MAX = 0.05

# How freely each emotion may rise at each level (teasing and sulking need familiarity).
# The caps live next to each emotion in emotion_spec.py; CEILINGS holds one dict per level.


def day_of(now: datetime) -> date:
    return (now.astimezone() if now.tzinfo else now).date()


@dataclass
class RelationshipState:
    favorability: float = START
    level: int = 1
    message_count: int = 0
    active_days: int = 0
    streak: int = 0
    best_streak: int = 0
    last_day: str | None = None
    day_gain: float = 0.0
    day_loss: float = 0.0

    @classmethod
    def from_dict(cls, data: Any) -> RelationshipState:
        """Tolerant loader: bad or missing fields fall back to defaults and every value is clamped."""
        state = cls()
        if not isinstance(data, dict):
            return state
        if finite(data.get("favorability")):
            state.favorability = clamp(float(data["favorability"]), 0.0, 1.0)
        for name in ("message_count", "active_days", "streak", "best_streak"):
            if finite(data.get(name)):
                setattr(state, name, max(0, int(data[name])))
        for name in ("day_gain", "day_loss"):
            if finite(data.get(name)):
                setattr(state, name, clamp(float(data[name]), 0.0, 1.0))
        if isinstance(data.get("last_day"), str):
            try:
                date.fromisoformat(data["last_day"])
                state.last_day = data["last_day"]
            except ValueError:
                pass
        if finite(data.get("level")):
            state.level = int(clamp(int(data["level"]), 0, len(LEVELS) - 1))
        else:
            state.level = max(
                i for i, t in enumerate(THRESHOLDS) if state.favorability >= t
            )
        return state


@dataclass(frozen=True)
class RelationshipChange:
    before: float
    after: float
    level_before: int
    level_after: int

    @property
    def delta(self) -> float:
        return round(self.after - self.before, 4)

    @property
    def level_changed(self) -> bool:
        return self.level_after != self.level_before


class Relationship:
    def __init__(self, state: RelationshipState | None = None) -> None:
        self.state = state or RelationshipState()

    @property
    def level_name(self) -> str:
        return LEVELS[self.state.level]

    @property
    def level_label(self) -> str:
        return LEVEL_LABELS_FR[self.level_name]

    def ceilings(self) -> dict[str, float]:
        return dict(CEILINGS[self.state.level])

    # ------------------------------------------------------------------------------------------ updates
    def update(self, appraisal: Appraisal, now: datetime) -> RelationshipChange:
        s = self.state
        before, level_before = s.favorability, s.level
        today = day_of(now)
        gain = loss = 0.0

        if s.last_day != today.isoformat():
            gap = (today - date.fromisoformat(s.last_day)).days if s.last_day else 0
            s.day_gain = s.day_loss = 0.0
            s.active_days += 1
            s.streak = s.streak + 1 if gap == 1 else 1
            s.best_streak = max(s.best_streak, s.streak)
            s.last_day = today.isoformat()
            if (
                gap > ABSENCE_GRACE_DAYS
            ):  # a gentle drift, never below the current level's floor
                drift = min(ABSENCE_MAX, ABSENCE_RATE * (gap - ABSENCE_GRACE_DAYS))
                s.favorability = max(
                    max(FLOOR, THRESHOLDS[s.level]), s.favorability - drift
                )
            gain += DAILY_BONUS + STREAK_BONUS * min(s.streak, 5)

        s.message_count += 1
        valence = clamp(appraisal.valence, -1.0, 1.0)
        if valence > 0.1:
            gain += TONE_STEP * valence
        elif valence < -0.1:
            loss += TONE_STEP * INSULT_FACTOR * -valence
        else:
            gain += PRESENCE_PER_MESSAGE

        gain = clamp(min(gain, DAILY_GAIN_CAP - s.day_gain), 0.0, DAILY_GAIN_CAP)
        loss = clamp(min(loss, DAILY_LOSS_CAP - s.day_loss), 0.0, DAILY_LOSS_CAP)
        s.day_gain += gain
        s.day_loss += loss
        s.favorability = clamp(s.favorability + gain - loss, 0.0, 1.0)
        self._settle_level()
        return RelationshipChange(
            round(before, 4), round(s.favorability, 4), level_before, s.level
        )

    def _gate_open(self, level: int) -> bool:
        return (
            self.state.message_count >= MIN_MESSAGES[level]
            and self.state.active_days >= MIN_ACTIVE_DAYS[level]
        )

    def _settle_level(self) -> None:
        s = self.state
        if s.level < len(LEVELS) - 1:
            nxt = s.level + 1
            if s.favorability >= THRESHOLDS[nxt]:
                if self._gate_open(nxt):
                    s.level = nxt
                else:  # closeness takes time: hold just under the threshold until the conversation has matured
                    s.favorability = min(s.favorability, THRESHOLDS[nxt] - 0.001)
        if s.level > 0 and s.favorability < THRESHOLDS[s.level] - HYSTERESIS:
            s.level -= 1

    def reset(self) -> None:
        self.state = RelationshipState()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self.state)
        data["favorability"] = round(data["favorability"], 4)
        data["day_gain"] = round(data["day_gain"], 4)
        data["day_loss"] = round(data["day_loss"], 4)
        return data
