"""Action role: what the avatar does right after a reply, decided in the background.

A reduced port of `OpenSakura-main/sakura/action_controller.py`: the same action library, the same
duration and count rules, but no global state, no prints, no settings coupling and a single chain (the
delayed follow-up query of the original is gone). The result is a message for the page
(`hub.publish("avatar_chain", …)`), which `CueController.chain()` plays.

This role is cosmetic: it never blocks the turn, never touches the history, the emotions or the
favorability. Any failure means "no chain", and the deterministic gestures of `character/cues.py` stay
the only animation.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections import deque
from typing import Any

from character.appraisal import Complete

MIN_DURATION = 2.0
MAX_DURATION = 8.0
MAX_ACTIONS = 3
LLM_TIMEOUT_S = 20.0

# Every clip of web/static/assets/clips.json that can be an action ("breathing" is the idle loop).
ACTION_LIBRARY: dict[str, str] = {
    "Talk": "Parle avec de petits gestes des mains ; explique, discute, répond (bonne fin)",
    "Think": "Poing sous le menton ; réfléchit, pèse le pour et le contre, hésite",
    "Idea": "Lève un doigt à côté du visage ; « j'ai une idée », « oh, je sais »",
    "LookAround": "Regarde à gauche et à droite ; curieuse, cherche quelque chose, s'ennuie",
    "Stretch": "S'étire les bras au-dessus de la tête ; fatiguée, détendue, s'ennuie (une fois)",
    "Wave": "Grand salut au-dessus de la tête ; bonjour, au revoir, « par ici ! »",
    "WaveSmall": "Petit salut à côté du visage ; un coucou discret",
    "Bow": "Révérence mains jointes ; salue, remercie, s'excuse (une fois)",
    "Yay": "Lève les deux bras au-dessus de la tête ; célèbre, très contente",
    "Hop": "Sautille sur place ; joie, excitation qui déborde",
    "Dance": "Danse d'un pied sur l'autre ; joueuse, de très bonne humeur",
    "Laugh": "Rit derrière sa main ; amusée, taquine",
    "Yes": "Hoche la tête ; oui, d'accord, c'est noté, déterminée",
    "Shy": "Joint les index, tête baissée et détournée ; timide, gênée",
    "Sad": "Tête basse, mains jointes ; triste, seule, déçue",
    "Cry": "Se couvre les yeux ; pleure, très triste",
    "Surprised": "Recule avec les mains levées ; surprise, « hein ? » (une fois)",
    "ShakeHead": "Secoue la tête ; non, pas d'accord, refuse",
    "Angry": "Poings serrés devant elle ; en colère, frustrée",
    "Pout": "Bras croisés, regard détourné ; boude, « humpf »",
    "FoldArms": "Bras croisés ; sceptique, pas impressionnée, attend",
    "HandsOnHips": "Mains sur les hanches ; sûre d'elle, fière, gronde un peu",
    "LeanIn": "Se penche vers toi, tête inclinée ; taquine, curieuse, joueuse",
    "OpenArms": "Ouvre les bras, paumes vers le haut ; « viens là », accueille",
    "Point": "Te pointe du doigt, l'autre main sur la hanche ; « toi ! », t'interpelle",
}

_CANONICAL = {name.lower(): name for name in ACTION_LIBRARY}

SYSTEM_PROMPT = """\
Tu diriges les gestes du corps d'un personnage 3D pour la réponse qu'elle vient d'écrire.
Réponds STRICTEMENT en JSON, sans texte autour : {{"actions": [{{"name": "Talk", "duration": 4.0}}]}}
- entre 1 et {max_actions} actions, dans l'ordre où elles se jouent ;
- "name" est un nom EXACT de la bibliothèque ;
- "duration" en secondes, entre {min_duration} et {max_duration} ; la somme doit approcher la durée demandée ;
- choisis des gestes qui collent à la réponse et à l'émotion, sans répéter les gestes récents ;
- si rien ne colle, garde Talk ou Think.

Bibliothèque :
{library}"""


def decide_action_count(reply_length: int, intensity: float) -> int:
    """How many actions a reply of this length and emotion deserves (1 to 3)."""
    if reply_length < 10:
        return 1
    if reply_length < 30:
        return 2 if intensity > 0.5 else 1
    return min(MAX_ACTIONS, reply_length // 15)


def calculate_action_duration(reply_text: str, intensity: float) -> float:
    """Total duration: longer reply and stronger emotion mean longer gestures, bounded to 2-8 s."""
    duration = len(reply_text) * 0.15 * (1.0 + intensity * 0.5)
    if "…" in reply_text or "~" in reply_text:
        duration *= 1.3
    return round(min(max(duration, MIN_DURATION), MAX_DURATION), 1)


def parse_chain(raw: Any, total_duration: float) -> list[dict[str, Any]]:
    """Strict parser: unknown names are dropped, durations are clamped, at most 3 actions. Never raises."""
    if not isinstance(raw, str) or "{" not in raw:
        return []
    start, end = raw.find("{"), raw.rfind("}")
    if end <= start:
        return []
    try:
        data = json.loads(raw[start : end + 1])
    except (json.JSONDecodeError, TypeError):
        return []
    items = data.get("actions") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    fallback = round(min(max(total_duration / 2.0, MIN_DURATION), MAX_DURATION), 1)
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items[:MAX_ACTIONS]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        name = _CANONICAL.get(item["name"].strip().lower())
        if (
            name is None or name in seen
        ):  # unknown name or twice in a row: the page would look stuck
            continue
        seen.add(name)
        actions.append(
            {"name": name, "duration": _duration(item.get("duration"), fallback)}
        )
    return actions


def _duration(raw: Any, fallback: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(value):
        return fallback
    return round(min(max(value, MIN_DURATION), MAX_DURATION), 1)


def _one_line(text: Any, limit: int) -> str:
    """Flatten and bound a text that goes into a prompt (tags removed, it must not look like a tag)."""
    flat = " ".join(str(text).replace("<", " ").replace(">", " ").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def build_prompt(
    user_text: str,
    reply: str,
    snapshot: dict[str, Any],
    recent: list[str],
    count: int,
    total: float,
) -> str:
    """The single user message of the action call: facts only, bounded."""
    mood = snapshot.get("dominant") or "neutre"
    try:
        intensity = float(snapshot.get("intensity", 0.5))
    except (TypeError, ValueError):
        intensity = 0.5
    level = snapshot.get("level_label") or snapshot.get("level") or "cordiale"
    return "\n".join(
        [
            f"Message de l'utilisateur : {_one_line(user_text, 600)}",
            f"Sa réponse : {_one_line(reply, 900)}",
            f"Émotion : {mood} (intensité {min(max(intensity, 0.0), 1.0):.2f}) ; relation : {level}",
            f"Gestes récents : {', '.join(recent) if recent else 'aucun'}",
            f"À produire : {count} action(s), environ {total:.1f} s au total.",
        ]
    )


class ActionDirector:
    """Post-turn avatar actions: one small model call, a strict chain, one hub message. Never raises."""

    def __init__(
        self,
        complete: Complete | None,
        hub: Any | None = None,
        timeout_s: float = LLM_TIMEOUT_S,
        history_size: int = 5,
    ) -> None:
        self._complete = complete
        self._hub = hub
        self._timeout = timeout_s
        self._recent: deque[str] = deque(maxlen=history_size)

    @property
    def enabled(self) -> bool:
        return self._complete is not None

    @property
    def recent(self) -> list[str]:
        return list(self._recent)

    async def suggest(
        self, user_text: str, reply: str, snapshot: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Decide and broadcast a chain for this finished reply; [] when there is nothing to play."""
        if self._complete is None or not str(reply).strip():
            return []
        snapshot = snapshot or {}
        total = calculate_action_duration(reply, _intensity(snapshot))
        count = decide_action_count(len(reply), _intensity(snapshot))
        system = SYSTEM_PROMPT.format(
            max_actions=MAX_ACTIONS,
            min_duration=MIN_DURATION,
            max_duration=MAX_DURATION,
            library="\n".join(
                f"- {name} : {text}" for name, text in ACTION_LIBRARY.items()
            ),
        )
        prompt = build_prompt(user_text, reply, snapshot, self.recent, count, total)
        try:
            async with asyncio.timeout(self._timeout):
                raw = await self._complete(system, prompt)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - optional role: a failure means "no chain"
            return []
        actions = parse_chain(raw, total)
        if not actions:
            return []
        self._recent.extend(action["name"] for action in actions)
        if self._hub is not None:
            self._hub.publish("avatar_chain", actions=actions, reason="action")
        return actions


def _intensity(snapshot: dict[str, Any]) -> float:
    try:
        return min(max(float(snapshot.get("intensity", 0.5)), 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.5
