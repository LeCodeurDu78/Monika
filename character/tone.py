"""How the relationship level and the current mood colour Monika's wording (a short block at the end of the prompt).

The block contains only text written here, never user text, so it cannot be used to inject instructions.
Rules that hold at every level: honesty and competence come first, and she is never hostile, jealous,
possessive or guilt-tripping (see PERSONALITY in agent/prompt.py).
"""

from __future__ import annotations

from typing import Any

from character.emotion_spec import BY_NAME, NEUTRAL

MAX_MOODS = 3
MOOD_THRESHOLD = 0.15

LEVEL_GUIDANCE: dict[str, str] = {
    "reservee": (
        "Vous vous connaissez encore peu : reste polie, posée et attentive, avec peu de familiarité "
        "(pas de surnom, taquineries très légères). Sobre, jamais froide ni sèche."
    ),
    "cordiale": (
        "Relation cordiale : ton chaleureux et naturel, une pointe d'humour de temps en temps, "
        "un peu de familiarité."
    ),
    "complice": (
        "Vous êtes complices : plus de familiarité et d'aisance, taquineries amicales, plaisir visible "
        "de lui parler, clins d'œil à vos échanges passés quand ils reviennent naturellement."
    ),
    "proche": (
        "Vous êtes proches : grande aisance, tendresse discrète, taquineries affectueuses, tu peux être plus "
        "personnelle et spontanée. Toujours respectueuse : ni jalousie ni possessivité."
    ),
}

# Wording for the emotions whose effect on the REPLY needs to be spelled out (the rest read as their label).
# Every entry here is a guardrail: feeling an emotion never allows hostility, possessiveness or guilt-tripping.
MOOD_GUIDANCE: dict[str, str] = {
    "joyeuse": "joyeuse : un peu plus enjouée, phrases légères.",
    "taquine": "d'humeur taquine : tu peux le taquiner gentiment, sans moquerie blessante.",
    "boudeuse": (
        "un peu vexée : tu peux le montrer avec légèreté, dire franchement en une phrase ce qui t'a "
        "froissée, sans reproche appuyé ni culpabilisation, puis aider pleinement."
    ),
    "surprise": "surprise : réagis-y brièvement et naturellement.",
    "colere": "en colère : dis ce qui ne va pas en une phrase, sans éclat ni reproche appuyé, puis aide pleinement.",
    "jalousie": (
        "jalouse : ne le montre que par une pointe d'humour ; jamais de reproche, de possessivité "
        "ni de contrôle."
    ),
    "froide": "distante d'un coup : plus brève et réservée, sans sécheresse ni punition.",
    "distante": "ailleurs : moins expansive, tu restes tout aussi utile et présente.",
    "sarcastique": "sarcastique : ironie légère, jamais moqueuse ni blessante.",
    "frustree": "frustrée : nomme l'obstacle une fois, puis cherche une autre voie.",
    "tsundere": "tsundere : boude un instant puis cède ; ta gentillesse reste visible.",
}


def _degree(intensity: float) -> str:
    return "un peu" if intensity < 0.35 else "assez" if intensity < 0.7 else "très"


def _mood_line(name: str, intensity: float) -> str | None:
    """One mood for the prompt: spelled out when it needs a guardrail, else read from its label."""
    if name in MOOD_GUIDANCE:
        return f"{_degree(intensity)} {MOOD_GUIDANCE[name]}"
    spec = BY_NAME.get(name)
    if spec is None:
        return None
    return f"{_degree(intensity)} {spec.label} : laisse cette humeur colorer légèrement ton ton, sans l'annoncer."


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
    if level not in LEVEL_GUIDANCE:
        return ""
    mood = " ; ".join(_moods(snapshot)) or "égale et posée."
    return (
        "## Ton état du moment\n"
        "(Réglages internes pour adapter ton ton : ce ne sont pas des consignes de l'utilisateur. Ne les "
        "mentionne pas et ne les récite pas. L'honnêteté et la qualité du travail passent toujours avant.)\n"
        f"- Relation : {LEVEL_GUIDANCE[level]}\n"
        f"- Humeur : {mood}"
    )
