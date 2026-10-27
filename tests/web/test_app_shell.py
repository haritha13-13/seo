"""S5: the app shell, through the Flask test client."""
import pytest

from helpers import csrf_token

from portal_auto.config import Settings
from portal_auto.web import create_app


@pytest.fixture
def app(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'web.db'}", root=tmp_path)
    app = create_app(settings)
    app.config.update(TESTING=True)

    @app.post("/_echo")
    def echo():
        return "accepted"

    return app


@pytest.fixture
def client(app):
    return app.test_client()


def test_health_reports_ok_when_database_is_reachable(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_post_without_csrf_token_is_rejected(client):
    assert client.post("/_echo").status_code == 400


def test_post_with_valid_csrf_token_is_accepted(client):
    token = csrf_token(client)
    assert client.post("/_echo", data={"csrf_token": token}).status_code == 200


def test_post_with_wrong_csrf_token_is_rejected(client):
    csrf_token(client)
    assert client.post("/_echo", data={"csrf_token": "nope"}).status_code == 400


def test_unknown_page_renders_friendly_not_found(client):
    response = client.get("/nope")
    assert response.status_code == 404
    assert b"Not found" in response.data
