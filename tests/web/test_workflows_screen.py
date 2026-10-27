"""Workflows screen, through the Flask test client."""
import pytest

from portal_auto.db.models import ScriptVersion
from portal_auto.services.workflows import WorkflowService


def post(client, token, path, **fields):
    return client.post(path, data={"csrf_token": token, **fields}, follow_redirects=True)


@pytest.fixture
def portal_id(client, token) -> int:
    post(client, token, "/portals/new", name="Acme", url="https://acme.test")
    return 1


def save(client, token, path, steps, name="Login", action="save", **extra):
    data = {"csrf_token": token, "name": name, "action": action, "step": steps, "active": "1", **extra}
    return client.post(path, data=data, follow_redirects=True)


def create_workflow(client, token, portal_id, steps=("Open page", "Verify dashboard"), name="Login"):
    return save(client, token, "/workflows/new", list(steps), name=name, portal_id=portal_id)


def stored_steps(app, workflow_id=1) -> list[str]:
    with app.extensions["session_factory"]() as session:
        return [s.instruction for s in WorkflowService(session).get(workflow_id).steps]


def test_created_workflow_appears_in_list_with_portal_name(client, token, portal_id):
    create_workflow(client, token, portal_id)
    html = client.get("/workflows/").get_data(as_text=True)
    assert "<td>Login</td>" in html and "<td>Acme</td>" in html


def test_duplicate_name_shows_error_and_creates_nothing(client, token, portal_id):
    create_workflow(client, token, portal_id)
    html = create_workflow(client, token, portal_id).get_data(as_text=True)
    assert "already has a workflow" in html
    assert client.get("/workflows/").get_data(as_text=True).count("<td>Login</td>") == 1


def test_workflow_without_steps_is_reported_on_the_form(client, token, portal_id):
    html = create_workflow(client, token, portal_id, steps=("",)).get_data(as_text=True)
    assert "at least one step" in html


def test_missing_csrf_is_rejected(client, portal_id):
    assert client.post("/workflows/new", data={"name": "x"}).status_code == 400


def test_inactive_portal_is_not_offered_in_portal_select(client, token, portal_id):
    post(client, token, "/portals/new", name="Dormant", url="https://d.test")
    post(client, token, "/portals/2/active", active="0")
    html = client.get("/workflows/new").get_data(as_text=True)
    assert "Acme" in html and "Dormant" not in html


def test_add_row_does_not_save_but_shows_an_extra_row(client, token, portal_id):
    create_workflow(client, token, portal_id)
    html = save(client, token, "/workflows/1/edit", ["Open page", "Verify dashboard"], action="add").get_data(
        as_text=True
    )
    assert html.count('name="step"') == 3


def test_saved_added_step_persists_in_order(app, client, token, portal_id):
    create_workflow(client, token, portal_id)
    save(client, token, "/workflows/1/edit", ["Open page", "Click login", "Verify dashboard"])
    assert stored_steps(app) == ["Open page", "Click login", "Verify dashboard"]


def test_remove_row_then_save_persists(app, client, token, portal_id):
    create_workflow(client, token, portal_id, steps=("A", "B", "Verify C"))
    rows = ["A", "B", "Verify C"]
    html = save(client, token, "/workflows/1/edit", rows, action="remove:1").get_data(as_text=True)
    assert html.count('name="step"') == 2
    save(client, token, "/workflows/1/edit", ["A", "Verify C"])
    assert stored_steps(app) == ["A", "Verify C"]


def test_move_down_and_up_reorder_rows_before_saving(app, client, token, portal_id):
    create_workflow(client, token, portal_id, steps=("A", "B", "Verify C"))
    html = save(client, token, "/workflows/1/edit", ["A", "B", "Verify C"], action="down:0").get_data(as_text=True)
    assert html.index(">B</textarea>") < html.index(">A</textarea>")
    html = save(client, token, "/workflows/1/edit", ["A", "B", "Verify C"], action="up:2").get_data(as_text=True)
    assert html.index(">Verify C</textarea>") < html.index(">B</textarea>")
    save(client, token, "/workflows/1/edit", ["B", "A", "Verify C"])
    assert stored_steps(app) == ["B", "A", "Verify C"]


def test_warning_when_no_verify_step(client, token, portal_id):
    html = create_workflow(client, token, portal_id, steps=("Open page",)).get_data(as_text=True)
    assert "flash-warning" in html and "did not error" in html


def test_no_warning_when_verify_step_present(client, token, portal_id):
    html = save(client, token, "/workflows/new", ["Open", "Verify heading"], portal_id=portal_id).get_data(as_text=True)
    assert "Workflow Login created" in html and "flash-warning" not in html


def test_editing_steps_with_a_script_flashes_stale(app, client, token, portal_id):
    create_workflow(client, token, portal_id)
    with app.extensions["session_factory"]() as session:
        session.add(ScriptVersion(workflow_id=1, version=1, steps_revision=1, code="pass"))
        session.commit()
    assert "Version 1" in client.get("/workflows/1/edit").get_data(as_text=True)
    html = save(client, token, "/workflows/1/edit", ["Open page", "Verify changed"]).get_data(as_text=True)
    assert "Stale" in html and "Version 1" not in html


def test_unchanged_steps_do_not_flash_stale(client, token, portal_id):
    create_workflow(client, token, portal_id)
    html = save(client, token, "/workflows/1/edit", ["Open page", "Verify dashboard"]).get_data(as_text=True)
    assert "Stale" not in html and "saved" in html


def test_deactivate_workflow(client, token, portal_id):
    create_workflow(client, token, portal_id)
    assert "Inactive" in post(client, token, "/workflows/1/active", active="0").get_data(as_text=True)


def test_delete_workflow_without_history(client, token, portal_id):
    create_workflow(client, token, portal_id)
    html = post(client, token, "/workflows/1/delete").get_data(as_text=True)
    assert "deleted" in html and "<td>Login</td>" not in html


def test_delete_workflow_with_script_versions_asks_for_confirmation(app, client, token, portal_id):
    create_workflow(client, token, portal_id)
    with app.extensions["session_factory"]() as session:
        session.add(ScriptVersion(workflow_id=1, version=1, steps_revision=1, code="pass"))
        session.commit()
    response = client.post("/workflows/1/delete", data={"csrf_token": token})
    assert response.status_code == 302 and response.headers["Location"].endswith("/workflows/1/delete")
    assert "deactivate the Workflow instead" in client.get("/workflows/1/delete").get_data(as_text=True)
