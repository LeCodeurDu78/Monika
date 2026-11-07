"""Rôle surface : une ligne propre, son propre modèle, et jamais de panne dans le tour."""

from __future__ import annotations

import asyncio

from agent.llm import Completion, LLMError, TextDelta
from agent.loop import Agent
from agent.surface import SurfaceReflex, clean
from monika.config import Settings
from tools.registry import ToolRegistry


class FakeLLM:
    def __init__(self, content: str = "", error: Exception | None = None) -> None:
        self.content = content
        self.error = error
        self.calls: list[str | None] = []
        self.deep_messages = []

    async def ask(self, messages, tools=None, temperature=None, model=None):
        self.calls.append(model)
        if self.error is not None:
            raise self.error
        return Completion(self.content)

    async def stream(self, messages, tools=None, **kwargs):
        self.deep_messages = messages
        yield TextDelta("C'est fait.")
        yield Completion("C'est fait.")


def config(**values) -> Settings:
    return Settings(_env_file=None, surface_timeout_s=5, **values)


def test_clean_keeps_one_unquoted_line() -> None:
    assert clean('« Je regarde ça. »\n\nJe vérifie ensuite.') == "Je regarde ça."
    assert clean("Monika :  Bonjour.  ") == "Bonjour."


def test_the_surface_role_is_off_without_its_own_model() -> None:
    llm = FakeLLM("bonjour")
    reflex = SurfaceReflex(config(surface_llm_model=""), llm)

    assert asyncio.run(reflex.line("salut")) == ""
    assert llm.calls == []


def test_the_surface_line_uses_its_model_and_is_cleaned() -> None:
    llm = FakeLLM("« Je regarde ça. »\n\nJe vérifie ensuite.")
    reflex = SurfaceReflex(config(surface_llm_model="petit-modele"), llm)

    assert asyncio.run(reflex.line("tu peux vérifier ?")) == "Je regarde ça."
    assert llm.calls == ["petit-modele"]


def test_a_surface_failure_leaves_the_turn_untouched() -> None:
    reflex = SurfaceReflex(
        config(surface_llm_model="petit-modele"), FakeLLM(error=LLMError("indisponible"))
    )

    assert asyncio.run(reflex.line("salut")) == ""


def test_the_surface_line_reaches_the_deep_prompt_and_the_final_reply() -> None:
    llm = FakeLLM("Je regarde.")
    agent = Agent(
        config(surface_llm_model="petit-modele"),
        llm,
        ToolRegistry(),
        surface=SurfaceReflex(config(surface_llm_model="petit-modele"), llm),
    )

    async def run() -> str:
        events = [event async for event in agent.run_turn("tu peux vérifier ?")]
        return "".join(e.text for e in events if isinstance(e, TextDelta))

    assert asyncio.run(run()) == "Je regarde.\n\nC'est fait."
    assert agent.history[-1]["content"] == "Je regarde.\n\nC'est fait."
    system = llm.deep_messages[0]["content"]
    assert "Ce que tu viens de dire" in system
    assert "Je regarde." in system
