"""Update help: compare the local checkout with its git remote, never modify it.

`python -m monika --update` prints the current commit, whether the remote has a newer one and
the exact commands to apply it. Pulling is deliberately left to the user: a blind `git pull`
could overwrite local edits or half of a running setup.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from monika.config import PROJECT_ROOT
from monika.i18n import tr


@dataclass(frozen=True)
class UpdateStatus:
    branch: str
    current: str
    remote: str
    dirty: bool
    error: str = ""

    @property
    def available(self) -> bool:
        return bool(self.remote and self.current and self.remote != self.current)


def _git(*args: str, timeout: float = 10.0) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip()[:200])
    return result.stdout.strip()


def check() -> UpdateStatus:
    """Read the local and remote HEAD; network failures become a message, never an exception."""
    try:
        branch = _git("rev-parse", "--abbrev-ref", "HEAD") or "?"
        current = _git("rev-parse", "--short", "HEAD")
        dirty = bool(_git("status", "--porcelain"))
        remote_line = _git("ls-remote", "origin", "HEAD")
        remote = remote_line.split()[0][:7] if remote_line else ""
        return UpdateStatus(branch, current, remote, dirty)
    except Exception as exc:  # noqa: BLE001 - offline, no remote, git missing
        return UpdateStatus("?", "", "", False, f"{type(exc).__name__}: {exc}")


def report() -> int:
    """Print the status and the commands; 0 when up to date, 2 when an update is available."""
    status = check()
    print(tr("update.title"))
    print(f"  {tr('update.branch')} : {status.branch}")
    print(f"  {tr('update.current')} : {status.current or '?'}")
    if status.error:
        print(f"  {tr('update.unknown')} : {status.error}")
        return 1
    if not status.available:
        print(f"  {tr('update.uptodate')} ({status.remote})")
        return 0
    print(f"  {tr('update.available')} : {status.remote}")
    if status.dirty:
        print(f"  {tr('update.dirty')}")
    print(f"\n{tr('update.howto')}")
    print("  git pull --ff-only")
    print("  .venv/bin/pip install -r requirements.txt")
    print("  .venv/bin/python -m monika --config")
    print(f"\n{tr('update.backup')}")
    return 2
