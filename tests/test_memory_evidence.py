"""Les souvenirs doivent citer une phrase réellement écrite par l'utilisateur."""

from __future__ import annotations

import asyncio

from memory.notes import NoteMemory, quote_supported
from monika.config import Settings

SAID = "J'ai enfin fini de déménager, la nouvelle cuisine est posée.\nMonika : Bravo !"


def test_a_verbatim_quote_is_supported() -> None:
    assert quote_supported("j'ai enfin fini de déménager", SAID)


def test_punctuation_and_case_do_not_break_a_quote() -> None:
    assert quote_supported("J’ai enfin fini de déménager", SAID)


def test_a_paraphrase_is_refused() -> None:
    assert not quote_supported("il a terminé son déménagement", SAID)


def test_a_too_short_quote_is_refused() -> None:
    assert not quote_supported("oui", SAID)


def test_only_sourced_entries_reach_the_notebook(tmp_path) -> None:
    memory = NoteMemory(tmp_path / "notes.json", Settings(_env_file=None))
    data = {
        "notes": "Il vient de déménager.",
        "notes_quotes": ["J'ai enfin fini de déménager"],
        "ongoing_topics": [
            {"text": "La cuisine", "quote": "la nouvelle cuisine est posée"},
            {"text": "Inventé", "quote": "une phrase qui n'a jamais été dite"},
        ],
        "completed_events": [
            {"text": "Déménagement terminé", "quote": "fini de déménager"}
        ],
        "strategic_guidance": {"lecture": "content"},
    }

    asyncio.run(memory._merge(data, SAID))
    items = asyncio.run(memory.list_all())
    by_text = {item.text: item for item in items}

    assert "La cuisine" in by_text
    assert "Inventé" not in by_text
    assert "Déménagement terminé" in by_text
    assert by_text["La cuisine"].quote == "la nouvelle cuisine est posée"
    assert by_text["Il vient de déménager."].quote == "J'ai enfin fini de déménager"


def test_rewriting_the_notes_drops_their_old_proofs(tmp_path) -> None:
    memory = NoteMemory(tmp_path / "notes.json", Settings(_env_file=None))
    asyncio.run(
        memory._merge(
            {
                "notes": "Il aime la cuisine.",
                "notes_quotes": ["la nouvelle cuisine est posée"],
            },
            SAID,
        )
    )

    asyncio.run(memory.update("notes", "Il déménage bientôt."))
    notes = asyncio.run(memory.list_all())[0]

    assert notes.text == "Il déménage bientôt."
    assert notes.quote == ""


def test_a_legacy_notebook_still_reads(tmp_path) -> None:
    path = tmp_path / "notes.json"
    path.write_text(
        '{"notes": "ancien", "ongoing_topics": ["sujet sans preuve"],'
        ' "completed_events": [{"at": 1, "event": "épisode ancien"}]}',
        encoding="utf-8",
    )
    memory = NoteMemory(path, Settings(_env_file=None))

    items = asyncio.run(memory.list_all())

    assert [item.text for item in items] == ["ancien", "sujet sans preuve", "épisode ancien"]
