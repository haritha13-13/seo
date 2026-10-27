"""S5: the Run page shows the Inputs a Run used; Regenerate script on a Workflow with Inputs goes to the Executor."""
import json
import re
from urllib.parse import parse_qs, urlparse

import pytest
from helpers import add_run, csrf_token

from portal_auto.db.models import Run, RunStatus
from portal_auto.services.workflows import WorkflowService

STORED_PATH = "/private/uploads/run-7/deck/secret-folder-name.pptx"


@pytest.fixture
def publish(app, world) -> int:
    with app.extensions["session_factory"]() as session:
        steps = ["Upload {{deck:file}}", "Enter {{description}} in the Description box"]
        return WorkflowService(session).create(world.bank, "Publish deck", steps).id


def recorded(description: str = "Quarterly results", filename: str = "q3.pptx") -> str:
    return json.dumps({
        "deck": {"kind": "file", "filename": filename, "path": STORED_PATH},
        "description": {"kind": "text", "value": description},
    })


def run_count(app) -> int:
    with app.extensions["session_factory"]() as session:
        return session.query(Run).count()


def test_run_page_lists_inputs_used_with_text_values_and_original_filenames(app, client, world, publish):
    run_id = add_run(app, world, workflow_id=publish, inputs=recorded())
    html = client.get(f"/runs/{run_id}").get_data(as_text=True)
    assert "Inputs used" in html
    table = html[html.index("Inputs used"):]
    assert re.search(r"<td>deck</td>\s*<td>file</td>\s*<td>q3.pptx</td>", table)
    assert re.search(r"<td>description</td>\s*<td>text</td>\s*<td>Quarterly results</td>", table)


def test_run_page_never_shows_the_stored_path(app, client, world, publish):
    run_id = add_run(app, world, workflow_id=publish, inputs=recorded())
    html = client.get(f"/runs/{run_id}").get_data(as_text=True)
    assert STORED_PATH not in html and "secret-folder-name" not in html


def test_run_page_escapes_input_values_and_filenames(app, client, world, publish):
    attack = "<script>alert(1)</script>"
    run_id = add_run(app, world, workflow_id=publish, inputs=recorded(attack, attack + ".pptx"))
    html = client.get(f"/runs/{run_id}").get_data(as_text=True)
    assert attack not in html
    assert html.count("&lt;script&gt;alert(1)&lt;/script&gt;") == 2


def test_run_without_inputs_shows_no_inputs_table(app, client, world):
    html = client.get(f"/runs/{add_run(app, world)}").get_data(as_text=True)
    assert "Inputs used" not in html


def test_regenerate_on_a_workflow_with_inputs_goes_to_the_prefilled_executor(app, client, world, publish):
    failed = add_run(app, world, workflow_id=publish, status=RunStatus.FAILED, inputs=recorded())
    response = client.post(f"/runs/{failed}/regenerate", data={"csrf_token": csrf_token(client)})
    assert response.status_code == 302
    location = urlparse(response.headers["Location"])
    assert location.path == "/executor/"
    assert parse_qs(location.query) == {
        "portal_id": [str(world.bank)], "workflow_id": [str(publish)], "credential_id": [str(world.main)],
        "force_generate": ["1"],
    }
    assert run_count(app) == 1
    html = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "This Workflow has Inputs: fill them in again and press Execute." in html
    assert re.search(r'name="force_generate" value="1" checked', html)
    assert 'value="Quarterly results"' in html


def test_executor_leaves_force_regeneration_unticked_by_default(client, world, publish):
    query = {"portal_id": world.bank, "workflow_id": publish, "credential_id": world.main}
    html = client.get("/executor/", query_string=query).get_data(as_text=True)
    assert re.search(r'name="force_generate" value="1">', html)
