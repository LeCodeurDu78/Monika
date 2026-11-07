"""Rôle réflexion : un conseil lu une fois, et une parole seulement si elle est autorisée."""

from __future__ import annotations

import asyncio

from agent.llm import Completion
from agent.reflection import BetweenTurns
from monika.config import Settings


class FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content
        self.models: list[str | None] = []

    async def ask(self, messages, tools=None, temperature=None, model=None):
        self.models.append(model)
        return Completion(self.content)


def config(**values) -> Settings:
    return Settings(_env_file=None, **values)


def test_the_advice_is_injected_once() -> None:
    async def run() -> tuple[str, str]:
        job = BetweenTurns(
            config(reflection_enabled=True, reflection_model="petit-modele"),
            FakeLLM('{"conseil": "Vérifier la météo avant de proposer une sortie.", "parler": ""}'),
        )
        job.submit("on sort demain ?", "Je regarde.")
        left = await job.drain()
        assert left == 0
        return job.take(), job.take()

    assert asyncio.run(run()) == (
        "Vérifier la météo avant de proposer une sortie.",
        "",
    )


def test_an_unreadable_answer_changes_nothing() -> None:
    async def run() -> str:
        job = BetweenTurns(config(reflection_enabled=True), FakeLLM("pas de JSON ici"))
        job.submit("salut", "Bonjour.")
        await job.drain()
        return job.take()

    assert asyncio.run(run()) == ""


def test_speaking_needs_the_explicit_setting() -> None:
    async def run(enabled: bool) -> list[str]:
        spoken: list[str] = []

        async def speak(text: str) -> None:
            spoken.append(text)

        job = BetweenTurns(
            config(reflection_enabled=True, reflection_speak=enabled),
            FakeLLM('{"conseil": "Rien de spécial.", "parler": "rappeler le rendez-vous"}'),
            speak,
        )
        job.submit("et demain ?", "Je te dirai.")
        await job.drain()
        return spoken

    assert asyncio.run(run(False)) == []
    assert asyncio.run(run(True)) == ["rappeler le rendez-vous"]
