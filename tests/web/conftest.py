"""Shared web-test fixtures (S5: Flask test client). One app per test, real SQLite, fake run launcher."""
import threading
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from helpers import PLAIN_PASSWORD, csrf_token

from portal_auto.config import Settings
from portal_auto.services.credentials import CredentialService
from portal_auto.services.portals import PortalService
from portal_auto.services.runs import RunService
from portal_auto.services.vault import CredentialVault
from portal_auto.services.workflows import WorkflowService
from portal_auto.web import create_app


@pytest.fixture
def app(tmp_path):
    timeout_elapsed = threading.Event()
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'web.db'}", vault_key=Fernet.generate_key().decode(), root=tmp_path
    )
    app = create_app(settings)
    app.config.update(TESTING=True)
    app.extensions["run_service"] = RunService(
        app.extensions["session_factory"], settings, launcher=lambda run_id: 4000 + run_id, sleep=lambda seconds: timeout_elapsed.wait(5),
        pid_alive=lambda pid: True,
    )
    yield app
    timeout_elapsed.set()
    app.extensions["run_service"].wait_for_watchdogs()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def world(app):
    settings = app.extensions["settings"]
    with app.extensions["session_factory"]() as session:
        portals = PortalService(session)
        credentials = CredentialService(session, CredentialVault(settings.vault_key))
        workflows = WorkflowService(session)
        bank = portals.create("Bank", "https://bank.example")
        other = portals.create("Other", "https://other.example")
        retired = portals.create("Retired", "https://retired.example")
        portals.set_active(retired.id, False)
        pay = workflows.create(bank.id, "Pay bill", ["open the page", "click pay"])
        old = workflows.create(bank.id, "Old flow", ["go"])
        workflows.set_active(old.id, False)
        workflows.create(other.id, "Other flow", ["go"])
        main = credentials.create(bank.id, "Main", "alice", PLAIN_PASSWORD)
        spare = credentials.create(bank.id, "Spare", "bob", PLAIN_PASSWORD)
        credentials.set_active(spare.id, False)
        return SimpleNamespace(
            bank=bank.id, other=other.id, retired=retired.id, pay=pay.id, old=old.id, main=main.id, spare=spare.id
        )


@pytest.fixture
def token(client) -> str:
    return csrf_token(client)
