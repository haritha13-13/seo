"""Portals screen, through the Flask test client."""

from portal_auto.services.credentials import CredentialService
from portal_auto.services.vault import CredentialVault


def post(client, token, path, **fields):
    return client.post(path, data={"csrf_token": token, **fields}, follow_redirects=True)


def create_portal(client, token, name="Acme", url="https://acme.test"):
    return post(client, token, "/portals/new", name=name, url=url)


def test_created_portal_appears_in_list(client, token):
    response = create_portal(client, token)
    html = response.get_data(as_text=True)
    assert "Acme" in html and "https://acme.test" in html and "Active" in html


def test_duplicate_name_shows_error_and_creates_nothing(client, token):
    create_portal(client, token)
    response = create_portal(client, token, url="https://other.test")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "already exists" in html
    assert "https://other.test" in html  # form keeps what was typed
    assert client.get("/portals/").get_data(as_text=True).count("<td>Acme</td>") == 1


def test_invalid_url_shows_error_on_the_form(client, token):
    html = create_portal(client, token, url="ftp://x").get_data(as_text=True)
    assert "must start with http" in html


def test_post_without_csrf_is_rejected(client):
    assert client.post("/portals/new", data={"name": "A", "url": "https://a.test"}).status_code == 400


def test_edit_changes_name_and_url(client, token):
    create_portal(client, token)
    html = post(client, token, "/portals/1/edit", name="Acme2", url="https://acme2.test").get_data(as_text=True)
    assert "Acme2" in html and "https://acme2.test" in html


def test_deactivate_and_activate_toggle_badge(client, token):
    create_portal(client, token)
    assert "Inactive" in post(client, token, "/portals/1/active", active="0").get_data(as_text=True)
    html = post(client, token, "/portals/1/active", active="1").get_data(as_text=True)
    assert "Inactive</span>" not in html


def test_delete_unused_portal_removes_it(client, token):
    create_portal(client, token)
    html = post(client, token, "/portals/1/delete").get_data(as_text=True)
    assert "Portal deleted" in html and "<td>Acme</td>" not in html


def test_delete_of_in_use_portal_flashes_the_inuse_message(app, client, token):
    create_portal(client, token)
    with app.app_context(), app.extensions["session_factory"]() as session:
        vault = CredentialVault(app.extensions["settings"].vault_key)
        CredentialService(session, vault).create(1, "main", "bob", "pw")
    response = post(client, token, "/portals/1/delete")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "deactivate it instead" in html and "<td>Acme</td>" in html


def test_extra_hosts_are_saved_and_shown_when_editing(client, token):
    post(client, token, "/portals/new", name="Behance", url="https://www.behance.net", extra_hosts="adobe.com")
    html = client.get("/portals/1/edit").get_data(as_text=True)
    assert 'name="extra_hosts"' in html and "adobe.com" in html


def test_invalid_extra_host_shows_an_error_and_creates_nothing(client, token):
    response = post(client, token, "/portals/new", name="Bad", url="https://bad.test", extra_hosts="http://evil.test")
    assert "hostname" in response.get_data(as_text=True).lower()
    assert "Bad" not in client.get("/portals/").get_data(as_text=True)
