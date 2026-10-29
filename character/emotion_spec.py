"""The emotion table: the single place where an emotion exists.

Everything else derives from `SPECS`: the names accepted by the appraisal, the decay half-lives, the
relationship ceilings, the TTS variants, the VRM expression weights, the fallback gestures, and the JSON
the page receives on `/api/emotions`. Adding an emotion means adding one line here.

"neutre" is NOT in this table: it is derived (1 - strongest active emotion), never stored (see emotions.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

NEUTRAL = "neutre"

# Decay bands, in seconds: how long an emotion takes to halve on its own.
VIF, NORMAL, LENT, TRES_LENT = 240.0, 900.0, 1200.0, 1800.0

# VRM preset expressions the weights below may use.
EXPRESSION_NAMES: tuple[str, ...] = ("happy", "angry", "sad", "relaxed", "surprised")

# How much a rising emotion pulls down the emotions it inhibits (same factor as before the table existed).
INHIBIT_FACTOR = 0.5

# A spec may pin its ceiling per relationship level (reservee, cordiale, complice, proche).
# Without a pin, base emotions may always rise fully and scene emotions need some familiarity.
BASE_CEILINGS: tuple[float, ...] = (1.0, 1.0, 1.0, 1.0)
SCENE_CEILINGS: tuple[float, ...] = (0.45, 0.60, 0.80, 1.0)


@dataclass(frozen=True)
class EmotionSpec:
    """One emotion and everything derived from it. `tts` and `clips` are ordered by rising intensity."""

    name: str
    label: str
    group: str                                    # "base" | "scene"
    half_life_s: float
    inhibits: tuple[str, ...] = ()
    tts: tuple[str, ...] = ()                     # GPT-SoVITS emotion tags (see character/tts_emotion.py)
    expr: tuple[tuple[str, float], ...] = ()      # VRM expression weights
    clips: tuple[str, ...] = ()                   # fallback gestures (names from web/static/clips.json)
    quadrant: str = ""                            # HAP | HAN | LAP | LAN, "" = no TTS transition lag
    ceilings: tuple[float, ...] | None = None     # per relationship level, when the group default does not fit

    def expr_weights(self) -> dict[str, float]:
        return dict(self.expr)


# ------------------------------------------------------------------------------------------------ base emotions
# The six broad emotions. Their ceilings and half-lives keep the values the previous 5-emotion vector used.
_BASE: tuple[EmotionSpec, ...] = (
    EmotionSpec("joyeuse", "joyeuse", "base", 900.0, inhibits=("boudeuse", "tristesse"),
                tts=("happy1", "happy2", "happy3"),
                expr=(("happy", 0.45), ("relaxed", 0.75)), clips=("Yay", "Laugh"), quadrant="HAP",
                ceilings=(0.6, 0.8, 1.0, 1.0)),
    EmotionSpec("tristesse", "triste", "base", TRES_LENT,
                tts=("sad1", "sad2", "sad3"),
                expr=(("sad", 1.0),), clips=("Sad", "Cry"), quadrant="LAN"),
    EmotionSpec("colere", "en colère", "base", NORMAL,
                tts=("angry1", "angry2", "angry3", "angry4"),
                expr=(("angry", 1.0),), clips=("Angry",), quadrant="HAN"),
    EmotionSpec("peur", "inquiète d'un coup", "base", VIF,
                tts=("worried1", "fear2", "fear3", "fear4"),   # fear1's recording does not match its tag
                expr=(("surprised", 0.5), ("sad", 0.4)), clips=("Surprised",), quadrant="HAN"),
    EmotionSpec("surprise", "surprise", "base", 25.0,
                tts=("surprise1", "surprise2"),
                expr=(("surprised", 0.7),), clips=("Surprised",)),
    EmotionSpec("degout", "dégoûtée", "base", LENT,
                tts=("annoyed3", "annoyed4"),
                expr=(("angry", 0.5), ("sad", 0.2)), clips=("Angry", "ShakeHead"), quadrant="LAN"),
)

# ------------------------------------------------------------------------------------------------ scene emotions
# The finer shades. They need a little familiarity (SCENE_CEILINGS) and never drive the tone alone.
_SCENE: tuple[EmotionSpec, ...] = (
    EmotionSpec("taquine", "taquine", "scene", 600.0,
                tts=("playful1", "playful2", "playful3"),
                expr=(("happy", 0.35), ("relaxed", 0.5)), clips=("LeanIn", "Laugh"), quadrant="HAP",
                ceilings=(0.2, 0.4, 0.7, 1.0)),
    EmotionSpec("boudeuse", "un peu vexée", "scene", 1200.0, inhibits=("joyeuse",),
                tts=("annoyed1", "annoyed2", "annoyed3", "annoyed4"),
                expr=(("angry", 0.4), ("relaxed", 0.25)), clips=("Pout", "FoldArms"), quadrant="HAN",
                ceilings=(0.3, 0.4, 0.6, 0.8)),
    EmotionSpec("jalousie", "jalouse", "scene", LENT,
                tts=("jealous1", "jealous2", "jealous3"),
                expr=(("angry", 0.45), ("sad", 0.3)), clips=("Pout", "FoldArms"), quadrant="HAN",
                ceilings=(0.2, 0.35, 0.6, 0.9)),
    EmotionSpec("tsundere", "tsundere", "scene", LENT,
                tts=("tsun1", "tsun2", "tsun3"),
                expr=(("angry", 0.4), ("relaxed", 0.25)), clips=("Pout", "Shy"), quadrant="HAP",
                ceilings=(0.2, 0.4, 0.7, 1.0)),
    EmotionSpec("timide", "timide", "scene", 600.0,
                tts=("shy1", "shy2", "shy3"),
                expr=(("relaxed", 0.5), ("happy", 0.25)), clips=("Shy",), quadrant="LAP"),
    EmotionSpec("attentionnee", "attentionnée", "scene", 900.0,
                tts=("caring1", "caring2", "caring3"),
                expr=(("relaxed", 0.7), ("happy", 0.2)), clips=("OpenArms", "LeanIn"), quadrant="LAP"),
    EmotionSpec("malicieuse", "malicieuse", "scene", 900.0,
                tts=("playful2", "playful3"),
                expr=(("happy", 0.35), ("relaxed", 0.5)), clips=("LeanIn", "Laugh"), quadrant="HAP"),
    EmotionSpec("sereine", "sereine", "scene", LENT,
                tts=("neutral1", "caring1"),
                expr=(("relaxed", 0.9),), clips=("Yay",), quadrant="LAP"),
    EmotionSpec("inquiete", "inquiète", "scene", 900.0,
                tts=("worried1", "worried2"),
                expr=(("sad", 0.5), ("surprised", 0.15)), clips=("Think", "LookAround"), quadrant="HAN"),
    EmotionSpec("genee", "gênée", "scene", 600.0,
                tts=("embarrassed1",),
                expr=(("relaxed", 0.3), ("sad", 0.2)), clips=("Shy",), quadrant="HAN"),
    EmotionSpec("soulagee", "soulagée", "scene", 600.0,
                tts=("relieved1", "relieved2"),
                expr=(("relaxed", 0.8),), clips=("Yay", "Yes"), quadrant="LAP"),
    EmotionSpec("fiere", "fière", "scene", 900.0,
                tts=("proud1",),
                expr=(("happy", 0.3), ("relaxed", 0.5)), clips=("HandsOnHips", "Yay"), quadrant="LAP"),
    EmotionSpec("solitaire", "seule", "scene", TRES_LENT,
                tts=("lonely1",),
                expr=(("sad", 0.8),), clips=("Sad", "LookAround"), quadrant="LAN"),
    EmotionSpec("protective", "protectrice", "scene", 900.0,
                tts=("protective1",),
                expr=(("angry", 0.2),), clips=("OpenArms", "FoldArms"), quadrant="HAP"),
    EmotionSpec("decue", "déçue", "scene", LENT,
                tts=("disappointed1",),
                expr=(("sad", 0.6),), clips=("Sad", "ShakeHead"), quadrant="LAN"),
    EmotionSpec("frustree", "frustrée", "scene", 900.0,
                tts=("frustrated1",),
                expr=(("angry", 0.7),), clips=("Angry", "ShakeHead"), quadrant="HAN"),
    EmotionSpec("sarcastique", "sarcastique", "scene", 600.0,
                tts=("sarcastic1",),
                expr=(("relaxed", 0.3), ("angry", 0.2)), clips=("LeanIn", "FoldArms"), quadrant="LAN"),
    EmotionSpec("froide", "distante d'un coup", "scene", TRES_LENT,
                tts=("cold1", "cold2", "cold3"),
                expr=(), clips=("FoldArms",), quadrant="LAN"),
    EmotionSpec("distante", "ailleurs", "scene", TRES_LENT,
                tts=("monotone1", "cold1", "cold2"),
                expr=(), clips=("LookAround", "FoldArms"), quadrant="LAN"),
    EmotionSpec("excitee", "excitée", "scene", VIF,
                tts=("happy2", "happy3"),
                expr=(("happy", 0.5), ("relaxed", 0.6), ("surprised", 0.3)),
                clips=("Hop", "Dance", "Yay"), quadrant="HAP"),
)

SPECS: tuple[EmotionSpec, ...] = _BASE + _SCENE
NAMES: tuple[str, ...] = tuple(spec.name for spec in SPECS)
ALL_NAMES: tuple[str, ...] = (NEUTRAL, *NAMES)
BY_NAME: dict[str, EmotionSpec] = {spec.name: spec for spec in SPECS}
BASE_NAMES: tuple[str, ...] = tuple(spec.name for spec in _BASE)
SCENE_NAMES: tuple[str, ...] = tuple(spec.name for spec in _SCENE)

HALF_LIFE_S: dict[str, float] = {spec.name: spec.half_life_s for spec in SPECS}
# Rising emotion -> the emotions it soothes, and by how much of the rise.
INHIBITS: dict[str, dict[str, float]] = {
    spec.name: {other: INHIBIT_FACTOR for other in spec.inhibits} for spec in SPECS if spec.inhibits
}

# What a perceived signal does to the emotions. Only appraisal.py applies these (see deltas_from).
# The code decides them, never the model: an LLM only reports valence/intensity/signals.
_SIGNAL_DELTAS: dict[str, tuple[tuple[str, float], ...]] = {
    "greeting": (("joyeuse", 0.10),),
    "farewell": (("solitaire", 0.06),),
    "thanks": (("joyeuse", 0.10), ("sereine", 0.05)),
    "compliment": (("joyeuse", 0.15), ("genee", 0.08), ("fiere", 0.05)),
    "affection": (("joyeuse", 0.15), ("timide", 0.08), ("attentionnee", 0.08)),
    "tease": (("taquine", 0.30), ("joyeuse", 0.10), ("malicieuse", 0.08)),
    "surprise": (("surprise", 0.40),),
    "insult": (("boudeuse", 0.25), ("colere", 0.15), ("decue", 0.08)),
    "hostility": (("colere", 0.20), ("tristesse", 0.10), ("frustree", 0.10)),
    "shouting": (("peur", 0.10), ("colere", 0.10)),
    "disappointment": (("decue", 0.15), ("inquiete", 0.08)),
    "user_distress": (("inquiete", 0.15), ("attentionnee", 0.10)),
    "task_issue": (("frustree", 0.12), ("inquiete", 0.06)),
    "jealousy": (("jalousie", 0.20), ("boudeuse", 0.10)),
    "shyness": (("timide", 0.20), ("genee", 0.10)),
    "worry": (("inquiete", 0.20), ("attentionnee", 0.05)),
    "pride": (("fiere", 0.20), ("sereine", 0.10)),
}
SIGNAL_DELTAS: dict[str, tuple[tuple[str, float], ...]] = _SIGNAL_DELTAS

# What a warm or cold tone does on its own (before the signals above).
POSITIVE_DELTAS: tuple[tuple[str, float], ...] = (("joyeuse", 0.40), ("boudeuse", -0.30), ("sereine", 0.10))
NEGATIVE_DELTAS: tuple[tuple[str, float], ...] = (("boudeuse", 0.45), ("joyeuse", -0.35), ("decue", 0.15))


def ceilings_for(name: str) -> tuple[float, ...]:
    """The cap of one emotion at each relationship level (reservee, cordiale, complice, proche)."""
    spec = BY_NAME.get(name)
    if spec is None:
        return BASE_CEILINGS
    if spec.ceilings is not None:
        return spec.ceilings
    return BASE_CEILINGS if spec.group == "base" else SCENE_CEILINGS


CEILINGS: tuple[dict[str, float], ...] = tuple(
    {spec.name: ceilings_for(spec.name)[level] for spec in SPECS} for level in range(4)
)


def payload() -> dict[str, Any]:
    """Everything the page needs to draw a mood: labels, expression weights, fallback gestures."""
    return {
        "neutral": NEUTRAL,
        "order": list(NAMES),
        "emotions": {
            spec.name: {"label": spec.label, "expr": spec.expr_weights(), "clips": list(spec.clips)}
            for spec in SPECS
        },
    }
