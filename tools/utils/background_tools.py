"""Tool that lets the main agent hand a long task to a background sub-agent."""

from core.background_jobs import CANCELLED, DONE, FAILED, RUNNING, Job, background_jobs
from core.settings import settings

_RESULT_PREVIEW_CHARS = 4000


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60} min {seconds % 60:02d} s" if seconds >= 60 else f"{seconds} s"


def _summary_line(job: Job) -> str:
    goal = job.goal if len(job.goal) <= 90 else job.goal[:87] + "..."
    timing = f", {_duration(job.elapsed())}" if job.started else ""
    return f"#{job.id} [{job.status}{timing}] {goal}"


def _details(job: Job) -> str:
    head = _summary_line(job)
    if job.status == RUNNING:
        return f"{head}\nToujours en cours : je préviendrai l'utilisateur à la fin."
    if not job.result:
        return head
    result = job.result
    if len(result) > _RESULT_PREVIEW_CHARS:
        result = result[:_RESULT_PREVIEW_CHARS] + "\n... [tronqué]"
    where = f"\nRésultat complet : {job.result_path}" if job.result_path else ""
    return f"{head}\n{result}{where}"


def background_task(action: str, goal: str = "", job_id: int = 0) -> str:
    """Start, follow or cancel a long task that runs beside the conversation."""
    if action == "start":
        if not goal.strip():
            return "Erreur : 'goal' est requis (la tâche à accomplir, formulée de façon complète et autonome)."
        active = [j for j in background_jobs.list() if j.active]
        if len(active) >= settings.BACKGROUND_MAX_ACTIVE:
            return (
                f"Erreur : {len(active)} tâches de fond sont déjà actives (maximum {settings.BACKGROUND_MAX_ACTIVE}). "
                "Dis-le à l'utilisateur, ou annule-en une avec action='cancel'."
            )
        job = background_jobs.start(goal)
        return (
            f"🧵 Tâche de fond #{job.id} lancée. Elle s'exécute pendant que tu continues à discuter avec l'utilisateur ; "
            "l'utilisateur sera prévenu automatiquement à la fin. Confirme-lui simplement que c'est parti, "
            "sans attendre le résultat."
        )

    if action == "list":
        jobs = background_jobs.list()
        if not jobs:
            return "Aucune tâche de fond."
        return "\n".join(_summary_line(j) for j in jobs[-15:])

    if action in ("status", "cancel"):
        if not job_id:
            return "Erreur : 'job_id' est requis (le numéro de la tâche, visible avec action='list')."
        if action == "cancel":
            job = background_jobs.cancel(job_id)
            if job is None:
                return f"Erreur : la tâche #{job_id} n'existe pas."
            if job.status in (DONE, FAILED):
                return f"La tâche #{job_id} était déjà terminée ({job.status})."
            if job.status == CANCELLED:
                return f"🛑 Tâche #{job_id} annulée."
            return (
                f"🛑 Annulation de la tâche #{job_id} demandée : elle s'arrête avant sa prochaine action "
                "(une commande déjà en cours finit d'abord)."
            )
        job = background_jobs.get(job_id)
        return _details(job) if job else f"Erreur : la tâche #{job_id} n'existe pas."

    return "Erreur : 'action' doit être 'start', 'status', 'list' ou 'cancel'."
