"""Local web server: serves the avatar page and streams live events.

It listens on 127.0.0.1 by default. When `WEB_HOST` puts it on the network, `WEB_TOKEN` must be set:
the token is accepted as an `X-Monika-Token` header, a `?token=` query or a cookie, and opening `/`
with `?token=` once stores it in the cookie so the page itself needs nothing to copy. Writes and the
WebSocket also require a same-origin Origin, every page carries a nonced CSP, and a DNS name is only
trusted when the token authenticates the client (DNS-rebinding protection).
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import re
import secrets
import socket
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles

from character import emotion_spec
from character.journal import MAX_IMPRESSION_CHARS, MAX_NOTE_CHARS
from web.hub import Hub

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_HOST = "127.0.0.1"
COOKIE = "monika_token"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365
Snapshot = Callable[[], Awaitable[dict[str, Any]]]
Actions = dict[str, Callable[..., Awaitable[None]]]
MAX_TEXT_CHARS = 4000  # une note du carnet peut être longue (MEMORY_NOTES_MAX_CHARS)


class WebError(Exception):
    """The web server could not start; the message is in French and ready to display."""


class ActionError(Exception):
    """An edit from the page was refused; the message is in French and shown to the user."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


def _host_and_port(netloc: str, scheme: str) -> tuple[str, int]:
    """The hostname and port of a Host/Origin value; a malformed port raises ValueError."""
    value = urlsplit(f"{scheme}://{netloc}")
    port = value.port or (443 if scheme == "https" else 80)
    return (value.hostname or "").lower(), port


def _is_ip(hostname: str) -> bool:
    """A bare address (LAN or localhost) cannot be an attacker-controlled DNS name."""
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return True


def _origin_ok(origin: str | None, host: str | None, token: str = "") -> bool:
    """Same-origin check for writes and the WebSocket; a missing Origin is a non-browser client.

    A DNS name is only trusted when a token authenticates the client. Otherwise a DNS-rebinding
    page (served under its own name, resolving to 127.0.0.1) would look same-origin and could read
    the API. Loopback and bare IP addresses stay open: that is the documented local/LAN use.
    """
    if not origin:
        return True  # curl, CLI, MCP: no browser to forge a request
    if not host:
        return False
    parsed = urlsplit(origin)
    if parsed.scheme not in ("http", "https"):
        return False
    try:
        left_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        right, right_port = _host_and_port(host, parsed.scheme)
    except ValueError:
        return False  # a malformed port is not a same-origin request
    left = (parsed.hostname or "").lower()
    if not left or left != right or left_port != right_port:
        return False
    return left in _LOOPBACK or _is_ip(left) or bool(token)


def _csp(nonce: str = "") -> str:
    """Content-Security-Policy: same-origin assets, one nonced inline script (the import map)."""
    scripts = "'self'" + (f" 'nonce-{nonce}'" if nonce else "")
    return (
        "default-src 'self'; "
        f"script-src {scripts}; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "media-src 'self' blob: data:; "
        "connect-src 'self' blob: ws: wss:; "
        "worker-src 'self' blob:; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    )


def create_app(
    hub: Hub,
    snapshot: Snapshot,
    static_dir: Path = STATIC_DIR,
    actions: Actions | None = None,
    settings_api: Any | None = None,
    chat: Any = None,
    voice_factory: Any = None,
    token: str = "",
) -> Any:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    actions = actions or {}
    if settings_api is not None:
        actions = {**actions, **settings_api.actions()}  # Same act(), same ActionError

    def allowed(given: str | None) -> bool:
        """No token configured (127.0.0.1 only) means open; otherwise the token must match exactly."""
        return not token or secrets.compare_digest(given or "", token)

    # A local app: its page, styles and scripts change with the code. Their URL carries the file's
    # mtime, so an updated file is never served from an old cache (see the middleware below).
    asset_ref = re.compile(r'\"/static/([^"?]+\.(?:js|css))\"')

    def page(name: str) -> HTMLResponse:
        html = (static_dir / name).read_text(encoding="utf-8")

        def versioned(match: re.Match[str]) -> str:
            rel = match.group(1)
            path = static_dir / rel
            stamp = int(path.stat().st_mtime) if path.exists() else 0
            return f'\"/static/{rel}?v={stamp}\"' if stamp else f'\"/static/{rel}\"'

        nonce = secrets.token_urlsafe(16)
        html = asset_ref.sub(versioned, html).replace(
            "<script", f'<script nonce="{nonce}"'
        )
        return HTMLResponse(
            html,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": _csp(nonce),
            },
        )

    @app.middleware("http")
    async def require_token(request: Request, call_next: Any) -> Any:
        """Every route and every file needs the token when one is configured."""
        given = (
            request.headers.get("x-monika-token")
            or request.query_params.get("token")
            or request.cookies.get(COOKIE)
        )
        if not allowed(given):
            return PlainTextResponse(
                "Jeton manquant ou incorrect : ouvre l'adresse avec ?token=…", status_code=401
            )
        if (
            token
            and request.method == "GET"
            and request.url.path == "/"
            and request.query_params.get("token")
        ):  # keep the token out of the address bar and the history
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(
                COOKIE,
                token,
                httponly=True,
                samesite="strict",
                # Behind `tailscale serve` the proxy announces HTTPS (X-Forwarded-Proto,
                # trusted from 127.0.0.1): the token then never travels over plain HTTP again.
                secure=request.url.scheme == "https",
                max_age=COOKIE_MAX_AGE,
            )
            return response
        return await call_next(request)

    @app.middleware("http")
    async def no_store(request: Request, call_next: Any) -> Any:
        """Code and pages are never cached; the big model and avatar files keep their cache."""
        response = await call_next(request)
        kind = response.headers.get("content-type", "")
        if kind.startswith(("text/html", "text/css")) or "javascript" in kind:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.middleware("http")
    async def security(request: Request, call_next: Any) -> Any:
        """Refuse a browser write from another origin, then add the security headers."""
        if request.method not in ("GET", "HEAD", "OPTIONS") and not _origin_ok(
            request.headers.get("origin"), request.headers.get("host"), token
        ):
            return JSONResponse({"error": "Origine refusée."}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Content-Security-Policy", _csp())
        return response

    @app.get("/")
    async def index() -> HTMLResponse:
        return page("index.html")

    @app.get("/api/status")
    async def status() -> JSONResponse:
        return JSONResponse(await snapshot(), headers={"Cache-Control": "no-store"})

    @app.get("/api/emotions")
    async def emotions() -> JSONResponse:
        """The emotion table (labels, expression weights, fallback clips): static, no secrets."""
        return JSONResponse(
            emotion_spec.payload(), headers={"Cache-Control": "no-store"}
        )

    async def act(name: str, *args: Any) -> JSONResponse:
        action = actions.get(name)
        if action is None:
            return JSONResponse(
                {"error": "Cette action n'est pas disponible."}, status_code=404
            )
        try:
            result = await action(*args)
        except ActionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status)
        except Exception as exc:
            return JSONResponse(
                {"error": f"L'action a échoué : {type(exc).__name__}"}, status_code=500
            )
        # Actions may describe what changed (e.g. the keys that need a restart); None means just "ok"
        return JSONResponse(
            {"ok": True, **(result if isinstance(result, dict) else {})}
        )

    @app.put("/api/memory/{memory_id}")
    async def memory_update(memory_id: str, request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except ValueError:
            body = None
        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
            return JSONResponse(
                {
                    "error": f"Le souvenir doit faire entre 1 et {MAX_TEXT_CHARS} caractères."
                },
                status_code=400,
            )
        return await act("memory_update", memory_id, text.strip())

    @app.delete("/api/memory/{memory_id}")
    async def memory_delete(memory_id: str) -> JSONResponse:
        return await act("memory_delete", memory_id)

    async def read_text(request: Request) -> str | None:
        try:
            body = await request.json()
        except ValueError:
            return None
        text = body.get("text") if isinstance(body, dict) else None
        return text if isinstance(text, str) else None

    @app.put("/api/journal/{note_id}")
    async def journal_update(note_id: str, request: Request) -> JSONResponse:
        text = await read_text(request)
        if text is None or not text.strip() or len(text) > MAX_NOTE_CHARS:
            return JSONResponse(
                {
                    "error": f"La note doit faire entre 1 et {MAX_NOTE_CHARS} caractères."
                },
                status_code=400,
            )
        return await act("journal_update", note_id, text.strip())

    @app.delete("/api/journal/{note_id}")
    async def journal_delete(note_id: str) -> JSONResponse:
        return await act("journal_delete", note_id)

    @app.delete("/api/journal")
    async def journal_clear() -> JSONResponse:
        return await act("journal_clear")

    @app.put("/api/impression")
    async def impression_update(request: Request) -> JSONResponse:
        text = await read_text(
            request
        )  # An empty text is allowed: it clears the impression
        if text is None or len(text) > MAX_IMPRESSION_CHARS:
            return JSONResponse(
                {
                    "error": f"L'impression doit faire au plus {MAX_IMPRESSION_CHARS} caractères."
                },
                status_code=400,
            )
        return await act("impression_update", text.strip())

    @app.post("/api/relation/reset")
    async def relation_reset() -> JSONResponse:
        return await act("relation_reset")

    @app.delete("/api/session/{session_id}")
    async def session_forget(session_id: str) -> JSONResponse:
        return await act("session_forget", session_id)

    @app.post("/api/session/{session_id}/resume")
    async def session_resume(session_id: str) -> JSONResponse:
        return await act("session_resume", session_id)

    @app.get("/api/chat")
    async def chat_read() -> JSONResponse:
        """The conversation so far, for a page that opens or reloads (then the WebSocket keeps it live)."""
        if chat is None:
            return JSONResponse({"enabled": False, "busy": False, "items": []})
        return JSONResponse(
            {"enabled": True, **chat.snapshot()}, headers={"Cache-Control": "no-store"}
        )

    @app.post("/api/chat")
    async def chat_send(request: Request) -> JSONResponse:
        text = await read_text(request)
        if text is None:
            return JSONResponse(
                {"error": "Message attendu (champ « text »)."}, status_code=400
            )
        return await act("chat_send", text)

    @app.post("/api/chat/stop")
    async def chat_stop() -> JSONResponse:
        return await act("chat_stop")

    if settings_api is not None:

        @app.get("/settings")
        async def settings_page() -> HTMLResponse:
            return page("settings.html")

        @app.get("/api/settings")
        async def settings_read() -> JSONResponse:
            return JSONResponse(
                settings_api.read(), headers={"Cache-Control": "no-store"}
            )

        @app.get("/api/health")
        async def health_grid() -> JSONResponse:
            return JSONResponse(
                await settings_api.health(), headers={"Cache-Control": "no-store"}
            )

        @app.post("/api/settings")
        async def settings_write(request: Request) -> JSONResponse:
            try:
                body = await request.json()
            except ValueError:
                body = None
            if not isinstance(body, dict):
                return JSONResponse(
                    {"error": "Réglages attendus sous forme d'objet JSON."},
                    status_code=400,
                )
            return await act("settings_update", body)

        @app.post("/api/settings/language")
        async def settings_language(request: Request) -> JSONResponse:
            try:
                body = await request.json()
            except ValueError:
                body = None
            if not isinstance(body, dict):
                return JSONResponse(
                    {"error": "Langue attendue sous forme d'objet JSON."}, status_code=400
                )
            return await act("settings_language", body)

        @app.post("/api/settings/provider")
        async def settings_provider(request: Request) -> JSONResponse:
            try:
                body = await request.json()
            except ValueError:
                body = None
            if not isinstance(body, dict):
                return JSONResponse(
                    {"error": "Fournisseur attendu sous forme d'objet JSON."},
                    status_code=400,
                )
            return await act("settings_provider", body)

    @app.websocket("/ws")
    async def live(ws: WebSocket) -> None:
        if not allowed(ws.query_params.get("token") or ws.cookies.get(COOKIE)):
            await ws.close(code=1008)  # the page must not open a live channel without it
            return
        if not _origin_ok(ws.headers.get("origin"), ws.headers.get("host"), token):
            await ws.close(code=1008)  # a page from another origin must not open the stream
            return
        await ws.accept()
        queue = hub.subscribe()
        voice = (
            voice_factory(ws.send_json, ws.send_bytes)
            if voice_factory is not None
            else None
        )
        try:
            await ws.send_json(hub.hello())

            async def pump() -> None:
                while True:
                    await ws.send_json(await queue.get())

            async def listen() -> None:
                while True:
                    message = await ws.receive()
                    if message.get("type") == "websocket.disconnect":
                        return
                    data = message.get("bytes")
                    if (
                        data
                    ):  # microphone audio from the page (16 kHz PCM, see web/voice.py)
                        if voice is not None:
                            voice.feed(data)
                        continue
                    text = message.get("text")
                    if not text or voice is None:
                        continue
                    try:
                        payload = json.loads(text)
                    except ValueError:
                        continue
                    if isinstance(payload, dict) and payload.get("type") == "voice":
                        await voice.toggle(bool(payload.get("on")))

            tasks = [asyncio.create_task(pump()), asyncio.create_task(listen())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        except WebSocketDisconnect:
            pass
        finally:
            if voice is not None:
                await voice.close()  # the page is gone: the microphone goes with it
            hub.unsubscribe(queue)

    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    return app


class _Server(uvicorn.Server):
    """uvicorn without its own signal handling: Ctrl+C stays under the control of the session."""

    def install_signal_handlers(self) -> None:
        pass

    @contextlib.contextmanager
    def capture_signals(self):  # type: ignore[override]
        yield


class WebServer:
    """Runs the app inside the current event loop, next to the chat."""

    def __init__(
        self,
        hub: Hub,
        snapshot: Snapshot,
        port: int,
        static_dir: Path = STATIC_DIR,
        actions: Actions | None = None,
        settings_api: Any | None = None,
        chat: Any = None,
        voice_factory: Any = None,
        host: str = DEFAULT_HOST,
        token: str = "",
    ) -> None:
        self.host = host
        self._token = token
        self._actions = actions
        self._settings_api = settings_api
        self._chat = chat
        self._voice_factory = voice_factory
        self._hub = hub
        self._snapshot = snapshot
        self.port = port
        self._static = static_dir
        self._server: _Server | None = None
        self._task: asyncio.Task | None = None

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}"

    async def start(self) -> str:
        """Start listening; returns the page address, or raises WebError."""
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind((self.host, self.port))
        except OSError as exc:
            raise WebError(
                f"Le port {self.port} est déjà utilisé ({exc.strerror}) : une autre instance de Monika "
                "tourne peut-être ; change WEB_PORT ou ferme-la."
            ) from exc
        finally:
            probe.close()
        app = create_app(
            self._hub,
            self._snapshot,
            self._static,
            self._actions,
            self._settings_api,
            self._chat,
            self._voice_factory,
            token=self._token,
        )
        config = uvicorn.Config(
            app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
            lifespan="off",
            timeout_graceful_shutdown=2,
        )
        self._server = _Server(config)
        self._task = asyncio.get_running_loop().create_task(self._run(self._server))
        for _ in range(100):
            if self._server.started:
                return self.url
            if self._task.done():
                break
            await asyncio.sleep(0.05)
        error = (
            self._task.exception()
            if self._task.done() and not self._task.cancelled()
            else None
        )
        await self.stop()
        raise WebError(
            f"Le serveur web n'a pas démarré{f' : {error}' if error else ''}."
        )

    @staticmethod
    async def _run(server: _Server) -> None:
        try:
            await server.serve()
        except SystemExit:  # uvicorn exits instead of raising when it cannot bind
            raise RuntimeError("le port n'a pas pu être ouvert") from None

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, 6)
            except (TimeoutError, asyncio.CancelledError, RuntimeError):
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
        self._server = self._task = None
