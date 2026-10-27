"""What the agent ends with decides the Run: DONE, BLOCKED with a reason, or anything else (a failure with a trace)."""
import pytest

import portal_auto.agent.runner as runner
from portal_auto.agent.runner import AgentRunFailed
from portal_auto.config import Settings
from portal_auto.workflow import Workflow

TRACE = ["goto(url='https://x.test') -> Navigated.", "get_page_snapshot() -> URL: https://x.test/ | Title: Welcome"]


class Plain:
    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs


class ScriptedCrew:
    answer: object = "DONE"
    error: Exception | None = None
    task_text = ""

    def __init__(self, *args, **kwargs):
        pass

    def kickoff(self):
        if ScriptedCrew.error:
            raise ScriptedCrew.error
        return ScriptedCrew.answer


class RecordingTask:
    description = ""

    def __init__(self, *args, **kwargs):
        RecordingTask.description = kwargs.get("description", "")
        RecordingTask.expected = kwargs.get("expected_output", "")


class FakeSession:
    secrets: dict = {}
    closed = 0

    def __init__(self, *args, **kwargs):
        pass

    def start(self, *args):
        pass

    def close(self):
        FakeSession.closed += 1

    def trace_lines(self):
        return list(TRACE)


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    ScriptedCrew.answer, ScriptedCrew.error, FakeSession.closed = "DONE", None, 0
    monkeypatch.setattr(runner, "Agent", Plain)
    monkeypatch.setattr(runner, "LLM", Plain)
    monkeypatch.setattr(runner, "Task", RecordingTask)
    monkeypatch.setattr(runner, "Crew", ScriptedCrew)
    monkeypatch.setattr(runner, "BrowserSession", FakeSession)
    monkeypatch.setattr(runner, "build_tools", lambda session: [])


def run():
    return runner.run_workflow(Workflow(name="w", url="https://x.test", steps=["s"]), Settings(), {})


@pytest.mark.parametrize("answer", ["DONE", "done.", "  DONE: every step is complete", "Done"])
def test_an_answer_that_says_done_is_a_success(answer):
    ScriptedCrew.answer = answer
    assert run() is not None
    assert FakeSession.closed == 1


def test_blocked_fails_the_run_with_the_agents_reason_and_its_trace():
    ScriptedCrew.answer = "BLOCKED: the page shows SIGN IN WITH GOOGLE instead of the dashboard"

    with pytest.raises(AgentRunFailed) as failure:
        run()

    assert "The agent was blocked: the page shows SIGN IN WITH GOOGLE instead of the dashboard" in str(failure.value)
    assert failure.value.trace == TRACE
    assert FakeSession.closed == 1


@pytest.mark.parametrize("answer", ["I could not find the Upload button", "", None, "NOT DONE"])
def test_any_other_answer_is_a_failure_not_a_silent_success(answer):
    ScriptedCrew.answer = answer

    with pytest.raises(AgentRunFailed, match="without saying DONE") as failure:
        run()

    assert failure.value.trace == TRACE


def test_an_error_inside_the_agent_keeps_its_message_and_adds_the_trace():
    ScriptedCrew.error = RuntimeError("called 'goto' with the same arguments 5 times")

    with pytest.raises(AgentRunFailed, match="same arguments 5 times") as failure:
        run()

    assert failure.value.trace == TRACE
    assert FakeSession.closed == 1


def test_the_task_tells_the_agent_how_to_report_a_page_that_does_not_match():
    run()

    assert "BLOCKED:" in RecordingTask.description
    assert "sign-in" in RecordingTask.description.lower()
    assert "BLOCKED" in RecordingTask.expected


def test_blocked_is_only_for_pages_the_steps_do_not_explain():
    run()

    assert "unless a Step tells you how to handle it" in RecordingTask.description


def test_crewais_error_does_not_repeat_the_whole_task_prompt():
    prompt = "The browser is already open on https://x.test.\nPerform these steps in order:\n1. Click Upload\n" * 20
    ScriptedCrew.error = RuntimeError(
        f"Tool 'goto' failed during '{prompt}': Agent 'Browser automation operator' called 'goto' with the same "
        "arguments 5 times and got the same result each time. Stopped because it is not making progress."
    )

    with pytest.raises(AgentRunFailed) as failure:
        run()

    message = str(failure.value)
    assert "Perform these steps" not in message
    assert message.startswith("Tool 'goto' failed")
    assert "called 'goto' with the same arguments 5 times" in message


def test_an_ordinary_error_message_is_left_alone():
    ScriptedCrew.error = RuntimeError("connection reset")
    with pytest.raises(AgentRunFailed, match="connection reset"):
        run()


def test_the_agent_is_told_not_to_change_settings_no_step_mentions():
    run()

    assert "Do not change any setting, checkbox or option that no Step mentions" in RecordingTask.description
