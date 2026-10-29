"""Emotion tags for the GPT-SoVITS voice: the French rewrite of OpenSakura's TTS mapper.

Three steps, in this order:
  1. quadrant lag — an abrupt mood change is spread over a few phrases instead of jumping
  2. S-curve — an emotion must be genuinely strong to reach the top variant of the voice
  3. state progression — N variants give 2N+1 states; a transition state splits the text in two
     so the voice starts on the lower tag and lands on the higher one mid-answer (2 tags maximum).

The emotion, its variants and its quadrant all come from `character/emotion_spec.py`: nothing about
an emotion is declared here. The mapper holds per-session state, so one instance lives per process
and `reset()` is called when the session starts over.

The tags only mean something on a voice pack that has a reference recording per tag; without one
the engine speaks `neutral1`. That is why `TTS_EMOTION_TAGS=auto` passes the tags the pack really
has as `available`, and nothing else is ever emitted.
"""

from __future__ import annotations

import math
import re

from character.emotion_spec import BY_NAME

DEFAULT_TAG = "neutral1"      # The tag every GPT-SoVITS voice pack understands
MIN_SPLIT_LENGTH = 30         # Shorter texts stay on one tag: no room for a transition
MIN_HALF_LENGTH = 10          # Neither half of a split text may be shorter than this

# Step 2: sigmoid shifted right, so most emotions stay low-mid and only a strong one reaches the top.
S_CURVE_STEEPNESS = 10.0
S_CURVE_MIDPOINT = 0.7

# Step 1: lag values, exactly as in OpenSakura (full strength after ~3 phrases, or ~4 after a diagonal).
ADJACENT_INITIAL_MULT = 0.15
ADJACENT_STEP = 0.35
DIAGONAL_INITIAL_MULT = 0.0
DIAGONAL_STEP = 0.25

DIAGONAL_PAIRS: frozenset[frozenset[str]] = frozenset({frozenset({"HAP", "LAN"}),
                                                       frozenset({"HAN", "LAP"})})

_SENTENCE_BREAKS = re.compile(r"(?<=[.!?])\s+")
_CLAUSE_BREAKS = re.compile(r"(?<=[,;])\s+|(?<=\s)--\s+")


def s_curve(value: float) -> float:
    """Compress an intensity: 0.3 -> ~0.02, 0.5 -> ~0.12, 0.7 -> ~0.50, 0.9 -> ~0.88."""
    x = max(0.0, min(1.0, float(value)))
    k, m = S_CURVE_STEEPNESS, S_CURVE_MIDPOINT
    low = 1.0 / (1.0 + math.exp(-k * (0.0 - m)))
    high = 1.0 / (1.0 + math.exp(-k * (1.0 - m)))
    raw = 1.0 / (1.0 + math.exp(-k * (x - m)))
    return (raw - low) / (high - low)


def find_split_point(text: str) -> int | None:
    """Best place to cut text for a two-tag transition, biased toward the middle; None if there is none."""
    if len(text) < MIN_SPLIT_LENGTH:
        return None
    center = len(text) / 2.0
    candidates: list[tuple[float, int]] = []
    for pattern, weight in ((_SENTENCE_BREAKS, 2.0), (_CLAUSE_BREAKS, 1.0)):
        for match in pattern.finditer(text):
            distance = abs(match.end() - center) / len(text)
            candidates.append((math.exp(-(distance * 4.0) ** 2) * weight, match.end()))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    best = candidates[0][1]
    if best < MIN_HALF_LENGTH or len(text) - best < MIN_HALF_LENGTH:
        return None
    return best


def emotion_state(variants: tuple[str, ...] | list[str], curved: float) -> tuple[str, str | None]:
    """(tag, second tag or None) for N variants: neutral, neutral+v1, v1, v1+v2, …, vN."""
    n = len(variants)
    if n == 0:
        return DEFAULT_TAG, None
    states = 2 * n + 1
    index = min(int(curved * states), states - 1)
    if index <= 0:
        return DEFAULT_TAG, None
    if index % 2 == 1:  # Transition: lower + upper neighbouring tags
        upper = (index - 1) // 2
        lower = DEFAULT_TAG if upper == 0 else variants[upper - 1]
        return lower, variants[upper]
    return variants[index // 2 - 1], None


class TtsEmotionMapper:
    """Turns (text, emotion, intensity) into a tagged text for the voice; keeps the transition state."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Back to a calm voice: the next phrase starts on its own emotion, without a lag."""
        self._quadrant = ""
        self._multiplier = 1.0
        self._step = 0.0

    @property
    def state(self) -> tuple[str, float, float]:
        """(quadrant, multiplier, step) — for the dashboard and the tests."""
        return self._quadrant, self._multiplier, self._step

    def _lag(self, emotion: str, intensity: float) -> float:
        """Effective intensity after softening the jump between quadrants; also advances the ramp."""
        quadrant = BY_NAME[emotion].quadrant if emotion in BY_NAME else ""
        if not quadrant:  # Neutral, surprise or an unknown name: no lag, and the ramp is forgotten
            self.reset()
            return max(0.0, min(1.0, intensity))
        if quadrant == self._quadrant:
            self._multiplier = min(1.0, self._multiplier + self._step)   # Ramp back up turn after turn
        elif not self._quadrant:
            self._multiplier, self._step = 1.0, 0.0
        elif frozenset({self._quadrant, quadrant}) in DIAGONAL_PAIRS:
            self._multiplier, self._step = DIAGONAL_INITIAL_MULT, DIAGONAL_STEP
        else:
            self._multiplier, self._step = ADJACENT_INITIAL_MULT, ADJACENT_STEP
        self._quadrant = quadrant
        return max(0.0, min(1.0, intensity)) * self._multiplier

    def tagged(self, text: str, emotion: str = "", intensity: float = 0.0,
               available: set[str] | frozenset[str] | None = None) -> str:
        """The text with 【tag】 prefixes; `available` limits them to what the voice really has."""
        text = " ".join(str(text).split())
        if not text:
            return ""
        spec = BY_NAME.get(str(emotion).strip().lower())
        variants = spec.tts if spec is not None else ()
        if available is not None:
            variants = tuple(tag for tag in variants if tag in available)
        effective = self._lag(str(emotion).strip().lower(), intensity)
        if not variants or effective <= 0.0:
            return f"【{DEFAULT_TAG}】{text}"
        first, second = emotion_state(variants, s_curve(effective))
        if second is None:
            return f"【{first}】{text}"
        cut = find_split_point(text)
        if cut is None:  # Too short or no natural pause: the whole phrase takes the higher tag
            return f"【{second}】{text}"
        return f"【{first}】{text[:cut].rstrip()} 【{second}】{text[cut:].lstrip()}"


__all__ = ["TtsEmotionMapper", "DEFAULT_TAG", "s_curve", "emotion_state", "find_split_point"]
