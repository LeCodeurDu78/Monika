"""The conversation in the page: a short transcript, streamed as it is written.

The page is a second client of the same agent: it sends a line, reads Monika's text as it is
produced (exactly like the terminal), and can stop everything. The transcript lives in memory only:
the durable log is the sessions database, this buffer just survives a page reload.
"""

from __future__ import annotations

from typing import Any

from web.hub import Hub

LIMIT = 40  # messages kept for a page that opens or reloads


class ChatView:
    """Accumulates the turn and fans it out to every open page."""

    def __init__(self, hub: Hub, limit: int = LIMIT) -> None:
        self._hub = hub
        self._limit = max(2, limit)
        self._items: list[dict[str, str]] = []
        self.busy = False

    # --- what the terminal (TextSession) calls -------------------------------------
    def begin(self, user_text: str, *, source: str = "user") -> None:
        self.busy = True
        self._add({"role": source, "text": user_text})
        self._hub.publish("chat", kind="user", role=source, text=user_text)

    def delta(self, text: str) -> None:
        if not text:
            return
        if self._items and self._items[-1]["role"] == "monika":
            self._items[-1]["text"] += text
        else:
            self._add({"role": "monika", "text": text})
        self._hub.publish("chat", kind="delta", text=text)

    def end(self, reply: str = "", *, error: str = "") -> None:
        """The turn is over: the exact text replaces what the deltas built (they may have dropped)."""
        if self._items and self._items[-1]["role"] == "monika":
            self._items[-1]["text"] = reply or self._items[-1]["text"]
        elif reply:
            self._add({"role": "monika", "text": reply})
        self.busy = False
        if error:
            self._hub.publish("chat", kind="error", text=error)
        self._hub.publish("chat", kind="end", text=reply)

    def notify(self, text: str) -> None:
        """Something to show in the middle of the conversation (a stop report, a warning)."""
        self._hub.publish("chat", kind="note", text=text)

    # --- what the page shows -------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        return {"busy": self.busy, "items": [dict(item) for item in self._items]}

    def _add(self, item: dict[str, str]) -> None:
        self._items.append(item)
        if len(self._items) > self._limit:
            self._items = self._items[-self._limit :]
