"""Windows paths: %APPDATA%, the msvcrt lock and Docker as the sandbox for custom tools."""

from __future__ import annotations

import pytest

from monika import lock as lock_module
from monika.config import default_home
from monika.lock import InstanceLock, LockError
from tools.custom_tools import ToolFailure, docker_argv, sandbox_command

# --- data folder -------------------------------------------------------------------------------


def test_home_uses_appdata_on_windows(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))

    assert default_home("nt") == tmp_path / "monika"


def test_home_is_xdg_elsewhere() -> None:
    from pathlib import Path

    assert default_home("posix") == Path("~/.config/monika")


# --- lock --------------------------------------------------------------------------------------


class FakeMsvcrt:
    """The two calls InstanceLock makes, and a way to look busy."""

    LK_NBLCK = 2
    LK_UNLCK = 0

    def __init__(self, busy: bool = False) -> None:
        self.busy = busy
        self.calls: list[tuple[int, int]] = []

    def locking(self, fd: int, mode: int, nbytes: int) -> None:
        self.calls.append((mode, nbytes))
        if mode == self.LK_NBLCK and self.busy:
            raise OSError("file is being used by another process")


def test_windows_lock_takes_one_byte(tmp_path, monkeypatch) -> None:
    fake = FakeMsvcrt()
    monkeypatch.setattr(lock_module, "msvcrt", fake)
    path = tmp_path / "instance.lock"

    lock = InstanceLock(path)
    lock.acquire()
    try:
        assert fake.calls[0] == (fake.LK_NBLCK, 1)
        assert path.read_text() == "monika"  # msvcrt can only lock a byte that exists
    finally:
        lock.release()

    assert fake.calls[-1] == (fake.LK_UNLCK, 1)


def test_windows_lock_reports_another_instance(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(lock_module, "msvcrt", FakeMsvcrt(busy=True))

    with pytest.raises(LockError):
        InstanceLock(tmp_path / "instance.lock").acquire()


# --- sandbox ------------------------------------------------------------------------------------


def test_docker_runs_the_test_with_only_the_tool_folder(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "tools.custom_tools.shutil.which",
        lambda name: "/usr/bin/docker" if name == "docker" else None,
    )

    argv, timeout_s = sandbox_command(tmp_path)

    assert argv[0] == "docker"
    assert f"{tmp_path}:/outil" in argv
    assert timeout_s == 240


def test_bwrap_wins_where_it_exists(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "tools.custom_tools.shutil.which", lambda name: f"/usr/bin/{name}"
    )

    argv, timeout_s = sandbox_command(tmp_path)

    assert argv[0] == "bwrap"
    assert timeout_s == 60


def test_no_sandbox_means_no_custom_tool(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("tools.custom_tools.shutil.which", lambda name: None)

    with pytest.raises(ToolFailure):
        sandbox_command(tmp_path)


def test_docker_command_keeps_the_network_and_hides_the_disk(tmp_path) -> None:
    argv = docker_argv(tmp_path)

    assert "-v" in argv and f"{tmp_path}:/outil" in argv
    assert "--network" in argv and "bridge" in argv
    assert "/root/.cache/pip" in " ".join(argv)
