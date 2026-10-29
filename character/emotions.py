"""The emotion vector: four active emotions that decay over time, plus a derived calm baseline."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from character.emotion_spec import HALF_LIFE_S, INHIBITS
from character.models import ACTIVE_EMOTIONS, EMOTIONS, clamp, finite

MAX_STEP = 0.5               # no single message moves an emotion by more than this
DOMINANT_THRESHOLD = 0.15    # below this, Monika is simply "neutre"
MAX_NOTABLE = 3              # at most this many emotions above the threshold: a readable mood, not a mush
# HALF_LIFE_S (time for an emotion to halve on its own) and INHIBITS (a rising emotion soothes these)
# are derived from the emotion table in emotion_spec.py.


class EmotionState:
    """Intensities in 0..1 for the active emotions. Time is always passed in, never read, so it stays testable."""

    def __init__(self, values: dict[str, float] | None = None, updated_at: datetime | None = None) -> None:
        self.values: dict[str, float] = {name: 0.0 for name in ACTIVE_EMOTIONS}
        for name, value in (values or {}).items():
            if name in self.values and finite(value):
                self.values[name] = clamp(float(value), 0.0, 1.0)
        self._limit_notable()
        self.updated_at = updated_at

    def decay(self, now: datetime) -> None:
        """Let every emotion fade for the time elapsed since the last update (also across sessions)."""
        if self.updated_at is not None:
            elapsed = (now - self.updated_at).total_seconds()
            if elapsed > 0:  # a clock set backwards changes nothing
                for name, half_life in HALF_LIFE_S.items():
                    self.values[name] *= 0.5 ** (elapsed / half_life)
        self.updated_at = now

    def apply(self, deltas: dict[str, float], now: datetime, ceilings: dict[str, float] | None = None) -> None:
        """Decay to `now`, then add bounded deltas. `ceilings` caps an emotion (teasing needs familiarity)."""
        self.decay(now)
        rises: dict[str, float] = {}
        for name, delta in deltas.items():
            if name not in self.values or not finite(delta):
                continue
            step = clamp(float(delta), -MAX_STEP, MAX_STEP)
            before = self.values[name]
            cap = clamp((ceilings or {}).get(name, 1.0), 0.0, 1.0)
            self.values[name] = clamp(before + step, 0.0, max(cap, 0.0))
            if self.values[name] > before:
                rises[name] = self.values[name] - before
        for name, rise in rises.items():
            for other, factor in INHIBITS.get(name, {}).items():
                if other not in rises:
                    self.values[other] = clamp(self.values[other] - rise * factor, 0.0, 1.0)
        self._limit_notable()

    def _limit_notable(self) -> None:
        """Keep the mood readable: at most MAX_NOTABLE emotions above the threshold.

        The weakest ones settle AT the threshold instead of being erased, so nothing is lost when the
        mood moves on, and they no longer count as a mood of their own.
        """
        notable = sorted((value, name) for name, value in self.values.items() if value > DOMINANT_THRESHOLD)
        for _value, name in notable[:-MAX_NOTABLE]:
            self.values[name] = DOMINANT_THRESHOLD

    def vector(self) -> dict[str, float]:
        """All five emotions; "neutre" is whatever the strongest active emotion leaves free."""
        out = {"neutre": round(1.0 - max(self.values.values(), default=0.0), 3)}
        out.update({name: round(self.values[name], 3) for name in ACTIVE_EMOTIONS})
        return {name: out[name] for name in EMOTIONS}

    def top(self, limit: int = MAX_NOTABLE) -> list[tuple[str, float]]:
        """The strongest emotions above the threshold, heaviest first (what colours the tone right now)."""
        notable = sorted(((value, name) for name, value in self.values.items() if value > DOMINANT_THRESHOLD),
                         reverse=True)
        return [(name, round(value, 3)) for value, name in notable[:limit]]

    def dominant(self) -> tuple[str, float]:
        name, value = max(self.values.items(), key=lambda item: item[1])
        if value < DOMINANT_THRESHOLD:
            return "neutre", round(1.0 - value, 3)
        return name, round(value, 3)

    def to_dict(self) -> dict[str, Any]:
        return {"values": {k: round(v, 4) for k, v in self.values.items()},
                "updated_at": self.updated_at.isoformat() if self.updated_at else None}

    @classmethod
    def from_dict(cls, data: Any) -> EmotionState:
        """Tolerant loader: a damaged or hand-edited file yields a calm state, never an exception."""
        if not isinstance(data, dict):
            return cls()
        updated = None
        if isinstance(data.get("updated_at"), str):
            try:
                updated = datetime.fromisoformat(data["updated_at"]).astimezone()  # naive text is read as local time
            except ValueError:
                updated = None
        values = data.get("values")
        return cls(values if isinstance(values, dict) else None, updated)
