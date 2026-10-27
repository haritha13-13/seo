from helpers import add_run, csrf_token

from portal_auto.db.models import Run, RunStatus, ScriptVersion


def test_list_is_newest_first_and_shows_names_label_and_badge(app, client, world):
    add_run(app, world, credential_label="First")
    add_run(app, world, credential_label="Second", status=RunStatus.FAILED)
    html = client.get("/runs/").get_data(as_text=True)
    assert html.index("Second") < html.index("First")
    assert "Bank" in html and "Pay bill" in html
    assert 'badge badge-failed' in html


def test_list_filters_by_portal_workflow_and_status(app, client, world):
    other_workflow = 3
    add_run(app, world, credential_label="OnPay")
    add_run(app, world, workflow_id=other_workflow, credential_label="OnOther", status=RunStatus.FAILED)
    by_portal = client.get(f"/runs/?portal_id={world.other}").get_data(as_text=True)
    assert "OnOther" in by_portal and "OnPay" not in by_portal
    by_workflow = client.get(f"/runs/?workflow_id={world.pay}").get_data(as_text=True)
    assert "OnPay" in by_workflow and "OnOther" not in by_workflow
    by_status = client.get("/runs/?status=failed").get_data(as_text=True)
    assert "OnOther" in by_status and "OnPay" not in by_status


def test_list_shows_script_version_number(app, client, world):
    with app.extensions["session_factory"]() as session:
        script = ScriptVersion(workflow_id=world.pay, version=7, steps_revision=1, code="x")
        session.add(script)
        session.commit()
        script_id = script.id
    add_run(app, world, script_version_id=script_id)
    assert "v7" in client.get("/runs/").get_data(as_text=True)


def test_detail_escapes_output_html(app, client, world):
    run_id = add_run(app, world, output="<script>alert(1)</script>", error="<b>boom</b>")
    html = client.get(f"/runs/{run_id}").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;b&gt;boom&lt;/b&gt;" in html
    assert "Main" in html


def test_unknown_run_is_404(client):
    assert client.get("/runs/99").status_code == 404


def test_active_run_page_refreshes_and_finished_one_does_not(app, client, world):
    active = add_run(app, world, status=RunStatus.RUNNING, active_slot=1)
    finished = add_run(app, world)
    assert 'http-equiv="refresh" content="3"' in client.get(f"/runs/{active}").get_data(as_text=True)
    assert 'http-equiv="refresh"' not in client.get(f"/runs/{finished}").get_data(as_text=True)


def test_screenshot_inside_artifacts_dir_is_served(app, client, world, tmp_path):
    shot = tmp_path / "artifacts" / "fail.png"
    shot.parent.mkdir()
    shot.write_bytes(b"PNGDATA")
    run_id = add_run(app, world, screenshot_path=str(shot))
    response = client.get(f"/runs/{run_id}/screenshot")
    assert response.status_code == 200 and response.data == b"PNGDATA"
    assert f"/runs/{run_id}/screenshot" in client.get(f"/runs/{run_id}").get_data(as_text=True)


def test_screenshot_outside_artifacts_dir_is_404(app, client, world, tmp_path):
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"SECRET")
    traversal = "../secret.png"
    for path in (str(secret), traversal):
        run_id = add_run(app, world, screenshot_path=path)
        assert client.get(f"/runs/{run_id}/screenshot").status_code == 404


def test_screenshot_missing_is_404(app, client, world):
    assert client.get(f"/runs/{add_run(app, world)}/screenshot").status_code == 404
    gone = add_run(app, world, screenshot_path="nope.png")
    assert client.get(f"/runs/{gone}/screenshot").status_code == 404


def test_failed_run_regenerate_starts_a_force_generate_run(app, client, world):
    failed = add_run(app, world, status=RunStatus.FAILED, show_browser=True)
    assert "Regenerate script" in client.get(f"/runs/{failed}").get_data(as_text=True)
    response = client.post(f"/runs/{failed}/regenerate", data={"csrf_token": csrf_token(client)})
    assert response.status_code == 302 and response.headers["Location"].endswith(f"/runs/{failed + 1}")
    with app.extensions["session_factory"]() as session:
        new = session.get(Run, failed + 1)
        assert (new.force_generate, new.show_browser, new.workflow_id, new.credential_id) == (
            True, True, world.pay, world.main,
        )


def test_regenerate_requires_csrf_and_a_failed_run(app, client, world):
    failed = add_run(app, world, status=RunStatus.FAILED)
    assert client.post(f"/runs/{failed}/regenerate").status_code == 400
    ok = add_run(app, world)
    html = client.get(f"/runs/{ok}").get_data(as_text=True)
    assert "Regenerate script" not in html
    client.post(f"/runs/{ok}/regenerate", data={"csrf_token": csrf_token(client)})
    with app.extensions["session_factory"]() as session:
        assert session.query(Run).count() == 2
