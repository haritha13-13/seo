"""The agent's recent actions are remembered, so a failed Run can say where it got stuck."""
import pytest
from site_server import serve

from portal_auto.agent.tools import build_tools
from portal_auto.browser.session import TRACE_ACTIONS, BrowserSession
from portal_auto.recording import ActionRecorder

PAGE = "<title>Dashboard</title><h1>Welcome back</h1><a href='/next'>Next</a>"


@pytest.fixture
def session():
    server, base_url = serve({"/": PAGE})
    browser = BrowserSession(ActionRecorder(), base_url, {})
    browser.start(base_url + "/")
    yield browser, base_url
    browser.close()
    server.shutdown()


def tools_of(browser):
    return {tool.name: tool for tool in build_tools(browser)}


def test_each_tool_call_is_remembered_with_its_arguments_and_a_short_result(session):
    browser, base = session
    tools = tools_of(browser)

    tools["get_page_snapshot"].run()
    tools["goto"].run(url=base + "/")

    first, second = browser.trace_lines()
    assert first.startswith("get_page_snapshot() -> URL: " + base) and "Title: Dashboard" in first
    assert second == f"goto(url={base + '/'!r}) -> Navigated."


def test_errors_are_remembered_too(session):
    browser, _ = session
    tools_of(browser)["goto"].run(url="https://evil.example/")

    assert "ERROR" in browser.trace_lines()[-1]


def test_only_the_most_recent_actions_are_kept(session):
    browser, base = session
    tools = tools_of(browser)

    for _ in range(TRACE_ACTIONS + 5):
        tools["goto"].run(url=base + "/")

    assert len(browser.trace_lines()) == TRACE_ACTIONS


def test_a_long_result_is_shortened(session):
    browser, _ = session
    browser.note("fill(ref=1) -> " + "x" * 5000)
    assert len(browser.trace_lines()[-1]) < 300
