from helpers import PLAIN_PASSWORD, csrf_token

from portal_auto.db.models import Run, RunStatus, ScriptVersion


def selection(world, **extra) -> dict:
    return {"portal_id": world.bank, "workflow_id": world.pay, "credential_id": world.main} | extra


def run_count(app) -> int:
    with app.extensions["session_factory"]() as session:
        return session.query(Run).count()


def post_execute(client, world, **extra):
    data = selection(world) | {"csrf_token": csrf_token(client)} | extra
    return client.post("/executor/", data=data)


def test_portal_dropdown_lists_only_active_portals(client, world):
    html = client.get("/executor/").get_data(as_text=True)
    assert "Bank" in html and "Other" in html
    assert "Retired" not in html


def test_workflow_and_credential_dropdowns_list_only_active_records_of_the_portal(client, world):
    html = client.get(f"/executor/?portal_id={world.bank}").get_data(as_text=True)
    assert "Pay bill" in html and "Main" in html
    assert "Old flow" not in html and "Spare" not in html and "Other flow" not in html


def test_summary_shows_steps_masked_credential_and_never_the_password(client, world):
    html = client.get("/executor/", query_string=selection(world)).get_data(as_text=True)
    assert "https://bank.example" in html
    assert html.index("open the page") < html.index("click pay")
    assert "alice" in html and "••••••••" in html
    assert PLAIN_PASSWORD not in html
    assert "no \"verify\" Step" in html


def test_summary_announces_generation_when_no_current_script_version(client, world):
    html = client.get("/executor/", query_string=selection(world)).get_data(as_text=True)
    assert "Generation will happen first" in html


def test_summary_shows_current_script_version_number(app, client, world):
    with app.extensions["session_factory"]() as session:
        session.add(ScriptVersion(workflow_id=world.pay, version=3, steps_revision=1, code="x"))
        session.commit()
    html = client.get("/executor/", query_string=selection(world)).get_data(as_text=True)
    assert "v3" in html and "Generation will happen first" not in html


def test_execute_without_verification_is_rejected_and_creates_no_run(app, client, world):
    response = post_execute(client, world)
    assert "Verification" in response.get_data(as_text=True)
    assert "flash-error" in response.get_data(as_text=True)
    assert run_count(app) == 0


def test_execute_with_verification_creates_a_run_and_redirects_to_it(app, client, world):
    response = post_execute(client, world, verified="1", show_browser="1")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/runs/1")
    assert run_count(app) == 1


def test_force_regeneration_checkbox_is_passed_to_the_run(app, client, world):
    post_execute(client, world, verified="1", force_generate="1")
    with app.extensions["session_factory"]() as session:
        assert session.get(Run, 1).force_generate is True


def test_second_execute_while_a_run_is_active_creates_no_second_run(app, client, world):
    post_execute(client, world, verified="1")
    response = post_execute(client, world, verified="1")
    assert response.status_code == 302 and response.headers["Location"].endswith("/runs/1")
    assert run_count(app) == 1


def test_inactive_credential_is_rejected_without_a_run(app, client, world):
    response = post_execute(client, world, verified="1", credential_id=world.spare)
    assert "flash-error" in response.get_data(as_text=True)
    assert run_count(app) == 0


def test_finished_run_allows_the_next_execute(app, client, world):
    post_execute(client, world, verified="1")
    with app.extensions["session_factory"]() as session:
        run = session.get(Run, 1)
        run.status, run.active_slot = RunStatus.SUCCEEDED, None
        session.commit()
    assert post_execute(client, world, verified="1").status_code == 302
