"""Let an LLM agent look around a website and propose the Steps of a Workflow for a goal.

The explorer only reads: it gets the snapshot, goto and click tools, and never fill, select_option or
upload_file, so it cannot type into forms or send files. Its prompt also forbids clicking anything that
submits, publishes, buys or deletes. The Steps it returns are a draft for a person to review and edit."""
import re
from collections.abc import Sequence

from crewai import LLM, Agent, Crew, Task

from portal_auto.agent.runner import AgentRunFailed, _without_prompt
from portal_auto.agent.tools import build_tools
from portal_auto.browser.session import BrowserSession
from portal_auto.config import Settings
from portal_auto.recording import ActionRecorder

EXPLORER_TOOLS = ("get_page_snapshot", "click", "goto")
_NUMBERED_STEP = re.compile(r"^\s*\d+[.)]\s+(.+?)\s*$")


def parse_steps(answer: object) -> list[str]:
    """The numbered lines of the agent's answer, without their numbers."""
    return [match.group(1) for line in str(answer or "").splitlines() if (match := _NUMBERED_STEP.match(line))]


def explore_site(
    url: str,
    goal: str,
    settings: Settings,
    headless: bool = True,
    allowed_hosts: Sequence[str] = (),
    storage_state_path: str | None = None,
    browser_channel: str | None = None,
    verbose: bool = False,
) -> list[str]:
    """Open `url`, look around for `goal`, and return the proposed Steps (plain sentences, in order)."""
    session = BrowserSession(ActionRecorder(), url, {}, allowed_hosts, storage_state_path, browser_channel)
    session.start(url, headless)
    try:
        agent = Agent(
            role="Website explorer",
            goal="Find out how a task is done on a website and write it down as clear steps",
            backstory=(
                "You look at a website only through the provided tools. Call get_page_snapshot before choosing an "
                "element, use the ref numbers it shows, and re-snapshot after every click. You never type into "
                "fields, and you never click anything that submits a form, publishes, posts, buys, sends or "
                "deletes: you only open pages, menus and links to see what is there."
            ),
            tools=[tool for tool in build_tools(session) if tool.name in EXPLORER_TOOLS],
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
                f"The browser is already open on {url}.\n"
                f"Goal: {goal}\n"
                "Explore the site just far enough to learn which pages, buttons and fields lead to this goal. "
                "Then reply with the Steps a person would follow, as a numbered list, one action per line, each "
                "naming the control by the text visible on the page (for example '3. Click the Upload button in the "
                "top right'). Where a value is typed or a file is chosen, write a placeholder such as {{title}} or "
                "{{file:file}} instead of a real value. Finish with a 'Verify' step that names text that appears "
                "when the goal is reached. If the goal cannot be reached without signing in or without a "
                "forbidden action, reply 'BLOCKED: ' followed by what you see."
            ),
            expected_output="A numbered list of Steps, or BLOCKED: <what the page shows>",
            agent=agent,
        )
        try:
            answer = Crew(agents=[agent], tasks=[task], verbose=verbose).kickoff()
        except Exception as error:
            raise AgentRunFailed(_without_prompt(str(error)), session.trace_lines()) from None
    finally:
        session.close()
    text = str(answer or "").strip()
    if text.upper().startswith("BLOCKED"):
        raise AgentRunFailed(f"The explorer was blocked: {text[len('BLOCKED'):].lstrip(' :-')}", session.trace_lines())
    if not (steps := parse_steps(text)):
        raise AgentRunFailed(f"The explorer gave no numbered steps: {text[:300] or '(no answer)'}", session.trace_lines())
    return steps
