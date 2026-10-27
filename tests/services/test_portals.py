import pytest

from portal_auto.db.models import Credential, Workflow
from portal_auto.services.errors import Duplicate, InUse, Invalid, NotFound
from portal_auto.services.portals import PortalService


@pytest.fixture
def portals(session):
    return PortalService(session)


def test_create_and_get(portals):
    portal = portals.create("Bank", "https://bank.example")
    fetched = portals.get(portal.id)
    assert (fetched.name, fetched.url, fetched.active) == ("Bank", "https://bank.example", True)


def test_duplicate_name_rejected_and_session_stays_usable(portals):
    portals.create("Bank", "https://bank.example")
    with pytest.raises(Duplicate):
        portals.create("Bank", "https://other.example")
    assert len(portals.list()) == 1
    portals.create("Other", "http://other.example")


def test_rename_to_existing_name_rejected(portals):
    portals.create("A", "http://a")
    b = portals.create("B", "http://b")
    with pytest.raises(Duplicate):
        portals.update(b.id, "A", "http://b")


@pytest.mark.parametrize("url", ["ftp://x", "bank.example", ""])
def test_url_must_be_http(portals, url):
    with pytest.raises(Invalid):
        portals.create("Bank", url)


def test_blank_name_rejected(portals):
    with pytest.raises(Invalid):
        portals.create("  ", "http://x")


def test_update_validates_url_and_saves(portals):
    portal = portals.create("Bank", "http://x")
    with pytest.raises(Invalid):
        portals.update(portal.id, "Bank", "nope")
    updated = portals.update(portal.id, "Bank2", "https://y")
    assert (updated.name, updated.url) == ("Bank2", "https://y")


def test_list_is_ordered_and_filters_inactive(portals):
    b = portals.create("B", "http://b")
    portals.create("A", "http://a")
    portals.set_active(b.id, False)
    assert [p.name for p in portals.list()] == ["A", "B"]
    assert [p.name for p in portals.list(active_only=True)] == ["A"]


def test_get_unknown_raises_not_found(portals):
    with pytest.raises(NotFound):
        portals.get(999)


def test_delete_unused_portal(portals):
    portal = portals.create("A", "http://a")
    portals.delete(portal.id)
    assert portals.list() == []


def test_delete_blocked_by_credentials(portals, session):
    portal = portals.create("A", "http://a")
    session.add(Credential(portal_id=portal.id, label="l", username="u", password_encrypted="x"))
    session.commit()
    with pytest.raises(InUse):
        portals.delete(portal.id)


def test_delete_blocked_by_workflows(portals, session):
    portal = portals.create("A", "http://a")
    session.add(Workflow(portal_id=portal.id, name="w"))
    session.commit()
    with pytest.raises(InUse):
        portals.delete(portal.id)


def _workflow_with_current_script(session, portal):
    from portal_auto.db.models import ScriptVersion
    from portal_auto.services.workflows import WorkflowService

    workflows = WorkflowService(session)
    workflow = workflows.create(portal.id, "Pay", ["open", "verify paid"])
    session.add(ScriptVersion(workflow_id=workflow.id, version=1, steps_revision=workflow.steps_revision, code="x"))
    session.commit()
    assert workflows.current_script(workflow.id) is not None
    return workflows, workflow


def test_changing_the_url_makes_the_portals_scripts_stale(session, portals):
    portal = portals.create("Bank", "https://bank.example")
    workflows, workflow = _workflow_with_current_script(session, portal)
    portals.update(portal.id, "Bank", "https://bank.example/v2")
    assert workflows.current_script(workflow.id) is None


def test_unchanged_url_keeps_scripts_current_even_when_renamed(session, portals):
    portal = portals.create("Bank", "https://bank.example")
    workflows, workflow = _workflow_with_current_script(session, portal)
    portals.update(portal.id, "Bank renamed", "https://bank.example")
    assert workflows.current_script(workflow.id) is not None


def test_extra_hosts_are_normalised_and_exposed_as_a_list(session):
    portal = PortalService(session).create("Behance", "https://www.behance.net", extra_hosts=" Adobe.com,\nauth.example.org ,, ")
    assert portal.allowed_hosts == ["adobe.com", "auth.example.org"]


@pytest.mark.parametrize("bad", ["http://evil.test", "evil.test/path", "has space.com", "a b"])
def test_extra_hosts_must_be_bare_hostnames(session, bad):
    with pytest.raises(Invalid):
        PortalService(session).create("Bad", "https://bad.test", extra_hosts=bad)


def test_extra_hosts_can_be_changed_on_update(session):
    service = PortalService(session)
    portal = service.create("Behance", "https://www.behance.net")
    assert service.update(portal.id, "Behance", "https://www.behance.net", extra_hosts="adobe.com").allowed_hosts == ["adobe.com"]
