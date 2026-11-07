"""Waiting phrases said while a slow tool is running."""

from __future__ import annotations

import random

from monika.i18n import tr_list


class FillerState:
    """Pick phrases for one turn: capped, random, never repeating the same one."""

    def __init__(self, max_per_turn: int, rng: random.Random | None = None) -> None:
        self._max = max_per_turn
        self._rng = rng or random.Random()
        self._used: set[str] = set()
        self._spoken = 0

    def next_phrase(self, variants: list[str] | None = None) -> str | None:
        """Return a phrase, or None once the per-turn cap is reached."""
        if self._spoken >= self._max:
            return None
        pool = variants or tr_list("filler.phrases")
        if not pool:
            return None
        fresh = [p for p in pool if p not in self._used] or pool
        phrase = self._rng.choice(fresh)
        self._used.add(phrase)
        self._spoken += 1
        return phrase
