"""launch_page is the one place a browser is started for a Run, with or without a Saved Session."""
import os

from portal_auto.browser.launch import CHANNEL_ENV, browser_channel_env, channel_from_env, launch_page


class FakePlaywright:
    def __init__(self):
        self.launch_kwargs = self.context_kwargs = None
        self.chromium = self

    def launch(self, **kwargs):
        self.launch_kwargs = kwargs
        return self

    def new_context(self, **kwargs):
        self.context_kwargs = kwargs
        return self

    def new_page(self):
        return "page"


def test_without_a_channel_it_uses_bundled_chromium_and_a_plain_context():
    playwright = FakePlaywright()
    browser, context, page = launch_page(playwright, True)
    assert playwright.launch_kwargs == {"headless": True}
    assert playwright.context_kwargs == {}
    assert (browser, context, page) == (playwright, playwright, "page")


def test_with_a_channel_and_a_state_file_it_uses_that_channel_and_loads_the_state():
    playwright = FakePlaywright()
    launch_page(playwright, False, "/tmp/state.json", channel="chrome")
    assert playwright.launch_kwargs == {"headless": False, "channel": "chrome"}
    assert playwright.context_kwargs == {"storage_state": "/tmp/state.json"}


def test_with_a_state_file_and_no_channel_it_uses_bundled_chromium():
    playwright = FakePlaywright()
    launch_page(playwright, True, "/tmp/state.json")
    assert playwright.launch_kwargs == {"headless": True}
    assert playwright.context_kwargs == {"storage_state": "/tmp/state.json"}


def test_the_channel_env_names_chrome_only_when_the_configured_command_is_installed():
    assert browser_channel_env("python3") == {CHANNEL_ENV: "chrome"}
    assert browser_channel_env("no-such-browser-command") == {}


def test_generation_and_the_script_pick_the_same_channel_for_the_same_env(monkeypatch):
    import runpy
    from portal_auto.codegen.renderer import render_script
    from portal_auto.recording import ActionRecorder, Goto
    from portal_auto.workflow import Workflow
    recorder = ActionRecorder()
    recorder.add(Goto("https://x.test"))
    code = render_script(Workflow(name="parity", url="https://x.test", steps=["s"]), recorder.actions)

    for env in ({CHANNEL_ENV: "chrome"}, {}):
        for name in (CHANNEL_ENV, "PORTAL_STORAGE_STATE"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        script = tmp_script(code)
        playwright = FakePlaywright()
        launch_page(playwright, True, "/tmp/state.json", channel=channel_from_env(os.environ))
        assert runpy.run_path(script)["CHANNEL"] == playwright.launch_kwargs.get("channel")


def tmp_script(code: str) -> str:
    import tempfile
    handle = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8")
    handle.write(code)
    handle.close()
    return handle.name
