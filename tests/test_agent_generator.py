import os

import pytest

from portal_auto.config import Settings
from portal_auto.recording import ActionRecorder, Fill
from portal_auto.runtime.generator import AgentScriptGenerator
from portal_auto.runtime.interfaces import GenerationFailed


def generator_with(engine) -> AgentScriptGenerator:
    return AgentScriptGenerator(Settings(agent_max_iterations=7), run_workflow=engine)


def test_runs_engine_with_workflow_and_renders_a_script():
    seen = {}

    def engine(workflow, settings, secrets):
        seen.update(url=workflow.url, steps=workflow.steps, headless=workflow.headless, iterations=settings.agent_max_iterations)
        return ActionRecorder()

    result = generator_with(engine).generate("https://x.test", ["Click Go"], False, {})
    assert seen == {"url": "https://x.test", "steps": ["Click Go"], "headless": False, "iterations": 7}
    compile(result.code, "script", "exec")


def finished_on(url: str | None):
    def engine(workflow, settings, secrets):
        recorder = ActionRecorder()
        recorder.final_page_url = url
        return recorder
    return engine


def test_the_page_the_agent_ended_on_is_handed_back_with_the_script():
    result = generator_with(finished_on("https://x.test/decks/42")).generate("https://x.test", ["s"], True, {})
    assert result.final_page_url == "https://x.test/decks/42"


@pytest.mark.parametrize("url", [None, "about:blank", "javascript:alert(1)", "file:///etc/passwd", "data:text/html,x"])
def test_only_a_web_address_counts_as_the_final_page(url):
    assert generator_with(finished_on(url)).generate("https://x.test", ["s"], True, {}).final_page_url is None


def test_env_is_passed_as_a_mapping_and_never_touches_os_environ():
    seen = {}

    def engine(workflow, settings, secrets):
        seen["secrets"] = dict(secrets)
        seen["environ"] = os.environ.get("PORTAL_PASSWORD")
        return ActionRecorder()

    os.environ.pop("PORTAL_PASSWORD", None)
    env = {"PORTAL_PASSWORD": "pw1", "PORTAL_USERNAME": "u1", "PORTAL_AUTO_VAULT_KEY": "vk"}
    generator_with(engine).generate("https://x.test", ["s"], True, env)
    assert seen["secrets"] == {"PORTAL_PASSWORD": "pw1", "PORTAL_USERNAME": "u1"}
    assert seen["environ"] is None
    assert "PORTAL_PASSWORD" not in os.environ


def test_generated_script_cannot_reference_other_environment_names():
    def engine(workflow, settings, secrets):
        recorder = ActionRecorder()
        recorder.add(Fill("page.locator('#x')", "{{ENV:PORTAL_AUTO_VAULT_KEY}}"))
        return recorder

    with pytest.raises(GenerationFailed):
        generator_with(engine).generate("https://x.test", ["s"], True, {"PORTAL_PASSWORD": "pw1"})


def test_agent_failure_becomes_generation_failed_without_leaking_secrets():
    def engine(workflow, settings, secrets):
        raise RuntimeError("typed pw1 and failed")

    with pytest.raises(GenerationFailed) as caught:
        generator_with(engine).generate("https://x.test", ["s"], True, {"PORTAL_PASSWORD": "pw1"})
    assert "pw1" not in str(caught.value)
    assert "failed" in str(caught.value)


def test_failure_message_redacts_every_env_value():
    def engine(workflow, settings, secrets):
        raise RuntimeError("saw u1 and pw1")

    with pytest.raises(GenerationFailed) as caught:
        generator_with(engine).generate("https://x.test", ["s"], True, {"PORTAL_PASSWORD": "pw1", "PORTAL_USERNAME": "u1"})
    assert "u1" not in str(caught.value) and "pw1" not in str(caught.value)


def test_declared_hosts_reach_the_engine():
    seen = {}

    def engine(workflow, settings, secrets):
        seen["hosts"] = workflow.allowed_hosts
        return ActionRecorder()

    generator_with(engine).generate("https://x.test", ["s"], True, {}, allowed_hosts=["adobe.com"])
    assert seen["hosts"] == ["adobe.com"]


def test_state_path_reaches_the_engine_but_not_the_secrets():
    seen = {}

    def engine(workflow, settings, secrets, storage_state_path=None):
        seen.update(secrets=dict(secrets), path=storage_state_path)
        return ActionRecorder()

    generator_with(engine).generate("https://x.test", ["s"], True, {"PORTAL_USERNAME": "u1", "PORTAL_STORAGE_STATE": "/s.json"})
    assert seen == {"secrets": {"PORTAL_USERNAME": "u1"}, "path": "/s.json"}


def test_the_browser_channel_in_env_reaches_the_engine():
    seen = {}

    def engine(workflow, settings, secrets, browser_channel=None):
        seen["channel"] = browser_channel
        return ActionRecorder()

    generator_with(engine).generate("https://x.test", ["s"], True, {"PORTAL_BROWSER_CHANNEL": "chrome"})
    assert seen == {"channel": "chrome"}


def test_a_failed_agent_run_reports_its_reason_and_its_last_actions():
    from portal_auto.agent.runner import AgentRunFailed

    def engine(workflow, settings, secrets):
        raise AgentRunFailed("The agent was blocked: sign-in page", ["goto(url='https://x.test') -> Navigated.", "click(ref=3) -> Clicked."])

    with pytest.raises(GenerationFailed) as failure:
        generator_with(engine).generate("https://x.test", ["s"], True, {})

    message = str(failure.value)
    assert "The agent was blocked: sign-in page" in message
    assert "Last agent actions:" in message
    assert "- goto(url='https://x.test') -> Navigated." in message and "- click(ref=3) -> Clicked." in message


def test_the_last_actions_never_leak_a_credential():
    from portal_auto.agent.runner import AgentRunFailed

    def engine(workflow, settings, secrets):
        raise AgentRunFailed("failed", ["fill(ref=2) -> typed hunter2-secret"])

    with pytest.raises(GenerationFailed) as failure:
        generator_with(engine).generate("https://x.test", ["s"], True, {"PORTAL_PASSWORD": "hunter2-secret"})

    assert "hunter2-secret" not in str(failure.value)
