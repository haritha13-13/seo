"""Carry out Steps one at a time: observe the page, let a model assess the Step, validate what it proposes, act,
then verify the visible result before moving on. A failed attempt is retried from a fresh observation, a
limited number of times, so a renamed control or a slow page is recovered from without repeating work.

The model is only a `decide` function, so the loop is tested without one; `llm_decider` is the real one."""
import json
import time
from collections.abc import Callable, Sequence
from typing import Literal, Protocol

from crewai import LLM
from pydantic import BaseModel, ValidationError

from portal_auto.config import Settings

MAX_ATTEMPTS = 2
VERIFY_POLLS = 5
VERIFY_INTERVAL_SECONDS = 1.0


class StepFailed(RuntimeError):
    """The Step could not be completed; the message says why and `step` which one."""

    def __init__(self, step: str, reason: str):
        super().__init__(f"{step!r}: {reason}")
        self.step = step


class Decision(BaseModel):
    action: Literal["click", "fill", "goto", "none"] = "none"
    ref: int | None = None
    value: str | None = None
    url: str | None = None
    expected_text: str | None = None  # text that must be on the page once the action worked
    already_done: bool = False  # the page already shows this Step's result: do nothing
    uncertain: bool = False  # no control clearly matches: stop and say why instead of guessing
    explanation: str = ""


class Browser(Protocol):
    def snapshot(self) -> str: ...
    def element_description(self, ref: int) -> str | None: ...
    def click(self, ref: int): ...
    def fill(self, ref: int, value: str): ...
    def goto(self, url: str) -> None: ...
    def assert_text(self, text: str) -> bool: ...


Decider = Callable[[str, str, str | None], Decision]  # (step, snapshot, what went wrong last time) -> Decision


def run_steps(
    browser: Browser,
    steps: Sequence[str],
    decide: Decider,
    max_attempts: int = MAX_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    for step in steps:
        run_step(browser, step, decide, max_attempts, sleep)


def run_step(browser: Browser, step: str, decide: Decider, max_attempts: int = MAX_ATTEMPTS,
             sleep: Callable[[float], None] = time.sleep) -> None:
    problem: str | None = None
    for _ in range(max_attempts):
        decision = decide(step, browser.snapshot(), problem)
        if decision.already_done:
            return  # nothing is repeated: a duplicate upload or post is worse than a stopped Run
        if decision.uncertain or decision.action == "none":
            raise StepFailed(step, decision.explanation or "the model found no control for this Step")
        problem = _problem_with(browser, decision)
        if problem is None:
            try:
                _act(browser, decision)
            except Exception as error:
                problem = f"the action failed: {str(error)[:200]}"
        if problem is None and (problem := _unverified(browser, decision, sleep)) is None:
            return
    raise StepFailed(step, f"{problem} (gave up after {max_attempts} attempts)")


def _problem_with(browser: Browser, decision: Decision) -> str | None:
    """Reject a proposal that points at nothing, before anything touches the page."""
    if decision.action in ("click", "fill") and (decision.ref is None or browser.element_description(decision.ref) is None):
        return f"element [{decision.ref}] is not in the latest snapshot"
    if decision.action == "fill" and decision.value is None:
        return "a fill needs a value"
    if decision.action == "goto" and not decision.url:
        return "a goto needs a url"
    return None


def _act(browser: Browser, decision: Decision) -> None:
    if decision.action == "click":
        browser.click(decision.ref)
    elif decision.action == "fill":
        browser.fill(decision.ref, decision.value)
    else:
        browser.goto(decision.url)


def _unverified(browser: Browser, decision: Decision, sleep: Callable[[float], None]) -> str | None:
    """None once the expected text is on the page (polled, for slow pages); otherwise what was missing."""
    if not decision.expected_text:
        return None
    for poll in range(VERIFY_POLLS):
        if browser.assert_text(decision.expected_text):
            return None
        if poll < VERIFY_POLLS - 1:
            sleep(VERIFY_INTERVAL_SECONDS)
    return f"{decision.expected_text!r} did not appear after the action"


_PROMPT = """You carry out one Step on a web page. Reply with ONE JSON object only, with these keys:
action ("click", "fill", "goto" or "none"), ref (number from the list below), value (text to type, for fill),
url (for goto), expected_text (text that will be visible once the action worked), already_done (true when the page
already shows this Step's result), uncertain (true when no control clearly matches), explanation.
Use only refs from the page below. Never click anything that submits, publishes, buys or deletes unless the Step
says so. Placeholders like {{{{title}}}} or {{{{ENV:NAME}}}} are passed exactly as written, never replaced.

Step: {step}
{problem}
Page:
{snapshot}"""


def llm_decider(settings: Settings) -> Decider:
    llm = LLM(model=settings.llm_model, base_url=settings.llm_base_url, api_key=settings.llm_api_key, temperature=0)

    def decide(step: str, snapshot: str, problem: str | None) -> Decision:
        prompt = _PROMPT.format(step=step, snapshot=snapshot,
                                problem=f"Last attempt went wrong: {problem}" if problem else "")
        reply = str(llm.call([{"role": "user", "content": prompt}]))
        try:
            return Decision.model_validate(json.loads(reply[reply.index("{"):reply.rindex("}") + 1]))
        except (ValueError, ValidationError):
            return Decision(uncertain=True, explanation=f"the model did not answer with a valid decision: {reply[:200]}")

    return decide
