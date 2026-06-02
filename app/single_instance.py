"""Single-instance guard.

Two concurrent `python -m app.run` would fight over the same Discord token
(Discord only keeps one valid gateway session per token, so interactions get
split or dropped between the instances) and the same web port. This module
holds a PID lockfile so the second launch refuses to start instead of silently
stealing events from the first.

A lock whose recorded PID is no longer alive (crash, hard kill) is treated as
stale and reclaimed automatically, so a dead instance never blocks a restart.
"""
from __future__ import annotations

import atexit
import os
from pathlib import Path

from .config import ROOT_DIR

LOCK_FILE = ROOT_DIR / "logs" / "app.lock"


class AlreadyRunningError(RuntimeError):
    """Raised when another live instance already holds the lock."""

    def __init__(self, pid: int):
        self.pid = pid
        super().__init__(f"another app instance is already running (pid={pid})")


def _pid_alive(pid: int) -> bool:
    """Return True if a process with this PID currently exists."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == still_active
            return False
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


def _read_pid(lock_file: Path) -> int:
    try:
        return int(lock_file.read_text(encoding="utf-8").strip() or "0")
    except (ValueError, OSError):
        return 0


def acquire(lock_file: Path = LOCK_FILE) -> None:
    """Claim the single-instance lock, or raise AlreadyRunningError.

    Registers cleanup so the lock is released when this process exits normally.
    """
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    if lock_file.exists():
        existing = _read_pid(lock_file)
        if existing and existing != os.getpid() and _pid_alive(existing):
            raise AlreadyRunningError(existing)
        # Otherwise the lock is stale (dead PID) — fall through and reclaim it.

    lock_file.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(release, lock_file)


def release(lock_file: Path = LOCK_FILE) -> None:
    """Remove the lockfile if and only if this process owns it."""
    try:
        if lock_file.exists() and _read_pid(lock_file) == os.getpid():
            lock_file.unlink()
    except OSError:
        pass
