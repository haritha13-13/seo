"""S5: deleting a Workflow; one that has history asks for confirmation and then takes its Runs and scripts with it."""
from helpers import add_run

from portal_auto.db.models import Run, RunStatus, ScriptVersion


def post(client, token, path, **fields):
    return client.post(path, data={"csrf_token": token, **fields})


def workflow_names(client) -> str:
    """The Workflows table cells, so a flash message that mentions a name does not count as the Workflow being listed."""
    return "".join(client.get("/workflows/").get_data(as_text=True).split("<table")[1:])


def add_script(app, workflow_id: int) -> None:
    with app.extensions["session_factory"]() as session:
        session.add(ScriptVersion(workflow_id=workflow_id, version=1, steps_revision=1, code="x"))
        session.commit()


def run_count(app) -> int:
    with app.extensions["session_factory"]() as session:
        return session.query(Run).count()


def test_a_workflow_with_no_history_is_deleted_at_once(client, token, world):
    response = post(client, token, f"/workflows/{world.old}/delete")

    assert response.status_code == 302 and response.headers["Location"].endswith("/workflows/")
    assert "Old flow" not in workflow_names(client)


def test_a_workflow_with_history_goes_to_a_confirmation_page_and_is_not_deleted(app, client, token, world):
    add_run(app, world)

    response = post(client, token, f"/workflows/{world.pay}/delete")

    assert response.status_code == 302 and response.headers["Location"].endswith(f"/workflows/{world.pay}/delete")
    assert "Pay bill" in workflow_names(client)


def test_the_confirmation_page_says_what_will_be_deleted(app, client, world):
    add_run(app, world)
    add_run(app, world, status=RunStatus.FAILED)
    add_script(app, world.pay)

    html = client.get(f"/workflows/{world.pay}/delete").get_data(as_text=True)

    assert "Pay bill" in html and "2 Runs" in html and "1 stored script" in html
    assert 'name="confirm"' in html and "cannot be undone" in html.lower()


def test_confirming_deletes_the_workflow_its_runs_and_its_scripts(app, client, token, world):
    add_run(app, world)
    add_script(app, world.pay)

    response = post(client, token, f"/workflows/{world.pay}/delete", confirm="1")

    assert response.status_code == 302
    assert "Pay bill" not in workflow_names(client) and run_count(app) == 0
    with app.extensions["session_factory"]() as session:
        assert session.query(ScriptVersion).count() == 0


def test_a_workflow_with_a_run_in_progress_cannot_be_deleted(app, client, token, world):
    add_run(app, world, status=RunStatus.RUNNING)

    post(client, token, f"/workflows/{world.pay}/delete", confirm="1")
    page = client.get(f"/workflows/{world.pay}/delete").get_data(as_text=True)

    assert "Pay bill" in workflow_names(client)
    assert "in progress" in page.lower() and 'name="confirm"' not in page


def test_the_confirmation_page_of_an_unknown_workflow_is_not_found(client):
    assert client.get("/workflows/999/delete").status_code == 404


def test_deleting_needs_the_security_token(client, world):
    assert client.post(f"/workflows/{world.old}/delete", data={}).status_code == 400
