"""Pressing a key (a tag box that adds the tag on Enter), and the final page address a script reports."""
import subprocess
import sys

import pytest
from site_server import serve

from portal_auto.agent.tools import build_tools
from portal_auto.browser.session import BrowserSession
from portal_auto.codegen.renderer import render_script
from portal_auto.recording import ActionRecorder, Fill, Goto, PressKey
from portal_auto.workflow import Workflow

PAGE = """
<label for="tags">Tags</label><input id="tags" type="text">
<script>
document.getElementById('tags').addEventListener('keydown', event => {
  if (event.key === 'Enter') fetch('/tag-added?value=' + encodeURIComponent(event.target.value));
});
</script>
"""
FINAL = "<p>published</p>"


@pytest.fixture
def requested():
    return []


@pytest.fixture
def site(requested):
    server, base_url = serve({"/": PAGE, "/final": FINAL}, requested)
    yield f"{base_url}/"
    server.shutdown()


@pytest.fixture
def session(site):
    browser = BrowserSession(ActionRecorder(), site, {})
    browser.start(site)
    browser.snapshot()
    yield browser
    browser.close()


def tags_ref(session) -> int:
    return next(e.ref for e in session._snapshot.elements.values() if e.id == "tags")


def wait_until_requested(requested, path_prefix, tries=40):
    import time
    for _ in range(tries):
        if any(path.startswith(path_prefix) for path in requested):
            return True
        time.sleep(0.1)
    return False


def test_pressing_enter_in_a_field_reaches_the_page_and_is_recorded(session, requested):
    session.fill(tags_ref(session), "automation")
    session.press_key(tags_ref(session), "Enter")

    assert wait_until_requested(requested, "/tag-added?value=automation")
    assert PressKey(session.recorder.actions[-1].locator, "Enter") == session.recorder.actions[-1]


@pytest.mark.parametrize("key", ["Delete", "F5", "Control+A", "Backspace", "enter ", ""])
def test_only_a_few_navigation_keys_may_be_pressed(session, key):
    with pytest.raises(ValueError, match="Key must be one of"):
        session.press_key(tags_ref(session), key)
    assert not [a for a in session.recorder.actions if isinstance(a, PressKey)]


def test_the_agent_tool_presses_the_key_and_reports_errors_in_words(session, requested):
    tools = {tool.name: tool for tool in build_tools(session)}
    session.fill(tags_ref(session), "invoice")

    assert "Pressed" in tools["press_key"].run(ref=tags_ref(session), key="Enter")
    assert "ERROR" in tools["press_key"].run(ref=tags_ref(session), key="F5")
    assert wait_until_requested(requested, "/tag-added?value=invoice")


def test_the_rendered_script_presses_the_same_key(site, requested, tmp_path):
    workflow = Workflow(name="press_demo", url=site, steps=["x"])
    actions = [Goto(site), Fill('page.get_by_label("Tags", exact=True)', "from-script"),
               PressKey('page.get_by_label("Tags", exact=True)', "Enter")]
    script = tmp_path / "script.py"
    script.write_text(render_script(workflow, actions, allowed_env=()))

    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60)

    assert result.returncode == 0, result.stderr
    assert wait_until_requested(requested, "/tag-added?value=from-script")


def test_a_script_reports_the_final_page_address(site, tmp_path):
    workflow = Workflow(name="final_demo", url=site, steps=["x"])
    script = tmp_path / "script.py"
    script.write_text(render_script(workflow, [Goto(site + "final")], allowed_env=()))

    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60)

    assert result.returncode == 0, result.stderr
    assert f"Final page: {site}final" in result.stdout


def test_the_recording_keeps_the_page_the_live_browser_ended_on(site):
    browser = BrowserSession(ActionRecorder(), site, {})
    browser.start(site)
    browser.goto(site + "final")
    browser.close()

    assert browser.recorder.final_page_url == f"{site}final"
