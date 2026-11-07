"""Facts about the machine and the moment, injected into the system prompt."""

from __future__ import annotations

import getpass
import os
import platform
import socket
from datetime import datetime
from pathlib import Path

from monika.i18n import is_english, tr

DAYS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
DAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]
MONTHS_EN = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def format_date(now: datetime) -> str:
    """Return e.g. 'jeudi 22 octobre 2026, 05:46 (CEST)' or its English equivalent."""
    if is_english():
        day = f"{DAYS_EN[now.weekday()]} {MONTHS_EN[now.month - 1]} {now.day}, {now.year}"
    else:
        day = f"{DAYS_FR[now.weekday()]} {now.day} {MONTHS_FR[now.month - 1]} {now.year}"
    return f"{day}, {now:%H:%M} ({now.tzname()})"


def format_date_fr(now: datetime) -> str:
    """Backward-compatible alias; the active LANGUAGE decides the wording."""
    return format_date(now)


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
    lines = [tr("sysinfo.system", os=_os_name(), kernel=platform.release(), host=socket.gethostname())]
    try:
        lines.append(
            tr("sysinfo.user", user=getpass.getuser(), home=Path.home())
        )
    except (KeyError, OSError):
        lines.append(tr("sysinfo.home", home=Path.home()))
    lines.append(tr("sysinfo.cwd", cwd=os.getcwd()))
    session = " / ".join(
        filter(
            None,
            (os.environ.get("XDG_CURRENT_DESKTOP"), os.environ.get("XDG_SESSION_TYPE")),
        )
    )
    shell = os.environ.get("SHELL")
    extras = [
        tr("sysinfo.desktop", session=session) if session else "",
        tr("sysinfo.shell", shell=shell) if shell else "",
    ]
    extras = [item for item in extras if item]
    if extras:
        lines.append(tr("sysinfo.environment", items=", ".join(extras)))
    return lines
