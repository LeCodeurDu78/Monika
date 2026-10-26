"""Wrap tool output so the model treats it as data, never as instructions."""

from __future__ import annotations

import re

TAG = "donnees_outil"
_TAG_IN_TEXT = re.compile(rf"<(/?)\s*{TAG}", re.IGNORECASE)


def wrap_tool_output(name: str, text: str, is_error: bool, source: str = "") -> str:
    """Return the tool output inside a data envelope that the content cannot close."""
    # A forged opening or closing tag inside the content is defused so it cannot break out
    safe = _TAG_IN_TEXT.sub(lambda m: f"‹{m.group(1)}{TAG}", text.strip() or "(vide)")
    attrs = f'nom="{_attr(name)}" statut="{"erreur" if is_error else "ok"}"'
    if source:
        attrs += f' source="{_attr(source)}"'
    return f"<{TAG} {attrs}>\n{safe}\n</{TAG}>"


def _attr(value: str) -> str:
    return re.sub(r'[\n"<>]', "_", value)[:100]
