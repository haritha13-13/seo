import pytest

from portal_auto.config import Settings
from portal_auto.db.models import Credential, Portal, Workflow, WorkflowStep
from portal_auto.services.errors import Invalid
from portal_auto.services.runs import RunService

NEEDS_SESSION = "This Workflow starts signed in, so the Credential needs a saved session"
NEEDS_PASSWORD = "This Credential has no password; use a Workflow that starts signed in, or add a password"


@pytest.fixture
def service(session_factory):
    service = RunService(
        session_factory, Settings(run_timeout_seconds=5), lambda run_id: 4000 + run_id,
        sleep=lambda seconds: None, pid_alive=lambda pid: True,
    )
    yield service
    service.wait_for_watchdogs()


def make_world(session, *, starts_signed_in: bool, password: str | None, saved_session: str | None):
    portal = Portal(name="Demo", url="https://demo.test")
    credential = Credential(
        portal=portal, label="main", username="alice", password_encrypted=password, session_encrypted=saved_session
    )
    workflow = Workflow(
        portal=portal, name="Login", starts_signed_in=starts_signed_in, steps=[WorkflowStep(position=1, instruction="Click")]
    )
    session.add_all([portal, credential, workflow])
    session.commit()
    return workflow.id, credential.id


def test_signed_in_workflow_needs_a_credential_with_a_saved_session(session, service):
    workflow_id, credential_id = make_world(session, starts_signed_in=True, password="x", saved_session=None)
    with pytest.raises(Invalid, match=NEEDS_SESSION):
        service.start_run(workflow_id, credential_id)


def test_signed_in_workflow_run_records_that_it_used_the_session(session, service):
    workflow_id, credential_id = make_world(session, starts_signed_in=True, password=None, saved_session="blob")
    assert service.start_run(workflow_id, credential_id).used_session is True


def test_ordinary_workflow_needs_a_credential_with_a_password(session, service):
    workflow_id, credential_id = make_world(session, starts_signed_in=False, password=None, saved_session="blob")
    with pytest.raises(Invalid, match=NEEDS_PASSWORD):
        service.start_run(workflow_id, credential_id)


def test_ordinary_workflow_run_does_not_use_the_session(session, service):
    workflow_id, credential_id = make_world(session, starts_signed_in=False, password="x", saved_session="blob")
    assert service.start_run(workflow_id, credential_id).used_session is False
