"""Capture panel on the Credentials edit page, through the Flask test client with fake adapters (S5)."""
import json

import pytest
from sqlalchemy import text

from portal_auto.services.capture import CaptureService
from portal_auto.services.errors import Invalid
from portal_auto.services.saved_sessions import SOURCE_CAPTURED

COOKIE_VALUE = "captured-cookie-value-XYZ"


class FakeHandle:
    port = 45000

    def terminate(self) -> None:
        pass

    def alive(self) -> bool:
        return True


class FakeLauncher:
    def __init__(self, error: Exception | None = None):
        self.error, self.urls = error, []

    def launch(self, url, profile_dir):
        if self.error:
            raise self.error
        self.urls.append(url)
        return FakeHandle()


class FakeReader:
    def read(self, port):
        cookie = {"name": "sid", "value": COOKIE_VALUE, "domain": "bank.example", "path": "/"}
        return json.dumps({"cookies": [cookie], "origins": []})


@pytest.fixture
def launcher():
    return FakeLauncher()


@pytest.fixture(autouse=True)
def capture_service(app, launcher, tmp_path):
    service = CaptureService(launcher, FakeReader(), temp_root=tmp_path)
    app.extensions["capture_service"] = service
    yield service
    service.shutdown()


def post(client, token, path):
    return client.post(path, data={"csrf_token": token}, follow_redirects=True)


def stored_session(app, credential_id):
    with app.extensions["session_factory"]() as session:
        return session.execute(
            text("SELECT session_encrypted, session_source FROM credential WHERE id = :id"), {"id": credential_id}
        ).one()


def test_start_opens_chrome_on_the_portal_url_and_shows_the_open_capture(client, token, world, launcher):
    page = post(client, token, f"/credentials/{world.main}/capture/start").get_data(as_text=True)
    assert launcher.urls == ["https://bank.example"]
    assert "Chrome is open. Sign in by hand, then click Save session here" in page
    assert "Capture open until" in page and "Save session" in page


def test_save_stores_a_captured_session_and_never_renders_it(app, client, token, world):
    post(client, token, f"/credentials/{world.main}/capture/start")
    page = post(client, token, f"/credentials/{world.main}/capture/save").get_data(as_text=True)
    assert "Saved session captured" in page
    encrypted, source = stored_session(app, world.main)
    assert source == SOURCE_CAPTURED and COOKIE_VALUE not in encrypted
    assert COOKIE_VALUE not in page and "Capture open until" not in page


def test_expired_capture_says_so_instead_of_a_generic_refusal(client, token, world, capture_service):
    post(client, token, f"/credentials/{world.main}/capture/start")
    capture_service._open.timer.cancel()
    capture_service._expire_by_id(capture_service.active().id)
    for action in ("save", "cancel"):
        page = post(client, token, f"/credentials/{world.main}/capture/{action}").get_data(as_text=True)
        assert "The capture expired; start it again" in page
    other = post(client, token, f"/credentials/{world.spare}/capture/save").get_data(as_text=True)
    assert "No capture is open for this Credential" in other


def test_cancel_closes_the_capture(client, token, world, capture_service):
    post(client, token, f"/credentials/{world.main}/capture/start")
    page = post(client, token, f"/credentials/{world.main}/capture/cancel").get_data(as_text=True)
    assert "Capture cancelled" in page and capture_service.active() is None


def test_other_credential_sees_start_unavailable_and_cannot_save_or_cancel(client, token, world, capture_service, app):
    post(client, token, f"/credentials/{world.main}/capture/start")
    page = client.get(f"/credentials/{world.spare}/edit").get_data(as_text=True)
    assert "Capture is unavailable" in page
    for action in ("save", "cancel"):
        refused = post(client, token, f"/credentials/{world.spare}/capture/{action}").get_data(as_text=True)
        assert "No capture is open for this Credential" in refused
    assert capture_service.active().credential_id == world.main
    assert "already open" in post(client, token, f"/credentials/{world.spare}/capture/start").get_data(as_text=True)


def test_save_without_a_capture_is_refused(client, token, world):
    page = post(client, token, f"/credentials/{world.main}/capture/save").get_data(as_text=True)
    assert "No capture is open for this Credential" in page


def test_missing_chrome_is_a_friendly_error(app, client, token, world, tmp_path):
    app.extensions["capture_service"] = CaptureService(
        FakeLauncher(Invalid("Google Chrome was not found; install it or set PORTAL_AUTO_CHROME_COMMAND")),
        FakeReader(), temp_root=tmp_path,
    )
    page = post(client, token, f"/credentials/{world.main}/capture/start").get_data(as_text=True)
    assert "Google Chrome was not found" in page and "Traceback" not in page


def test_capture_posts_require_csrf(client, world):
    assert client.post(f"/credentials/{world.main}/capture/start").status_code == 400


def test_edit_page_no_longer_promises_capture_is_coming(client, world):
    assert "is coming" not in client.get(f"/credentials/{world.main}/edit").get_data(as_text=True)
