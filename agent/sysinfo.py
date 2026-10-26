"""Facts about the machine and the moment, injected into the system prompt."""

from __future__ import annotations

import getpass
import os
import platform
import socket
from datetime import datetime
from pathlib import Path

DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
          "septembre", "octobre", "novembre", "décembre"]


def format_date_fr(now: datetime) -> str:
    """Return e.g. 'jeudi 22 octobre 2026, 05:46 (CEST)', independent of the locale."""
    day = f"{DAYS[now.weekday()]} {now.day} {MONTHS[now.month - 1]} {now.year}"
    return f"{day}, {now:%H:%M} ({now.tzname()})"


def _os_name() -> str:
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.system() or "Linux"


def system_context() -> list[str]:
    """Return short factual lines about the machine (best effort, never raises)."""
    lines = [f"Système : {_os_name()}, noyau {platform.release()}, machine {socket.gethostname()}."]
    try:
        lines.append(f"Utilisateur du système : {getpass.getuser()}, dossier personnel {Path.home()}.")
    except (KeyError, OSError):
        lines.append(f"Dossier personnel : {Path.home()}.")
    lines.append(f"Dossier de travail actuel : {os.getcwd()}.")
    session = " / ".join(filter(None, (os.environ.get("XDG_CURRENT_DESKTOP"),
                                       os.environ.get("XDG_SESSION_TYPE"))))
    shell = os.environ.get("SHELL")
    extras = [x for x in (f"bureau {session}" if session else "", f"shell {shell}" if shell else "") if x]
    if extras:
        lines.append("Environnement : " + ", ".join(extras) + ".")
    return lines
