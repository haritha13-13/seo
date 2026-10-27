"""S7: SavedSessionService through its public interface, on a real SQLite database."""
import json

import pytest
from sqlalchemy import select

from portal_auto.db.models import Credential
from portal_auto.services.credentials import CredentialService
from portal_auto.services.errors import Invalid, NotFound
from portal_auto.services.portals import PortalService
from portal_auto.services.saved_sessions import MAX_SESSION_BYTES, SavedSessionService
from portal_auto.services.vault import CredentialVault

COOKIE_VALUE = "cookie-value-SECRET-123"
GOOD = {
    "cookies": [{"name": "sid", "value": COOKIE_VALUE, "domain": ".bank.example", "path": "/"}],
    "origins": [{"origin": "https://www.bank.example", "localStorage": [{"name": "t", "value": "tok"}]}],
}


def as_bytes(state: dict) -> bytes:
    return json.dumps(state).encode()


@pytest.fixture
def vault(vault_key):
    return CredentialVault(vault_key)


@pytest.fixture
def portal(session):
    return PortalService(session).create("Bank", "https://www.bank.example/")


@pytest.fixture
def credential(session, vault, portal):
    return CredentialService(session, vault).create(portal.id, "google", "me@bank.example")


@pytest.fixture
def sessions(session, vault):
    return SavedSessionService(session, vault)


def stored_column(session, credential_id: int):
    session.expire_all()
    return session.scalar(select(Credential.session_encrypted).where(Credential.id == credential_id))


def test_an_imported_session_is_stored_encrypted_and_loads_back(sessions, credential, session):
    sessions.import_session(credential.id, as_bytes(GOOD))

    assert COOKIE_VALUE not in stored_column(session, credential.id)
    assert json.loads(sessions.load(credential.id)) == GOOD
    assert credential.has_session and credential.session_source == "imported"
    assert credential.session_updated_at is not None


def test_a_credential_without_a_session_loads_none(sessions, credential):
    assert sessions.load(credential.id) is None


def test_importing_again_replaces_the_session(sessions, credential):
    sessions.import_session(credential.id, as_bytes(GOOD))
    newer = {**GOOD, "cookies": [{**GOOD["cookies"][0], "value": "newer"}]}

    sessions.import_session(credential.id, as_bytes(newer))

    assert json.loads(sessions.load(credential.id))["cookies"][0]["value"] == "newer"


@pytest.mark.parametrize("raw", [
    b"not json at all", b"\xff\xfe\x00bad utf8", b"[]", b'"text"', b"{}", b'{"cookies": [], "origins": "x"}',
    b'{"cookies": "x", "origins": []}', b'{"cookies": ["a"], "origins": []}', b'{"origins": []}',
])
def test_a_file_that_is_not_a_playwright_session_is_rejected_and_nothing_changes(sessions, credential, raw):
    sessions.import_session(credential.id, as_bytes(GOOD))

    with pytest.raises(Invalid):
        sessions.import_session(credential.id, raw)

    assert json.loads(sessions.load(credential.id)) == GOOD


def test_a_file_over_the_size_cap_is_rejected(sessions, credential):
    padding = "x" * (MAX_SESSION_BYTES + 1)
    with pytest.raises(Invalid, match="2 MB"):
        sessions.import_session(credential.id, as_bytes({**GOOD, "padding": padding}))
    assert not credential.has_session


def test_a_session_for_the_portals_host_gives_no_warning(sessions, credential):
    assert sessions.import_session(credential.id, as_bytes(GOOD)).warnings == []


def test_a_session_with_no_cookie_for_the_portal_is_accepted_with_a_warning(sessions, credential):
    other = {"cookies": [{"name": "sid", "value": "v", "domain": ".other.test", "path": "/"}], "origins": []}

    result = sessions.import_session(credential.id, as_bytes(other))

    assert len(result.warnings) == 1 and "www.bank.example" in result.warnings[0]
    assert credential.has_session


def test_a_cookie_for_a_declared_extra_host_counts_as_matching(sessions, credential, portal, session):
    PortalService(session).update(portal.id, portal.name, portal.url, extra_hosts="adobe.com")
    adobe = {"cookies": [{"name": "s", "value": "v", "domain": ".adobe.com", "path": "/"}], "origins": []}

    assert sessions.import_session(credential.id, as_bytes(adobe)).warnings == []


def test_refresh_replaces_the_session_and_marks_it_refreshed(sessions, credential):
    sessions.import_session(credential.id, as_bytes(GOOD))
    rotated = {**GOOD, "cookies": [{**GOOD["cookies"][0], "value": "rotated"}]}

    sessions.refresh(credential.id, json.dumps(rotated))

    assert json.loads(sessions.load(credential.id))["cookies"][0]["value"] == "rotated"
    assert credential.session_source == "refreshed"


def test_refresh_with_an_invalid_state_changes_nothing(sessions, credential):
    sessions.import_session(credential.id, as_bytes(GOOD))
    with pytest.raises(Invalid):
        sessions.refresh(credential.id, "{}")
    assert json.loads(sessions.load(credential.id)) == GOOD


def test_clear_removes_the_session(sessions, credential):
    sessions.import_session(credential.id, as_bytes(GOOD))
    sessions.clear(credential.id)
    assert sessions.load(credential.id) is None and not credential.has_session


def test_an_unknown_credential_is_not_found(sessions):
    with pytest.raises(NotFound):
        sessions.import_session(999, as_bytes(GOOD))


@pytest.mark.parametrize("bad", [
    {"cookies": [{"name": "sid"}], "origins": []},
    {"cookies": [{"name": 1, "value": "v"}], "origins": []},
    {"cookies": [{"name": "sid", "value": None}], "origins": []},
    {"cookies": [], "origins": [{"origin": "https://a.test", "localStorage": "x"}]},
    {"cookies": [], "origins": [{"origin": "https://a.test", "localStorage": [{"name": "t"}]}]},
    {"cookies": [], "origins": [{"origin": "https://a.test", "localStorage": [3]}]},
])
def test_import_and_refresh_reject_cookies_and_storage_items_without_string_name_and_value(sessions, credential, bad):
    with pytest.raises(Invalid):
        sessions.import_session(credential.id, as_bytes(bad))
    sessions.import_session(credential.id, as_bytes(GOOD))
    with pytest.raises(Invalid):
        sessions.refresh(credential.id, json.dumps(bad))
    assert json.loads(sessions.load(credential.id)) == GOOD


def rotated(value: str) -> str:
    return json.dumps({**GOOD, "cookies": [{**GOOD["cookies"][0], "value": value}]})


def test_refresh_if_unchanged_stores_when_the_session_is_the_one_that_was_loaded(sessions, credential):
    sessions.import_session(credential.id, as_bytes(GOOD))
    expected = sessions.updated_at(credential.id)

    assert sessions.refresh_if_unchanged(credential.id, rotated("rotated"), expected) is True
    assert json.loads(sessions.load(credential.id))["cookies"][0]["value"] == "rotated"
    assert credential.session_source == "refreshed"


def test_refresh_if_unchanged_does_not_overwrite_a_session_re_imported_meanwhile(sessions, credential):
    sessions.import_session(credential.id, as_bytes(GOOD))
    expected = sessions.updated_at(credential.id)
    newer = {**GOOD, "cookies": [{**GOOD["cookies"][0], "value": "operator-import"}]}
    sessions.import_session(credential.id, as_bytes(newer))

    assert sessions.refresh_if_unchanged(credential.id, rotated("rotated"), expected) is False
    assert json.loads(sessions.load(credential.id)) == newer


def test_refresh_if_unchanged_does_not_restore_a_session_removed_meanwhile(sessions, credential):
    sessions.import_session(credential.id, as_bytes(GOOD))
    expected = sessions.updated_at(credential.id)
    sessions.clear(credential.id)

    assert sessions.refresh_if_unchanged(credential.id, rotated("rotated"), expected) is False
    assert sessions.load(credential.id) is None


def test_refresh_if_unchanged_rejects_an_invalid_state(sessions, credential):
    sessions.import_session(credential.id, as_bytes(GOOD))
    with pytest.raises(Invalid):
        sessions.refresh_if_unchanged(credential.id, "{}", sessions.updated_at(credential.id))


def cookies(*domains: str) -> str:
    return json.dumps({"cookies": [{"name": f"c{i}", "value": "v", "domain": d, "path": "/"} for i, d in enumerate(domains)],
                       "origins": []})


def test_a_state_with_no_cookie_for_the_portal_looks_logged_out(sessions, credential):
    previous = cookies(".bank.example")
    assert sessions.looks_logged_out(credential.id, previous, cookies(".other.test")) is True
    assert sessions.looks_logged_out(credential.id, previous, cookies(".bank.example")) is False


def test_a_state_with_under_half_the_stored_cookies_looks_logged_out(sessions, credential):
    previous = cookies(*[".bank.example"] * 4)
    assert sessions.looks_logged_out(credential.id, previous, cookies(".bank.example")) is True
    assert sessions.looks_logged_out(credential.id, previous, cookies(*[".bank.example"] * 2)) is False
