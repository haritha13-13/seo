"""S6: a Saved Session carries a Run past a sign-in the script cannot do. Opt-in: `pytest -m e2e`
(needs the LLM key and Chromium; the 'portal' is a local page that only greets a visitor with the cookie)."""
import io
import json
import time

import pytest
from cookie_site import COOKIE_NAME, COOKIE_VALUE, serve_cookie_gated
from dotenv import dotenv_values
from sqlalchemy import select

from portal_auto.config import Settings
from portal_auto.db.models import Credential, Run, RunStatus, ScriptVersion
from portal_auto.web import create_app

pytestmark = pytest.mark.e2e

RUN_DEADLINE_SECONDS = 420
SESSION_STATE = {
    "cookies": [{
        "name": COOKIE_NAME, "value": COOKIE_VALUE, "domain": "127.0.0.1", "path": "/", "expires": -1,
        "httpOnly": False, "secure": False, "sameSite": "Lax",
    }],
    "origins": [],
}


@pytest.fixture
def portal_site():
    server, base_url = serve_cookie_gated()
    yield base_url
    server.shutdown()


@pytest.fixture
def app(tmp_path, monkeypatch):
    env = dotenv_values(".env")
    for key in ("PORTAL_AUTO_LLM_MODEL", "PORTAL_AUTO_LLM_BASE_URL", "PORTAL_AUTO_LLM_API_KEY", "PORTAL_AUTO_VAULT_KEY"):
        monkeypatch.setenv(key, env.get(key) or "")
    monkeypatch.setenv("PORTAL_AUTO_DATABASE_URL", f"sqlite:///{tmp_path / 'session-e2e.db'}")
    monkeypatch.chdir(tmp_path)  # the app and its workers keep uploads and artifacts under the working directory
    app = create_app(Settings.from_env())
    app.config.update(TESTING=True)
    return app


def post(client, path, **fields):
    client.get("/health")
    with client.session_transaction() as flask_session:
        fields["csrf_token"] = flask_session["csrf_token"]
    return client.post(path, data=fields)


def wait_for_run(app, run_id: int) -> Run:
    deadline = time.monotonic() + RUN_DEADLINE_SECONDS
    while time.monotonic() < deadline:
        with app.extensions["session_factory"]() as session:
            run = session.get(Run, run_id)
            if run.status in RunStatus.FINISHED:
                return run
        time.sleep(2)
    raise AssertionError(f"Run {run_id} did not finish in {RUN_DEADLINE_SECONDS}s")


def execute(client) -> int:
    response = post(client, "/executor/", portal_id=1, workflow_id=1, credential_id=1, verified="1")
    assert response.status_code == 302, response.get_data(as_text=True)[:600]
    return int(response.headers["Location"].rstrip("/").rsplit("/", 1)[1])


def test_a_saved_session_signs_the_run_in_and_is_refreshed(app, portal_site):
    client = app.test_client()
    assert post(client, "/portals/new", name="Local", url=portal_site + "/").status_code == 302
    assert post(client, "/credentials/new", portal_id=1, label="by hand", username="me@example.com").status_code == 302

    upload = post(client, "/credentials/1/session", session_file=(io.BytesIO(json.dumps(SESSION_STATE).encode()), "s.json"))
    assert upload.status_code == 302

    created = post(client, "/workflows/new", portal_id=1, name="Check dashboard", active="on", action="save",
                   starts_signed_in="1", step=['Verify the page shows the text "signed in"'])
    assert created.status_code == 302, created.get_data(as_text=True)[:600]

    first = wait_for_run(app, execute(client))
    second = wait_for_run(app, execute(client))

    assert (first.status, second.status) == (RunStatus.SUCCEEDED, RunStatus.SUCCEEDED), (first.error, second.error, first.output)
    assert first.used_session and second.used_session
    assert first.generated and not second.generated
    assert first.output.startswith("Generated live:") and "OK:" not in first.output, "the first Run must not replay"
    with app.extensions["session_factory"]() as session:
        versions = session.scalars(select(ScriptVersion)).all()
        credential = session.get(Credential, 1)
        assert len(versions) == 1 and second.script_version_id == versions[0].id
        assert credential.session_source == "refreshed"
    leaked = [first.output, second.output, first.error, second.error, versions[0].code]
    assert not any(COOKIE_VALUE in text for text in leaked)
