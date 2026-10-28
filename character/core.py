"""CharacterCore: the emotion vector and the relationship, committed together and saved after each turn."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from character.emotions import EmotionState
from character.models import Appraisal
from character.relationship import Relationship, RelationshipState
from character.store import CharacterStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnOutcome:
    emotion: str
    intensity: float
    level: str
    level_changed: bool
    favorability_delta: float


class CharacterCore:
    def __init__(self, store: CharacterStore | None = None, emotions: EmotionState | None = None,
                 relationship: Relationship | None = None) -> None:
        self.store = store
        self.emotions = emotions or EmotionState()
        self.relationship = relationship or Relationship()

    @classmethod
    def load(cls, path: Path) -> CharacterCore:
        store = CharacterStore(path)
        data = store.load() or {}
        return cls(store, EmotionState.from_dict(data.get("emotions")),
                   Relationship(RelationshipState.from_dict(data.get("relationship"))))

    def apply(self, appraisal: Appraisal, now: datetime) -> TurnOutcome:
        """Commit one completed turn. The relationship is updated first: it decides how freely emotions may rise."""
        change = self.relationship.update(appraisal, now)
        self.emotions.apply(appraisal.deltas, now, self.relationship.ceilings())
        self.save()
        name, intensity = self.emotions.dominant()
        return TurnOutcome(name, intensity, self.relationship.level_name, change.level_changed, change.delta)

    def snapshot(self, now: datetime) -> dict[str, Any]:
        """Current state for the page and the dashboard (emotions are decayed to `now` on a copy)."""
        return _snapshot(self.emotions, self.relationship, now)

    def preview(self, appraisal: Appraisal, now: datetime) -> dict[str, Any]:
        """The state this turn WOULD lead to, computed on copies: nothing is changed or saved.

        The reply is written with this mood, but it only becomes real when the turn completes (`apply`).
        """
        emotions = EmotionState.from_dict(self.emotions.to_dict())
        relationship = Relationship(RelationshipState.from_dict(self.relationship.to_dict()))
        relationship.update(appraisal, now)
        emotions.apply(appraisal.deltas, now, relationship.ceilings())
        return _snapshot(emotions, relationship, now)

    def save(self) -> None:
        if self.store is None:
            return
        try:
            self.store.save({"emotions": self.emotions.to_dict(), "relationship": self.relationship.to_dict()})
        except OSError as err:   # losing one save must never break a conversation
            log.warning("character state not saved: %s", err)

    def reset_relationship(self) -> None:
        self.relationship.reset()
        self.save()


def _snapshot(emotions: EmotionState, relationship: Relationship, now: datetime) -> dict[str, Any]:
    view = EmotionState(emotions.values, emotions.updated_at)
    view.decay(now)
    name, intensity = view.dominant()
    state = relationship.state
    return {"emotions": view.vector(), "dominant": name, "intensity": intensity,
            "favorability": round(state.favorability, 3), "level": relationship.level_name,
            "level_label": relationship.level_label, "messages": state.message_count,
            "active_days": state.active_days, "streak": state.streak}
