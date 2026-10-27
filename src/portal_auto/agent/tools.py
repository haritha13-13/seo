"""Thin crewai tool wrappers over a BrowserSession. No browser logic lives here."""
from collections.abc import Callable, Iterable
from functools import wraps

from crewai.tools import BaseTool, tool

from portal_auto.browser.locators import LocatorSpec
from portal_auto.browser.session import BrowserSession
from portal_auto.inputs import is_input_env
from portal_auto.redaction import redact

MAX_ERROR_CHARS = 300
GIST_LINES = 2  # a snapshot is summed up by its URL and title lines
GIST_CHARS = 160


def guarded(secrets: Iterable[str]) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """Return browser errors to the model as text so it can recover instead of crashing the run.
    Secret values are scrubbed from the text first."""
    def decorate(fn: Callable[..., str]) -> Callable[..., str]:
        @wraps(fn)
        def wrapper(*args, **kwargs) -> str:
            try:
                return fn(*args, **kwargs)
            except Exception as error:
                message = redact(str(error), secrets)[:MAX_ERROR_CHARS]
                return f"ERROR: {message}. Call get_page_snapshot and retry."
        return wrapper
    return decorate


def recorded(reply: str, locator: LocatorSpec) -> str:
    """Tells the agent when nothing on the page identifies its element uniquely, so the script takes it by position."""
    if not locator.positional:
        return reply
    which = "the first match" if locator.first else f"match number {locator.nth + 1}"
    return f"{reply} Note: no locator identifies this element uniquely, so the script uses {which}: {locator.to_code()}."


def _arguments(kwargs: dict) -> str:
    return ", ".join(f"{name}={value!r}" for name, value in kwargs.items())


def _gist(result: str) -> str:
    return " | ".join(line.strip() for line in str(result).splitlines()[:GIST_LINES])[:GIST_CHARS]


def traced(session: BrowserSession) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """Remember each call on the session, so a failed Generation can show where the agent got stuck."""
    def decorate(fn: Callable[..., str]) -> Callable[..., str]:
        @wraps(fn)
        def wrapper(*args, **kwargs) -> str:
            result = fn(*args, **kwargs)
            session.note(f"{fn.__name__}({_arguments(kwargs)}) -> {_gist(result)}")
            return result
        return wrapper
    return decorate


def build_tools(session: BrowserSession) -> list[BaseTool]:
    guard = guarded([value for name, value in session.secrets.items() if not is_input_env(name)])
    record = traced(session)

    def guarded_tool(fn: Callable[..., str]) -> Callable[..., str]:
        return record(guard(fn))

    @tool("get_page_snapshot")
    @guarded_tool
    def get_page_snapshot() -> str:
        """Return the current page URL, visible text and numbered interactive elements. Call this before choosing an element ref."""
        return session.snapshot()

    @tool("click")
    @guarded_tool
    def click(ref: int) -> str:
        """Click the element with this ref number from the latest snapshot."""
        return recorded("Clicked. Call get_page_snapshot to see the new page.", session.click(ref))

    @tool("fill")
    @guarded_tool
    def fill(ref: int, value: str) -> str:
        """Type text into an input by ref. For secrets and Inputs pass the placeholder exactly as written, e.g. {{ENV:DEMO_PASS}} or {{ENV:PORTAL_INPUT_TITLE}}."""
        return recorded("Filled.", session.fill(ref, value))

    @tool("press_key")
    @guarded_tool
    def press_key(ref: int, key: str) -> str:
        """Press a key in the field with this ref, e.g. Enter after typing a tag so the page adds it. Allowed keys:
        Enter, Tab, Escape, ArrowDown, ArrowUp."""
        return recorded(f"Pressed {key}. Call get_page_snapshot to see what changed.", session.press_key(ref, key))

    @tool("select_option")
    @guarded_tool
    def select_option(ref: int, option: str) -> str:
        """Choose an option by its visible label in a <select> element. For an Input pass its placeholder exactly as written, e.g. {{ENV:PORTAL_INPUT_CATEGORY}}."""
        return recorded("Selected.", session.select_option(ref, option))

    @tool("upload_file")
    @guarded_tool
    def upload_file(ref: int, value: str) -> str:
        """Upload a file or image Input. `ref` is the upload button, label or file input from the latest snapshot;
        `value` is the Input placeholder exactly as written, e.g. {{ENV:PORTAL_INPUT_REPORT}}."""
        return recorded("Uploaded. Call get_page_snapshot to see the page.", session.upload_file(ref, value))

    @tool("goto")
    @guarded_tool
    def goto(url: str) -> str:
        """Navigate the browser to a URL."""
        session.goto(url)
        return "Navigated."

    @tool("assert_text")
    @guarded_tool
    def assert_text(text: str) -> str:
        """Verify the current page contains this text. Use for every 'verify' step."""
        return "Assertion passed." if session.assert_text(text) else f"FAILED: {text!r} not found on page."

    @tool("wait_until_present")
    @guarded_tool
    def wait_until_present(text: str, timeout_seconds: int = 300) -> str:
        """Wait until a field label, placeholder or text containing `text` is visible on the page. Use it when a
        person must act in the browser first (typing an emailed code, solving a prompt): do not type anything
        yourself, name what WILL appear once they are done (e.g. "Password"). Waits up to timeout_seconds."""
        session.wait_until_present(text, timeout_seconds)
        return f"{text!r} appeared. Call get_page_snapshot to continue."

    return [get_page_snapshot, click, fill, press_key, select_option, upload_file, goto, assert_text, wait_until_present]
