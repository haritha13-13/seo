"""The explorer proposes Steps, and may only look: it is never given a tool that types or uploads."""
import pytest

import portal_auto.agent.explorer as explorer
from portal_auto.agent.runner import AgentRunFailed
from portal_auto.config import Settings


class _Fake:
    answer = "1. Click About\n2) Click Contact\nnot a step\n3. Verify the text 'Contact us' appears"
    tools_given: list = []

    def __init__(self, *args, **kwargs):
        _Fake.tools_given = kwargs.get("tools", _Fake.tools_given)

    def kickoff(self):
        return _Fake.answer


class _Tool:
    def __init__(self, name):
        self.name = name


class _FakeSession:
    def __init__(self, *args, **kwargs):
        pass

    def start(self, *args):
        pass

    def close(self):
        pass

    def trace_lines(self):
        return []


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    for name in ("Agent", "Crew", "Task", "LLM"):
        monkeypatch.setattr(explorer, name, _Fake)
    monkeypatch.setattr(explorer, "BrowserSession", _FakeSession)
    names = ("get_page_snapshot", "click", "fill", "press_key", "select_option", "upload_file", "goto",
             "assert_text", "wait_until_present")
    monkeypatch.setattr(explorer, "build_tools", lambda session: [_Tool(n) for n in names])
    _Fake.answer = "1. Click About\n2) Click Contact\nnot a step\n3. Verify the text 'Contact us' appears"


def test_the_numbered_lines_of_the_answer_become_the_steps():
    steps = explorer.explore_site("https://x.test", "find contact", Settings())
    assert steps == ["Click About", "Click Contact", "Verify the text 'Contact us' appears"]


def test_the_explorer_is_only_given_tools_that_read_and_navigate():
    explorer.explore_site("https://x.test", "find contact", Settings())
    assert [tool.name for tool in _Fake.tools_given] == ["get_page_snapshot", "click", "goto"]


def test_a_blocked_answer_fails_with_what_the_page_showed():
    _Fake.answer = "BLOCKED: a sign-in form"
    with pytest.raises(AgentRunFailed, match="a sign-in form"):
        explorer.explore_site("https://x.test", "goal", Settings())


def test_an_answer_without_numbered_steps_fails():
    _Fake.answer = "I looked around and it seems fine."
    with pytest.raises(AgentRunFailed, match="no numbered steps"):
        explorer.explore_site("https://x.test", "goal", Settings())
