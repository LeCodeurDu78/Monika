"""Run calls one after another on ONE dedicated thread.

Monika is called from several threads (keyboard, scheduled tasks, Discord). Playwright (sync API) binds its
objects to the thread that created them: a browser opened from one thread and used from another crashes with
"Cannot switch to a different thread". By sending every agent turn through this same thread, tools always see
the same thread, and two turns never overlap (no race on the history, the memory or the browser profile).
"""

import concurrent.futures
import queue
import threading
from typing import Any, Callable


class SerialExecutor:
    def __init__(self, name: str = "monika-agent") -> None:
        self._name = name
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()

    def _ensure_thread(self) -> None:
        with self._start_lock:
            if self._thread is None or not self._thread.is_alive():
                # daemon: does not block program exit (Ctrl+C during a turn).
                self._thread = threading.Thread(target=self._work, daemon=True, name=self._name)
                self._thread.start()

    def _work(self) -> None:
        while True:
            future, fn, args, kwargs = self._queue.get()
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(fn(*args, **kwargs))
            except BaseException as exc:  # noqa: BLE001 - handed back to the caller as is
                future.set_exception(exc)

    def run(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run fn(*args, **kwargs) on the dedicated thread and return its result (or re-raise its exception)."""
        if self._thread is not None and threading.current_thread() is self._thread:
            return fn(*args, **kwargs)  # nested call: do not wait on ourselves
        self._ensure_thread()
        future: concurrent.futures.Future = concurrent.futures.Future()
        self._queue.put((future, fn, args, kwargs))
        return future.result()
