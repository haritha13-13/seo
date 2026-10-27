"""S6: the whole system against a real portal. Opt-in: `pytest -m e2e` (needs network, LLM key, Chromium)."""
import time

import pytest
from dotenv import dotenv_values

from portal_auto.config import Settings
from portal_auto.db.models import Run, RunStatus, ScriptVersion
from portal_auto.web import create_app

pytestmark = pytest.mark.e2e

DEMO_URL = "https://the-internet.herokuapp.com/login"
STEPS = "\n".join([
    "Enter {{username}} in the username field",
    "Enter {{password}} in the password field",
    "Click the Login button",
    'Verify the page shows a message containing "You logged into a secure area!"',
])
SECURE_URL = "https://the-internet.herokuapp.com/secure"
RUN_DEADLINE_SECONDS = 420


@pytest.fixture
def app(tmp_path, monkeypatch):
    env = dotenv_values(".env")
    for key in ("PORTAL_AUTO_LLM_MODEL", "PORTAL_AUTO_LLM_BASE_URL", "PORTAL_AUTO_LLM_API_KEY", "PORTAL_AUTO_VAULT_KEY"):
        monkeypatch.setenv(key, env.get(key) or "")
    monkeypatch.setenv("PORTAL_AUTO_DATABASE_URL", f"sqlite:///{tmp_path / 'e2e.db'}")
    monkeypatch.chdir(tmp_path)  # the app and its workers keep uploads and artifacts under the working directory
    app = create_app(Settings.from_env())
    app.config.update(TESTING=True)
    return app


def post(client, path, **fields):
    client.get("/health")
    with client.session_transaction() as flask_session:
        fields["csrf_token"] = flask_session["csrf_token"]
    return client.post(path, data=fields)


def wait_for_run(app, run_id) -> Run:
    deadline = time.monotonic() + RUN_DEADLINE_SECONDS
    while time.monotonic() < deadline:
        with app.extensions["session_factory"]() as session:
            run = session.get(Run, run_id)
            if run.status in RunStatus.FINISHED:
                return run
        time.sleep(2)
    raise AssertionError(f"Run {run_id} did not finish in {RUN_DEADLINE_SECONDS}s")


def workflow_form(client) -> dict:
    client.get("/health")
    with client.session_transaction() as flask_session:
        token = flask_session["csrf_token"]
    return {"csrf_token": token, "portal_id": 1, "name": "Login", "active": "on", "action": "save",
            "step": STEPS.split("\n")}


def execute(client, ids) -> int:
    response = post(client, "/executor/", verified="on", **ids)
    assert response.status_code == 302, response.get_data(as_text=True)[:500]
    return int(response.headers["Location"].rstrip("/").rsplit("/", 1)[1])


def test_first_run_is_the_live_generation_then_second_run_replays(app):
    client = app.test_client()
    assert post(client, "/portals/new", name="Demo", url=DEMO_URL).status_code == 302
    assert post(client, "/credentials/new", portal_id=1, label="main", username="tomsmith",
                password="SuperSecretPassword!").status_code == 302
    created = client.post("/workflows/new", data=workflow_form(client))
    assert created.status_code == 302, created.get_data(as_text=True)[:500]

    ids = {"portal_id": 1, "workflow_id": 1, "credential_id": 1}
    first = wait_for_run(app, execute(client, ids))
    second = wait_for_run(app, execute(client, ids))

    assert (first.status, second.status) == (RunStatus.SUCCEEDED, RunStatus.SUCCEEDED), (first.error, second.error, first.output)
    with app.extensions["session_factory"]() as session:
        versions = session.query(ScriptVersion).all()
    assert len(versions) == 1, "second run must replay the stored script, not generate again"
    assert first.script_version_id == second.script_version_id == versions[0].id
    assert first.generated and not second.generated
    assert first.output.startswith("Generated live:") and "OK:" not in first.output, "the first Run must not replay"
    assert first.final_page_url == second.final_page_url == SECURE_URL
    assert "SuperSecretPassword!" not in first.output + second.output + versions[0].code
