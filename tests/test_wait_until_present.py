"""A person may need to act in the browser (e.g. type an email code). The agent waits, and the script waits too."""
import subprocess
import sys
import pytest
from site_server import serve

from portal_auto.agent.tools import build_tools
from portal_auto.browser.session import BrowserSession
from portal_auto.codegen.renderer import render_script
from portal_auto.recording import ActionRecorder, Goto, WaitFor
from portal_auto.workflow import Workflow

PAGE = """
<p>Enter the code we emailed you</p>
<script>setTimeout(() => {
  document.body.insertAdjacentHTML('beforeend', '<label for="p">Password</label><input id="p" type="password">');
}, 1200);</script>
"""


@pytest.fixture
def site():
    server, base_url = serve({"/": PAGE})
    yield f"{base_url}/"
    server.shutdown()


@pytest.fixture
def session(site):
    recorder = ActionRecorder()
    browser = BrowserSession(recorder, site, {})
    browser.start(site)
    yield browser
    browser.close()


def test_waits_until_the_text_appears_and_records_the_wait(session):
    session.wait_until_present("Password", timeout_seconds=15)

    waits = [a for a in session.recorder.actions if isinstance(a, WaitFor)]
    assert waits == [WaitFor("Password", 15000)]


def test_gives_up_with_a_clear_error_and_records_nothing_when_it_never_appears(session):
    with pytest.raises(ValueError, match="Nothing matching 'Never shown' appeared within 1 seconds"):
        session.wait_until_present("Never shown", timeout_seconds=1)

    assert not [a for a in session.recorder.actions if isinstance(a, WaitFor)]


def test_the_agent_tool_waits_and_reports_back_in_words(session):
    tools = {tool.name: tool for tool in build_tools(session)}

    assert "appeared" in tools["wait_until_present"].run(text="Password", timeout_seconds=15)
    assert "ERROR" in tools["wait_until_present"].run(text="Never shown", timeout_seconds=1)


def test_the_wait_is_capped_so_it_cannot_outlive_the_run(session):
    with pytest.raises(ValueError, match="at most"):
        session.wait_until_present("Password", timeout_seconds=100000)


def test_the_generated_script_performs_the_same_wait(site, tmp_path):
    workflow = Workflow(name="wait_demo", url=site, steps=["x"])
    script = tmp_path / "script.py"
    script.write_text(render_script(workflow, [Goto(site), WaitFor("Password", 15000)], allowed_env=()))

    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60)

    assert result.returncode == 0, result.stderr
