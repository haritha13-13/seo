"""Runs a stored script in a child Python process with the Credential passed through the environment."""
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import IO

from portal_auto.runtime.interfaces import ExecutionResult
from portal_auto.runtime.processes import kill_group

TIMEOUT_EXIT_CODE = 124
MAX_OUTPUT_BYTES = 1_000_000
TRUNCATION_MARKER = "[output truncated]\n"
_READ_CHUNK = 65536
_DRAIN_SECONDS = 5
_SCREENSHOT_LINE = re.compile(r"^Screenshot: (.+)$", re.MULTILINE)
_INHERITED_NAMES = frozenset({
    "PATH", "HOME", "USER", "LANG", "TZ", "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR",
})
_INHERITED_PREFIXES = ("LC_", "PLAYWRIGHT_", "PYTHON")


class SubprocessScriptExecutor:
    def execute(self, code: str, env: dict[str, str], headless: bool, timeout_seconds: int) -> ExecutionResult:
        child_env = {**_inherited_environment(), **env, "HEADLESS": "1" if headless else "0"}
        with tempfile.TemporaryDirectory(prefix="portal_auto_") as workdir:
            script = Path(workdir) / "script.py"
            script.write_text(code)
            exit_code, output = _run(script, child_env, timeout_seconds)
        return ExecutionResult(exit_code, output, _find_screenshot(output))


def _inherited_environment() -> dict[str, str]:
    return {
        name: value for name, value in os.environ.items()
        if name in _INHERITED_NAMES or name.startswith(_INHERITED_PREFIXES)
    }


class _OutputTail:
    """Reads a stream incrementally, keeping only the last MAX_OUTPUT_BYTES."""

    def __init__(self, stream: IO[bytes]):
        self._stream = stream
        self._tail = bytearray()
        self._truncated = False
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()

    def text(self) -> str:
        self._thread.join(timeout=_DRAIN_SECONDS)
        text = bytes(self._tail).decode(errors="replace")
        return TRUNCATION_MARKER + text if self._truncated else text

    def _drain(self) -> None:
        while chunk := self._stream.read1(_READ_CHUNK):
            self._tail += chunk
            overflow = len(self._tail) - MAX_OUTPUT_BYTES
            if overflow > 0:
                del self._tail[:overflow]
                self._truncated = True


def _run(script: Path, env: dict[str, str], timeout_seconds: int) -> tuple[int, str]:
    process = subprocess.Popen(
        [sys.executable, str(script)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    tail = _OutputTail(process.stdout)
    timed_out = False
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        kill_group(process.pid)
        process.wait()
    output = tail.text()
    if timed_out:
        return TIMEOUT_EXIT_CODE, f"{output}\nScript timed out after {timeout_seconds} seconds and was killed.\n"
    return process.returncode, output


def _find_screenshot(output: str) -> str | None:
    matches = _SCREENSHOT_LINE.findall(output)
    return matches[-1].strip() if matches else None
