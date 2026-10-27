"""The one place a browser is started, shared by Generation (BrowserSession)."""
import shutil
from collections.abc import Mapping

CHANNEL_ENV = "PORTAL_BROWSER_CHANNEL"
REAL_CHROME = "chrome"


def browser_channel_env(chrome_command: str) -> dict[str, str]:
    """Real Chrome's fingerprint is trusted more by portals than bundled Chromium's; the worker decides once for both."""
    return {CHANNEL_ENV: REAL_CHROME} if shutil.which(chrome_command) else {}


def channel_from_env(env: Mapping[str, str]) -> str | None:
    return env.get(CHANNEL_ENV) or None


def launch_page(playwright, headless: bool, storage_state_path: str | None = None, channel: str | None = None):
    launch_options: dict = {"headless": headless}
    context_options: dict = {}
    if storage_state_path:
        context_options["storage_state"] = str(storage_state_path)
    if channel:
        launch_options["channel"] = channel
    browser = playwright.chromium.launch(**launch_options)
    context = browser.new_context(**context_options)
    return browser, context, context.new_page()
