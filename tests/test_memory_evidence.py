"""La mémoire cite une phrase réellement écrite, et les corrections gardent l'ancienne version."""

from __future__ import annotations

import asyncio
import json
import time

from agent.llm import Completion
from memory.notes import EvidenceMemory, quote_supported
from monika.config import Settings

SAID = "J'ai enfin fini de déménager, la nouvelle cuisine est posée.\nMonika : Bravo !"


class FakeLLM:
    """Répond un JSON fixe ; aucun réseau, aucun modèle."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    async def ask(self, messages, **kwargs) -> Completion:  # noqa: ANN001, ARG002
        self.calls += 1
        return Completion(json.dumps(self.payload, ensure_ascii=False), [])


def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        monika_home=tmp_path,
        memory_consolidate_turns=2,
        memory_embeddings_enabled=False,
    )


def test_a_verbatim_quote_is_supported() -> None:
    assert quote_supported("j'ai enfin fini de déménager", SAID)


def test_punctuation_and_case_do_not_break_a_quote() -> None:
    assert quote_supported("J’ai enfin fini de déménager", SAID)


def test_a_paraphrase_is_refused() -> None:
    assert not quote_supported("il a terminé son déménagement", SAID)


def test_a_too_short_quote_is_refused() -> None:
    assert not quote_supported("oui", SAID)


def test_capture_extracts_a_quoted_fact(tmp_path) -> None:
    memory = EvidenceMemory(tmp_path / "notes.json", settings(tmp_path))
    memory.bind_llm(
        FakeLLM(
            {
                "facts": [
                    {
                        "text": "Il a fini de déménager et la cuisine est posée.",
                        "kind": "user_fact",
                        "quotes": [
                            {
                                "source_id": "e1",
                                "speaker": "user",
                                "quote": "J'ai enfin fini de déménager, la nouvelle cuisine est posée.",
                            }
                        ],
                        "supersedes": [],
                    }
                ]
            }
        )
    )

    asyncio.run(memory.capture(SAID, "Bravo !"))
    asyncio.run(memory.capture("Un second message pour déclencher le lot.", "D'accord."))
    items = asyncio.run(memory.list_all())

    assert len(items) == 1
    assert items[0].text.startswith("Il a fini de déménager")
    assert items[0].quote.startswith("J'ai enfin fini")
    assert items[0].status == "active"


def test_a_fact_without_a_real_quote_is_dropped(tmp_path) -> None:
    memory = EvidenceMemory(tmp_path / "notes.json", settings(tmp_path))
    memory.bind_llm(
        FakeLLM(
            {
                "facts": [
                    {
                        "text": "Il a terminé son déménagement.",
                        "kind": "user_fact",
                        "quotes": [
                            {
                                "source_id": "e1",
                                "speaker": "user",
                                "quote": "il a terminé son déménagement",
                            }
                        ],
                    }
                ]
            }
        )
    )

    asyncio.run(memory.capture(SAID, "Bravo !"))
    asyncio.run(memory.capture("Un second message pour déclencher le lot.", "D'accord."))

    assert asyncio.run(memory.list_all()) == []


def test_recall_renders_the_quote_and_the_fact(tmp_path) -> None:
    memory = EvidenceMemory(tmp_path / "notes.json", settings(tmp_path))
    memory.bind_llm(
        FakeLLM(
            {
                "facts": [
                    {
                        "text": "Il a fini de déménager et la cuisine est posée.",
                        "kind": "user_fact",
                        "quotes": [
                            {
                                "source_id": "e1",
                                "speaker": "user",
                                "quote": "la nouvelle cuisine est posée",
                            }
                        ],
                    }
                ]
            }
        )
    )
    asyncio.run(memory.capture(SAID, "Bravo !"))
    asyncio.run(memory.capture("Un second message pour déclencher le lot.", "D'accord."))

    block = asyncio.run(memory.recall("déménagement cuisine"))

    assert "la nouvelle cuisine est posée" in block
    assert "Il a fini de déménager" in block
    assert "e1" not in block  # le prompt ne voit jamais les identifiants


def test_a_correction_keeps_the_old_fact_and_the_new_one(tmp_path) -> None:
    memory = EvidenceMemory(tmp_path / "notes.json", settings(tmp_path))
    old = asyncio.run(memory.add_fact("Il aime le thé."))
    event = {
        "id": "e1",
        "at": time.time(),
        "user": "Je préfère le café maintenant, plus de thé.",
        "monika": "",
    }

    new_ids = asyncio.run(
        memory._merge(
            {
                "facts": [
                    {
                        "text": "Il préfère le café au thé.",
                        "kind": "user_fact",
                        "quotes": [
                            {
                                "source_id": "e1",
                                "speaker": "user",
                                "quote": "Je préfère le café maintenant, plus de thé.",
                            }
                        ],
                        "supersedes": ["f1"],
                    }
                ]
            },
            [event],
        )
    )
    items = {item.id: item for item in asyncio.run(memory.list_all())}

    assert new_ids == ["f2"]
    assert items[old.id].status == "superseded"
    assert items["f2"].status == "active"
    block = asyncio.run(memory.recall("café"))
    assert "version antérieure, corrigée depuis" in block

    asyncio.run(memory.delete("f2"))  # supprimer la correction réactive l'ancienne
    items = {item.id: item for item in asyncio.run(memory.list_all())}
    assert items["f1"].status == "active"


def test_rewriting_a_fact_drops_its_old_proof(tmp_path) -> None:
    memory = EvidenceMemory(tmp_path / "notes.json", settings(tmp_path))
    asyncio.run(memory.add_fact("Il aime la cuisine."))
    asyncio.run(
        memory._merge(
            {
                "facts": [
                    {
                        "text": "Il a fini de déménager.",
                        "kind": "user_fact",
                        "quotes": [
                            {
                                "source_id": "e1",
                                "speaker": "user",
                                "quote": "fini de déménager",
                            }
                        ],
                    }
                ]
            },
            [
                {
                    "id": "e1",
                    "at": time.time(),
                    "user": "J'ai enfin fini de déménager.",
                    "monika": "",
                }
            ],
        )
    )

    asyncio.run(memory.update("f2", "Il déménage bientôt."))
    rewritten = next(i for i in asyncio.run(memory.list_all()) if i.id == "f2")

    assert rewritten.text == "Il déménage bientôt."
    assert rewritten.quote == ""
    assert rewritten.kind == "manuel"


def test_the_old_notebook_is_archived_not_read(tmp_path) -> None:
    path = tmp_path / "notes.json"
    path.write_text(
        '{"notes": "ancien", "ongoing_topics": ["sujet sans preuve"]}',
        encoding="utf-8",
    )

    memory = EvidenceMemory(path, settings(tmp_path))

    assert asyncio.run(memory.list_all()) == []
    assert list(tmp_path.glob("notes.legacy-*.json"))


def test_manual_facts_are_visible_but_marked_as_uncited(tmp_path) -> None:
    memory = EvidenceMemory(tmp_path / "notes.json", settings(tmp_path))

    item = asyncio.run(memory.add_fact("Il travaille sur Monika."))

    assert item.id == "f1"
    assert item.kind == "manuel"
    block = asyncio.run(memory.recall("Monika"))
    assert "sans citation" in block
