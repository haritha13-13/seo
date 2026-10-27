import pytest

from portal_auto.db.models import Credential, Run, ScriptVersion
from portal_auto.services.errors import Duplicate, InUse, Invalid, NotFound
from portal_auto.services.portals import PortalService
from portal_auto.services.workflows import WorkflowService


@pytest.fixture
def portal(session):
    return PortalService(session).create("Bank", "https://bank.example")


@pytest.fixture
def workflows(session):
    return WorkflowService(session)


def instructions(workflow) -> list[tuple[int, str]]:
    return [(s.position, s.instruction) for s in workflow.steps]


def add_script(session, workflow, version: int, revision: int) -> ScriptVersion:
    script = ScriptVersion(workflow_id=workflow.id, version=version, steps_revision=revision, code="x")
    session.add(script)
    session.commit()
    return script


def test_create_numbers_steps_and_drops_blanks(workflows, portal):
    wf = workflows.create(portal.id, "Pay", ["  open page ", "", "   ", "click pay"])
    assert instructions(wf) == [(1, "open page"), (2, "click pay")]
    assert wf.steps_revision == 1


def test_create_needs_a_non_blank_step_and_name(workflows, portal):
    with pytest.raises(Invalid):
        workflows.create(portal.id, "Pay", ["", "  "])
    with pytest.raises(Invalid):
        workflows.create(portal.id, " ", ["go"])
    with pytest.raises(NotFound):
        workflows.create(999, "Pay", ["go"])


def test_duplicate_name_per_portal_only(workflows, portal, session):
    workflows.create(portal.id, "Pay", ["go"])
    with pytest.raises(Duplicate):
        workflows.create(portal.id, "Pay", ["go"])
    other = PortalService(session).create("Other", "http://o")
    workflows.create(other.id, "Pay", ["go"])


def test_update_renames_and_rejects_clash(workflows, portal):
    workflows.create(portal.id, "A", ["go"])
    b = workflows.create(portal.id, "B", ["go"])
    with pytest.raises(Duplicate):
        workflows.update(b.id, "A")
    assert workflows.update(b.id, "C").name == "C"


def test_replace_steps_with_change_renumbers_and_bumps_revision(workflows, portal):
    wf = workflows.create(portal.id, "Pay", ["a", "b", "c"])
    assert workflows.replace_steps(wf.id, ["x", "", "y"]) is True
    fresh = workflows.get(wf.id)
    assert instructions(fresh) == [(1, "x"), (2, "y")]
    assert fresh.steps_revision == 2


def test_replace_steps_same_list_after_cleaning_is_no_change(workflows, portal):
    wf = workflows.create(portal.id, "Pay", ["a", "b"])
    assert workflows.replace_steps(wf.id, [" a", "", "b "]) is False
    fresh = workflows.get(wf.id)
    assert fresh.steps_revision == 1
    assert instructions(fresh) == [(1, "a"), (2, "b")]


def test_reordering_counts_as_change(workflows, portal):
    wf = workflows.create(portal.id, "Pay", ["a", "b"])
    assert workflows.replace_steps(wf.id, ["b", "a"]) is True


def test_replace_steps_rejects_empty_list(workflows, portal):
    wf = workflows.create(portal.id, "Pay", ["a"])
    with pytest.raises(Invalid):
        workflows.replace_steps(wf.id, [" "])
    assert instructions(workflows.get(wf.id)) == [(1, "a")]


def test_current_script_none_until_generated(workflows, portal):
    wf = workflows.create(portal.id, "Pay", ["a"])
    assert workflows.current_script(wf.id) is None


def test_current_script_is_newest_matching_revision(workflows, portal, session):
    wf = workflows.create(portal.id, "Pay", ["a"])
    add_script(session, wf, 1, 1)
    newest = add_script(session, wf, 2, 1)
    assert workflows.current_script(wf.id).id == newest.id


def test_current_script_becomes_none_after_steps_change(workflows, portal, session):
    wf = workflows.create(portal.id, "Pay", ["a"])
    add_script(session, wf, 1, 1)
    assert workflows.current_script(wf.id) is not None
    workflows.replace_steps(wf.id, ["b"])
    assert workflows.current_script(wf.id) is None


def test_current_script_survives_unchanged_steps(workflows, portal, session):
    wf = workflows.create(portal.id, "Pay", ["a"])
    add_script(session, wf, 1, 1)
    workflows.replace_steps(wf.id, ["a"])
    assert workflows.current_script(wf.id) is not None


@pytest.mark.parametrize("steps,expected", [
    (["open", "VERIFY the total"], True),
    (["Please Verify code"], True),
    (["open", "click"], False),
])
def test_has_verify_step_is_case_insensitive(workflows, portal, steps, expected):
    wf = workflows.create(portal.id, "Pay", steps)
    assert workflows.has_verify_step(wf.id) is expected


def test_list_filters_by_portal_and_active(workflows, portal, session):
    other = PortalService(session).create("Other", "http://o")
    a = workflows.create(portal.id, "A", ["go"])
    workflows.create(portal.id, "B", ["go"])
    workflows.create(other.id, "C", ["go"])
    workflows.set_active(a.id, False)
    assert [w.name for w in workflows.list()] == ["A", "B", "C"]
    assert [w.name for w in workflows.list(portal_id=portal.id)] == ["A", "B"]
    assert [w.name for w in workflows.list(portal_id=portal.id, active_only=True)] == ["B"]


def test_get_unknown_raises_not_found(workflows):
    with pytest.raises(NotFound):
        workflows.get(999)


def test_delete_unused_workflow(workflows, portal):
    wf = workflows.create(portal.id, "Pay", ["go"])
    workflows.delete(wf.id)
    assert workflows.list() == []


def test_delete_blocked_by_script_versions(workflows, portal, session):
    wf = workflows.create(portal.id, "Pay", ["go"])
    add_script(session, wf, 1, 1)
    with pytest.raises(InUse):
        workflows.delete(wf.id)


def test_delete_blocked_by_runs(workflows, portal, session):
    wf = workflows.create(portal.id, "Pay", ["go"])
    cred = Credential(portal_id=portal.id, label="l", username="u", password_encrypted="x")
    session.add(cred)
    session.flush()
    session.add(Run(workflow_id=wf.id, credential_id=cred.id, credential_label="l"))
    session.commit()
    with pytest.raises(InUse):
        workflows.delete(wf.id)


def test_a_new_workflow_does_not_start_signed_in(workflows, portal):
    assert workflows.create(portal.id, "W", ["a"]).starts_signed_in is False


def test_toggling_starts_signed_in_makes_the_current_script_stale(workflows, portal, session):
    workflow = workflows.create(portal.id, "W", ["a"])
    add_script(session, workflow, 1, workflow.steps_revision)
    assert workflows.current_script(workflow.id) is not None

    workflows.set_starts_signed_in(workflow.id, True)

    assert workflow.starts_signed_in is True
    assert workflows.current_script(workflow.id) is None


def test_setting_starts_signed_in_to_its_current_value_keeps_scripts_current(workflows, portal, session):
    workflow = workflows.create(portal.id, "W", ["a"])
    add_script(session, workflow, 1, workflow.steps_revision)

    workflows.set_starts_signed_in(workflow.id, False)

    assert workflows.current_script(workflow.id) is not None


def test_create_can_start_signed_in(workflows, portal):
    assert workflows.create(portal.id, "Pay", ["go"], starts_signed_in=True).starts_signed_in is True
    assert workflows.create(portal.id, "Other", ["go"]).starts_signed_in is False


def add_run(session, workflow, status: str = "succeeded", script=None) -> Run:
    credential = Credential(portal_id=workflow.portal_id, label=f"c{workflow.id}-{status}", username="u")
    session.add(credential)
    session.flush()
    run = Run(workflow_id=workflow.id, credential_id=credential.id, credential_label=credential.label,
              status=status, script_version_id=script.id if script else None)
    session.add(run)
    session.commit()
    return run


def test_usage_counts_the_runs_and_scripts_a_workflow_has(workflows, portal, session):
    workflow = workflows.create(portal.id, "W", ["a"])
    script = add_script(session, workflow, 1, workflow.steps_revision)
    add_run(session, workflow, script=script)
    add_run(session, workflow, status="failed")

    usage = workflows.usage(workflow.id)

    assert (usage.runs, usage.script_versions, usage.active_runs) == (2, 1, 0)


def test_deleting_with_history_removes_the_workflow_its_runs_and_its_scripts(workflows, portal, session):
    doomed = workflows.create(portal.id, "Doomed", ["a"])
    kept = workflows.create(portal.id, "Kept", ["b"])
    doomed_script = add_script(session, doomed, 1, doomed.steps_revision)
    add_run(session, doomed, script=doomed_script)
    kept_script = add_script(session, kept, 1, kept.steps_revision)
    kept_run = add_run(session, kept, script=kept_script)

    workflows.delete(doomed.id, with_history=True)

    assert [w.name for w in workflows.list()] == ["Kept"]
    assert session.query(Run).count() == 1 and session.query(ScriptVersion).count() == 1
    assert session.get(Run, kept_run.id) is not None


def test_deleting_with_history_is_refused_while_a_run_is_in_progress(workflows, portal, session):
    workflow = workflows.create(portal.id, "Busy", ["a"])
    add_run(session, workflow, status="running")

    with pytest.raises(Invalid, match="in progress"):
        workflows.delete(workflow.id, with_history=True)

    assert workflows.get(workflow.id) is not None


def test_deleting_without_history_still_refuses_a_workflow_that_has_runs(workflows, portal, session):
    workflow = workflows.create(portal.id, "Used", ["a"])
    add_run(session, workflow)

    with pytest.raises(InUse):
        workflows.delete(workflow.id)
