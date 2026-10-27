"""The agent runner must not echo page text or tool arguments to the worker's stdout."""
import portal_auto.agent.runner as runner
from portal_auto.config import Settings
from portal_auto.workflow import Workflow


class _Spy:
    verbose_flags: list = []

    def __init__(self, *args, **kwargs):
        _Spy.verbose_flags.append(kwargs.get("verbose"))

    def kickoff(self):
        return "DONE"


class _FakeSession:
    secrets: dict = {}
    allowed_hosts: list = []
    storage_state_path = None
    browser_channel = None

    def __init__(self, recorder, start_url, secrets, allowed_hosts=(), storage_state_path=None, browser_channel=None,
                 input_files=()):
        self.seen = (start_url, dict(secrets))
        _FakeSession.storage_state_path = storage_state_path
        _FakeSession.browser_channel = browser_channel
        _FakeSession.allowed_hosts = list(allowed_hosts)

    def start(self, *args):
        pass

    def close(self):
        pass


def test_verbose_output_is_off_by_default(monkeypatch):
    _Spy.verbose_flags = []
    monkeypatch.setattr(runner, "Agent", _Spy)
    monkeypatch.setattr(runner, "Crew", _Spy)
    monkeypatch.setattr(runner, "Task", _Spy)
    monkeypatch.setattr(runner, "LLM", _Spy)
    monkeypatch.setattr(runner, "BrowserSession", _FakeSession)
    monkeypatch.setattr(runner, "build_tools", lambda session: [])
    workflow = Workflow(name="w", url="https://x.test", steps=["s"])
    runner.run_workflow(workflow, Settings(), {})
    assert {flag for flag in _Spy.verbose_flags if flag is not None} == {False}


def test_declared_hosts_are_handed_to_the_browser_session(monkeypatch):
    for name in ("Agent", "Crew", "Task", "LLM"):
        monkeypatch.setattr(runner, name, _Spy)
    monkeypatch.setattr(runner, "BrowserSession", _FakeSession)
    monkeypatch.setattr(runner, "build_tools", lambda session: [])
    workflow = Workflow(name="w", url="https://x.test", steps=["s"], allowed_hosts=["adobe.com"])
    runner.run_workflow(workflow, Settings(), {})
    assert _FakeSession.allowed_hosts == ["adobe.com"]


def test_storage_state_path_is_handed_to_the_browser_session(monkeypatch):
    for name in ("Agent", "Crew", "Task", "LLM"):
        monkeypatch.setattr(runner, name, _Spy)
    monkeypatch.setattr(runner, "BrowserSession", _FakeSession)
    monkeypatch.setattr(runner, "build_tools", lambda session: [])
    runner.run_workflow(Workflow(name="w", url="https://x.test", steps=["s"]), Settings(), {}, storage_state_path="/s.json")
    assert _FakeSession.storage_state_path == "/s.json"


def test_browser_channel_is_handed_to_the_browser_session(monkeypatch):
    for name in ("Agent", "Crew", "Task", "LLM"):
        monkeypatch.setattr(runner, name, _Spy)
    monkeypatch.setattr(runner, "BrowserSession", _FakeSession)
    monkeypatch.setattr(runner, "build_tools", lambda session: [])
    runner.run_workflow(Workflow(name="w", url="https://x.test", steps=["s"]), Settings(), {}, browser_channel="chrome")
    assert _FakeSession.browser_channel == "chrome"
