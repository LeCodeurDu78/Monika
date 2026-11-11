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
    ahead: int = 0
    behind: int = 0
    tracked: bool = True  # remote is the branch's upstream, not just the remote default HEAD
    error: str = ""

    @property
    def available(self) -> bool:
        if self.behind > 0:
            return True
        # Without an upstream, a different remote commit is the only signal there is.
        return bool(
            self.remote
            and self.current
            and self.remote != self.current
            and not self.tracked
        )


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


def _upstream() -> tuple[str, int, int]:
    """The branch's upstream and (ahead, behind); RuntimeError when it has none."""
    upstream = _git("rev-parse", "--short", "@{u}")
    counts = _git("rev-list", "--left-right", "--count", "HEAD...@{u}").split()
    return upstream, int(counts[0]), int(counts[1])


def check() -> UpdateStatus:
    """Read the local and upstream state; network failures become a message, never an exception."""
    try:
        branch = _git("rev-parse", "--abbrev-ref", "HEAD") or "?"
        current = _git("rev-parse", "--short", "HEAD")
        dirty = bool(_git("status", "--porcelain"))
        try:
            remote, ahead, behind = _upstream()
            return UpdateStatus(branch, current, remote, dirty, ahead, behind)
        except RuntimeError:  # no upstream: fall back to the remote's default branch
            remote_line = _git("ls-remote", "origin", "HEAD")
            remote = remote_line.split()[0][:7] if remote_line else ""
            return UpdateStatus(branch, current, remote, dirty, tracked=False)
    except Exception as exc:  # noqa: BLE001 - offline, no remote, git missing
        return UpdateStatus("", "", "", False, error=f"{type(exc).__name__}: {exc}")


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
