"""Web server of Monika's 3D companion."""

from __future__ import annotations

import base64
import hashlib
import re
import threading
import webbrowser
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from web import state as avatar_state
from core.settings import settings

STATIC_DIR = Path(__file__).parent / "static"
AVATAR_STATIC = STATIC_DIR / "avatar"  # script.js, style.css, model.glb
DASHBOARD_STATIC = STATIC_DIR / "dashboard"  # dashboard.html (fragment), dashboard.css, dashboard.js

app = FastAPI(title="Monika Avatar")

# Dashboard (what Monika knows and does): its JSON API is added BEFORE the static mount below, which answers every
# path, and its UI (static/dashboard/dashboard.html) is injected into the avatar page as a side panel. The avatar
# page works on its own: without the dashboard, the page is served without that fragment.
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
DASHBOARD_FRAGMENT = DASHBOARD_STATIC / "dashboard.html"
# The dashboard is two things: dashboard.py (data + API) and static/dashboard/ (UI). Delete them to remove it.
DASHBOARD_ACTIVE = (
    settings.DASHBOARD_ENABLED
    and settings.AVATAR_HOST in _LOOPBACK_HOSTS
    and DASHBOARD_FRAGMENT.exists()
    and (Path(__file__).parent / "dashboard.py").exists()
)
if DASHBOARD_ACTIVE:
    from web.dashboard import TOKEN as DASHBOARD_TOKEN, guard as dashboard_guard, router as dashboard_router

    app.include_router(dashboard_router)
elif settings.DASHBOARD_ENABLED and settings.AVATAR_HOST not in _LOOPBACK_HOSTS:
    print(
        f"⚠️ Tableau de bord désactivé : AVATAR_HOST={settings.AVATAR_HOST!r} n'est pas une adresse locale "
        "(il affiche des données personnelles et permet d'effacer des souvenirs). Utilise 127.0.0.1."
    )

_IMPORTMAP = re.compile(r"<script type=\"importmap\">(.*?)</script>", re.S)


def _page_response(token: str) -> HTMLResponse:
    """index.html with the dashboard fragment (and its token) injected, and a CSP that fits the page.

    Unlike the old standalone dashboard, this page runs the 3D scene: it needs Three.js from the jsDelivr CDN and
    its inline import map (allowed by hash, so no 'unsafe-inline'). Everything else stays on this origin.
    """
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    fragment = ""
    if token:  # no token = no dashboard: the avatar page is served alone
        fragment = DASHBOARD_FRAGMENT.read_text(encoding="utf-8").replace("__DASHBOARD_TOKEN__", token)
    html = html.replace("<!--DASHBOARD-->", fragment)
    scripts = ["'self'", "https://cdn.jsdelivr.net"]
    importmap = _IMPORTMAP.search(html)
    if importmap:
        digest = base64.b64encode(hashlib.sha256(importmap.group(1).encode("utf-8")).digest()).decode()
        scripts.append(f"'sha256-{digest}'")
    csp = (
        f"default-src 'none'; script-src {' '.join(scripts)}; style-src 'self'; connect-src 'self' blob: data:; "
        "img-src 'self' data: blob:; worker-src blob:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    return HTMLResponse(
        html, headers={"Content-Security-Policy": csp, "X-Frame-Options": "DENY", "Cache-Control": "no-store"}
    )


if DASHBOARD_ACTIVE:

    # The page carries the token, so it gets the same Host/Origin checks as the API (anti DNS rebinding).
    @app.get("/", dependencies=[Depends(dashboard_guard)])
    def index() -> HTMLResponse:
        return _page_response(DASHBOARD_TOKEN)

    @app.get("/dashboard")
    def legacy_dashboard_url() -> RedirectResponse:
        return RedirectResponse("/#dashboard")  # the dashboard used to have its own page

else:

    @app.get("/")
    def index() -> HTMLResponse:
        return _page_response("")  # no token: the page is served without the dashboard


@app.get("/api/state")
def get_state() -> dict:
    return avatar_state.snapshot()


# /avatar/* and /dashboard/* assets (the 3D model is /avatar/model.glb).
app.mount("/", StaticFiles(directory=str(STATIC_DIR)), name="static")

_server_thread: threading.Thread | None = None


def _run_server(host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning")


def start_avatar_server() -> None:
    """Start the server (once) and open the browser if configured."""
    global _server_thread

    if not settings.AVATAR_ENABLED:
        return

    if not (AVATAR_STATIC / "model.glb").exists():
        print(
            f"⚠️ Avatar 3D désactivé : fichier introuvable ({AVATAR_STATIC / 'model.glb'}). "
            "Placez-y votre .glb (voir static/avatar/model.glb)."
        )
        return

    if _server_thread is not None and _server_thread.is_alive():
        return  # already started

    host, port = settings.AVATAR_HOST, settings.AVATAR_PORT
    _server_thread = threading.Thread(target=_run_server, args=(host, port), daemon=True)
    _server_thread.start()

    print(f"🧑‍🎨 Avatar 3D disponible sur http://{host}:{port}")
    if DASHBOARD_ACTIVE:
        print("🔎 Tableau de bord intégré : bouton « Tableau de bord » en haut à droite de la page")

    if settings.AVATAR_OPEN_BROWSER:
        webbrowser.open(f"http://{host}:{port}")
