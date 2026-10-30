"""The persistent kernel: one process owns memory, documents, tools, the page and the voice.

The terminal and the page become its clients. The transport is a private Unix socket
(`<MONIKA_HOME>/kernel.sock`, `0600`): newline-delimited JSON, and the kernel sends back the very
text the terminal would have printed. Rendering therefore stays in one place (`modes/text.py`)
and a client only prints what it receives.

Protocol (one line = one JSON object)

    client -> kernel   {"op": "line", "text": "..."}   a message or a /command
                       {"op": "cancel"}                cut the running turn
                       {"op": "voice", "on": true}     open (or close) the microphone
                       {"op": "ping"}                  liveness
                       {"op": "detach"}                this client is leaving
    kernel -> client   {"type": "hello", "voice": bool}
                       {"type": "out", "text": "..."}  text to display, as it is produced
                       {"type": "turn_end"}            the turn (or the command) is over
                       {"type": "stopped", "text": "…"}  a `/stop` was honoured: what was stopped
                       {"type": "busy"}                another turn is running
                       {"type": "error", "text": "..."}
                       {"type": "info", "text": "..."}
                       {"type": "voice", "on": bool}
                       {"type": "bye"}

Nothing here may break a turn: a broken client is dropped, a failed request is answered with an
error message, and the kernel keeps serving.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import signal
import socket
import struct
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MAX_LINE_CHARS = 200_000  # a pasted message, not a file dump
MAX_SOCKET_PATH = 100     # Linux allows 108 bytes for sun_path, terminator included


class KernelError(Exception):
    """The kernel cannot start; the message is in French and ready to display."""


class KernelUnavailable(KernelError):
    """No kernel answers on that socket."""


class InstanceLock:
    """One kernel at a time: the local Qdrant folders cannot be opened by two processes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    def acquire(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self.path, "a+", encoding="utf-8")
        except OSError as exc:
            raise KernelError(f"Verrou du noyau inaccessible ({self.path}) : {exc}") from exc
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise KernelError(
                "Une autre instance Monika utilise déjà ce dossier de données (mémoire et documents "
                "ne supportent qu'un seul processus). Arrête-la, ou rattache-toi avec --attach si "
                "c'est le noyau."
            ) from exc
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._handle.close()
        self._handle = None


class _Broadcast:
    """Stands in for the terminal's stdout: everything written goes to every attached client."""

    def __init__(self, server: KernelServer) -> None:
        self._server = server

    def write(self, text: str) -> None:
        self._server.broadcast({"type": "out", "text": text})

    def flush(self) -> None:
        pass


class _ClientOut:
    """A turn's output goes to the client that asked for it, not to every terminal."""

    def __init__(self, server: KernelServer, writer: asyncio.StreamWriter) -> None:
        self._server = server
        self._writer = writer

    def write(self, text: str) -> None:
        self._server._send(self._writer, {"type": "out", "text": text})

    def flush(self) -> None:
        pass


class KernelServer:
    """Serves turns, commands and the microphone over a private Unix socket."""

    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self.session: Any = None            # modes.text.TextSession
        self._server: asyncio.AbstractServer | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._stop = asyncio.Event()
        self._turn_task: asyncio.Task | None = None
        self._voice_task: asyncio.Task | None = None
        self._voice_factory = None          # async () -> VoiceListener | None
        self._listener: Any = None
        self._speaker: Any = None
        self._hub: Any = None
        self._out = _Broadcast(self)

    # ------------------------------------------------------------------ serving
    async def serve(self, *, session: Any, hub: Any = None, speaker: Any = None,
                    voice_factory=None) -> None:
        """Serve until stop(); returns once the socket is closed and the clients said goodbye."""
        if len(str(self.socket_path)) >= MAX_SOCKET_PATH:
            raise KernelError(f"Chemin de socket trop long pour Unix ({self.socket_path}) : "
                              "choisis un KERNEL_SOCKET plus court.")
        self.session, self._hub, self._speaker, self._voice_factory = (
            session, hub, speaker, voice_factory)
        session.set_output(self._out)
        try:
            self.socket_path.parent.mkdir(parents=True, exist_ok=True)
            if self.socket_path.exists():
                self.socket_path.unlink()  # the instance lock guarantees a stale file is a leftover
            self._server = await asyncio.start_unix_server(self._handle, path=str(self.socket_path))
            os.chmod(self.socket_path, 0o600)
        except OSError as exc:
            raise KernelError(f"Le noyau n'a pas pu ouvrir {self.socket_path} : {exc}") from exc
        self._install_signals()
        print(f"Noyau prêt : {self.socket_path} (terminal : python -m monika --attach)")
        try:
            await self._stop.wait()
        finally:
            await self._shutdown()

    def stop(self) -> None:
        self._stop.set()

    async def _shutdown(self) -> None:
        await self._stop_voice()
        self.cancel()
        if self._turn_task is not None:  # let the cut turn finish writing what it can
            await asyncio.gather(self._turn_task, return_exceptions=True)
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except (ConnectionError, asyncio.CancelledError):
                pass
        for writer in list(self._clients):
            self._send(writer, {"type": "bye"})
            writer.close()
        self._clients.clear()
        try:
            self.socket_path.unlink()
        except OSError:
            pass

    def _install_signals(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self.stop)
            except (NotImplementedError, RuntimeError):  # not the main thread, or unsupported
                continue

    # ------------------------------------------------------------------ connections
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if not self._same_user(writer):
            writer.close()
            return
        self._clients.add(writer)
        try:
            self._send(writer, {"type": "hello", "voice": self._voice_task is not None})
            while True:
                line = await reader.readline()
                if not line:
                    break
                if len(line) > MAX_LINE_CHARS:
                    self._send(writer, {"type": "error", "text": "Ligne trop longue, ignorée."})
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    self._send(writer, {"type": "error", "text": "Message illisible (JSON attendu)."})
                    continue
                if not isinstance(message, dict):
                    self._send(writer, {"type": "error", "text": "Message inattendu."})
                    continue
                try:
                    await self._dispatch(writer, message)
                except (ConnectionError, asyncio.CancelledError):
                    raise
                except Exception as exc:  # noqa: BLE001 - a client message must not drop the client
                    log.warning("requête %r en échec (%s: %s)", message.get("op"),
                                type(exc).__name__, exc)
                    self._send(writer, {"type": "error",
                                        "text": f"Requête impossible : {type(exc).__name__}: {exc}"})
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except asyncio.CancelledError:
            raise
        finally:
            self._clients.discard(writer)
            try:
                writer.close()
            except (ConnectionError, RuntimeError):
                pass

    @staticmethod
    def _same_user(writer: asyncio.StreamWriter) -> bool:
        """Only the owner may talk to the kernel; the socket's 0600 already says so, this confirms."""
        sock = writer.get_extra_info("socket")
        if sock is None or not hasattr(socket, "SO_PEERCRED"):
            return True  # not Linux: the file permissions are the only guarantee
        try:
            _pid, uid, _gid = struct.unpack(
                "3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        except OSError:
            return True
        return uid == os.getuid()

    async def _dispatch(self, writer: asyncio.StreamWriter, message: dict[str, Any]) -> None:
        op = message.get("op")
        if op == "line":
            text = str(message.get("text", ""))
            if text.strip() == "/stop":  # Works even while a turn runs: that is the point
                await self.stop_everything(writer)
            else:
                self._run_line(writer, text)
        elif op == "cancel":
            await self.stop_everything()
        elif op == "voice":
            await self._set_voice(writer, bool(message.get("on")))
        elif op == "ping":
            self._send(writer, {"type": "pong"})
        elif op == "detach":
            self._send(writer, {"type": "bye"})
        else:
            self._send(writer, {"type": "error", "text": f"Opération inconnue : {op!r}."})

    # ------------------------------------------------------------------ turns and commands
    def _run_line(self, writer: asyncio.StreamWriter, line: str) -> None:
        """Start one message or command; one at a time, so two clients never share the history."""
        if not line.strip():
            self._send(writer, {"type": "turn_end"})
            return
        self._start_turn(line, writer)

    def _start_turn(self, line: str, writer: asyncio.StreamWriter | None = None
                    ) -> asyncio.Task | None:
        """Run the turn in the background: the client must stay able to send `cancel`."""
        if self._turn_task is not None and not self._turn_task.done():
            if writer is not None:
                self._send(writer, {"type": "busy"})
            else:
                self.broadcast({"type": "info", "text": "Je suis déjà en train de répondre : je "
                                                           "n'ai pas retenu la dernière phrase."})
            return None
        task = asyncio.create_task(self._turn(line, writer))
        self._turn_task = task
        return task

    async def _turn(self, line: str, writer: asyncio.StreamWriter | None) -> None:
        """One turn: the session writes to the client that asked, then everything is rolled back."""
        self.session.set_output(_ClientOut(self, writer) if writer is not None else self._out)
        try:
            await self._execute(line)
        except asyncio.CancelledError:
            pass  # cut on purpose (client cancel or shutdown); the session already said so
        except Exception as exc:  # noqa: BLE001 - a turn must never take the kernel down
            log.warning("tour interrompu par une erreur (%s: %s)", type(exc).__name__, exc)
            message = {"type": "error", "text": f"Le tour a échoué : {type(exc).__name__}: {exc}"}
            if writer is not None:
                self._send(writer, message)
            else:
                self.broadcast(message)
        finally:
            self._turn_task = None
            self.session.set_output(self._out)
            if writer is not None:
                self._send(writer, {"type": "turn_end"})

    async def _execute(self, line: str) -> None:
        if not await self.session.handle_command(line):
            await self.session.reply(line)

    def cancel(self) -> None:
        """Cut the running turn; the same path as Ctrl+C in the local session."""
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()

    async def stop_everything(self, writer: asyncio.StreamWriter | None = None) -> list[str]:
        """Ctrl+C, `cancel` and `/stop`: the turn, the voice, the indexing.

        With a writer (an explicit `/stop`), the report goes back to the client that asked. Without
        one (Ctrl+C, which already printed « [interrompu] »), nothing is added: a silent stop.
        """
        had_turn = self._turn_task is not None and not self._turn_task.done()
        self.cancel()
        if self._turn_task is not None:
            await asyncio.gather(self._turn_task, return_exceptions=True)
        stopped = await self.session.stop_all(turn=had_turn) if self.session is not None else []
        if writer is not None:
            report = "Arrêté : " + ", ".join(stopped) + "." if stopped else "Rien ne tournait."
            # `stopped` closes the request: the client gets its report and its prompt back
            self._send(writer, {"type": "stopped", "text": report})
        return stopped

    async def fire_task(self, prompt: str) -> None:
        """A scheduled task is a turn like any other: it waits for the current answer, then speaks."""
        while self._turn_task is not None and not self._turn_task.done():
            await asyncio.gather(self._turn_task, return_exceptions=True)
        task = self._start_turn(prompt)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    # ------------------------------------------------------------------ voice
    async def _set_voice(self, writer: asyncio.StreamWriter, on: bool) -> None:
        if not on:
            await self._stop_voice()
            self.broadcast({"type": "voice", "on": False})
            return
        if self._voice_task is not None:
            self._send(writer, {"type": "voice", "on": True})
            return
        if self._voice_factory is None:
            self._send(writer, {"type": "error", "text": "La voix n'est pas disponible ici."})
            return
        self.broadcast({"type": "info", "text": "Ouverture du microphone (le modèle peut prendre " "quelques secondes)…"})
        try:
            listener = await self._voice_factory()
        except Exception as exc:  # noqa: BLE001
            self._send(writer, {"type": "error",
                                "text": f"Microphone indisponible : {type(exc).__name__}: {exc}"})
            return
        if listener is None:
            self._send(writer, {"type": "error", "text": "Microphone indisponible (voir le journal)."})
            return
        self._listener = listener
        self._voice_task = asyncio.create_task(self._voice_loop(listener))
        self.broadcast({"type": "voice", "on": True})

    async def _voice_loop(self, listener) -> None:
        from modes.voice import VoiceReader

        reader = VoiceReader(listener, self._out.write, self._speaker, hub=self._hub)
        try:
            while True:
                try:
                    text = await reader()
                except EOFError:  # a whole-sentence goodbye
                    break
                if text.strip():
                    await self._voice_turn(text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the chat keeps working without the microphone
            log.warning("écoute interrompue (%s: %s)", type(exc).__name__, exc)
            self.broadcast({"type": "info",
                            "text": f"Écoute arrêtée ({type(exc).__name__} : {exc})."})
        finally:
            self._voice_task = None
            listener, self._listener = self._listener, None
            if listener is not None:  # a loop that ended on its own must release the microphone
                try:
                    await listener.close()
                except Exception:  # noqa: BLE001
                    pass
            self.broadcast({"type": "voice", "on": False})
            self.broadcast({"type": "info", "text": "Fin de l'écoute."})

    async def _voice_turn(self, text: str) -> None:
        """Half duplex: wait for the reply before listening again (otherwise she hears herself)."""
        task = self._start_turn(text)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def _stop_voice(self) -> None:
        if self._voice_task is not None:
            self._voice_task.cancel()
            await asyncio.gather(self._voice_task, return_exceptions=True)
            self._voice_task = None
        if self._listener is not None:
            try:
                await self._listener.close()
            except Exception:  # noqa: BLE001 - closing a broken microphone is best effort
                pass
            self._listener = None

    # ------------------------------------------------------------------ sending
    def broadcast(self, message: dict[str, Any]) -> None:
        for writer in list(self._clients):
            self._send(writer, message)

    @staticmethod
    def _send(writer: asyncio.StreamWriter, message: dict[str, Any]) -> None:
        """shortcut: buffered without drain; fine for terminal output, revisit for large streams."""
        if writer.is_closing():
            return
        try:
            writer.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        except (ConnectionError, RuntimeError):
            pass
