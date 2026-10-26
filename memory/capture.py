"""Background capture: after each exchange, feed memory backends without slowing the chat."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger("memory")

Sink = Callable[[str, str], Awaitable[Any]]


class MemoryCapture:
    """Sequential background worker: one exchange at a time, errors never reach the chat."""

    def __init__(self, sinks: list[Sink], min_chars: int = 20) -> None:
        self._sinks = sinks
        self._min_chars = min_chars
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=50)
        self._worker: asyncio.Task[None] | None = None
        self._seen_errors: set[str] = set()
        self.captured = 0
        self.failures = 0
        self.last_error = ""

    @property
    def pending(self) -> int:
        return self._queue.qsize() + (1 if self._busy else 0)

    _busy = False

    def submit(self, user_text: str, assistant_text: str) -> bool:
        """Queue an exchange; short messages and a full queue are skipped. Returns True if queued."""
        if len(user_text.strip()) < self._min_chars or not self._sinks:
            return False
        try:
            self._queue.put_nowait((user_text, assistant_text))
        except asyncio.QueueFull:
            log.warning("File de capture pleine : un échange n'est pas mémorisé.")
            return False
        if self._worker is None or self._worker.done():
            self._worker = asyncio.get_running_loop().create_task(self._run())
        return True

    async def _run(self) -> None:
        while True:
            user_text, assistant_text = await self._queue.get()
            self._busy = True
            try:
                for sink in self._sinks:
                    try:
                        await sink(user_text, assistant_text)
                        self.captured += 1
                    except Exception as exc:  # A failing backend must not stop the others
                        self.failures += 1
                        self.last_error = f"{type(exc).__name__}: {exc}"[:300]
                        if self.last_error not in self._seen_errors:
                            self._seen_errors.add(self.last_error)
                            log.warning("Capture mémoire impossible : %s", self.last_error)
            finally:
                self._busy = False
                self._queue.task_done()

    async def drain(self, timeout: float = 60.0) -> int:
        """Wait for queued captures, then stop the worker; return how many were left unprocessed."""
        left = 0
        if self._worker is not None and not self._worker.done():
            try:
                await asyncio.wait_for(self._queue.join(), timeout)
            except TimeoutError:
                left = self.pending
                log.warning("Capture interrompue : %d échange(s) non mémorisé(s).", left)
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
        return left
