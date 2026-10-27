"""Starts signed in across the Workflows, Executor and Runs screens (S5: Flask test client)."""
from datetime import datetime, timedelta, timezone

import pytest
from helpers import PLAIN_PASSWORD, add_run, csrf_token

from portal_auto.db.models import Credential, Run, RunStatus, ScriptVersion, Workflow
from portal_auto.services.workflows import WorkflowService

SESSION_BLOB = "super-secret-session-blob"
HELP = "Steps assume the browser is already signed in; the Run loads the Credential's saved session"
HINT = "This Run used a saved session; it may have expired. Capture the sign-in again."


@pytest.fixture
def signed_in(app, world):
    """A Starts-signed-in Workflow, a session-only Credential and a password-only Credential (default 'Main')."""
    with app.extensions["session_factory"]() as session:
        workflow = Workflow(portal_id=world.bank, name="Dashboard", starts_signed_in=True)
        saved = Credential(
            portal_id=world.bank, label="Sessioned", username="carol", session_encrypted=SESSION_BLOB,
            session_updated_at=datetime.now(timezone.utc) - timedelta(hours=2), session_source="captured",
        )
        session.add_all([workflow, saved])
        session.commit()
        return type("Ids", (), {"workflow": workflow.id, "credential": saved.id})


def choose(world, workflow, credential) -> dict:
    return {"portal_id": world.bank, "workflow_id": workflow, "credential_id": credential}


def executor_html(client, world, workflow, credential) -> str:
    return client.get("/executor/", query_string=choose(world, workflow, credential)).get_data(as_text=True)


def form(client, **fields) -> dict:
    return {"csrf_token": csrf_token(client), "action": "save", "step": ["Verify it"], "active": "1", **fields}


def starts_signed_in(app, workflow_id) -> bool:
    with app.extensions["session_factory"]() as session:
        return WorkflowService(session).get(workflow_id).starts_signed_in


# Behavior 1: Workflows screen -------------------------------------------------------------------

def test_create_form_offers_starts_signed_in_with_help_text(client, world):
    html = client.get("/workflows/new").get_data(as_text=True)
    assert "Starts signed in" in html and HELP in html


def test_creating_with_starts_signed_in_stores_it_and_the_list_badges_it(app, client, world):
    client.post("/workflows/new", data=form(client, name="Inbox", portal_id=world.bank, starts_signed_in="1"))
    with app.extensions["session_factory"]() as session:
        created = next(w for w in WorkflowService(session).list() if w.name == "Inbox")
    assert created.starts_signed_in
    html = client.get("/workflows/").get_data(as_text=True)
    assert html.count("signed in</span>") == 1


def test_edit_form_reflects_and_saves_the_setting(app, client, world, signed_in):
    assert 'name="starts_signed_in" value="1" checked' in client.get(f"/workflows/{signed_in.workflow}/edit").get_data(as_text=True)
    client.post(f"/workflows/{signed_in.workflow}/edit", data=form(client, name="Dashboard"))
    assert not starts_signed_in(app, signed_in.workflow)


def test_flipping_the_setting_with_a_script_flashes_stale(app, client, world):
    with app.extensions["session_factory"]() as session:
        session.add(ScriptVersion(workflow_id=world.pay, version=1, steps_revision=1, code="pass"))
        session.commit()
    html = client.post(
        f"/workflows/{world.pay}/edit", data=form(client, name="Pay bill", step=["open the page", "click pay"], starts_signed_in="1"),
        follow_redirects=True,
    ).get_data(as_text=True)
    assert "Stale" in html and starts_signed_in(app, world.pay)


# Behavior 3: Executor ---------------------------------------------------------------------------

def test_ordinary_workflow_offers_only_credentials_with_a_password(client, world, signed_in):
    html = executor_html(client, world, world.pay, "")
    assert "Main" in html and "Sessioned" not in html


def test_signed_in_workflow_offers_only_credentials_with_a_saved_session(client, world, signed_in):
    html = executor_html(client, world, signed_in.workflow, "")
    assert "Sessioned" in html and ">Main<" not in html


def test_no_qualifying_credential_says_which_kind_is_needed_and_links_to_credentials(app, client, world):
    with app.extensions["session_factory"]() as session:
        workflow = Workflow(portal_id=world.bank, name="Inbox", starts_signed_in=True)
        session.add(workflow)
        session.commit()
        workflow_id = workflow.id
    html = executor_html(client, world, workflow_id, "")
    assert "starts signed in, so it needs an active Credential that has a saved session" in html and f"/credentials/new?portal_id={world.bank}" in html


def test_signed_in_summary_shows_session_age_and_never_secrets(client, world, signed_in):
    html = executor_html(client, world, signed_in.workflow, signed_in.credential)
    assert "Saved session: captured 2 h ago" in html
    assert "This Run starts from the saved session (no username/password typing)" in html
    assert "carol" in html and SESSION_BLOB not in html and PLAIN_PASSWORD not in html


def test_summary_without_a_saved_session_says_so(client, world):
    html = executor_html(client, world, world.pay, world.main)
    assert "No saved session" in html and "••••••••" in html


def test_summary_masks_a_missing_password_with_a_dash(app, client, world):
    with app.extensions["session_factory"]() as session:
        session.add(Credential(portal_id=world.bank, label="Keyless", username="dan", session_encrypted="x"))
        workflow = Workflow(portal_id=world.bank, name="Dash", starts_signed_in=True)
        session.add(workflow)
        session.commit()
        wid = workflow.id
        cid = session.query(Credential).filter_by(label="Keyless").one().id
    html = executor_html(client, world, wid, cid)
    assert "password —" in html and "••••••••" not in html


def test_execute_with_a_mismatched_pairing_surfaces_the_error_and_starts_nothing(app, client, world, signed_in):
    data = choose(world, signed_in.workflow, world.main) | {"csrf_token": csrf_token(client), "verified": "1"}
    response = client.post("/executor/", data=data)
    assert response.status_code == 200
    with app.extensions["session_factory"]() as session:
        assert session.query(Run).count() == 0


def test_execute_a_signed_in_run_records_that_it_used_the_session(app, client, world, signed_in):
    data = choose(world, signed_in.workflow, signed_in.credential) | {"csrf_token": csrf_token(client), "verified": "1"}
    assert client.post("/executor/", data=data).status_code == 302
    with app.extensions["session_factory"]() as session:
        assert session.query(Run).one().used_session


# Behavior 4: Runs -------------------------------------------------------------------------------

def test_failed_run_that_used_a_session_hints_it_may_have_expired(app, client, world):
    failed = add_run(app, world, status=RunStatus.FAILED, used_session=True)
    assert HINT in client.get(f"/runs/{failed}").get_data(as_text=True)


@pytest.mark.parametrize("status,used", [(RunStatus.FAILED, False), (RunStatus.SUCCEEDED, True)])
def test_expired_session_hint_is_only_for_failed_session_runs(app, client, world, status, used):
    run = add_run(app, world, status=status, used_session=used)
    assert HINT not in client.get(f"/runs/{run}").get_data(as_text=True)


def test_runs_screens_label_session_runs_in_the_credential_cell(app, client, world):
    run = add_run(app, world, used_session=True)
    assert "signed-in session" in client.get("/runs/").get_data(as_text=True)
    assert "signed-in session" in client.get(f"/runs/{run}").get_data(as_text=True)


def test_runs_without_a_session_have_no_signed_in_label(app, client, world):
    run = add_run(app, world)
    assert "signed-in session" not in client.get("/runs/").get_data(as_text=True)
    assert "signed-in session" not in client.get(f"/runs/{run}").get_data(as_text=True)
