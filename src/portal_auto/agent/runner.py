"""Run one workflow with an LLM agent and return what it did."""
import os
from collections.abc import Collection, Mapping, Sequence

from crewai import LLM, Agent, Crew, Task

from portal_auto.agent.tools import build_tools
from portal_auto.browser.session import BrowserSession
from portal_auto.config import Settings
from portal_auto.literals import env_names
from portal_auto.recording import ActionRecorder
from portal_auto.workflow import Workflow


class AgentRunFailed(RuntimeError):
    """The agent did not finish its Steps; `trace` lists its last tool calls."""

    def __init__(self, reason: str, trace: Sequence[str] = ()):
        super().__init__(reason)
        self.trace = list(trace)


def _without_prompt(message: str) -> str:
    """CrewAI copies the whole task prompt into some errors ("Tool 'x' failed during '<prompt>': <reason>")."""
    head, during, rest = message.partition(" failed during '")
    if not during or "': " not in rest:
        return message
    return f"{head} failed: {rest.rsplit(chr(39) + ': ', 1)[1]}"


def _verdict(answer: object) -> str | None:
    """None when the agent said DONE, otherwise why the Run must fail."""
    text = str(answer or "").strip()
    if text.upper().startswith("DONE"):
        return None
    if text.upper().startswith("BLOCKED"):
        return f"The agent was blocked: {text[len('BLOCKED'):].lstrip(' :-')}"
    return f"The agent finished without saying DONE: {text[:300] or '(no answer)'}"


def run_workflow(
    workflow: Workflow,
    settings: Settings,
    secrets: Mapping[str, str] | None = None,
    verbose: bool = False,
    storage_state_path: str | None = None,
    browser_channel: str | None = None,
    input_files: Collection[str] = (),
) -> ActionRecorder:
    """`secrets` are the values `{{ENV:NAME}}` may resolve to. When omitted (standalone CLI), they are
    read from the process environment for exactly the names the workflow's own Steps declare.
    `verbose` stays off by default: crewai's verbose output echoes page text and tool arguments."""
    if secrets is None:
        secrets = {name: os.environ[name] for step in workflow.steps for name in env_names(step) if name in os.environ}
    recorder = ActionRecorder()
    session = BrowserSession(recorder, workflow.url, secrets, workflow.allowed_hosts, storage_state_path,
                             browser_channel, input_files=input_files)
    session.start(workflow.url, workflow.headless)
    try:
        agent = Agent(
            role="Browser automation operator",
            goal="Carry out portal workflows step by step in a real browser",
            backstory=(
                "You operate a browser only through the provided tools. Call get_page_snapshot before "
                "choosing an element, use the ref numbers it shows, and re-snapshot after every click. "
                "When a Step says a person must do something manually in the browser, do not attempt it: "
                "call wait_until_present with text that will appear once they are done. "
                "Input placeholders such as {{ENV:PORTAL_INPUT_TITLE}} are passed exactly as given, never their "
                "values; a file or image Input is always sent with upload_file."
            ),
            tools=build_tools(session),
            llm=LLM(
                model=settings.llm_model,
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                temperature=0,
            ),
            max_iter=settings.agent_max_iterations,
            verbose=verbose,
        )
        task = Task(
            description=(
                f"The browser is already open on {workflow.url}.\n"
                f"Perform these steps in order:\n{workflow.numbered_steps}\n"
                "Use assert_text for any 'verify' step. Do not skip steps. Do not change any setting, checkbox or "
                "option that no Step mentions: leave everything else on the page as you found it. "
                "Reply DONE when every step is complete.\n"
                "If the page does not match what a Step expects (for example it shows a sign-in or sign-up prompt "
                "although you should already be signed in, or a control the Step names does not exist), and no "
                "Step tells you how to handle it, do NOT keep trying or open the same page again: reply 'BLOCKED: ' "
                "followed by what you see on the page. Handle it as the Step says whenever a Step does tell you how "
                "(that is unless a Step tells you how to handle it)."
            ),
            expected_output="DONE once all steps are completed, or BLOCKED: <what the page shows>",
            agent=agent,
        )
        try:
            answer = Crew(agents=[agent], tasks=[task], verbose=verbose).kickoff()
        except Exception as error:
            raise AgentRunFailed(_without_prompt(str(error)), session.trace_lines()) from None
        if (reason := _verdict(answer)) is not None:
            raise AgentRunFailed(reason, session.trace_lines())
    finally:
        session.close()
    return recorder
