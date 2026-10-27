"""Changing a Portal's URL can leave a Credential's Saved Session belonging to the old site."""
import json

import pytest

from portal_auto.services.credentials import CredentialService
from portal_auto.services.portals import PortalService
from portal_auto.services.saved_sessions import SavedSessionService
from portal_auto.services.vault import CredentialVault

EMPTY_SESSION = json.dumps({"cookies": [], "origins": []}).encode()


@pytest.fixture
def portal_id(app):
    with app.extensions["session_factory"]() as db:
        return PortalService(db).create("Bank", "https://bank.example").id


def add_credential(app, portal_id: int, with_session: bool) -> None:
    vault = CredentialVault(app.extensions["settings"].vault_key)
    with app.extensions["session_factory"]() as db:
        credential = CredentialService(db, vault).create(portal_id, "main", "alice")
        if with_session:
            SavedSessionService(db, vault).import_session(credential.id, EMPTY_SESSION)


def edit_url(client, token, portal_id: int, url: str) -> str:
    response = client.post(
        f"/portals/{portal_id}/edit",
        data={"csrf_token": token, "name": "Bank", "url": url, "extra_hosts": ""},
        follow_redirects=True,
    )
    return response.get_data(as_text=True)


def test_a_url_change_warns_when_a_credential_has_a_saved_session(app, client, token, portal_id):
    add_credential(app, portal_id, with_session=True)

    html = edit_url(client, token, portal_id, "https://new-bank.example")

    assert "saved session" in html.lower() and "capture" in html.lower()


def test_no_warning_when_the_url_is_unchanged(app, client, token, portal_id):
    add_credential(app, portal_id, with_session=True)
    assert "saved session" not in edit_url(client, token, portal_id, "https://bank.example").lower()


def test_no_warning_when_no_credential_has_a_saved_session(app, client, token, portal_id):
    add_credential(app, portal_id, with_session=False)
    assert "saved session" not in edit_url(client, token, portal_id, "https://new-bank.example").lower()
