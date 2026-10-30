"""How long Monika breathes between two sentences: punctuation first, emotion a little.

The voice only gets silence here, never a hesitation: no "euh", no filler sound. `neutral` (the
default) adds nothing at all and keeps the previous behaviour, sentence against sentence; `natural`
inserts a short silence after the sentence's own punctuation, modulated by the mood it was spoken in.
"""

from __future__ import annotations

import unicodedata

STYLES = ("neutre", "naturel")

# Base silence after a sentence, in seconds, by its final punctuation
BASE_S: dict[str, float] = {
    "…": 0.75,  # a suspended thought breathes longer
    ".": 0.50,
    "?": 0.45,  # a question leaves room for the answer
    ":": 0.40,
    ";": 0.40,
    "!": 0.35,  # exclamations push forward
    ",": 0.25,
}
DEFAULT_S = 0.40  # no punctuation at all (a fragment the splitter could not close)

# How the mood stretches or tightens the silence (1.0 = unchanged); the intensity scales the deviation
EMOTION_FACTORS: dict[str, float] = {
    "colere": 0.65,
    "frustree": 0.70,
    "excitee": 0.75,
    "joyeuse": 0.80,
    "surprise": 0.80,
    "sarcastique": 0.90,
    "taquine": 0.90,
    "malicieuse": 0.95,
    "timide": 1.15,
    "genee": 1.15,
    "sereine": 1.15,
    "attentionnee": 1.15,
    "inquiete": 1.20,
    "froide": 1.20,
    "peur": 1.25,
    "distante": 1.30,
    "decue": 1.30,
    "solitaire": 1.30,
    "tristesse": 1.35,
}

_CLOSERS = "\"'»”’)]}»"  # a sentence often ends with « … » or « (…) » : the mark is before them


def _last_mark(text: str) -> str:
    """The sentence's final punctuation, looking past closing quotes and brackets."""
    stripped = text.strip().rstrip(_CLOSERS).rstrip()
    if not stripped:
        return ""
    return stripped[-1]


def _emotion_factor(emotion: str, intensity: float) -> float:
    if not emotion:
        return 1.0
    name = "".join(
        c
        for c in unicodedata.normalize("NFKD", emotion.lower())
        if not unicodedata.combining(c)
    )
    factor = EMOTION_FACTORS.get(name.strip(), 1.0)
    weight = min(max(float(intensity), 0.0), 1.0)  # a faint mood changes almost nothing
    return 1.0 + (factor - 1.0) * weight


def pause_after(
    text: str,
    emotion: str = "",
    intensity: float = 0.5,
    style: str = "neutre",
    scale: float = 1.0,
    max_s: float = 1.2,
) -> float:
    """Seconds of silence to leave after this sentence; 0.0 for the neutral style or a zero scale."""
    if style != "naturel" or scale <= 0.0 or max_s <= 0.0:
        return 0.0
    base = BASE_S.get(_last_mark(text), DEFAULT_S)
    seconds = base * _emotion_factor(emotion, intensity) * float(scale)
    return round(min(max(seconds, 0.0), float(max_s)), 3)
