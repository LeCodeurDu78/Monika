"""The terminal as a client of the kernel: `--attach`.

The client is deliberately thin: it sends one line and prints what the kernel sends back. All the
rendering, the memory and the tools live in the kernel, so two terminals (and, with task 2.1, the
page) share the same conversation.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
from pathlib import Path
from typing import Any

from monika.kernel import KernelUnavailable, MAX_LINE_CHARS


def kernel_alive(socket_path: Path) -> bool:
    """Is a kernel listening there? A plain connect is enough, and harmless to the kernel."""
    if not socket_path.exists():
        return False
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(0.5)
    try:
        probe.connect(str(socket_path))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def _send(writer: asyncio.StreamWriter, message: dict[str, Any]) -> None:
    writer.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))


async def _read(reader: asyncio.StreamReader) -> dict[str, Any] | None:
    """Next message, or None when the kernel closed the connection."""
    line = await reader.readline()
    if not line:
        return None
    try:
        message = json.loads(line)
    except ValueError:
        return {"type": "error", "text": "Le noyau a envoyé un message illisible."}
    return message if isinstance(message, dict) else {"type": "error", "text": "Message inattendu."}


def _print(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


async def _interrupted(writer: asyncio.StreamWriter) -> None:
    """Ctrl+C: cut the reply, not the session (same rule as the local mode)."""
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        task.uncancel()
    _send(writer, {"op": "cancel"})


async def attach(socket_path: Path, voice: bool = False) -> int:
    """Connect and run until the user leaves. Returns a process exit code."""
    if not kernel_alive(socket_path):
        print(f"✗ Aucun noyau ne répond sur {socket_path}.\n"
              "  Démarre-le :  python -m monika --serve   (ou systemctl --user start monika)\n"
              "  Vérifie aussi que KERNEL_ENABLED=true des deux côtés.")
        return 1
    try:
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
    except OSError as exc:
        raise KernelUnavailable(f"Connexion au noyau impossible : {exc}") from exc
    try:
        hello = await _read(reader)
        if hello is None or hello.get("type") not in ("hello", "pong"):
            print("✗ Le noyau n'a pas répondu.")
            return 1
        if voice:
            await _voice_session(reader, writer)
        else:
            await _text_session(reader, writer)
        _send(writer, {"op": "detach"})
        await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        print("\n✗ La connexion au noyau est tombée.")
        return 1
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, asyncio.CancelledError):
            pass
    print("À bientôt.")
    return 0


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Print until the turn ends; Ctrl+C cancels it and keeps the session."""
    while True:
        try:
            message = await _read(reader)
        except asyncio.CancelledError:
            await _interrupted(writer)
            continue
        if message is None:
            raise ConnectionError("noyau fermé")
        kind = message.get("type")
        if kind == "out":
            _print(str(message.get("text", "")))
        elif kind == "turn_end":
            return
        elif kind == "busy":
            print("\n  [le noyau finit une autre réponse, réessaie]")
            return
        elif kind == "error":
            print(f"\n  [noyau] {message.get('text', '')}")
            return
        elif kind == "info":
            print(f"\n  [{message.get('text', '')}]")
        elif kind == "voice":
            print(f"\n  [micro {'ouvert' if message.get('on') else 'fermé'}]")
        # hello / pong / bye: nothing to display


async def _text_session(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    from prompt_toolkit import PromptSession

    print("Connecté au noyau. /quitter (ou Ctrl+D) pour rendre le terminal ; "
          "Ctrl+C coupe une réponse.")
    prompt: PromptSession = PromptSession()
    while True:
        try:
            line = (await prompt.prompt_async("Toi › ")).strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not line:
            continue
        if line in ("/quitter", "/q"):
            return
        if len(line) > MAX_LINE_CHARS:
            print("  Message trop long.")
            continue
        _send(writer, {"op": "line", "text": line})
        try:
            await writer.drain()
        except ConnectionError:
            raise
        await _pump(reader, writer)


async def _voice_session(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """No local prompt: the kernel listens. Ctrl+C cuts the reply; /quitter or Ctrl+D detaches."""
    from modes.voice import BANNER

    _send(writer, {"op": "voice", "on": True})
    await writer.drain()
    print(BANNER)
    print("  (détacher : tape /quitter puis Entrée, ou Ctrl+D — Ctrl+C coupe seulement la voix)")
    keys = asyncio.create_task(_keyboard(writer))
    try:
        await _pump_forever(reader, writer)
    finally:
        keys.cancel()
        await asyncio.gather(keys, return_exceptions=True)


async def _keyboard(writer: asyncio.StreamWriter) -> None:
    """Read stdin in the background so /quitter still works while the kernel talks."""
    while True:
        try:
            line = await asyncio.to_thread(sys.stdin.readline)
        except (OSError, ValueError):
            return
        if not line or line.strip() in ("/quitter", "/q"):
            _send(writer, {"op": "detach"})
            try:
                await writer.drain()
            except ConnectionError:
                pass
            return


async def _pump_forever(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while True:
        try:
            message = await _read(reader)
        except asyncio.CancelledError:
            await _interrupted(writer)
            continue
        if message is None:
            return
        kind = message.get("type")
        if kind == "out":
            _print(str(message.get("text", "")))
        elif kind == "voice":
            _print(f"\n  [micro {'ouvert' if message.get('on') else 'fermé'}]\n")
        elif kind == "error":
            _print(f"\n  [noyau : {message.get('text', '')}]\n")
        elif kind == "info":
            _print(f"\n  [{message.get('text', '')}]\n")
        elif kind == "bye":
            return
