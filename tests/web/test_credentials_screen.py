"""Credentials screen, through the Flask test client."""
import pytest
from sqlalchemy import text


PLAIN = "s3cret-Pa55"


def post(client, token, path, **fields):
    return client.post(path, data={"csrf_token": token, **fields}, follow_redirects=True)


@pytest.fixture
def portal_id(client, token) -> int:
    post(client, token, "/portals/new", name="Acme", url="https://acme.test")
    return 1


def create_credential(client, token, portal_id, label="main", password=PLAIN):
    return post(
        client, token, "/credentials/new", portal_id=portal_id, label=label, username="bob", password=password
    )


def stored_password(app) -> str:
    with app.extensions["session_factory"]() as session:
        return session.execute(text("SELECT password_encrypted FROM credential")).scalar_one()


def test_index_without_portal_offers_a_portal_selector(client, portal_id):
    html = client.get("/credentials/").get_data(as_text=True)
    assert '<select name="portal_id">' in html and "Acme" in html


def test_created_credential_is_listed_with_masked_password(client, token, portal_id):
    html = create_credential(client, token, portal_id).get_data(as_text=True)
    assert "main" in html and "bob" in html and "••••••••" in html


def test_password_never_appears_in_any_response_and_is_encrypted_at_rest(app, client, token, portal_id):
    pages = [
        create_credential(client, token, portal_id),
        client.get(f"/credentials/?portal_id={portal_id}"),
        client.get("/credentials/1/edit"),
        client.get(f"/credentials/new?portal_id={portal_id}"),
    ]
    encrypted = stored_password(app)
    assert encrypted != PLAIN
    for page in pages:
        html = page.get_data(as_text=True)
        assert PLAIN not in html and encrypted not in html


def test_failed_create_does_not_echo_the_password(client, token, portal_id):
    create_credential(client, token, portal_id)
    html = create_credential(client, token, portal_id, password="other-Pw-99").get_data(as_text=True)
    assert "already has a credential" in html and "other-Pw-99" not in html


def test_duplicate_label_creates_nothing(app, client, token, portal_id):
    create_credential(client, token, portal_id)
    create_credential(client, token, portal_id)
    html = client.get(f"/credentials/?portal_id={portal_id}").get_data(as_text=True)
    assert html.count("<td>main</td>") == 1


def test_missing_csrf_is_rejected(client, portal_id):
    assert client.post("/credentials/new", data={"portal_id": portal_id}).status_code == 400


def test_blank_password_on_edit_keeps_the_old_one(app, client, token, portal_id):
    create_credential(client, token, portal_id)
    before = stored_password(app)
    html = post(client, token, "/credentials/1/edit", label="renamed", username="alice", password="").get_data(
        as_text=True
    )
    assert "renamed" in html and "alice" in html
    assert stored_password(app) == before


def test_new_password_on_edit_replaces_it(app, client, token, portal_id):
    create_credential(client, token, portal_id)
    before = stored_password(app)
    post(client, token, "/credentials/1/edit", label="main", username="bob", password="brand-new-pw")
    assert stored_password(app) != before


def test_deactivate_credential(client, token, portal_id):
    create_credential(client, token, portal_id)
    html = post(client, token, "/credentials/1/active", active="0").get_data(as_text=True)
    assert "Inactive" in html and "deactivated" in html


def test_delete_credential(client, token, portal_id):
    create_credential(client, token, portal_id)
    html = post(client, token, "/credentials/1/delete").get_data(as_text=True)
    assert "Credential deleted" in html and "<td>main</td>" not in html
