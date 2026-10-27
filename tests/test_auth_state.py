"""authentication_state: sign-out proves a session, a sign-in form disproves it, silence proves nothing."""
import pytest
from site_server import serve

from portal_auto.browser.auth_state import AUTHENTICATED, UNAUTHENTICATED, UNKNOWN
from portal_auto.browser.session import BrowserSession
from portal_auto.recording import ActionRecorder

PAGES = {
    "/in": "<nav><a href='#'>Log out</a></nav><h1>Dashboard</h1>",
    "/menu": "<ul><li role='menuitem'>Sign out</li></ul>",
    "/login": "<form><input type='email'><input type='password'><button>Log in</button></form>",
    "/button": "<a href='#'>Log in</a><p>Welcome</p>",
    "/challenge": "<p>Please verify you are human</p>",
    "/plain": "<h1>Welcome</h1><p>Nothing to see</p>",
    "/mixed": "<a href='#'>Log out</a><input type='password'>",
}


@pytest.fixture(scope="module")
def base_url():
    server, url = serve(PAGES)
    yield url
    server.shutdown()


def state_of(base_url: str, path: str, start_url: str | None = None) -> str:
    session = BrowserSession(ActionRecorder(), start_url or base_url, {})
    session.start(f"{base_url}{path}")
    try:
        return session.authentication_state()
    finally:
        session.close()


@pytest.mark.parametrize("path, expected", [
    ("/in", AUTHENTICATED), ("/menu", AUTHENTICATED), ("/login", UNAUTHENTICATED),
    ("/button", UNAUTHENTICATED), ("/challenge", UNAUTHENTICATED), ("/plain", UNKNOWN),
    ("/mixed", UNAUTHENTICATED),
])
def test_the_state_follows_the_evidence_on_the_page(base_url, path, expected):
    assert state_of(base_url, path) == expected


def test_a_page_on_another_host_says_nothing(base_url):
    other = base_url.replace("127.0.0.1", "localhost")
    session = BrowserSession(ActionRecorder(), other, {}, allowed_hosts=["127.0.0.1"])
    session.start(f"{base_url}/in")
    try:
        assert session.authentication_state() == UNKNOWN
    finally:
        session.close()
