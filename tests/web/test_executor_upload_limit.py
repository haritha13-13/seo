"""S5: POST /executor/ accepts large uploads; every other route keeps the 3 MB request limit."""
import io

import pytest
from helpers import csrf_token

from portal_auto.db.models import Run
from portal_auto.inputs import MB
from portal_auto.services.workflows import WorkflowService


@pytest.fixture
def presentation_workflow(app, world) -> int:
    with app.extensions["session_factory"]() as session:
        return WorkflowService(session).create(world.bank, "Upload deck", ["Upload {{deck:file}}"]).id


def execute_with(client, world, workflow_id: int, content: bytes, **overrides):
    data = {
        "portal_id": world.bank, "workflow_id": workflow_id, "credential_id": world.main, "verified": "1",
        "csrf_token": csrf_token(client), "file__deck": (io.BytesIO(content), "big deck.pptx"),
    }
    return client.post("/executor/", data=data, content_type="multipart/form-data", **overrides)


def test_a_multi_megabyte_presentation_is_accepted_on_the_executor(app, client, world, presentation_workflow):
    content = b"p" * (25 * MB)
    response = execute_with(client, world, presentation_workflow, content)
    assert response.status_code == 302, response.get_data(as_text=True)[:400]
    with app.extensions["session_factory"]() as session:
        run = session.query(Run).one()
    assert run.input_values["deck"]["filename"] == "big deck.pptx"
    with open(run.input_values["deck"]["path"], "rb") as stored:
        assert len(stored.read()) == len(content)


def test_a_request_over_the_executor_limit_gets_a_friendly_413(client, world, presentation_workflow):
    response = execute_with(
        client, world, presentation_workflow, b"x", environ_overrides={"CONTENT_LENGTH": str(121 * MB)}
    )
    assert response.status_code == 413
    assert "The upload is too large (limit 100 MB for a file, 20 MB for an image)." in response.get_data(as_text=True)


def test_other_routes_keep_the_3_mb_limit(client, world):
    data = {"csrf_token": csrf_token(client), "name": "Big", "url": "https://big.example", "note": "x" * (4 * MB)}
    response = client.post("/portals/new", data=data, content_type="multipart/form-data")
    assert response.status_code == 413
    assert "100 MB" not in response.get_data(as_text=True)

