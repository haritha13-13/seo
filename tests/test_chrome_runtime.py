"""Chrome adapters without a real Chrome: debug-endpoint readiness and CDP reader robustness."""
import pytest

from portal_auto.runtime.chrome import CdpStateReader, storage_state_json, wait_for_debug_endpoint
from portal_auto.services.errors import Invalid

CHROME = {"Browser": "Chrome/156.0.8078.47"}


def test_returns_once_the_endpoint_answers_as_chrome():
    answers = iter([None, None, CHROME])
    polls = []

    wait_for_debug_endpoint(41000, alive=lambda: True, fetch=lambda port: next(answers), sleep=polls.append)

    assert len(polls) == 2


@pytest.mark.parametrize("browser", ["Chrome/156.0", "HeadlessChrome/156.0"])
def test_both_chrome_flavours_are_accepted(browser):
    wait_for_debug_endpoint(41000, alive=lambda: True, fetch=lambda port: {"Browser": browser}, sleep=lambda s: None)


def test_an_endpoint_that_is_not_chrome_means_another_program_holds_the_port():
    with pytest.raises(Invalid, match="(?i)another program"):
        wait_for_debug_endpoint(41000, alive=lambda: True, fetch=lambda port: {"Browser": "SomethingElse/1"}, sleep=lambda s: None)


def test_gives_up_at_the_deadline():
    with pytest.raises(Invalid, match="did not open its debugging port"):
        wait_for_debug_endpoint(41000, alive=lambda: True, fetch=lambda port: None, timeout=0, sleep=lambda s: None)


def test_stops_early_when_chrome_exits():
    with pytest.raises(Invalid, match="Chrome exited before"):
        wait_for_debug_endpoint(41000, alive=lambda: False, fetch=lambda port: None, sleep=lambda s: None)


class FakeContext:
    def storage_state(self):
        return {"cookies": [], "origins": []}


class FakeBrowser:
    def __init__(self, contexts):
        self.contexts = contexts


def test_state_is_read_from_the_first_context():
    assert storage_state_json(FakeBrowser([FakeContext()])) == '{"cookies": [], "origins": []}'


def test_chrome_with_no_window_is_a_friendly_error():
    with pytest.raises(Invalid, match="Chrome has no open window; start the capture again"):
        storage_state_json(FakeBrowser([]))


def test_reader_is_a_state_reader():
    assert callable(CdpStateReader().read)
