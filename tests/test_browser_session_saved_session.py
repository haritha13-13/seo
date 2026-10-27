"""Generation starts from the Saved Session file when BrowserSession is given one."""
import json

import pytest
from cookie_site import COOKIE_NAME, COOKIE_VALUE, serve_cookie_gated

from portal_auto.browser.session import BrowserSession
from portal_auto.recording import ActionRecorder


@pytest.fixture
def site():
    server, url = serve_cookie_gated()
    yield url
    server.shutdown()


def signed_in(url: str, storage_state_path: str | None) -> bool:
    browser = BrowserSession(ActionRecorder(), url, {}, storage_state_path=storage_state_path)
    browser.start(url)
    try:
        return browser.assert_text("signed in")
    finally:
        browser.close()


def test_session_file_makes_the_browser_signed_in(tmp_path, site):
    cookie = {"name": COOKIE_NAME, "value": COOKIE_VALUE, "domain": "127.0.0.1", "path": "/"}
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"cookies": [cookie], "origins": []}))
    assert signed_in(site, str(path)) is True
    assert signed_in(site, None) is False
