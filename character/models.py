"""Plain data types shared by the character modules."""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field
from typing import Any

# The emotion vector. "neutre" is the calm baseline: it is derived (1 - strongest active emotion), never stored.
EMOTIONS: tuple[str, ...] = ("neutre", "joyeuse", "taquine", "boudeuse", "surprise")
ACTIVE_EMOTIONS: tuple[str, ...] = EMOTIONS[1:]

# What an appraisal may report about a user message. Anything else coming from a model is dropped.
SIGNALS: frozenset[str] = frozenset({
    "greeting", "farewell", "thanks", "compliment", "affection", "tease", "surprise",
    "insult", "hostility", "disappointment", "shouting", "user_distress", "task_issue",
})


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def fold_text(text: str) -> str:
    """Lower case without accents, curly apostrophes straightened: 'Génial' -> 'genial'."""
    text = text.lower().replace("’", "'")
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def finite(value: Any) -> bool:
    """True for real numbers only (bool is refused: True is not a valence)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True)
class Appraisal:
    """How a user message sounds. It describes the message only: it never sets any state by itself."""

    valence: float = 0.0                      # -1 hostile .. +1 warm, towards Monika / the conversation
    intensity: float = 0.0                    # 0..1, how strongly the tone is expressed
    deltas: dict[str, float] = field(default_factory=dict)   # emotion changes, each in [-0.5, 0.5]
    signals: tuple[str, ...] = ()
    source: str = "lexicon"                   # "lexicon" | "llm"


@dataclass(frozen=True)
class AvatarCue:
    """An instruction for the 3D avatar, sent to the page as {"type": "avatar", ...}.

    `emotion` None leaves the face as it is (a gesture that does not change the mood). `clip` None changes the
    face only. The page only plays clips found in its manifest and falls back to the emotion's own clips.
    """

    emotion: str | None = None
    intensity: float = 0.5
    clip: str | None = None
    reason: str = ""
    priority: int = 2
    duration_s: float | None = None

    def payload(self) -> dict[str, Any]:
        data: dict[str, Any] = {"intensity": round(clamp(self.intensity, 0.0, 1.0), 3), "priority": self.priority}
        if self.emotion:
            data["emotion"] = self.emotion
        if self.clip:
            data["clip"] = self.clip
        if self.reason:
            data["reason"] = self.reason
        if self.duration_s:
            data["duration_s"] = self.duration_s
        return data

    def to_message(self) -> dict[str, Any]:
        return {"type": "avatar", **self.payload()}
