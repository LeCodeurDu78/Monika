"""Deciding what the avatar does: a reaction to the user's message, then gestures that fit Monika's own reply.

Everything here is deterministic and cheap (no model call), so it can run on every message and every sentence.
Clip names refer to web/static/clips.json; the page ignores names it does not know.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

from character.models import Appraisal, AvatarCue, fold_text
from character.relationship import LEVELS

# ------------------------------------------------------------------------------- reaction to the user

# signal -> options (minimum level index, clip, emotion), first that fits wins. Order = which signal wins.
_REACTIONS: tuple[tuple[str, tuple[tuple[int, str, str], ...]], ...] = (
    ("surprise", ((0, "Surprised", "surprise"),)),
    # Visible anger is reserved for a close relationship: below that, the pout carries the same message.
    ("hostility", ((3, "Angry", "colere"), (2, "Pout", "boudeuse"), (0, "ShakeHead", "boudeuse"))),
    ("insult", ((3, "Angry", "colere"), (2, "Pout", "boudeuse"), (0, "ShakeHead", "boudeuse"))),
    ("disappointment", ((2, "Pout", "boudeuse"), (0, "ShakeHead", "boudeuse"))),
    ("shouting", ((2, "Pout", "boudeuse"), (0, "ShakeHead", "boudeuse"))),
    ("user_distress", ((2, "OpenArms", "attentionnee"), (0, "Think", "inquiete"))),
    ("affection", ((1, "Shy", "joyeuse"), (0, "Yes", "joyeuse"))),
    ("compliment", ((1, "Shy", "joyeuse"), (0, "Yes", "joyeuse"))),
    ("thanks", ((0, "Yes", "joyeuse"),)),
    ("tease", ((1, "LeanIn", "taquine"),)),
    ("greeting", ((1, "Wave", "joyeuse"), (0, "WaveSmall", "joyeuse"))),
    ("farewell", ((0, "Wave", "joyeuse"),)),
    # Finer signals, checked last so they never steal a reaction from the ones above.
    ("jealousy", ((1, "Pout", "jalousie"), (0, "ShakeHead", "boudeuse"))),
    ("shyness", ((0, "Shy", "timide"),)),
    ("pride", ((0, "HandsOnHips", "fiere"),)),
    ("worry", ((0, "Think", "inquiete"),)),
)


def sensitive_cue(tier: str | None) -> AvatarCue | None:
    """Immediate reaction to a coarse or hurtful word: a face, never a wall. 'mild' gets nothing."""
    if tier == "bad":
        # Above the character's own reaction (priority 2): otherwise the mood cue, which arrives just after,
        # replaces it on the page and the pout never shows.
        return AvatarCue(emotion="boudeuse", intensity=0.55, clip="Pout",
                         reason="user:sensitive:bad", priority=3)
    if tier == "severe":
        # Folded arms and a cool face: quieter than anger, and never hostile back.
        return AvatarCue(emotion="froide", intensity=0.7, clip="FoldArms",
                         reason="user:sensitive:severe", priority=4)
    return None


def level_index(snapshot: dict[str, Any]) -> int:
    level = snapshot.get("level")
    return LEVELS.index(level) if level in LEVELS else 1


def reaction_cues(appraisal: Appraisal, snapshot: dict[str, Any]) -> list[AvatarCue]:
    """One cue at the start of the turn: a gesture when the message calls for one, else just the mood on the face."""
    level = level_index(snapshot)
    for signal, options in _REACTIONS:
        if signal not in appraisal.signals:
            continue
        for min_level, clip, emotion in options:
            if level >= min_level:
                return [AvatarCue(emotion=emotion, intensity=max(0.45, appraisal.intensity), clip=clip,
                                  reason=f"user:{signal}", priority=2)]
    dominant = snapshot.get("dominant", "neutre")
    intensity = snapshot.get("intensity", 0.5)
    return [AvatarCue(emotion=dominant, intensity=intensity if isinstance(intensity, (int, float)) else 0.5,
                      reason="mood", priority=1)]


# ------------------------------------------------------------------------------- gestures in her reply

# (pattern on folded text, clip, emotion or None, minimum level, only at the very start of the reply)
_REPLY_RULES: tuple[tuple[re.Pattern[str], str, str | None, int, bool], ...] = tuple(
    (re.compile(pattern), clip, emotion, level, opening)
    for pattern, clip, emotion, level, opening in (
        (r"^\W*(bonjour|salut|coucou|hello|bonsoir|hey)\b", "Wave", "joyeuse", 0, True),
        (r"^\W*(oui|d'accord|bien sur|entendu|compris|avec plaisir|volontiers|absolument|tout a fait)\b", "Yes", None, 0, True),
        (r"^\W*non\b", "ShakeHead", None, 0, True),
        (r"^\W*(malheureusement|desolee|je suis desolee)\b", "Sad", None, 0, True),
        (r"\b(j'ai une idee|et si on|et si tu|je te propose|une piste)\b", "Idea", None, 0, False),
        (r"\b(haha+|ahah+|hihi+|mdr|ptdr)\b", "Laugh", "joyeuse", 0, False),
        (r"\b(bravo|felicitations|youpi|ravie|trop contente|je suis contente)\b", "Yay", "joyeuse", 0, False),
        (r"\b(tu me fais rougir|je rougis)\b", "Shy", "joyeuse", 1, False),
        (r"\b(je vais pleurer|j'ai envie de pleurer|ca me rend triste|je suis triste)\b", "Cry", "tristesse", 0, False),
        (r"\b(je m'ennuie|on s'ennuie|je baille|j'ai sommeil)\b", "Stretch", None, 0, False),
        (r"\b(j'ai hate|trop hate|vivement)\b", "Hop", "excitee", 0, False),
        (r"\b(on danse|je danse|c'est la fete)\b", "Dance", "excitee", 0, False),
        (r"\b(toi aussi|a toi de|c'est toi qui)\b", "Point", None, 0, False),
        (r"\b(je suis la pour toi|tu peux compter sur moi|viens la)\b", "OpenArms", "attentionnee", 1, False),
        (r"\b(petit malin|petit coquin|dis donc|tu fais expres)\b", "LeanIn", "taquine", 1, False),
        (r"^\W*(oh|waouh|wow|incroyable)\b[^.?]*!", "Surprised", "surprise", 0, False),
    ))
_SENTENCE_END = re.compile(r"(?:[.!?…]+|\n)(?=\s|$)")
OPENING_MIN_CHARS = 10


class ReplyCues:
    """Reads Monika's reply as it streams and proposes a gesture when a sentence calls for one.

    Restraint is the point: a gesture is only proposed after a pause since the last one, at most a few times per
    reply, and never a clip that already played this turn.
    """

    def __init__(self, level: int = 1, played: set[str] | None = None, cooldown_s: float = 8.0, max_cues: int = 3,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._level = level
        self._played = set(played or ())
        self._cooldown = cooldown_s
        self._max = max_cues
        self._clock = clock
        self._buffer = ""
        self._pending = ""         # text of the sentence in progress
        self._opening_done = False
        self._count = 0
        self._last = -1e9

    def feed(self, text: str) -> list[AvatarCue]:
        """Add streamed text; returns the cues (at most one) that became due."""
        self._buffer += text
        self._pending += text
        cues: list[AvatarCue] = []
        if not self._opening_done and (len(self._pending.strip()) >= OPENING_MIN_CHARS or _SENTENCE_END.search(self._pending)):
            self._opening_done = True
            cues += self._look(self._pending, opening=True)
        while True:
            match = _SENTENCE_END.search(self._pending)
            if not match:
                break
            sentence, self._pending = self._pending[:match.end()], self._pending[match.end():]
            cues += self._look(sentence, opening=False)
        return cues[:1]

    def flush(self) -> list[AvatarCue]:
        """The reply is over: look at what remains."""
        rest, self._pending = self._pending, ""
        return self._look(rest, opening=False)[:1] if rest.strip() else []

    def _look(self, sentence: str, opening: bool) -> list[AvatarCue]:
        if self._count >= self._max or self._clock() - self._last < self._cooldown:
            return []
        folded = fold_text(sentence)
        for pattern, clip, emotion, min_level, opening_only in _REPLY_RULES:
            if opening_only and not opening:
                continue
            if self._level < min_level or clip in self._played or not pattern.search(folded):
                continue
            self._played.add(clip)
            self._count += 1
            self._last = self._clock()
            return [AvatarCue(emotion=emotion, intensity=0.6, clip=clip, reason="reply", priority=1)]
        return []
