import pytest
from sqlalchemy import select

from portal_auto.db.models import Credential, Run, Workflow
from portal_auto.services.credentials import CredentialService
from portal_auto.services.errors import Duplicate, InUse, NotFound
from portal_auto.services.portals import PortalService
from portal_auto.services.vault import CredentialVault


@pytest.fixture
def vault(vault_key):
    return CredentialVault(vault_key)


@pytest.fixture
def credentials(session, vault):
    return CredentialService(session, vault)


@pytest.fixture
def portal(session):
    return PortalService(session).create("Bank", "https://bank.example")


def stored_password(session, credential_id: int) -> str:
    session.expire_all()
    return session.scalar(select(Credential.password_encrypted).where(Credential.id == credential_id))


def test_password_is_stored_encrypted_and_decrypts(credentials, portal, session, vault):
    cred = credentials.create(portal.id, "main", "alice", "hunter2")
    raw = stored_password(session, cred.id)
    assert raw != "hunter2" and "hunter2" not in raw
    assert vault.decrypt(raw) == "hunter2"


def test_create_requires_a_known_portal(credentials, portal):
    with pytest.raises(NotFound):
        credentials.create(999, "main", "alice", "pw")


@pytest.mark.parametrize("no_password", [None, ""])
def test_a_credential_can_be_created_without_a_password(credentials, portal, session, no_password):
    cred = credentials.create(portal.id, "google", "me@example.com", no_password)
    assert stored_password(session, cred.id) is None
    assert cred.has_password is False


def test_a_credential_with_neither_password_nor_session_is_not_usable(credentials, portal):
    assert not credentials.create(portal.id, "google", "me@example.com", None).usable
    with_password = credentials.create(portal.id, "main", "alice", "pw")
    assert with_password.has_password and with_password.usable


def test_duplicate_label_per_portal_only(credentials, portal, session):
    credentials.create(portal.id, "main", "alice", "pw")
    with pytest.raises(Duplicate):
        credentials.create(portal.id, "main", "bob", "pw")
    other = PortalService(session).create("Other", "http://o")
    credentials.create(other.id, "main", "bob", "pw")


@pytest.mark.parametrize("password", [None, ""])
def test_blank_password_on_update_keeps_old(credentials, portal, session, vault, password):
    cred = credentials.create(portal.id, "main", "alice", "hunter2")
    credentials.update(cred.id, "renamed", "alice2", password)
    assert vault.decrypt(stored_password(session, cred.id)) == "hunter2"
    assert (credentials.get(cred.id).label, credentials.get(cred.id).username) == ("renamed", "alice2")


def test_new_password_on_update_replaces_old(credentials, portal, session, vault):
    cred = credentials.create(portal.id, "main", "alice", "hunter2")
    credentials.update(cred.id, "main", "alice", "newpw")
    assert vault.decrypt(stored_password(session, cred.id)) == "newpw"


def test_update_to_existing_label_rejected(credentials, portal):
    credentials.create(portal.id, "a", "u", "pw")
    b = credentials.create(portal.id, "b", "u", "pw")
    with pytest.raises(Duplicate):
        credentials.update(b.id, "a", "u")


def test_list_for_portal_filters_and_orders(credentials, portal, session):
    b = credentials.create(portal.id, "b", "u", "pw")
    credentials.create(portal.id, "a", "u", "pw")
    other = PortalService(session).create("Other", "http://o")
    credentials.create(other.id, "z", "u", "pw")
    credentials.set_active(b.id, False)
    assert [c.label for c in credentials.list_for_portal(portal.id)] == ["a", "b"]
    assert [c.label for c in credentials.list_for_portal(portal.id, active_only=True)] == ["a"]


def test_get_unknown_raises_not_found(credentials):
    with pytest.raises(NotFound):
        credentials.get(999)


def test_delete_unused(credentials, portal):
    cred = credentials.create(portal.id, "main", "alice", "pw")
    credentials.delete(cred.id)
    assert credentials.list_for_portal(portal.id) == []


def test_delete_blocked_when_a_run_used_it(credentials, portal, session):
    cred = credentials.create(portal.id, "main", "alice", "pw")
    workflow = Workflow(portal_id=portal.id, name="w")
    session.add(workflow)
    session.flush()
    session.add(Run(workflow_id=workflow.id, credential_id=cred.id, credential_label="main"))
    session.commit()
    with pytest.raises(InUse):
        credentials.delete(cred.id)
