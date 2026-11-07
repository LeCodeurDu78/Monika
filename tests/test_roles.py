"""Les trois rôles : un modèle par rôle, et la préséance des réglages dédiés."""

from __future__ import annotations

from agent.roles import (
    action_model,
    memory_model,
    nightly_model,
    reflection_model,
    roles,
    surface_model,
)
from monika.config import Settings


def settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


def test_every_role_falls_back_to_the_main_model() -> None:
    config = settings(reflection_model="", action_llm_model="", surface_llm_model="")

    assert [role.model for role in roles(config)] == [config.model_name] * 4
    assert reflection_model(config) == config.model_name
    assert surface_model(config) == config.model_name


def test_the_reflection_role_covers_memory_and_nightly() -> None:
    config = settings(reflection_model="petit-modele", memory_llm_model="")

    assert memory_model(config) == "petit-modele"
    assert nightly_model(config) == "petit-modele"


def test_a_dedicated_setting_wins_over_the_role() -> None:
    config = settings(
        reflection_model="role", memory_llm_model="directeur", nightly_llm_model="nuit"
    )

    assert memory_model(config) == "directeur"
    assert nightly_model(config) == "nuit"


def test_the_action_role_is_off_until_enabled() -> None:
    off = settings(action_llm_enabled=False, action_llm_model="")
    on = settings(action_llm_enabled=True, action_llm_model="gestes")

    assert [r.active for r in roles(off) if r.key == "action"] == [False]
    assert [r.model for r in roles(on) if r.key == "action"] == ["gestes"]
    assert action_model(on) == "gestes"


def test_the_four_roles_are_listed_in_order() -> None:
    assert [role.key for role in roles(settings())] == [
        "surface",
        "deep",
        "reflexion",
        "action",
    ]


def test_the_surface_role_is_off_until_it_has_its_own_model() -> None:
    off = settings(surface_llm_model="")
    on = settings(surface_llm_model="petit-modele")

    assert [r.active for r in roles(off) if r.key == "surface"] == [False]
    assert [r.model for r in roles(on) if r.key == "surface"] == ["petit-modele"]
    assert surface_model(on) == "petit-modele"
