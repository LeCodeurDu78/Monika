"""The dashboard (what Monika knows and does): access rules, data functions and JSON API in one module.

Its UI is a fragment injected into the avatar page (static/dashboard/); this module only serves the data, on the
avatar server (avatar/server.py). To remove the dashboard, delete this file and static/dashboard/.

Three parts:
1. Access rules: loopback only, Origin check, per-launch change token.
2. Data: what the dashboard shows and does, as plain functions.
3. HTTP: the FastAPI router that exposes them.
"""

import hmac
import secrets
import time
from typing import Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core import action_log
from core.background_jobs import background_jobs
from core.settings import settings
from memory.common import MemoryUnavailable, describe_error
from memory.manager import memory_manager

# =============================================================================================
# 1. Access rules
# =============================================================================================
# The page shows personal data and can delete memories, and it lives on a local web server: any web page open
# in the same browser can talk to 127.0.0.1. Three protections, all in `check_request`:
# - the Host header must be a loopback name (stops DNS rebinding: an attacker's domain pointing at 127.0.0.1);
# - the Origin of a browser request must be loopback too;
# - every change needs a random per-launch token, handed to the page itself (data-token on the panel) and sent
#   back in a header, which a foreign page cannot read.
# And the dashboard is only served at all when AVATAR_HOST is a loopback address.
TOKEN = secrets.token_urlsafe(32)
TOKEN_HEADER = "x-dashboard-token"
LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
SECURITY_HEADERS = {"Content-Security-Policy": CSP, "X-Frame-Options": "DENY", "Cache-Control": "no-store"}


def _hostname(netloc: str) -> str:
    """'localhost:4228' -> 'localhost'; '[::1]:4228' -> '[::1]'."""
    netloc = netloc.strip().lower()
    if netloc.startswith("["):
        return netloc.split("]")[0] + "]"
    if netloc.count(":") > 1:  # a bare IPv6 address such as "::1" (AVATAR_HOST), not host:port
        return netloc
    return netloc.split(":")[0]


def check_request(
    *, method: str, host: str, origin: Optional[str], token: Optional[str], bind_host: str, expected_token: str = TOKEN
) -> Optional[tuple[int, str]]:
    """None if the request may go through, else (HTTP status, message)."""
    if _hostname(bind_host) not in LOOPBACK:
        return 403, "Tableau de bord réservé à une adresse locale : AVATAR_HOST doit être 127.0.0.1."
    if _hostname(host) not in LOOPBACK:
        return 403, "Hôte refusé."
    if origin and origin != "null" and _hostname(urlsplit(origin).netloc) not in LOOPBACK:
        return 403, "Origine refusée."
    if origin == "null":
        return 403, "Origine refusée."
    if method.upper() not in ("GET", "HEAD") and not hmac.compare_digest(token or "", expected_token):
        return 403, "Jeton manquant ou invalide : recharge la page."
    return None


# =============================================================================================
# 2. Data (no web framework in here)
# =============================================================================================
MAX_FACT_CHARS = 500
_FACTS_LIMIT = 500


class ServiceError(Exception):
    """A request the dashboard refuses; `status` is the HTTP code to answer with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


# --- live activity -------------------------------------------------------------------------
def _job_dict(job) -> dict:
    return {
        "id": job.id,
        "goal": job.goal,
        "status": job.status,
        "active": job.active,
        "elapsed_s": int(job.elapsed()),
        "result": job.result if not job.active else "",
        "result_path": job.result_path,
    }


def overview() -> dict:
    jobs = background_jobs.list()
    shown = [j for j in jobs if j.active] + [j for j in reversed(jobs) if not j.active][:5]
    return {
        "now": time.time(),
        "main_task": action_log.main_task(),
        "running": action_log.in_flight(),
        "jobs": [_job_dict(j) for j in sorted(shown, key=lambda j: j.id, reverse=True)],
    }


def cancel_job(job_id: int) -> dict:
    job = background_jobs.cancel(job_id)
    if job is None:
        raise ServiceError(404, f"La tâche #{job_id} n'existe pas.")
    action_log.record("tableau de bord", "background_task", {"action": "cancel", "job_id": job_id}, action_log.OK)
    return _job_dict(job)


# --- memory --------------------------------------------------------------------------------
def _mem0():
    mem0 = memory_manager.mem0
    if mem0 is None:
        raise ServiceError(404, "La mémoire rapide (mem0) est désactivée (MEM0_ENABLED=false).")
    return mem0


def _unavailable(exc: MemoryUnavailable) -> ServiceError:
    return ServiceError(503, str(exc))


def list_facts() -> dict:
    """Everything mem0 knows about the user, most recently touched first."""
    try:
        items = _mem0().list_all(limit=_FACTS_LIMIT, wait=0.5)
    except MemoryUnavailable as exc:
        raise _unavailable(exc)
    except ServiceError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ServiceError(500, f"Lecture de la mémoire impossible : {describe_error(exc)}")
    facts = [
        {
            "id": str(item.get("id", "")),
            "text": str(item.get("memory", "")).strip(),
            "created": item.get("created_at") or "",
            "updated": item.get("updated_at") or "",
        }
        for item in items
        if str(item.get("memory", "")).strip()
    ]
    facts.sort(key=lambda f: f["updated"] or f["created"], reverse=True)
    return {"facts": facts, "truncated": len(items) >= _FACTS_LIMIT}


def _find_text(mem0, memory_id: str) -> str:
    try:
        for item in mem0.list_all(limit=_FACTS_LIMIT, wait=0.5):
            if str(item.get("id")) == memory_id:
                return str(item.get("memory", ""))
    except Exception:  # noqa: BLE001 - only used to make the log readable
        pass
    return ""


def update_fact(memory_id: str, text: str) -> dict:
    text = (text or "").strip()
    if not memory_id:
        raise ServiceError(400, "Identifiant du souvenir manquant.")
    if not text:
        raise ServiceError(400, "Le souvenir ne peut pas être vide : utilise « Oublier » pour le supprimer.")
    if len(text) > MAX_FACT_CHARS:
        raise ServiceError(400, f"Souvenir trop long ({len(text)} caractères, maximum {MAX_FACT_CHARS}).")
    mem0 = _mem0()
    before = _find_text(mem0, memory_id)
    try:
        mem0.update(memory_id, text, wait=0.5)
    except MemoryUnavailable as exc:
        raise _unavailable(exc)
    except Exception as exc:  # noqa: BLE001
        raise ServiceError(500, f"Correction impossible : {describe_error(exc)}")
    action_log.record(
        "tableau de bord", "corriger_souvenir", {"id": memory_id, "avant": before, "après": text}, action_log.OK
    )
    return {"id": memory_id, "text": text}


def forget_fact(memory_id: str) -> dict:
    if not memory_id:
        raise ServiceError(400, "Identifiant du souvenir manquant.")
    mem0 = _mem0()
    before = _find_text(mem0, memory_id)
    try:
        mem0.delete(memory_id, wait=0.5)
    except MemoryUnavailable as exc:
        raise _unavailable(exc)
    except Exception as exc:  # noqa: BLE001
        raise ServiceError(500, f"Suppression impossible : {describe_error(exc)}")
    action_log.record("tableau de bord", "oublier_souvenir", {"id": memory_id, "texte": before}, action_log.OK)
    return {"id": memory_id}


# --- journal -------------------------------------------------------------------------------
def actions(
    limit: int = 50,
    before_id: Optional[int] = None,
    status: Optional[str] = None,
    tool: Optional[str] = None,
    source: Optional[str] = None,
) -> dict:
    if status and status not in ("failed", "ok", "error", "blocked", "cancelled"):
        raise ServiceError(400, "Filtre de statut inconnu.")
    rows = action_log.recent(limit, before_id, status, tool, source)
    return {"actions": rows, "has_more": len(rows) >= max(1, min(limit, 200))}


def failures(days: int = 7) -> dict:
    return {"days": days, "tools": action_log.failure_summary(days)}


# =============================================================================================
# 3. HTTP
# =============================================================================================
def guard(request: Request) -> None:
    refusal = check_request(
        method=request.method,
        host=request.headers.get("host", ""),
        origin=request.headers.get("origin"),
        token=request.headers.get(TOKEN_HEADER),
        bind_host=settings.AVATAR_HOST,
    )
    if refusal:
        raise HTTPException(status_code=refusal[0], detail=refusal[1])


router = APIRouter(dependencies=[Depends(guard)])


class FactText(BaseModel):
    text: str


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ServiceError as exc:
        return JSONResponse({"detail": exc.message}, status_code=exc.status, headers=SECURITY_HEADERS)


def _json(payload) -> JSONResponse:
    return payload if isinstance(payload, JSONResponse) else JSONResponse(payload, headers=SECURITY_HEADERS)


# --- data -----------------------------------------------------------------------------------
@router.get("/api/dashboard/overview")
def route_overview():
    return _json(overview())


@router.post("/api/dashboard/jobs/{job_id}/cancel")
def route_cancel_job(job_id: int):
    return _json(_call(cancel_job, job_id))


@router.get("/api/dashboard/facts")
def route_facts():
    return _json(_call(list_facts))


@router.put("/api/dashboard/facts/{memory_id}")
def route_update_fact(memory_id: str, body: FactText):
    return _json(_call(update_fact, memory_id, body.text))


@router.delete("/api/dashboard/facts/{memory_id}")
def route_forget_fact(memory_id: str):
    return _json(_call(forget_fact, memory_id))


@router.get("/api/dashboard/actions")
def route_actions(
    limit: int = 50,
    before_id: Optional[int] = None,
    status: Optional[str] = None,
    tool: Optional[str] = None,
    source: Optional[str] = None,
):
    return _json(_call(actions, limit, before_id, status or None, tool or None, source or None))


@router.get("/api/dashboard/failures")
def route_failures(days: int = 7):
    return _json(failures(days))
