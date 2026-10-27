"""Real Chrome adapters for Capture: spawn Chrome with a debug port, then read its storage state over CDP."""
import json
import os
import signal
import socket
import subprocess
import time
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from portal_auto.services.errors import Invalid

LOOPBACK = "127.0.0.1"
CHROME_MISSING = "Google Chrome was not found; install it or set PORTAL_AUTO_CHROME_COMMAND"
PORT_WAIT_SECONDS = 15.0
TERM_GRACE_SECONDS = 3.0
POLL_SECONDS = 0.2


class ChromeHandle(Protocol):
    port: int

    def alive(self) -> bool: ...

    def terminate(self) -> None: ...


class ChromeLauncher(Protocol):
    def launch(self, url: str, profile_dir: Path) -> ChromeHandle: ...


class StateReader(Protocol):
    def read(self, port: int) -> str: ...


class _ProcessGroupHandle:
    def __init__(self, process: subprocess.Popen, port: int = 0):
        self._process = process
        self.port = port

    def alive(self) -> bool:
        return self._process.poll() is None

    def terminate(self) -> None:
        self._signal(signal.SIGTERM)
        try:
            self._process.wait(TERM_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            self._signal(signal.SIGKILL)
            self._process.wait()

    def _signal(self, sig: int) -> None:
        try:
            os.killpg(self._process.pid, sig)
        except ProcessLookupError:
            pass


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((LOOPBACK, 0))
        return probe.getsockname()[1]


class RealChromeLauncher:
    """Starts a normal Chrome window. The debug port must be an explicit number: with
    `--remote-debugging-port=0` Chrome sets `navigator.webdriver` to true, and Google then refuses the
    sign-in ("this browser or app may not be secure")."""

    def __init__(
        self, command: str = "google-chrome", extra_args: Sequence[str] = (),
        pick_port: Callable[[], int] = free_loopback_port,
    ):
        self._command = command
        self._extra_args = tuple(extra_args)
        self._pick_port = pick_port

    def launch(self, url: str, profile_dir: Path) -> ChromeHandle:
        port = self._pick_port()
        args = [
            self._command, f"--remote-debugging-port={port}", f"--user-data-dir={profile_dir}", "--no-first-run",
            *self._extra_args, url,
        ]
        try:
            process = subprocess.Popen(
                args, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except FileNotFoundError:
            raise Invalid(CHROME_MISSING) from None
        handle = _ProcessGroupHandle(process)
        try:
            wait_for_debug_endpoint(port, handle.alive)
            handle.port = port
        except BaseException:
            handle.terminate()
            raise
        return handle


def fetch_debug_version(port: int) -> dict | None:
    """What the debug port answers on /json/version, or None while nothing is listening yet."""
    try:
        with urllib.request.urlopen(f"http://{LOOPBACK}:{port}/json/version", timeout=1) as response:
            answer = json.load(response)
    except (OSError, ValueError):
        return None
    return answer if isinstance(answer, dict) else None


def wait_for_debug_endpoint(
    port: int,
    alive: Callable[[], bool],
    timeout: float = PORT_WAIT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    fetch: Callable[[int], dict | None] = fetch_debug_version,
) -> None:
    """Block until Chrome itself answers on `port`; anything else answering there is not our Chrome."""
    deadline = time.monotonic() + timeout
    while (answer := fetch(port)) is None:
        if not alive():
            raise Invalid("Chrome exited before opening its debugging port; start the capture again")
        if time.monotonic() >= deadline:
            raise Invalid("Chrome did not open its debugging port; start the capture again")
        sleep(POLL_SECONDS)
    if not str(answer.get("Browser", "")).startswith(("Chrome/", "HeadlessChrome/")):
        raise Invalid("Another program is using the debug port Chrome was given; start the capture again")


def storage_state_json(browser) -> str:
    try:
        context = browser.contexts[0]
    except IndexError:
        raise Invalid("Chrome has no open window; start the capture again") from None
    return json.dumps(context.storage_state())


class CdpStateReader:
    def read(self, port: int) -> str:
        from playwright.sync_api import Error, sync_playwright

        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.connect_over_cdp(f"http://{LOOPBACK}:{port}")
            except Error:
                raise Invalid("Could not connect to Chrome; start the capture again") from None
            try:
                return storage_state_json(browser)
            finally:
                browser.close()
