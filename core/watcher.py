"""Factory for Monika's background threads."""

import threading
from typing import Callable


def start_watcher(interval_seconds: float, tick: Callable[[], None]) -> threading.Event:
    """Start a daemon thread that calls `tick` every `interval_seconds`."""
    stop_event = threading.Event()

    def _loop() -> None:
        while not stop_event.wait(interval_seconds):
            tick()

    threading.Thread(target=_loop, daemon=True).start()
    return stop_event
