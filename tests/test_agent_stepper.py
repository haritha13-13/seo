"""The step loop: assess, validate, act, verify, and recover a limited number of times without repeating work."""
import pytest

from portal_auto.agent.stepper import Decision, StepFailed, run_step, run_steps


class FakeBrowser:
    def __init__(self, labels, visible_after=0):
        self.labels = labels  # ref -> description in the latest snapshot
        self.visible_after = visible_after  # assert_text is False for this many calls first
        self.clicked, self.filled, self.opened, self.asserts = [], [], [], 0

    def snapshot(self):
        return "page"

    def element_description(self, ref):
        return self.labels.get(ref)

    def click(self, ref):
        self.clicked.append(ref)

    def fill(self, ref, value):
        self.filled.append((ref, value))

    def goto(self, url):
        self.opened.append(url)

    def assert_text(self, text):
        self.asserts += 1
        return self.asserts > self.visible_after


def scripted(*decisions):
    calls = []
    queue = list(decisions)

    def decide(step, snapshot, problem):
        calls.append(problem)
        return queue.pop(0)

    decide.calls = calls
    return decide


def no_sleep(seconds):
    pass


def test_a_valid_click_is_executed_and_verified():
    browser = FakeBrowser({3: "Upload"})
    run_step(browser, "Click Upload", scripted(Decision(action="click", ref=3, expected_text="Choose file")), sleep=no_sleep)
    assert browser.clicked == [3]


def test_a_renamed_control_is_found_again_from_a_fresh_observation():
    browser = FakeBrowser({7: "Add media"})
    decide = scripted(Decision(action="click", ref=3), Decision(action="click", ref=7))
    run_step(browser, "Click Upload", decide, sleep=no_sleep)
    assert browser.clicked == [7]  # the stale ref 3 was refused before touching the page
    assert "[3] is not in the latest snapshot" in decide.calls[1]


def test_a_slow_page_is_waited_for_before_the_result_is_judged():
    browser = FakeBrowser({1: "Next"}, visible_after=3)
    run_step(browser, "Next", scripted(Decision(action="click", ref=1, expected_text="Step two")), sleep=no_sleep)
    assert browser.clicked == [1] and browser.asserts == 4


def test_an_already_completed_step_does_nothing():
    browser = FakeBrowser({1: "Upload"})
    run_step(browser, "Upload the file", scripted(Decision(already_done=True)), sleep=no_sleep)
    assert browser.clicked == [] and browser.filled == []


def test_an_action_whose_result_never_appears_is_not_repeated_beyond_the_attempt_limit():
    browser = FakeBrowser({1: "Next"}, visible_after=10 ** 6)
    decision = Decision(action="click", ref=1, expected_text="Never")
    with pytest.raises(StepFailed, match="did not appear"):
        run_step(browser, "Next", scripted(decision, decision, decision), max_attempts=2, sleep=no_sleep)
    assert browser.clicked == [1, 1]


def test_an_uncertain_model_stops_the_step_with_its_explanation():
    browser = FakeBrowser({})
    with pytest.raises(StepFailed, match="two Publish buttons"):
        run_step(browser, "Publish", scripted(Decision(uncertain=True, explanation="two Publish buttons")), sleep=no_sleep)


def test_a_failing_action_is_reported_to_the_next_attempt():
    class Flaky(FakeBrowser):
        def click(self, ref):
            if not self.clicked:
                self.clicked.append("failed")
                raise ValueError("element detached")
            super().click(ref)

    browser = Flaky({1: "Next"})
    decide = scripted(Decision(action="click", ref=1), Decision(action="click", ref=1))
    run_step(browser, "Next", decide, sleep=no_sleep)
    assert "element detached" in decide.calls[1]


def test_run_steps_goes_in_order_and_stops_at_the_first_failure():
    browser = FakeBrowser({1: "A"})
    decide = scripted(Decision(action="goto", url="https://x.test/a"), Decision(uncertain=True, explanation="stuck"),
                      Decision(action="goto", url="https://x.test/never"))
    with pytest.raises(StepFailed, match="stuck"):
        run_steps(browser, ["one", "two", "three"], decide, sleep=no_sleep)
    assert browser.opened == ["https://x.test/a"]
