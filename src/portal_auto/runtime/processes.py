"""Process-group helpers shared by the Run watchdog and the script executor."""
import os
import signal
import time
from collections.abc import Callable

TERMINATION_GRACE_SECONDS = 3.0
_POLL_SECONDS = 0.05
_SIGTERM_EXIT_CODE = 143


def kill_group(pgid: int, sig: int = signal.SIGKILL) -> bool:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def terminate_group(
    pgid: int, *, grace: float = TERMINATION_GRACE_SECONDS, sleep: Callable[[float], None] = time.sleep
) -> None:
    if not kill_group(pgid, signal.SIGTERM):
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and _group_exists(pgid):
        sleep(_POLL_SECONDS)
    kill_group(pgid, signal.SIGKILL)


def pid_alive(pid: int) -> bool:
    if _reaped(pid):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def install_sigterm_exit() -> None:
    def exit_so_cleanup_runs(signum: int, frame: object) -> None:
        raise SystemExit(_SIGTERM_EXIT_CODE)

    signal.signal(signal.SIGTERM, exit_so_cleanup_runs)


def _group_exists(pgid: int) -> bool:
    _reaped(pgid)
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _reaped(pid: int) -> bool:
    try:
        finished, _ = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return False
    return finished == pid
