"""One live browser. Every action both executes and is recorded."""
from collections import deque
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from portal_auto.browser.auth_state import authentication_state
from portal_auto.browser.launch import launch_page
from portal_auto.browser.locators import LocatorSpec, choose_locator
from portal_auto.browser.snapshot import REF_ATTRIBUTE, Snapshot, take_snapshot
from portal_auto.browser.worker import BrowserWorker, on_worker
from portal_auto.browser.origin import is_allowed, origin_of
from portal_auto.inputs import is_input_env
from portal_auto.literals import ENV_PLACEHOLDER, env_names, resolve_env
from portal_auto.recording import (
    CHOOSER, FILE_INPUT, ActionRecorder, AssertText, Click, Fill, Goto, PressKey, Select, UploadFile, WaitFor,
)

ACTION_TIMEOUT_MS = 5000
MAX_WAIT_SECONDS = 480  # must stay below the Run timeout so a stuck wait cannot outlive the Run
SECRET_NAME_MARKER = "PASS"  # placeholders named like PORTAL_PASSWORD / DEMO_PASS hold passwords
ALLOWED_KEYS = ("Enter", "Tab", "Escape", "ArrowDown", "ArrowUp")  # navigation only: no shortcuts or deletions
TRACE_ACTIONS = 12  # how many recent tool calls a failed Generation reports
TRACE_LINE_CHARS = 240
MIN_INPUT_LITERAL_CHARS = 3  # shorter Input values are too likely to be typed for other reasons
NOT_AN_INPUT_FILE = (
    "upload_file takes the placeholder of a file or image Input exactly as written, e.g. {{ENV:PORTAL_INPUT_REPORT}}"
)


def _is_secret_name(name: str) -> bool:
    return not is_input_env(name) and SECRET_NAME_MARKER in name.upper()


def _input_literals(secrets: Mapping[str, str], input_files: Collection[str]) -> dict[str, str]:
    """Each Input value (text, file path and file name) the agent must not type literally, with its env name."""
    literals: dict[str, str] = {}
    for name, value in secrets.items():
        if is_input_env(name):
            forms = (value, Path(value).name) if value in input_files else (value,)
            literals.update({form: name for form in forms if len(form) >= MIN_INPUT_LITERAL_CHARS})
    return literals


class BrowserSession:
    def __init__(
        self, recorder: ActionRecorder, start_url: str, secrets: Mapping[str, str], allowed_hosts: Sequence[str] = (),
        storage_state_path: str | None = None, browser_channel: str | None = None, input_files: Collection[str] = (),
    ):
        """`start_url` fixes the one origin the agent may visit; `secrets` is exactly the set of
        values `{{ENV:NAME}}` placeholders may resolve to; `allowed_hosts` are operator-declared extra hosts;
        `storage_state_path` is a Saved Session file the browser starts from (a path, not a secret); `browser_channel` is the Playwright channel to launch, or None for bundled Chromium;
        `input_files` are the paths of this Run's uploaded Inputs, the only files upload_file may send."""
        self.recorder = recorder
        self.secrets = dict(secrets)
        self._input_files = frozenset(input_files)
        self._input_literals = _input_literals(self.secrets, self._input_files)
        self._origin = origin_of(start_url)
        self._allowed_hosts = tuple(allowed_hosts)
        self._storage_state_path = storage_state_path
        self._browser_channel = browser_channel
        self.worker = BrowserWorker()
        self._snapshot: Snapshot | None = None
        self._trace: deque[str] = deque(maxlen=TRACE_ACTIONS)
        self._playwright = self._browser = self._page = None

    @on_worker
    def start(self, url: str, headless: bool = True) -> None:
        self._check_navigable(url)
        self._playwright = sync_playwright().start()
        self._browser, _, self._page = launch_page(
            self._playwright, headless, self._storage_state_path, self._browser_channel
        )
        self._open(url)

    @on_worker
    def stop(self) -> None:
        if self._browser:
            self.recorder.final_page_url = self._page.url
            self._browser.close()
            self._playwright.stop()

    def close(self) -> None:
        self.stop()
        self.worker.shutdown()

    @on_worker
    def goto(self, url: str) -> None:
        self._open(url)

    @on_worker
    def snapshot(self) -> str:
        self._page.wait_for_load_state("domcontentloaded", timeout=ACTION_TIMEOUT_MS)
        self._snapshot = take_snapshot(self._page)
        return self._snapshot.render()

    # Each element action returns the locator its script step uses, chosen on the live page before acting.
    @on_worker
    def click(self, ref: int) -> LocatorSpec:
        element, target = self._target(ref)
        locator = choose_locator(element, self._page, target)
        target.click(timeout=ACTION_TIMEOUT_MS)
        self._page.wait_for_load_state("domcontentloaded")
        self.recorder.add(Click(locator.to_code()))
        return locator

    @on_worker
    def fill(self, ref: int, value: str) -> LocatorSpec:
        element, target = self._target(ref)
        self._check_value_allowed(element, value)
        locator = choose_locator(element, self._page, target)
        target.fill(resolve_env(value, self.secrets), timeout=ACTION_TIMEOUT_MS)
        self.recorder.add(Fill(locator.to_code(), value))
        return locator

    @on_worker
    def press_key(self, ref: int, key: str) -> LocatorSpec:
        if key not in ALLOWED_KEYS:
            raise ValueError(f"Key must be one of {', '.join(ALLOWED_KEYS)}, got {key!r}")
        element, target = self._target(ref)
        self._check_on_portal()
        locator = choose_locator(element, self._page, target)
        target.press(key, timeout=ACTION_TIMEOUT_MS)
        self.recorder.add(PressKey(locator.to_code(), key))
        return locator

    @on_worker
    def select_option(self, ref: int, option: str) -> LocatorSpec:
        element, target = self._target(ref)
        self._check_value_allowed(element, option)
        locator = choose_locator(element, self._page, target)
        target.select_option(label=resolve_env(option, self.secrets), timeout=ACTION_TIMEOUT_MS)
        self.recorder.add(Select(locator.to_code(), option))
        return locator

    @on_worker
    def upload_file(self, ref: int, value: str) -> LocatorSpec:
        """Sends a file Input: set on a file input directly, or through the file chooser the element opens."""
        element, target = self._target(ref)
        self._check_value_allowed(element, value)
        path = self._input_file(value)
        via = FILE_INPUT if element.tag == "input" and element.type == "file" else CHOOSER
        locator = choose_locator(element, self._page, target)
        if via == FILE_INPUT:
            target.set_input_files(path, timeout=ACTION_TIMEOUT_MS)
        else:
            with self._page.expect_file_chooser(timeout=ACTION_TIMEOUT_MS) as chooser:
                target.click(timeout=ACTION_TIMEOUT_MS)
            chooser.value.set_files(path, timeout=ACTION_TIMEOUT_MS)
        self.recorder.add(UploadFile(locator.to_code(), value, via))
        return locator

    @on_worker
    def assert_text(self, text: str) -> bool:
        found = text in self._page.inner_text("body")
        if found:
            self.recorder.add(AssertText(text))
        return found

    @on_worker
    def wait_until_present(self, text: str, timeout_seconds: int = 300) -> None:
        """Block until a field label, placeholder or text containing `text` is visible."""
        if not 1 <= timeout_seconds <= MAX_WAIT_SECONDS:
            raise ValueError(f"Wait at most {MAX_WAIT_SECONDS} seconds (and at least 1), got {timeout_seconds}")
        action = WaitFor(text, timeout_seconds * 1000)
        page = self._page
        target = page.get_by_label(text).or_(page.get_by_placeholder(text)).or_(page.get_by_text(text)).first
        try:
            target.wait_for(timeout=action.timeout_ms)
        except PlaywrightTimeout:
            raise ValueError(f"Nothing matching {text!r} appeared within {timeout_seconds} seconds") from None
        self.recorder.add(action)

    @on_worker
    def authentication_state(self) -> str:
        """`authenticated`, `unauthenticated` or `unknown` for the page the browser is on (see browser/auth_state.py)."""
        return authentication_state(self._page, self._origin[1])

    def element_description(self, ref: int) -> str | None:
        """What the latest snapshot shows for this ref, or None when no such element is listed."""
        element = self._snapshot.elements.get(int(ref)) if self._snapshot else None
        return element.description if element else None

    def note(self, line: str) -> None:
        """Remember one tool call (name, arguments and a short result) for the report of a failed Generation."""
        self._trace.append(line[:TRACE_LINE_CHARS])

    def trace_lines(self) -> list[str]:
        return list(self._trace)

    def _check_navigable(self, url: str) -> None:
        if not is_allowed(url, self._origin, self._allowed_hosts):
            raise ValueError("Navigation outside the Portal origin is not allowed")

    def _check_on_portal(self) -> None:
        if not is_allowed(self._page.url, self._origin, self._allowed_hosts):
            raise ValueError("The page has left the Portal origin; filling is refused")

    def _check_value_allowed(self, element, value: str) -> None:
        self._check_on_portal()
        if value in self._input_literals:
            raise ValueError(
                f"That is the value of an Input; pass its placeholder {{{{ENV:{self._input_literals[value]}}}}} "
                "instead, so the script works with the next Run's values"
            )
        if any(_is_secret_name(name) for name in env_names(value)) and not (
            element.tag == "input" and element.type == "password"
        ):
            raise ValueError("Password placeholders may only be filled into password inputs")

    def _input_file(self, value: str) -> str:
        match = ENV_PLACEHOLDER.fullmatch(value)
        if not match or not is_input_env(match.group(1)):
            raise ValueError(NOT_AN_INPUT_FILE)
        path = resolve_env(value, self.secrets)
        if path not in self._input_files:
            raise ValueError(NOT_AN_INPUT_FILE)
        return path

    def _open(self, url: str) -> None:
        self._check_navigable(url)
        self._page.goto(url)
        self.recorder.add(Goto(url))

    def _target(self, ref: int):
        if self._snapshot is None or int(ref) not in self._snapshot.elements:
            raise ValueError(f"No element [{ref}] in the latest snapshot")
        return self._snapshot.elements[int(ref)], self._page.locator(f'[{REF_ATTRIBUTE}="{int(ref)}"]')
