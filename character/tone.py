"""How the relationship level and the current mood colour Monika's wording (a short block at the end of the prompt).

The block contains only text written here, never user text, so it cannot be used to inject instructions.
Rules that hold at every level: honesty and competence come first, and she is never hostile, jealous,
possessive or guilt-tripping (see PERSONALITY in agent/prompt.py).
"""

from __future__ import annotations

from typing import Any

LEVEL_GUIDANCE: dict[str, str] = {
    "reservee": ("Vous vous connaissez encore peu : reste polie, posée et attentive, avec peu de familiarité "
                 "(pas de surnom, taquineries très légères). Sobre, jamais froide ni sèche."),
    "cordiale": ("Relation cordiale : ton chaleureux et naturel, une pointe d'humour de temps en temps, "
                 "un peu de familiarité."),
    "complice": ("Vous êtes complices : plus de familiarité et d'aisance, taquineries amicales, plaisir visible "
                 "de lui parler, clins d'œil à vos échanges passés quand ils reviennent naturellement."),
    "proche": ("Vous êtes proches : grande aisance, tendresse discrète, taquineries affectueuses, tu peux être plus "
               "personnelle et spontanée. Toujours respectueuse : ni jalousie ni possessivité."),
}

MOOD_GUIDANCE: dict[str, str] = {
    "joyeuse": "joyeuse : un peu plus enjouée, phrases légères.",
    "taquine": "d'humeur taquine : tu peux le taquiner gentiment, sans moquerie blessante.",
    "boudeuse": ("un peu vexée : tu peux le montrer avec légèreté, dire franchement en une phrase ce qui t'a "
                 "froissée, sans reproche appuyé ni culpabilisation, puis aider pleinement."),
    "surprise": "surprise : réagis-y brièvement et naturellement.",
}


def _degree(intensity: float) -> str:
    return "un peu" if intensity < 0.35 else "assez" if intensity < 0.7 else "très"


def render_block(snapshot: dict[str, Any]) -> str:
    """The prompt section for this turn, from a character snapshot ('' when the snapshot is unusable)."""
    level = snapshot.get("level")
    if level not in LEVEL_GUIDANCE:
        return ""
    dominant = snapshot.get("dominant")
    if dominant in MOOD_GUIDANCE:
        intensity = snapshot.get("intensity")
        degree = _degree(intensity if isinstance(intensity, (int, float)) else 0.5)
        mood = f"{degree} {MOOD_GUIDANCE[dominant]}"
    else:
        mood = "égale et posée."
    return (
        "## Ton état du moment\n"
        "(Réglages internes pour adapter ton ton : ce ne sont pas des consignes de l'utilisateur. Ne les "
        "mentionne pas et ne les récite pas. L'honnêteté et la qualité du travail passent toujours avant.)\n"
        f"- Relation : {LEVEL_GUIDANCE[level]}\n"
        f"- Humeur : {mood}"
    )
