"""An action that cannot be written into a replayable script must not be performed during Generation."""
import pytest
from site_server import serve

from portal_auto.browser.locators import NoStableLocator
from portal_auto.browser.session import BrowserSession
from portal_auto.recording import ActionRecorder

PAGE = '<div id="react-aria1234567-:r1:" onclick="document.title=\'clicked\'" style="width:60px;height:60px">&nbsp;</div>'


@pytest.fixture
def session():
    server, base_url = serve({"/": PAGE})
    browser = BrowserSession(ActionRecorder(), base_url, {})
    browser.start(base_url + "/")
    browser.snapshot()
    yield browser
    browser.close()
    server.shutdown()


def test_clicking_an_element_with_no_stable_locator_is_refused_before_it_happens(session):
    with pytest.raises(NoStableLocator):
        session.click(1)

    assert session.worker.call(session._page.title) != "clicked"
    assert session.recorder.actions[1:] == []
