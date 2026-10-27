"""Generation safety: page content is untrusted, so the browser session confines the agent."""
import pytest
from site_server import serve

from portal_auto.agent.tools import build_tools
from portal_auto.browser.session import BrowserSession
from portal_auto.recording import ActionRecorder, Fill, Goto

SECRETS = {"PORTAL_USERNAME": "alice", "PORTAL_PASSWORD": "pw-s3cret-value"}
PASSWORD = "{{ENV:PORTAL_PASSWORD}}"
USERNAME = "{{ENV:PORTAL_USERNAME}}"


@pytest.fixture
def sites():
    other_pages = {"/login": '<input id="u" type="text"><input id="p" type="password">'}
    other, other_url = serve(other_pages)
    home_pages = {
        "/login": (
            '<label for="u">User</label><input id="u" type="text" value="typed-username-value">'
            '<label for="p">Pass</label><input id="p" type="password" value="prefilled-pw-value">'
            '<textarea id="t">typed-textarea-value</textarea>'
            '<input type="submit" value="Sign in">'
            f'<a href="{other_url}/login">away</a>'
        ),
    }
    home, home_url = serve(home_pages)
    yield home_url, other_url
    home.shutdown()
    other.shutdown()


@pytest.fixture
def session(sites):
    home_url, _ = sites
    recorder = ActionRecorder()
    browser = BrowserSession(recorder, home_url, SECRETS)
    browser.start(f"{home_url}/login")
    browser.snapshot()
    yield browser
    browser.close()


def element_ref(session, element_id):
    session.snapshot()
    return next(e.ref for e in session._snapshot.elements.values() if e.id == element_id)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "data:text/html,<p>x</p>", "javascript:alert(1)"])
def test_goto_refuses_non_http_schemes(session, url):
    with pytest.raises(ValueError, match="not allowed"):
        session.goto(url)


def test_goto_refuses_another_origin_and_does_not_record_it(session, sites):
    _, other_url = sites
    with pytest.raises(ValueError, match="origin"):
        session.goto(f"{other_url}/login")
    assert not any(isinstance(a, Goto) and other_url in a.url for a in session.recorder.actions)


def test_goto_within_origin_is_allowed(session, sites):
    home_url, _ = sites
    session.goto(f"{home_url}/login")


def test_start_refuses_a_foreign_start_url(sites):
    home_url, other_url = sites
    browser = BrowserSession(ActionRecorder(), home_url, SECRETS)
    try:
        with pytest.raises(ValueError, match="origin"):
            browser.start(f"{other_url}/login")
    finally:
        browser.close()


def test_goto_tool_returns_an_error_instead_of_navigating(session, sites):
    _, other_url = sites
    goto = next(t for t in build_tools(session) if t.name == "goto")
    assert goto.run(url=f"{other_url}/login").startswith("ERROR")


def test_password_placeholder_refused_for_text_input(session):
    with pytest.raises(ValueError, match="password"):
        session.fill(element_ref(session, "u"), PASSWORD)
    assert not any(isinstance(a, Fill) for a in session.recorder.actions)


def test_password_placeholder_accepted_for_password_input(session):
    session.fill(element_ref(session, "p"), PASSWORD)
    assert [a.value for a in session.recorder.actions if isinstance(a, Fill)] == [PASSWORD]


def test_username_placeholder_goes_into_text_input(session):
    session.fill(element_ref(session, "u"), USERNAME)


def test_unknown_env_placeholder_is_refused_and_never_resolved(session, monkeypatch):
    monkeypatch.setenv("PORTAL_AUTO_VAULT_KEY", "vault-key-value")
    with pytest.raises(ValueError, match="PORTAL_AUTO_VAULT_KEY") as caught:
        session.fill(element_ref(session, "u"), "{{ENV:PORTAL_AUTO_VAULT_KEY}}")
    assert "vault-key-value" not in str(caught.value)


def test_fill_refused_after_navigating_off_origin(session):
    link = next(e.ref for e in session._snapshot.elements.values() if e.tag == "a")
    session.click(link)
    assert "127.0.0.1" in session.snapshot()
    ref = element_ref(session, "p")
    with pytest.raises(ValueError, match="origin"):
        session.fill(ref, PASSWORD)


def test_snapshot_never_exposes_typed_values(session):
    rendered = session.snapshot()
    for leaked in ("typed-username-value", "prefilled-pw-value", "typed-textarea-value"):
        assert leaked not in rendered
    assert "Sign in" in rendered


def test_a_declared_extra_host_can_be_visited_and_its_password_input_filled(sites):
    home_url, other_url = sites
    other_port = other_url.rsplit(":", 1)[1]
    recorder = ActionRecorder()
    browser = BrowserSession(recorder, home_url, SECRETS, allowed_hosts=("localhost",))
    browser.start(f"{home_url}/login")
    try:
        browser.goto(f"http://localhost:{other_port}/login")
        browser.fill(element_ref(browser, "p"), PASSWORD)
    finally:
        browser.close()
    assert any(isinstance(action, Fill) for action in recorder.actions)


def test_an_undeclared_host_is_still_refused_when_other_hosts_are_declared(sites):
    home_url, other_url = sites
    browser = BrowserSession(ActionRecorder(), home_url, SECRETS, allowed_hosts=("adobe.com",))
    browser.start(f"{home_url}/login")
    try:
        with pytest.raises(ValueError, match="not allowed"):
            browser.goto(f"{other_url}/login")
    finally:
        browser.close()
