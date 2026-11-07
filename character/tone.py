"""How the relationship level and the current mood colour Monika's wording (a short block at the end of the prompt).

The block contains only text written here, never user text, so it cannot be used to inject instructions.
Rules that hold at every level: honesty and competence come first, and she is never hostile, jealous,
possessive or guilt-tripping (see PERSONALITY in agent/prompt.py).
"""

from __future__ import annotations

from typing import Any

from character.emotion_spec import BY_NAME, NEUTRAL
from monika.i18n import has, tr

MAX_MOODS = 3
MOOD_THRESHOLD = 0.15

def _degree(intensity: float) -> str:
    return tr("tone.degree_low") if intensity < 0.35 else tr("tone.degree_mid") if intensity < 0.7 else tr("tone.degree_high")


def _mood_line(name: str, intensity: float) -> str | None:
    """One mood for the prompt: spelled out when it needs a guardrail, else read from its label."""
    key = f"tone.mood_{name}"
    if has(key):
        return f"{_degree(intensity)} {tr(key)}"
    spec = BY_NAME.get(name)
    if spec is None:
        return None
    return tr("tone.mood_from_label", degree=_degree(intensity), label=spec.label)


def _moods(snapshot: dict[str, Any]) -> list[str]:
    """The strongest emotions of the moment, heaviest first (never more than MAX_MOODS)."""
    emotions = snapshot.get("emotions")
    if not isinstance(emotions, dict):
        return []
    notable = sorted(
        (
            (value, name)
            for name, value in emotions.items()
            if name != NEUTRAL
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value >= MOOD_THRESHOLD
        ),
        reverse=True,
    )[:MAX_MOODS]
    return [line for value, name in notable if (line := _mood_line(name, float(value)))]


def render_block(snapshot: dict[str, Any]) -> str:
    """The prompt section for this turn, from a character snapshot ('' when the snapshot is unusable)."""
    level = snapshot.get("level")
    key = f"tone.level_{level}"
    if not has(key):
        return ""
    mood = " ; ".join(_moods(snapshot)) or tr("tone.mood_calm")
    return tr("tone.header", level=tr(key), mood=mood)
