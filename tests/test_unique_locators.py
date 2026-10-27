"""S10: what a Generation records must find exactly one element on replay, even when a hidden twin of the field
shares its placeholder (a tag box whose original input is hidden behind a visible token field)."""
import os
import subprocess
import sys
import time

import pytest
from site_server import serve

from portal_auto.agent.tools import build_tools
from portal_auto.browser.session import BrowserSession
from portal_auto.codegen.renderer import render_script
from portal_auto.recording import ActionRecorder, Fill, PressKey
from portal_auto.workflow import Workflow

PLACEHOLDER = "Type something and hit enter"
REPORT_ENTER = """
<script>
document.querySelectorAll('input').forEach(input => input.addEventListener('keydown', event => {
  if (event.key === 'Enter') fetch('/tag-added?field=' + input.id + '&value=' + encodeURIComponent(input.value));
}));
</script>
"""
TAG_BOX = f"""
<input id="tags" type="text" name="tags" tabindex="-1" class="form-control diable_entery" placeholder="{PLACEHOLDER}"
       style="display:none">
<input dir="auto" type="text" tabindex="0" autocomplete="off" spellcheck="false" id="tags-tokenfield"
       class="token-input tt-input" placeholder="{PLACEHOLDER}">
{REPORT_ENTER}"""
TWIN_NOTES = f"""<input placeholder="Note"><input placeholder="Note">{REPORT_ENTER}"""
HIDDEN_TWIN_FIRST = f"""<input placeholder="Note" style="display:none"><input placeholder="Note">{REPORT_ENTER}"""
REPORT_CLICKS = """
<script>
document.querySelectorAll('[data-which]').forEach(el => el.addEventListener('click', () =>
  fetch('/clicked?which=' + el.dataset.which)));
</script>
"""
HIDDEN_GOOGLE_BUTTON = f"""
<div style="display:none"><button data-which="hidden">SIGN IN WITH GOOGLE</button></div>
<div><button data-which="visible">SIGN IN WITH GOOGLE</button></div>{REPORT_CLICKS}"""
HIDDEN_TEXT_TWIN = f"""
<div style="display:none"><div onclick="" data-which="hidden">Continue with Google</div></div>
<div><div onclick="" data-which="visible">Continue with Google</div></div>{REPORT_CLICKS}"""
TWO_VISIBLE_LINKS = f"""<a href="#" data-which="first">Join now</a> <a href="#" data-which="second">Join now</a>{REPORT_CLICKS}"""


@pytest.fixture
def requested():
    return []


@pytest.fixture
def site(requested):
    server, base_url = serve({"/": TAG_BOX, "/twins": TWIN_NOTES, "/hidden-first": HIDDEN_TWIN_FIRST,
                              "/google": HIDDEN_GOOGLE_BUTTON, "/links": TWO_VISIBLE_LINKS,
                              "/text-twin": HIDDEN_TEXT_TWIN}, requested)
    yield base_url
    server.shutdown()


def open_session(url: str) -> BrowserSession:
    browser = BrowserSession(ActionRecorder(), url, {})
    browser.start(url)
    browser.snapshot()
    return browser


@pytest.fixture
def session(site):
    browser = open_session(f"{site}/")
    yield browser
    browser.close()


def first_ref(session: BrowserSession) -> int:
    return min(session._snapshot.elements)


def tag_requests(requested: list[str], value: str, tries: int = 50) -> list[str]:
    for _ in range(tries):
        matches = [path for path in requested if path.startswith("/tag-added") and f"value={value}" in path]
        if matches:
            return matches
        time.sleep(0.1)
    return []


def run_script(workflow: Workflow, session: BrowserSession, tmp_path) -> subprocess.CompletedProcess:
    script = tmp_path / "script.py"
    script.write_text(render_script(workflow, session.recorder.actions, allowed_env=()))
    return subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60, cwd=tmp_path,
                          env={**os.environ, "HEADLESS": "1"})


def test_a_field_with_a_hidden_twin_is_recorded_by_what_only_it_has(session, requested):
    ref = first_ref(session)
    session.fill(ref, "automation")
    session.press_key(ref, "Enter")

    fill, press = session.recorder.actions[1:]
    assert fill == Fill('page.locator("#tags-tokenfield")', "automation")
    assert press == PressKey('page.locator("#tags-tokenfield")', "Enter")
    assert tag_requests(requested, "automation") == ["/tag-added?field=tags-tokenfield&value=automation"]


def test_the_recorded_script_types_and_presses_enter_in_the_visible_field(site, session, requested, tmp_path):
    ref = first_ref(session)
    session.fill(ref, "from-script")
    session.press_key(ref, "Enter")
    assert tag_requests(requested, "from-script")
    requested.clear()

    result = run_script(Workflow(name="tag_box", url=f"{site}/", steps=["x"]), session, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "strict mode violation" not in result.stderr
    assert tag_requests(requested, "from-script") == ["/tag-added?field=tags-tokenfield&value=from-script"]


def test_when_nothing_is_unique_the_first_match_is_recorded_and_the_agent_is_told(site, requested, tmp_path):
    session = open_session(f"{site}/twins")
    try:
        reply = {tool.name: tool for tool in build_tools(session)}["fill"].run(ref=first_ref(session), value="first")
        session.press_key(first_ref(session), "Enter")
    finally:
        session.close()

    assert "first" in reply.lower() and 'page.get_by_placeholder("Note", exact=True).first' in reply
    assert session.recorder.actions[1] == Fill('page.get_by_placeholder("Note", exact=True).first', "first")
    requested.clear()
    result = run_script(Workflow(name="twins", url=f"{site}/twins", steps=["x"]), session, tmp_path)
    assert result.returncode == 0, result.stderr
    assert tag_requests(requested, "first")


def test_a_field_found_only_after_a_hidden_twin_is_recorded_as_the_visible_one(site, requested, tmp_path):
    session = open_session(f"{site}/hidden-first")
    try:
        session.fill(first_ref(session), "typed")
        assert session.worker.call(session._page.input_value, "input >> visible=true") == "typed"
    finally:
        session.close()

    code = 'page.get_by_placeholder("Note", exact=True).filter(visible=True)'
    assert session.recorder.actions[1] == Fill(code, "typed")
    result = run_script(Workflow(name="hidden_first", url=f"{site}/hidden-first", steps=["x"]), session, tmp_path)
    assert result.returncode == 0, result.stderr


def clicks(requested: list[str], tries: int = 50) -> list[str]:
    for _ in range(tries):
        found = [path for path in requested if path.startswith("/clicked")]
        if found:
            return found
        time.sleep(0.1)
    return []


def test_a_button_with_a_hidden_twin_needs_no_extra_narrowing_because_roles_skip_hidden_elements(site, requested, tmp_path):
    session = open_session(f"{site}/google")
    try:
        session.click(first_ref(session))
        assert clicks(requested) == ["/clicked?which=visible"]
    finally:
        session.close()

    assert session.recorder.actions[1].locator == 'page.get_by_role("button", name="SIGN IN WITH GOOGLE")'
    requested.clear()
    result = run_script(Workflow(name="google", url=f"{site}/google", steps=["x"]), session, tmp_path)
    assert result.returncode == 0, result.stderr
    assert clicks(requested) == ["/clicked?which=visible"]


def test_a_text_match_that_also_hits_a_hidden_twin_is_narrowed_to_the_visible_one(site, requested, tmp_path):
    session = open_session(f"{site}/text-twin")
    try:
        session.click(first_ref(session))
        assert clicks(requested) == ["/clicked?which=visible"]
    finally:
        session.close()

    assert session.recorder.actions[1].locator == 'page.get_by_text("Continue with Google", exact=True).filter(visible=True)'
    requested.clear()
    result = run_script(Workflow(name="text_twin", url=f"{site}/text-twin", steps=["x"]), session, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "strict mode violation" not in result.stderr
    assert clicks(requested) == ["/clicked?which=visible"]


def test_two_identical_visible_controls_are_told_apart_by_position(site, requested, tmp_path):
    session = open_session(f"{site}/links")
    try:
        second = max(session._snapshot.elements)
        reply = {tool.name: tool for tool in build_tools(session)}["click"].run(ref=second)
        assert clicks(requested) == ["/clicked?which=second"]
    finally:
        session.close()

    assert session.recorder.actions[1].locator == 'page.get_by_role("link", name="Join now").nth(1)'
    assert "match number 2" in reply
    requested.clear()
    result = run_script(Workflow(name="links", url=f"{site}/links", steps=["x"]), session, tmp_path)
    assert result.returncode == 0, result.stderr
    assert clicks(requested) == ["/clicked?which=second"]
