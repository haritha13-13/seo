"""A Saved Session as files for one Run: a private directory that never outlives the Run."""
import os
import shutil
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from portal_auto.services.saved_sessions import MAX_SESSION_BYTES

STATE_ENV = "PORTAL_STORAGE_STATE"
STATE_OUT_ENV = "PORTAL_STORAGE_STATE_OUT"
PRIVATE_FILE_MODE = 0o600
DIRECTORY_PREFIX = "portal_session_"


@dataclass(frozen=True)
class SessionFiles:
    state: Path
    out: Path

    def env(self) -> dict[str, str]:
        return {STATE_ENV: str(self.state), STATE_OUT_ENV: str(self.out)}

    def read_out(self) -> str | None:
        """The saved state, or None when it is missing, larger than a Saved Session may be, or not UTF-8."""
        try:
            with self.out.open("rb") as handle:
                raw = handle.read(MAX_SESSION_BYTES + 1)
            return raw.decode("utf-8") if len(raw) <= MAX_SESSION_BYTES else None
        except (OSError, UnicodeDecodeError):
            return None


@contextmanager
def session_files(state_json: str) -> Iterator[SessionFiles]:
    with tempfile.TemporaryDirectory(prefix=DIRECTORY_PREFIX) as directory:
        files = SessionFiles(Path(directory) / "state.json", Path(directory) / "state-out.json")
        descriptor = os.open(files.state, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(state_json)
        yield files


def sweep_stale_session_dirs(max_age_seconds: float) -> None:
    """Removes directories a killed worker could not clean up; younger ones may belong to a live Run."""
    cutoff = time.time() - max_age_seconds
    for directory in Path(tempfile.gettempdir()).glob(f"{DIRECTORY_PREFIX}*"):
        try:
            if directory.is_dir() and directory.stat().st_mtime < cutoff:
                shutil.rmtree(directory, ignore_errors=True)
        except OSError:
            continue
