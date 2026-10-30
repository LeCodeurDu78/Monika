"""Tiered sensitive-word matcher (three levels), ported from OpenSakura.

The bank is the user's: the repository ships no word at all. Matching is deterministic and cheap, so it
runs at the start of every turn without a model call:

- NFKC then accent folding, lower case, zero-width characters removed;
- leetspeak (`1 0 3 4 5 @ $`), glue characters (`_ - + .`) turned into separators;
- apostrophes count as separators, so French elisions are seen (`l'idiot` -> `l idiot`, `t'es con`);
- whole words only, plural `s` tolerated (`\\b(?:… )s?\\b`), so `rigger` never matches `trigger`;
- `match()` returns the highest level hit: `severe` > `bad` > `mild`, or None.

Nothing here ever returns the matched word: callers only learn the level, which is what the audit trail
and the page get. Filtering the reply is deliberately not done here (see the plan, module 6): this colours
the tone, it does not build a wall.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

from character.models import fold_text

TIERS: tuple[str, ...] = ("mild", "bad", "severe")
MAX_WORD_CHARS = 64
MAX_WORDS_PER_TIER = 500
NOTE = (
    "Une entrée par ligne, trois niveaux. Aucun mot n'est livré avec le projet : cette liste est la "
    "tienne (SENSITIVE_WORDS_ENABLED=false pour couper le filtre)."
)

_ZERO_WIDTH = re.compile(r"[\u200B-\u200D\uFEFF\u2060]")
# `@` at a word boundary is a mention prefix (separator); inside a word it is leetspeak for `a`.
_SEP_AT = re.compile(r"(^|\W)@")
_LEETSPEAK = str.maketrans(
    {"1": "i", "0": "o", "3": "e", "4": "a", "@": "a", "$": "s", "5": "s"}
)
_GLUE_TO_SPACE = re.compile(r"[_\-+.]")


class WordsError(Exception):
    """The word list could not be read or was refused; the message is in French and ready to display."""


def normalize(text: str) -> str:
    """Fold a message the way the bank is folded: no accents, no case, no leetspeak, no glue."""
    folded = fold_text(unicodedata.normalize("NFKC", text or ""))
    folded = _ZERO_WIDTH.sub("", folded)
    folded = _SEP_AT.sub(
        r"\1 ", folded
    )  # "@clanker" -> " clanker", "cl@nker" untouched
    folded = folded.translate(_LEETSPEAK)  # remaining @ (mid-word) becomes 'a'
    folded = folded.replace("'", " ")  # French elision: l'idiot -> l idiot
    return _GLUE_TO_SPACE.sub(" ", folded)


def clean_word(raw: str) -> str:
    """One bank entry, stored folded exactly like the text it will meet."""
    return " ".join(normalize(raw).split())


def validate_tiers(raw: Any) -> dict[str, list[str]]:
    """Strict validation of what the page sends or what words.json holds. French error, nothing partial."""
    if not isinstance(raw, dict):
        raise WordsError(
            "La liste doit être un objet JSON avec les niveaux mild, bad et severe."
        )
    unknown = set(raw) - set(TIERS) - {"_note"}
    if unknown:
        raise WordsError(
            "Niveaux inconnus : "
            + ", ".join(sorted(unknown))
            + ". Attendu : mild, bad, severe."
        )
    cleaned: dict[str, list[str]] = {}
    for tier in TIERS:
        values = raw.get(tier) or []
        if isinstance(
            values, str
        ):  # One word per line, as typed in the page's textarea
            values = values.splitlines()
        if not isinstance(values, list):
            raise WordsError(f"« {tier} » doit être une liste de mots.")
        if len(values) > MAX_WORDS_PER_TIER:
            raise WordsError(f"« {tier} » : {MAX_WORDS_PER_TIER} mots au maximum.")
        words: list[str] = []
        for value in values:
            if not isinstance(value, str):
                raise WordsError(f"« {tier} » ne contient que du texte.")
            for line in value.splitlines():
                word = clean_word(line)
                if not word:
                    continue
                if len(word) > MAX_WORD_CHARS:
                    raise WordsError(
                        f"Mot trop long dans « {tier} » ({MAX_WORD_CHARS} caractères au "
                        f"maximum) : {word[:20]}…"
                    )
                if word not in words:
                    words.append(word)
        cleaned[tier] = words
    return cleaned


def read_words(path: Path) -> dict[str, list[str]]:
    """Read the bank, creating an empty one on first use. Raises WordsError on an unusable file."""
    if not path.exists():
        empty = {tier: [] for tier in TIERS}
        write_words(path, empty)
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {tier: [] for tier in TIERS}
    except (OSError, ValueError) as exc:
        raise WordsError(
            f"{path} est illisible ({exc}) : corrige ou supprime-le."
        ) from exc
    return validate_tiers(data)


def write_words(path: Path, tiers: dict[str, list[str]]) -> None:
    """Write the bank in one go, private to the user."""
    payload = {"_note": NOTE, **{tier: list(tiers.get(tier, [])) for tier in TIERS}}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(
            dir=str(path.parent), prefix=".words-", suffix=".tmp"
        )
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError as exc:
        raise WordsError(
            f"Impossible d'écrire {path} : {exc.strerror or exc}."
        ) from exc


class WordBank:
    """Three compiled patterns; empty levels cost nothing and match nothing."""

    def __init__(self, tiers: dict[str, list[str]] | None = None) -> None:
        tiers = tiers or {}
        self._patterns: dict[str, re.Pattern[str] | None] = {}
        self._counts: dict[str, int] = {}
        self._words: dict[str, tuple[str, ...]] = {}
        for tier in TIERS:
            words = [clean_word(w) for w in (tiers.get(tier) or []) if clean_word(w)]
            self._words[tier] = tuple(words)
            self._counts[tier] = len(words)
            self._patterns[tier] = (
                re.compile(
                    rf"\b(?:{'|'.join(re.escape(w) for w in words)})s?\b", re.IGNORECASE
                )
                if words
                else None
            )

    @classmethod
    def load(cls, path: Path) -> WordBank:
        """Read the file and build the bank; raises WordsError (the caller keeps the filter off)."""
        return cls(read_words(path))

    @property
    def counts(self) -> dict[str, int]:
        return dict(self._counts)

    @property
    def words(self) -> dict[str, list[str]]:
        """What the settings page shows (already folded: accents and case are gone)."""
        return {tier: list(values) for tier, values in self._words.items()}

    @property
    def empty(self) -> bool:
        return not any(self._counts.values())

    def match(self, text: str) -> str | None:
        """Highest level that matches this message: 'severe', 'bad', 'mild' or None."""
        if self.empty or not text:
            return None
        folded = normalize(text)
        for tier in reversed(TIERS):
            pattern = self._patterns[tier]
            if pattern is not None and pattern.search(folded):
                return tier
        return None
