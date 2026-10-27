"""Saved Session import/remove on the Credentials screen, through the Flask test client."""
import io
import json

import pytest
from sqlalchemy import text

COOKIE_VALUE = "cookie-value-XYZ-123"


def session_file(host: str = "acme.test", value: str = COOKIE_VALUE) -> bytes:
    cookie = {"name": "sid", "value": value, "domain": host, "path": "/"}
    return json.dumps({"cookies": [cookie], "origins": []}).encode()


def post(client, token, path, **fields):
    return client.post(path, data={"csrf_token": token, **fields}, follow_redirects=True)


def upload(client, token, raw: bytes | None, credential_id: int = 1):
    fields = {"csrf_token": token}
    if raw is not None:
        fields["session_file"] = (io.BytesIO(raw), "state.json")
    return client.post(
        f"/credentials/{credential_id}/session", data=fields, content_type="multipart/form-data",
        follow_redirects=True,
    )


def stored_session(app):
    with app.extensions["session_factory"]() as session:
        return session.execute(text("SELECT session_encrypted FROM credential")).scalar_one()


@pytest.fixture
def credential(client, token):
    post(client, token, "/portals/new", name="Acme", url="https://acme.test")
    post(client, token, "/credentials/new", portal_id=1, label="main", username="bob", password="")
    return 1


def test_credential_can_be_created_without_a_password(client, credential):
    html = client.get("/credentials/?portal_id=1").get_data(as_text=True)
    assert "<td>main</td>" in html and "Needs a password or a saved session" in html and "No saved session" in html


def test_password_field_is_not_required_on_the_forms(client, credential):
    for url in ("/credentials/new?portal_id=1", "/credentials/1/edit"):
        html = client.get(url).get_data(as_text=True)
        password_field = html[html.index('name="password"'):].split(">")[0]
        assert "required" not in password_field


def test_edit_page_offers_a_multipart_upload_form_and_hint(client, credential):
    html = client.get("/credentials/1/edit").get_data(as_text=True)
    assert 'enctype="multipart/form-data"' in html and 'name="session_file"' in html
    assert 'action="/credentials/1/session"' in html
    assert "Sign in by hand once and keep the session" in html


def test_import_stores_it_encrypted_and_lists_it_without_leaking(app, client, token, credential):
    response = upload(client, token, session_file())
    html = response.get_data(as_text=True)
    assert "Saved session imported" in html and "Edit Credential" in html
    encrypted = stored_session(app)
    assert COOKIE_VALUE not in encrypted
    listing = client.get("/credentials/?portal_id=1").get_data(as_text=True)
    assert "Saved session: imported just now" in listing
    assert "Needs a password or a saved session" not in listing
    for page in (response, client.get("/credentials/1/edit"), client.get("/credentials/?portal_id=1")):
        body = page.get_data(as_text=True)
        assert COOKIE_VALUE not in body and encrypted not in body


def test_session_for_another_site_is_imported_with_a_warning(client, token, credential):
    html = upload(client, token, session_file(host="elsewhere.example")).get_data(as_text=True)
    assert "Saved session imported" in html and "may belong to a different site" in html


def test_invalid_file_shows_an_error_on_the_edit_page_and_stores_nothing(app, client, token, credential):
    html = upload(client, token, b"not json").get_data(as_text=True)
    assert "not valid JSON" in html and "Edit Credential" in html
    assert stored_session(app) is None


def test_missing_file_asks_to_choose_one(client, token, credential):
    assert "Choose a session file" in upload(client, token, None).get_data(as_text=True)


def test_empty_file_is_reported_as_invalid_not_as_missing(client, token, credential):
    assert "not valid JSON" in upload(client, token, b"").get_data(as_text=True)


def test_unknown_credential_is_a_404(client, token):
    assert upload(client, token, session_file(), credential_id=99).status_code == 404


def test_upload_without_csrf_is_rejected(client, credential):
    data = {"session_file": (io.BytesIO(session_file()), "state.json")}
    assert client.post("/credentials/1/session", data=data, content_type="multipart/form-data").status_code == 400


def test_oversized_upload_gets_a_friendly_413(client, token, credential):
    response = upload(client, token, b"x" * (3 * 1024 * 1024 + 1))
    assert response.status_code == 413
    assert "at most 2 MB" in response.get_data(as_text=True)


def test_import_with_an_invalid_vault_key_shows_a_friendly_error(app, client, token, credential):
    app.extensions["settings"] = type(app.extensions["settings"])(
        database_url=app.extensions["settings"].database_url, vault_key="", root=app.extensions["settings"].root
    )
    response = upload(client, token, session_file())
    assert response.status_code == 200 and "Edit Credential" in response.get_data(as_text=True)
    assert "Saved session imported" not in response.get_data(as_text=True)


def test_remove_clears_the_saved_session(app, client, token, credential):
    upload(client, token, session_file())
    html = post(client, token, "/credentials/1/session/remove").get_data(as_text=True)
    assert "Saved session removed" in html
    assert stored_session(app) is None
    assert "No saved session" in client.get("/credentials/?portal_id=1").get_data(as_text=True)


def test_remove_on_unknown_credential_is_a_404(client, token):
    assert post(client, token, "/credentials/99/session/remove").status_code == 404
