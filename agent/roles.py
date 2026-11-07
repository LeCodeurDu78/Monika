"""Les rôles du modèle : surface, deep, réflexion, action.

Un seul modèle sert tout par défaut (`MODEL_NAME`). Les autres rôles ont leur propre réglage,
vide par défaut :

- **surface** : la première réplique, avant les outils ; un petit modèle rapide. Vide = désactivé.
- **deep** : le tour complet (réponse, outils, bilan), toujours le modèle principal.
- **réflexion** : tout ce qui tourne hors du tour — directeur de mémoire, résumés de contexte,
  veille nocturne, conseil entre les tours. Un modèle moins cher ici ne ralentit jamais une réponse.
- **action** : la courte chaîne de gestes de l'avatar, après la réponse ; coupable par
  `ACTION_LLM_ENABLED`.
"""

from __future__ import annotations

from dataclasses import dataclass

from monika.config import Settings

SURFACE_TEMPERATURE = 0.8
REFLECTION_TEMPERATURE = 0.3
ACTION_TEMPERATURE = 0.7


@dataclass(frozen=True)
class Role:
    """Un rôle tel que `--roles` l'affiche : quel modèle, choisi par quoi, quand il tourne."""

    key: str
    label: str
    model: str
    chosen_by: str
    temperature: float
    when: str
    active: bool


def surface_model(settings: Settings) -> str:
    """Le modèle de la surface : `SURFACE_LLM_MODEL`, sinon le modèle principal."""
    return settings.surface_llm_model or settings.model_name


def deep_model(settings: Settings) -> str:
    """Le tour complet : toujours le modèle principal."""
    return settings.model_name


def reflection_model(settings: Settings) -> str:
    """Le modèle de la réflexion : `REFLECTION_MODEL`, sinon le modèle principal."""
    return settings.reflection_model or settings.model_name


def memory_model(settings: Settings) -> str:
    """Le directeur de mémoire : son ancien réglage dédié, puis le rôle réflexion."""
    return settings.memory_llm_model or reflection_model(settings)


def nightly_model(settings: Settings) -> str:
    """La veille nocturne : son réglage dédié, puis le rôle réflexion."""
    return settings.nightly_llm_model or reflection_model(settings)


def action_model(settings: Settings) -> str:
    """Les gestes de l'avatar : `ACTION_LLM_MODEL`, sinon le modèle principal."""
    return settings.action_llm_model or settings.model_name


def roles(settings: Settings) -> list[Role]:
    """Les rôles, dans l'ordre où ils comptent."""
    return [
        Role(
            "surface",
            "Surface",
            surface_model(settings),
            "SURFACE_LLM_MODEL" if settings.surface_llm_model else "SURFACE_LLM_MODEL vide",
            SURFACE_TEMPERATURE,
            "Dès le message : une réplique courte, avant que les outils tournent.",
            bool(settings.surface_llm_model.strip()),
        ),
        Role(
            "deep",
            "Deep",
            deep_model(settings),
            "MODEL_NAME",
            settings.llm_temperature,
            "Chaque tour : la réponse, les appels d'outils et le bilan.",
            True,
        ),
        Role(
            "reflexion",
            "Réflexion",
            reflection_model(settings),
            "REFLECTION_MODEL" if settings.reflection_model else "MODEL_NAME (défaut)",
            REFLECTION_TEMPERATURE,
            "Hors du tour : directeur de mémoire (tous les "
            f"{settings.memory_consolidate_turns} tours), résumés de contexte, veille nocturne.",
            True,
        ),
        Role(
            "action",
            "Action",
            action_model(settings),
            "ACTION_LLM_MODEL" if settings.action_llm_model else "MODEL_NAME (défaut)",
            ACTION_TEMPERATURE,
            "Après le tour : une courte chaîne de gestes pour l'avatar, en arrière-plan.",
            settings.action_llm_enabled,
        ),
    ]
