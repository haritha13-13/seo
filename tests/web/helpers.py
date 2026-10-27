"""Plain helpers shared by the web tests (fixtures live in conftest.py)."""
from portal_auto.db.models import Run, RunStatus

PLAIN_PASSWORD = "s3cret-plaintext-pw"


def csrf_token(client) -> str:
    client.get("/health")
    with client.session_transaction() as flask_session:
        return flask_session["csrf_token"]


def add_run(app, world, **fields) -> int:
    values = dict(
        workflow_id=world.pay, credential_id=world.main, credential_label="Main", status=RunStatus.SUCCEEDED
    ) | fields
    with app.extensions["session_factory"]() as session:
        run = Run(**values)
        session.add(run)
        session.commit()
        return run.id
