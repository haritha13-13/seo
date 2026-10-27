"""Review-finding fixes on the web layer, through the Flask test client."""
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from helpers import PLAIN_PASSWORD, add_run, csrf_token

from portal_auto.config import Settings
from portal_auto.db.models import RunStatus, ScriptVersion
from portal_auto.services.credentials import CredentialService
from portal_auto.services.errors import Invalid
from portal_auto.services.portals import PortalService
from portal_auto.services.vault import CredentialVault
from portal_auto.services.workflows import WorkflowService
from portal_auto.web import create_app


@pytest.fixture
def keyless(tmp_path):
    """An app whose PORTAL_AUTO_VAULT_KEY is missing, seeded with a stored credential."""
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'k.db'}", vault_key=None, root=tmp_path))
    app.config.update(TESTING=True)
    with app.extensions["session_factory"]() as session:
        portal = PortalService(session).create("Bank", "https://bank.example")
        workflow = WorkflowService(session).create(portal.id, "Pay", ["open", "verify paid"])
        cred = CredentialService(session, CredentialVault(Fernet.generate_key().decode())).create(
            portal.id, "Main", "alice", PLAIN_PASSWORD
        )
        ids = (portal.id, workflow.id, cred.id)
    return app.test_client(), ids


# 1. missing vault key ---------------------------------------------------------------------------

def test_listing_credentials_works_without_a_vault_key(keyless):
    client, (portal_id, _, _) = keyless
    response = client.get(f"/credentials/?portal_id={portal_id}")
    assert response.status_code == 200 and "Main" in response.get_data(as_text=True)


def test_executor_with_a_portal_selected_works_without_a_vault_key(keyless):
    client, (portal_id, workflow_id, cred_id) = keyless
    response = client.get(f"/executor/?portal_id={portal_id}&workflow_id={workflow_id}&credential_id={cred_id}")
    assert response.status_code == 200 and "Summary" in response.get_data(as_text=True)


def test_activate_and_delete_unused_credential_work_without_a_vault_key(keyless):
    client, (_, _, cred_id) = keyless
    token = csrf_token(client)
    assert client.post(f"/credentials/{cred_id}/active", data={"csrf_token": token, "active": "0"}).status_code == 302
    assert client.post(f"/credentials/{cred_id}/delete", data={"csrf_token": token}).status_code == 302


def test_new_credential_form_warns_when_the_vault_key_is_missing(keyless):
    client, (portal_id, _, _) = keyless
    html = client.get(f"/credentials/new?portal_id={portal_id}").get_data(as_text=True)
    assert "PORTAL_AUTO_VAULT_KEY" in html and "flash-warning" in html


def test_new_credential_form_has_no_warning_when_the_key_is_set(client, world):
    html = client.get(f"/credentials/new?portal_id={world.bank}").get_data(as_text=True)
    assert "PORTAL_AUTO_VAULT_KEY" not in html


def test_saving_a_new_credential_without_a_vault_key_shows_an_error_on_the_form(keyless):
    client, (portal_id, _, _) = keyless
    response = client.post(
        "/credentials/new",
        data={"csrf_token": csrf_token(client), "portal_id": portal_id, "label": "New", "username": "u", "password": "pw"},
    )
    html = response.get_data(as_text=True)
    assert response.status_code == 200 and "flash-error" in html and "PORTAL_AUTO_VAULT_KEY" in html


def test_editing_a_credential_without_changing_the_password_works_without_a_vault_key(keyless):
    client, (_, _, cred_id) = keyless
    response = client.post(
        f"/credentials/{cred_id}/edit",
        data={"csrf_token": csrf_token(client), "label": "Renamed", "username": "alice", "password": ""},
    )
    assert response.status_code == 302


# 2. NotFound -> 404 -----------------------------------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("get", "/credentials/999/edit"),
    ("post", "/credentials/999/active"),
    ("post", "/credentials/999/delete"),
    ("get", "/credentials/?portal_id=999"),
    ("get", "/credentials/new?portal_id=999"),
    ("get", "/portals/999/edit"),
    ("post", "/portals/999/active"),
    ("post", "/portals/999/delete"),
    ("get", "/workflows/999/edit"),
    ("post", "/workflows/999/active"),
    ("post", "/workflows/999/delete"),
])
def test_unknown_ids_are_404_not_found_pages(client, path, method):
    token = csrf_token(client)
    response = client.post(path, data={"csrf_token": token}) if method == "post" else client.get(path)
    assert response.status_code == 404
    assert "Not found" in response.get_data(as_text=True)


def test_other_domain_errors_stay_422(app, client):
    @app.get("/_invalid")
    def invalid():
        raise Invalid("nope")

    response = client.get("/_invalid")
    assert response.status_code == 422 and "Cannot do that" in response.get_data(as_text=True)


# 4. double click on Execute ---------------------------------------------------------------------

def test_second_execute_redirects_to_the_active_run_with_a_notice(app, client, world):
    token = csrf_token(client)
    data = {"csrf_token": token, "portal_id": world.bank, "workflow_id": world.pay,
            "credential_id": world.main, "verified": "1"}
    first = client.post("/executor/", data=data)
    second = client.post("/executor/", data=data)
    assert second.status_code == 302 and second.headers["Location"] == first.headers["Location"]
    page = client.get(second.headers["Location"]).get_data(as_text=True)
    assert "already in progress" in page


# 5. failed replay UX ----------------------------------------------------------------------------

def _script(app, world, created_at, version=1):
    with app.extensions["session_factory"]() as session:
        script = ScriptVersion(workflow_id=world.pay, version=version, steps_revision=1, code="x", created_at=created_at)
        session.add(script)
        session.commit()
        return script.id


def test_failed_replay_of_a_stored_script_explains_and_offers_regenerate(app, client, world):
    long_ago = datetime.now(timezone.utc) - timedelta(days=2)
    script_id = _script(app, world, long_ago)
    run_id = add_run(app, world, status=RunStatus.FAILED, script_version_id=script_id, error="Script exited with code 1")
    html = client.get(f"/runs/{run_id}").get_data(as_text=True)
    assert "The stored script failed. The portal may have changed. Use Regenerate script." in html
    assert "Regenerate script</button>" in html


def test_failed_generation_shows_the_stored_error_and_offers_regenerate(app, client, world):
    run_id = add_run(app, world, status=RunStatus.FAILED, error="Generation gave up after 40 steps")
    html = client.get(f"/runs/{run_id}").get_data(as_text=True)
    assert "Generation gave up after 40 steps" in html
    assert "The stored script failed" not in html
    assert "Regenerate script</button>" in html


def test_older_failed_run_with_a_fresh_script_has_no_regenerate_but_the_latest_does(app, client, world):
    now = datetime.now(timezone.utc)
    script_id = _script(app, world, now - timedelta(hours=1))
    older = add_run(app, world, status=RunStatus.FAILED, script_version_id=script_id, generated=True,
                    queued_at=now - timedelta(hours=1))
    newest = add_run(app, world, status=RunStatus.FAILED, script_version_id=script_id, queued_at=now)
    assert "Regenerate script</button>" not in client.get(f"/runs/{older}").get_data(as_text=True)
    assert "Regenerate script</button>" in client.get(f"/runs/{newest}").get_data(as_text=True)


def test_executor_summary_shows_the_status_of_the_last_run(app, client, world):
    add_run(app, world, status=RunStatus.SUCCEEDED, finished_at=datetime.now(timezone.utc) - timedelta(hours=3))
    add_run(app, world, status=RunStatus.FAILED, finished_at=datetime.now(timezone.utc) - timedelta(minutes=2))
    html = client.get("/executor/", query_string={
        "portal_id": world.bank, "workflow_id": world.pay, "credential_id": world.main}).get_data(as_text=True)
    assert "Last run failed 2 min ago" in html


def test_executor_summary_without_runs_says_never_run(client, world):
    html = client.get("/executor/", query_string={
        "portal_id": world.bank, "workflow_id": world.pay, "credential_id": world.main}).get_data(as_text=True)
    assert "Never run" in html


# 6. empty states --------------------------------------------------------------------------------

def test_executor_without_portals_points_to_creating_one(client):
    html = client.get("/executor/").get_data(as_text=True)
    assert 'class="empty"' in html and 'href="/portals/new"' in html


def test_executor_portal_without_workflows_or_credentials_links_to_both(app, client):
    with app.extensions["session_factory"]() as session:
        portal = PortalService(session).create("Lonely", "https://lonely.example")
    html = client.get(f"/executor/?portal_id={portal.id}").get_data(as_text=True)
    assert f'href="/workflows/new?portal_id={portal.id}"' in html
    assert f'href="/credentials/new?portal_id={portal.id}"' in html


def test_new_workflow_without_active_portals_says_to_create_a_portal(client):
    html = client.get("/workflows/new").get_data(as_text=True)
    assert 'class="empty"' in html and 'href="/portals/new"' in html


@pytest.mark.parametrize("path,text", [
    ("/portals/", "No Portals yet"), ("/workflows/", "No Workflows yet"), ("/runs/", "No Runs yet"),
])
def test_list_pages_show_a_friendly_empty_message(client, path, text):
    html = client.get(path).get_data(as_text=True)
    assert text in html and 'class="empty"' in html


# 7. time display --------------------------------------------------------------------------------

def test_timestamps_show_utc_without_microseconds_and_duration_is_computed(app, client, world):
    start = datetime(2026, 1, 2, 3, 4, 5, 123456)  # SQLite hands these back naive
    run_id = add_run(app, world, started_at=start, finished_at=start + timedelta(seconds=65))
    html = client.get(f"/runs/{run_id}").get_data(as_text=True)
    assert "2026-01-02 03:04:05 UTC" in html and "2026-01-02 03:05:10 UTC" in html
    assert "123456" not in html and "1m 5s" in html
    assert "2026-01-02 03:04:05 UTC" in client.get("/runs/").get_data(as_text=True)


def test_running_run_shows_running_duration_and_unstarted_run_shows_none(app, client, world):
    running = add_run(app, world, status=RunStatus.RUNNING, active_slot=1, started_at=datetime(2026, 1, 2, 3, 4, 5))
    assert "running…" in client.get(f"/runs/{running}").get_data(as_text=True)
    queued = add_run(app, world, status=RunStatus.FAILED)
    assert "Duration" not in client.get(f"/runs/{queued}").get_data(as_text=True)


# 9. security ------------------------------------------------------------------------------------

def test_session_cookie_is_lax_and_httponly(client):
    cookie = client.get("/health").headers["Set-Cookie"]
    assert "SameSite=Lax" in cookie and "HttpOnly" in cookie


@pytest.mark.parametrize("host,status", [
    ("localhost", 200), ("localhost:5001", 200), ("127.0.0.1:5001", 200), ("evil.example", 400),
    ("evil.example:5001", 400), ("127.0.0.1.evil.example", 400),
])
def test_host_header_must_be_local(client, host, status):
    assert client.get("/health", headers={"Host": host}).status_code == status


def test_screenshot_must_be_a_png_regular_file_inside_artifacts(app, client, world, tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "notes.txt").write_text("secret")
    (artifacts / "dir.png").mkdir()
    (artifacts / "ok.png").write_bytes(b"PNG")
    for name in ("notes.txt", "dir.png"):
        run_id = add_run(app, world, screenshot_path=name)
        assert client.get(f"/runs/{run_id}/screenshot").status_code == 404
    ok = client.get(f"/runs/{add_run(app, world, screenshot_path='ok.png')}/screenshot")
    assert ok.status_code == 200 and ok.mimetype == "image/png"
