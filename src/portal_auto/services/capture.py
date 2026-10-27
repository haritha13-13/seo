"""Capture: the operator signs in by hand in a real Chrome window, then the Saved Session is read from it."""
import secrets
import shutil
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from portal_auto.db.models import utcnow
from portal_auto.runtime.chrome import ChromeHandle, ChromeLauncher, StateReader
from portal_auto.services.errors import Invalid

DEFAULT_TTL_SECONDS = 900
PROFILE_PREFIX = "portal-auto-capture-"


class Cancellable(Protocol):
    def cancel(self) -> None: ...


Scheduler = Callable[[float, Callable[[], None]], Cancellable]


def daemon_timer(delay_seconds: float, callback: Callable[[], None]) -> Cancellable:
    timer = threading.Timer(delay_seconds, callback)
    timer.daemon = True
    timer.start()
    return timer


@dataclass(frozen=True)
class CaptureInfo:
    id: str
    credential_id: int
    url: str
    port: int
    expires_at: datetime


@dataclass(frozen=True)
class _OpenCapture:
    info: CaptureInfo
    handle: ChromeHandle
    profile_dir: Path
    timer: Cancellable
    owns_profile: bool  # a temporary profile is deleted with the capture; a persistent one never is


class CaptureService:
    """One Capture at a time, safe to call from request and timer threads. Chrome is always stopped, and a
    temporary profile always deleted, whatever the exit path. With `profiles_dir` every Credential keeps its
    own private, persistent Chrome profile under it (ADR 0004), so one Credential's sign-ins never mix with
    another's and capturing again finds the Credential still signed in."""

    def __init__(
        self,
        launcher: ChromeLauncher,
        reader: StateReader,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        clock: Callable[[], datetime] = utcnow,
        temp_root: Path | None = None,
        scheduler: Scheduler = daemon_timer,
        profiles_dir: Path | None = None,
    ):
        self._launcher = launcher
        self._reader = reader
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock
        self._temp_root = temp_root
        self._scheduler = scheduler
        self._profiles_dir = profiles_dir
        self._open: _OpenCapture | None = None
        self._expired_credential_id: int | None = None
        self._lock = threading.RLock()

    def start(self, credential_id: int, url: str) -> CaptureInfo:
        with self._lock:
            current = self.active()
            if current:
                raise Invalid(f"A capture is already open for credential {current.credential_id}; save or cancel it first")
            self._expired_credential_id = None
            self._open = self._launch(credential_id, url)
            return self._open.info

    def finish(self, capture_id: str) -> str:
        with self._lock:
            capture = self._require(capture_id)
            try:
                if not capture.handle.alive():
                    raise Invalid("Chrome was closed before the session was saved; start the capture again")
                return self._reader.read(capture.info.port)
            finally:
                self._close(capture)

    def cancel(self, capture_id: str) -> None:
        with self._lock:
            self._close(self._require(capture_id))

    def active(self) -> CaptureInfo | None:
        with self._lock:
            if self._open and self._clock() >= self._open.info.expires_at:
                self._expire(self._open)
            return self._open.info if self._open else None

    def expired_for(self, credential_id: int) -> bool:
        with self._lock:
            return self._expired_credential_id == credential_id

    def shutdown(self) -> None:
        with self._lock:
            if self._open:
                self._close(self._open)

    def sweep_stale_profiles(self) -> None:
        """Remove profile directories left by a server that died; only deletes, never signals a process."""
        root = self._temp_root or Path(tempfile.gettempdir())
        oldest_kept = self._clock() - self._ttl
        with self._lock:
            live = self._open.profile_dir if self._open else None
            for directory in root.glob(f"{PROFILE_PREFIX}*"):
                if directory != live and _is_stale(directory, oldest_kept):
                    shutil.rmtree(directory, ignore_errors=True)

    def _launch(self, credential_id: int, url: str) -> _OpenCapture:
        profile_dir, owns_profile = self._profile(credential_id)
        try:
            handle = self._launcher.launch(url, profile_dir)
        except BaseException:
            if owns_profile:
                shutil.rmtree(profile_dir, ignore_errors=True)
            raise
        info = CaptureInfo(secrets.token_urlsafe(16), credential_id, url, handle.port, self._clock() + self._ttl)
        timer = self._scheduler(self._ttl.total_seconds(), lambda: self._expire_by_id(info.id))
        return _OpenCapture(info, handle, profile_dir, timer, owns_profile)

    def _profile(self, credential_id: int) -> tuple[Path, bool]:
        if self._profiles_dir is None:
            return Path(tempfile.mkdtemp(prefix=PROFILE_PREFIX, dir=self._temp_root)), True
        profile_dir = self._profiles_dir / f"credential-{credential_id}"
        for directory in (self._profiles_dir, profile_dir):
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(0o700)
        return profile_dir, False

    def _expire_by_id(self, capture_id: str) -> None:
        with self._lock:
            if self._open and self._open.info.id == capture_id:
                self._expire(self._open)

    def _expire(self, capture: _OpenCapture) -> None:
        self._expired_credential_id = capture.info.credential_id
        self._close(capture)

    def _require(self, capture_id: str) -> _OpenCapture:
        current = self.active()
        if current is None or current.id != capture_id or self._open is None:
            raise Invalid("This capture is no longer open; start a new one")
        return self._open

    def _close(self, capture: _OpenCapture) -> None:
        self._open = None
        capture.timer.cancel()
        try:
            capture.handle.terminate()
        finally:
            if capture.owns_profile:
                shutil.rmtree(capture.profile_dir, ignore_errors=True)


def _is_stale(directory: Path, oldest_kept: datetime) -> bool:
    if directory.is_symlink() or not directory.is_dir():
        return False
    return datetime.fromtimestamp(directory.stat().st_mtime, tz=timezone.utc) < oldest_kept
