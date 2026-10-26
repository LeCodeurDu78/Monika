"""Local web server: serves the avatar page and streams live events. Reachable from this computer only."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import secrets
import socket
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from web.hub import Hub

STATIC_DIR = Path(__file__).resolve().parent / "static"
HOST = "127.0.0.1"
Snapshot = Callable[[], Awaitable[dict[str, Any]]]
Actions = dict[str, Callable[..., Awaitable[None]]]
TOKEN_PLACEHOLDER = "__MONIKA_TOKEN__"
MAX_TEXT_CHARS = 1000


class WebError(Exception):
    """The web server could not start; the message is in French and ready to display."""


class ActionError(Exception):
    """An edit from the page was refused; the message is in French and shown to the user."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def allowed_hosts(port: int) -> frozenset[str]:
    return frozenset({f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"})


class LocalOnly:
    """ASGI guard: refuses requests whose Host or Origin is not this computer (DNS rebinding, hostile pages)."""

    def __init__(self, app: Any, port: int) -> None:
        self._app = app
        self._hosts = allowed_hosts(port)
        self._csp = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; connect-src 'self' blob: data: "
            + " ".join(f"ws://{h}" for h in sorted(self._hosts) if not h.startswith("["))  # IPv6 literals are invalid in a CSP source list
            + "; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        origin = headers.get("origin")
        origin_ok = origin is None or origin.split("://", 1)[-1] in self._hosts
        if scope["type"] == "http" and scope["method"] not in ("GET", "HEAD", "OPTIONS") and origin is None:
            origin_ok = False  # A change must come from the page itself, which browsers mark with its Origin
        if headers.get("host") not in self._hosts or not origin_ok:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
                await send({"type": "http.response.body", "body": "Accès refusé : page locale.".encode()})
            return

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                extra = [(b"content-security-policy", self._csp.encode()), (b"x-frame-options", b"DENY"),
                         (b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer")]
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        await self._app(scope, receive, send_with_headers)


def create_app(hub: Hub, snapshot: Snapshot, port: int, static_dir: Path = STATIC_DIR,
               actions: Actions | None = None) -> Any:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    token = secrets.token_urlsafe(24)  # Fresh at each launch, only given to the page we serve
    actions = actions or {}

    @app.get("/")
    async def index() -> HTMLResponse:
        html = (static_dir / "index.html").read_text(encoding="utf-8").replace(TOKEN_PLACEHOLDER, token)
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.get("/api/status")
    async def status() -> JSONResponse:
        return JSONResponse(await snapshot(), headers={"Cache-Control": "no-store"})

    async def act(name: str, given: str | None, *args: Any) -> JSONResponse:
        if given is None or not hmac.compare_digest(given.encode(), token.encode()):
            return JSONResponse({"error": "Action refusée : recharge la page."}, status_code=403)
        action = actions.get(name)
        if action is None:
            return JSONResponse({"error": "Cette action n'est pas disponible."}, status_code=404)
        try:
            await action(*args)
        except ActionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status)
        except Exception as exc:
            return JSONResponse({"error": f"L'action a échoué : {type(exc).__name__}"}, status_code=500)
        return JSONResponse({"ok": True})

    @app.put("/api/memory/{memory_id}")
    async def memory_update(memory_id: str, request: Request,
                            x_monika_token: str | None = Header(None)) -> JSONResponse:
        try:
            body = await request.json()
        except ValueError:
            body = None
        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
            return JSONResponse({"error": f"Le souvenir doit faire entre 1 et {MAX_TEXT_CHARS} caractères."},
                                status_code=400)
        return await act("memory_update", x_monika_token, memory_id, text.strip())

    @app.delete("/api/memory/{memory_id}")
    async def memory_delete(memory_id: str, x_monika_token: str | None = Header(None)) -> JSONResponse:
        return await act("memory_delete", x_monika_token, memory_id)

    @app.websocket("/ws")
    async def live(ws: WebSocket) -> None:
        await ws.accept()
        queue = hub.subscribe()
        try:
            await ws.send_json(hub.hello())

            async def pump() -> None:
                while True:
                    await ws.send_json(await queue.get())

            async def listen() -> None:
                while True:  # The page never sends commands; reading detects the disconnection
                    await ws.receive_text()

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
            hub.unsubscribe(queue)

    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    return LocalOnly(app, port)


class _Server(uvicorn.Server):
    """uvicorn without its own signal handling: Ctrl+C stays under the control of """

    def install_signal_handlers(self) -> None:
        pass

    @contextlib.contextmanager
    def capture_signals(self):  # type: ignore[override]
        yield


class WebServer:
    """Runs the app inside the current event loop, next to the chat."""

    def __init__(self, hub: Hub, snapshot: Snapshot, port: int, static_dir: Path = STATIC_DIR,
                 actions: Actions | None = None) -> None:
        self._actions = actions
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
            probe.bind((HOST, self.port))
        except OSError as exc:
            raise WebError(f"Le port {self.port} est déjà utilisé ({exc.strerror}) : une autre instance de Monika "
                           "tourne peut-être ; change WEB_PORT ou ferme-la.") from exc
        finally:
            probe.close()
        app = create_app(self._hub, self._snapshot, self.port, self._static, self._actions)
        config = uvicorn.Config(app, host=HOST, port=self.port, log_level="warning", access_log=False,
                                lifespan="off", timeout_graceful_shutdown=2)
        self._server = _Server(config)
        self._task = asyncio.get_running_loop().create_task(self._run(self._server))
        for _ in range(100):
            if self._server.started:
                return self.url
            if self._task.done():
                break
            await asyncio.sleep(0.05)
        error = self._task.exception() if self._task.done() and not self._task.cancelled() else None
        await self.stop()
        raise WebError(f"Le serveur web n'a pas démarré{f' : {error}' if error else ''}.")

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
