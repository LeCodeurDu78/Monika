"""Live events for the web page: one hub, many listeners (WebSocket connections)."""

from __future__ import annotations

import asyncio
from typing import Any

QUEUE_SIZE = 256
STATES = ("idle", "listening", "thinking")


class Hub:
    """Fan-out of small JSON messages; a slow listener loses its oldest messages, never blocks """

    def __init__(self) -> None:
        self._queues: set[asyncio.Queue[dict[str, Any]]] = set()
        self.state = "idle"
        self.level = 0.0

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(QUEUE_SIZE)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._queues.discard(queue)

    @property
    def listeners(self) -> int:
        return len(self._queues)

    def hello(self) -> dict[str, Any]:
        """What a page needs on connection to show the current situation."""
        return {"type": "hello", "state": self.state, "level": self.level}

    def publish(self, kind: str, /, **data: Any) -> None:
        message = {"type": kind, **data}
        for queue in self._queues:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(message)

    def set_state(self, state: str) -> None:
        """idle, listening (waiting for the user to speak) or thinking (a turn is running)."""
        if state not in STATES:
            raise ValueError(f"état inconnu : {state}")
        if state != self.state:
            self.state = state
            self.publish("state", state=state)

    def on_level(self, level: float) -> None:
        """Loudness (0..1) of the sound being heard right now; plugs into PacatSink(on_level=...)."""
        self.level = round(min(max(float(level), 0.0), 1.0), 3)
        self.publish("level", value=self.level)

    def activity(self, kind: str, **data: Any) -> None:
        """Something happened that the dashboard may want to refresh (tool call, end of turn...)."""
        self.publish("activity", kind=kind, **data)
