"""S6: one Script Version serves Runs with different Inputs. Opt-in: `pytest -m e2e` (needs the LLM key and
Chromium; the 'portal' is a local upload page that records what each Publish sent)."""
import io
import threading
import time
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default as default_policy
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from dotenv import dotenv_values
from sqlalchemy import select

from portal_auto.config import Settings
from portal_auto.db.models import Run, RunStatus, ScriptVersion
from portal_auto.web import create_app

pytestmark = pytest.mark.e2e

RUN_DEADLINE_SECONDS = 600
UPLOAD_PAGE = b"""<!doctype html>
<html><head><title>Upload portal</title>
<style>.visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
label.button { display: inline-block; padding: 4px 12px; border: 1px solid #333; cursor: pointer; }</style></head>
<body><h1>Publish a presentation</h1>
<form method="post" action="/publish" enctype="multipart/form-data">
  <p><input type="file" id="presentation" name="presentation" class="visually-hidden">
     <label for="presentation" class="button">Choose file</label></p>
  <p><label for="description">Description</label> <input type="text" id="description" name="description"></p>
  <p><label for="category">Category</label>
     <select id="category" name="category">
       <option value="">Pick one</option><option>Finance</option><option>Marketing</option><option>Engineering</option>
     </select></p>
  <p><button type="submit">Publish</button></p>
</form></body></html>"""
STEPS = [
    "Click the Choose file button and upload {{presentation:file}}",
    "Enter {{description}} in the Description box",
    "Select {{category}} in the Category dropdown",
    "Click Publish",
    'Verify the page shows the text "Published"',
]


@dataclass(frozen=True)
class Published:
    description: str
    category: str
    filename: str
    content: bytes


FIRST = Published("Quarterly results for the sales team", "Finance", "deck-one.pptx", b"FIRST-DECK-BYTES-0001")
SECOND = Published("Annual summary for the board", "Marketing", "deck-two.pdf", b"%PDF-1.4 SECOND-DECK-BYTES-0002")


def _published(content_type: str, body: bytes) -> Published:
    message = BytesParser(policy=default_policy).parsebytes(f"Content-Type: {content_type}\r\n\r\n".encode() + body)
    parts = {part.get_param("name", header="content-disposition"): part for part in message.iter_parts()}
    upload = parts["presentation"]
    return Published(
        parts["description"].get_content().strip(), parts["category"].get_content().strip(),
        upload.get_filename() or "", upload.get_payload(decode=True) or b"",
    )


def serve_upload_portal(received: list[Published], served: list[str]) -> tuple[HTTPServer, str]:
    """`served` collects the path of every page the portal answers, in order."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self._reply(UPLOAD_PAGE)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            received.append(_published(self.headers["Content-Type"], body))
            self._reply(b"<!doctype html><html><body><h1>Published</h1></body></html>")

        def _reply(self, page: bytes):
            served.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


@pytest.fixture
def received() -> list[Published]:
    return []


@pytest.fixture
def served() -> list[str]:
    return []


@pytest.fixture
def portal_site(received, served):
    server, base_url = serve_upload_portal(received, served)
    yield base_url
    server.shutdown()


@pytest.fixture
def app(tmp_path, monkeypatch):
    env = dotenv_values(".env")
    for key in ("PORTAL_AUTO_LLM_MODEL", "PORTAL_AUTO_LLM_BASE_URL", "PORTAL_AUTO_LLM_API_KEY", "PORTAL_AUTO_VAULT_KEY"):
        monkeypatch.setenv(key, env.get(key) or "")
    monkeypatch.setenv("PORTAL_AUTO_DATABASE_URL", f"sqlite:///{tmp_path / 'inputs-e2e.db'}")
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


def execute(client, supplied: Published) -> int:
    response = post(
        client, "/executor/", portal_id=1, workflow_id=1, credential_id=1, verified="1",
        input__description=supplied.description, input__category=supplied.category,
        file__presentation=(io.BytesIO(supplied.content), supplied.filename),
    )
    assert response.status_code == 302, response.get_data(as_text=True)[:800]
    return int(response.headers["Location"].rstrip("/").rsplit("/", 1)[1])


def test_the_live_generation_publishes_once_and_its_script_replays_with_new_inputs(
        app, portal_site, received, served, tmp_path):
    client = app.test_client()
    assert post(client, "/portals/new", name="Uploads", url=portal_site + "/").status_code == 302
    assert post(client, "/credentials/new", portal_id=1, label="Editor", username="editor",
                password="any-password").status_code == 302
    created = post(client, "/workflows/new", portal_id=1, name="Publish deck", active="on", action="save", step=STEPS)
    assert created.status_code == 302, created.get_data(as_text=True)[:800]

    first = wait_for_run(app, execute(client, FIRST))
    after_first = len(received)
    last_served_during_first = served[-1] if served else None
    second = wait_for_run(app, execute(client, SECOND))

    assert (first.status, second.status) == (RunStatus.SUCCEEDED, RunStatus.SUCCEEDED), (
        first.error, second.error, first.output[-3000:], second.output[-3000:],
    )
    assert first.generated and not second.generated
    with app.extensions["session_factory"]() as session:
        versions = session.scalars(select(ScriptVersion)).all()
    assert len(versions) == 1 and second.script_version_id == versions[0].id
    assert received[:after_first] == [FIRST], "the first Run publishes live once and does not replay"
    assert received[after_first:] == [SECOND]
    assert "Final page:" in first.output
    assert first.final_page_url == f"{portal_site}{last_served_during_first}"
    code = versions[0].code
    for supplied in (FIRST, SECOND):
        for literal in (supplied.description, supplied.category, supplied.filename, supplied.content.decode()):
            assert literal not in code
    assert not any((tmp_path / "uploads").glob("run-*"))
