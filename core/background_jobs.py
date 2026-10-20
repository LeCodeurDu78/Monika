"""Long tasks that run beside the conversation, so Monika stays available while they work.

The main agent runs on ONE thread (core/serial_executor.py): a 5-minute task there makes her unreachable.
Here, a task is handed to a small pool of daemon worker threads that each run their own sub-agent (see
agents/background.py). The conversation only pays for the call that starts the task; the result comes back
through a notifier (terminal / voice / Discord, wired in agent.py).

Limits worth knowing:
- Cancellation is cooperative: it takes effect before the sub-agent's next tool call. A tool already running
  (a long run_script) finishes first.
- Tasks live in memory: they do not survive a restart of Monika. Finished results are also written to
  `APP_DIR/background/job_<id>.md`.
"""

import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from core.settings import settings

PENDING, RUNNING, DONE, FAILED, CANCELLED = "en attente", "en cours", "terminée", "échouée", "annulée"
_ACTIVE = (PENDING, RUNNING)
_KEEP_FINISHED = 50


class JobCancelled(BaseException):
    """Raised inside a sub-agent to stop it.

    BaseException on purpose: the tool executor catches `Exception` and hands it to the model as text, which
    would let the model carry on; this one must cross the whole ReAct loop.
    """


@dataclass
class Job:
    id: int
    goal: str
    status: str = PENDING
    created: float = field(default_factory=time.time)
    started: Optional[float] = None
    finished: Optional[float] = None
    result: str = ""
    result_path: str = ""
    cancel_event: threading.Event = field(default_factory=threading.Event)
    timed_out: bool = False

    @property
    def active(self) -> bool:
        return self.status in _ACTIVE

    def elapsed(self) -> float:
        if self.started is None:
            return 0.0
        return (self.finished or time.time()) - self.started


class BackgroundJobs:
    def __init__(self) -> None:
        self._jobs: dict[int, Job] = {}
        self._queue: "queue.Queue[Job]" = queue.Queue()
        self._lock = threading.Lock()
        self._next_id = 1
        self._workers: list[threading.Thread] = []
        self._runner: Optional[Callable[[str, Callable[[], bool]], str]] = None
        self._notifier: Optional[Callable[[str], None]] = None

    # --- wiring ---------------------------------------------------------------------------
    def set_runner(self, runner: Callable[[str, Callable[[], bool]], str]) -> None:
        """`runner(goal, should_stop)` runs the sub-agent and returns its final reply."""
        self._runner = runner

    def set_notifier(self, notifier: Optional[Callable[[str], None]]) -> None:
        """`notifier(text)` delivers a message to the user when a job ends (called from a worker thread)."""
        self._notifier = notifier

    # --- public API -----------------------------------------------------------------------
    def start(self, goal: str) -> Job:
        if self._runner is None:
            raise RuntimeError("aucun exécuteur de tâches de fond n'est configuré")
        with self._lock:
            job = Job(id=self._next_id, goal=goal.strip())
            self._next_id += 1
            self._jobs[job.id] = job
            self._prune_locked()
            self._ensure_workers_locked()
        self._queue.put(job)
        return job

    def get(self, job_id: int) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.id)

    def cancel(self, job_id: int) -> Optional[Job]:
        """Ask a job to stop. Returns the job (None if unknown); a finished job is left unchanged."""
        job = self.get(job_id)
        if job is None or not job.active:
            return job
        job.cancel_event.set()
        if job.status == PENDING:  # never started: the worker will skip it
            job.status, job.finished = CANCELLED, time.time()
        return job

    # --- workers --------------------------------------------------------------------------
    def _ensure_workers_locked(self) -> None:
        self._workers = [t for t in self._workers if t.is_alive()]
        while len(self._workers) < max(1, settings.BACKGROUND_MAX_WORKERS):
            # daemon: a task still running must not keep the program from exiting.
            thread = threading.Thread(target=self._work, daemon=True, name=f"monika-bg-{len(self._workers) + 1}")
            thread.start()
            self._workers.append(thread)

    def _prune_locked(self) -> None:
        finished = [j for j in self._jobs.values() if not j.active]
        for job in sorted(finished, key=lambda j: j.id)[: max(0, len(finished) - _KEEP_FINISHED)]:
            del self._jobs[job.id]

    def _work(self) -> None:
        while True:
            job = self._queue.get()
            if job.status != PENDING:  # cancelled while waiting
                continue
            self._run(job)

    def _run(self, job: Job) -> None:
        job.status, job.started = RUNNING, time.time()
        deadline = job.started + settings.BACKGROUND_MAX_MINUTES * 60

        def should_stop() -> bool:
            if job.cancel_event.is_set():
                return True
            if time.time() > deadline:
                job.timed_out = True
                return True
            return False

        try:
            reply = (self._runner(job.goal, should_stop) or "").strip()  # type: ignore[misc]
            if reply.startswith("⚠️"):
                job.status, job.result = FAILED, reply
            else:
                job.status, job.result = DONE, reply or "Tâche terminée sans message de retour."
        except JobCancelled:
            if job.timed_out and not job.cancel_event.is_set():
                job.status = FAILED
                job.result = f"Arrêtée : durée maximale de {settings.BACKGROUND_MAX_MINUTES} minutes dépassée."
            else:
                job.status, job.result = CANCELLED, "Annulée à ta demande."
        except Exception as exc:  # noqa: BLE001 - a crashing job must never kill its worker
            job.status, job.result = FAILED, f"⚠️ {type(exc).__name__}: {exc}"
        finally:
            job.finished = time.time()
        job.result_path = self._save_result(job)
        self._notify(job)

    # --- results --------------------------------------------------------------------------
    def _save_result(self, job: Job) -> str:
        try:
            folder = settings.APP_DIR / "background"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"job_{job.id}.md"
            started = datetime.fromtimestamp(job.started or job.created).strftime("%d/%m/%Y %H:%M")
            path.write_text(
                f"# Tâche de fond #{job.id} ({job.status})\n\n"
                f"**Demande :** {job.goal}\n\n**Début :** {started} ({int(job.elapsed())} s)\n\n---\n\n{job.result}\n",
                encoding="utf-8",
            )
            return str(path)
        except OSError:
            return ""

    def _notify(self, job: Job) -> None:
        if self._notifier is None or job.status == CANCELLED:  # the user just asked for the cancellation
            return
        if job.status == DONE:
            text = f"Ma tâche de fond #{job.id} est terminée.\n{job.result}"
        else:
            text = f"Ma tâche de fond #{job.id} a échoué : {job.result}"
        try:
            self._notifier(text)
        except Exception as exc:  # noqa: BLE001 - a delivery problem must not lose the stored result
            print(f"⚠️ [Fond] Notification impossible ({type(exc).__name__}: {exc}).")


background_jobs = BackgroundJobs()
