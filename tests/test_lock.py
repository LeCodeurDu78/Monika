"""The instance lock: one Monika per data folder, and the lock is released cleanly."""

from __future__ import annotations

import pytest

from monika.lock import InstanceLock, LockError


def test_second_instance_is_refused(tmp_path) -> None:
    path = tmp_path / "instance.lock"
    first = InstanceLock(path)
    first.acquire()
    try:
        with pytest.raises(LockError):
            InstanceLock(path).acquire()
    finally:
        first.release()

    second = InstanceLock(path)
    second.acquire()
    second.release()


def test_release_is_idempotent(tmp_path) -> None:
    lock = InstanceLock(tmp_path / "instance.lock")

    lock.release()
    lock.acquire()
    lock.release()
    lock.release()
