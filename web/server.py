"""Local web server: serves the avatar page and streams live events. Reachable from this computer only.

No access control: every route answers without a token and without an Origin check, and the page
sends whatever it has to say (messages, /commands and, when it asks for it, microphone audio) to the
agent.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from character import emotion_spec
from character.journal import MAX_IMPRESSION_CHARS, MAX_NOTE_CHARS
from web.hub import Hub

STATIC_DIR = Path(__file__).resolve().parent / "static"
HOST = "127.0.0.1"
Snapshot = Callable[[], Awaitable[dict[str, Any]]]
Actions = dict[str, Callable[..., Awaitable[None]]]
MAX_TEXT_CHARS = 1000


class WebError(Exception):
    """The web server could not start; the message is in French and ready to display."""


class ActionError(Exception):
    """An edit from the page was refused; the message is in French and shown to the user."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def create_app(
    hub: Hub,
    snapshot: Snapshot,
    static_dir: Path = STATIC_DIR,
    actions: Actions | None = None,
    settings_api: Any | None = None,
    chat: Any = None,
    voice_factory: Any = None,
) -> Any:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    actions = actions or {}
    if settings_api is not None:
        actions = {**actions, **settings_api.actions()}  # Same act(), same ActionError

    @app.get("/")
    async def index() -> HTMLResponse:
        return HTMLResponse(
            (static_dir / "index.html").read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-store"},
        )

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
            return HTMLResponse(
                (static_dir / "settings.html").read_text(encoding="utf-8"),
                headers={"Cache-Control": "no-store"},
            )

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

        @app.post("/api/settings/words")
        async def settings_words(request: Request) -> JSONResponse:
            try:
                body = await request.json()
            except ValueError:
                body = None
            if not isinstance(body, dict):
                return JSONResponse(
                    {"error": "Liste de mots attendue sous forme d'objet JSON."},
                    status_code=400,
                )
            return await act("settings_words", body)

        @app.post("/api/settings/secret")
        async def settings_secret(request: Request) -> JSONResponse:
            try:
                body = await request.json()
            except ValueError:
                body = None
            name = body.get("name") if isinstance(body, dict) else None
            value = body.get("value") if isinstance(body, dict) else None
            if not isinstance(name, str) or not isinstance(value, str):
                return JSONResponse(
                    {"error": "Nom et valeur du secret attendus."}, status_code=400
                )
            return await act("settings_secret", name, value)

    @app.websocket("/ws")
    async def live(ws: WebSocket) -> None:
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
    ) -> None:
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
            probe.bind((HOST, self.port))
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
        )
        config = uvicorn.Config(
            app,
            host=HOST,
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
