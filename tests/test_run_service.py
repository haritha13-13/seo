import threading
from datetime import timedelta

import pytest

from portal_auto.config import Settings
from portal_auto.db.models import Credential, Portal, Run, RunStatus, ScriptVersion, Workflow, WorkflowStep, utcnow
from portal_auto.services.errors import Invalid, NotFound, RunAlreadyActive
from portal_auto.services.runs import RunService


DEAD_PID = 1


class Launcher:
    def __init__(self):
        self.launched: list[int] = []

    def __call__(self, run_id: int) -> int:
        self.launched.append(run_id)
        return 4000 + run_id


@pytest.fixture
def launcher():
    return Launcher()


@pytest.fixture
def killed():
    return []


@pytest.fixture
def timeout_elapsed():
    event = threading.Event()
    yield event
    event.set()


@pytest.fixture
def service(session_factory, launcher, killed, timeout_elapsed):
    service = RunService(
        session_factory, Settings(run_timeout_seconds=5), launcher,
        kill=killed.append, sleep=lambda seconds: timeout_elapsed.wait(5), pid_alive=lambda pid: pid != DEAD_PID,
    )
    yield service
    timeout_elapsed.set()
    service.wait_for_watchdogs()


@pytest.fixture
def world(session):
    portal = Portal(name="Demo", url="https://demo.test")
    credential = Credential(portal=portal, label="main", username="alice", password_encrypted="x")
    workflow = Workflow(portal=portal, name="Login", steps=[WorkflowStep(position=1, instruction="Click")])
    session.add_all([portal, credential, workflow])
    session.commit()
    return portal, credential, workflow


def start(service, world, **kwargs) -> Run:
    return service.start_run(world[2].id, world[1].id, **kwargs)


def test_start_run_queues_snapshots_label_launches_and_stores_pid(service, world, launcher):
    run = start(service, world, show_browser=True, force_generate=True)
    assert run.status == RunStatus.QUEUED
    assert run.active_slot == 1
    assert run.credential_label == "main"
    assert (run.show_browser, run.force_generate) == (True, True)
    assert launcher.launched == [run.id]
    assert service.get_run(run.id).pid == 4000 + run.id


def test_second_start_while_active_is_rejected(service, world, launcher):
    start(service, world)
    with pytest.raises(RunAlreadyActive):
        start(service, world)
    assert len(launcher.launched) == 1


def test_new_run_allowed_after_previous_finished(service, world, session_factory):
    first = start(service, world)
    with session_factory() as s:
        run = s.get(Run, first.id)
        run.status, run.active_slot = RunStatus.SUCCEEDED, None
        s.commit()
    assert start(service, world).id != first.id


@pytest.mark.parametrize("target", ["portal", "workflow", "credential"])
def test_inactive_records_are_invalid(service, world, session, target):
    record = {"portal": world[0], "workflow": world[2], "credential": world[1]}[target]
    record.active = False
    session.commit()
    with pytest.raises(Invalid):
        start(service, world)


def test_credential_of_another_portal_is_invalid(service, world, session):
    other = Portal(name="Other", url="https://o.test")
    foreign = Credential(portal=other, label="x", username="u", password_encrypted="x")
    session.add_all([other, foreign])
    session.commit()
    with pytest.raises(Invalid):
        service.start_run(world[2].id, foreign.id)


def test_unknown_workflow_is_not_found(service, world):
    with pytest.raises(NotFound):
        service.start_run(999, world[1].id)


def test_launch_failure_fails_run_and_frees_slot(session_factory, world):
    def broken(run_id: int) -> int:
        raise OSError("no python")

    service = RunService(session_factory, Settings(), broken, sleep=lambda s: None)
    run = start(service, world)
    assert run.status == RunStatus.FAILED and run.active_slot is None
    assert start(service, world).id != run.id


def test_watchdog_marks_still_active_run_failed_kills_pid_and_frees_slot(service, world, killed, timeout_elapsed):
    run = start(service, world)
    timeout_elapsed.set()
    service.wait_for_watchdogs()
    stored = service.get_run(run.id)
    assert stored.status == RunStatus.FAILED
    assert "timed out" in stored.error
    assert stored.active_slot is None and stored.finished_at is not None
    assert killed == [4000 + run.id]
    assert service.active_run() is None


def test_watchdog_leaves_finished_run_alone(session_factory, world, killed, launcher):
    def finishing_sleep(seconds: float) -> None:
        with session_factory() as s:
            run = s.query(Run).one()
            run.status, run.active_slot = RunStatus.SUCCEEDED, None
            s.commit()

    service = RunService(session_factory, Settings(), launcher, kill=killed.append, sleep=finishing_sleep)
    run = start(service, world)
    service.wait_for_watchdogs()
    assert service.get_run(run.id).status == RunStatus.SUCCEEDED
    assert killed == []


def test_recover_stale_runs_fails_runs_with_dead_pid(service, session, world):
    session.add(Run(workflow_id=world[2].id, credential_id=world[1].id, credential_label="main",
                    status=RunStatus.RUNNING, active_slot=1, pid=DEAD_PID))
    session.commit()
    assert service.recover_stale_runs() == 1
    run = service.list_runs()[0]
    assert run.status == RunStatus.FAILED and "interrupted" in run.error and run.active_slot is None


def test_recover_stale_runs_keeps_live_runs(service, session, world):
    session.add(Run(workflow_id=world[2].id, credential_id=world[1].id, credential_label="main",
                    status=RunStatus.RUNNING, active_slot=1, pid=4242))
    session.commit()
    assert service.recover_stale_runs() == 0
    assert service.active_run() is not None


def test_get_run_unknown_is_not_found(service):
    with pytest.raises(NotFound):
        service.get_run(5)


def test_list_runs_newest_first_with_filters(service, session, world):
    portal, credential, workflow = world
    other_portal = Portal(name="Other", url="https://o.test")
    other_wf = Workflow(portal=other_portal, name="Other", steps=[WorkflowStep(position=1, instruction="x")])
    session.add_all([other_portal, other_wf])
    session.commit()
    ids = []
    for wf, status in [(workflow, RunStatus.SUCCEEDED), (other_wf, RunStatus.FAILED), (workflow, RunStatus.FAILED)]:
        run = Run(workflow_id=wf.id, credential_id=credential.id, credential_label="main", status=status)
        session.add(run)
        session.commit()
        ids.append(run.id)
    assert [r.id for r in service.list_runs()] == ids[::-1]
    assert [r.id for r in service.list_runs(portal_id=other_portal.id)] == [ids[1]]
    assert [r.id for r in service.list_runs(workflow_id=workflow.id)] == [ids[2], ids[0]]
    assert [r.id for r in service.list_runs(status=RunStatus.FAILED)] == [ids[2], ids[1]]
    assert len(service.list_runs(limit=1)) == 1


def add_active_run(session, world, **fields) -> Run:
    run = Run(workflow_id=world[2].id, credential_id=world[1].id, credential_label="main",
              status=RunStatus.RUNNING, active_slot=1, **fields)
    session.add(run)
    session.commit()
    return run


def test_start_run_reaps_an_active_run_whose_worker_died_and_proceeds(service, world, session_factory):
    first = start(service, world)
    with session_factory() as s:
        s.get(Run, first.id).pid = DEAD_PID
        s.commit()
    second = start(service, world)
    assert second.id != first.id
    reaped = service.get_run(first.id)
    assert reaped.status == RunStatus.FAILED and "interrupted" in reaped.error and reaped.active_slot is None


def test_active_run_reaps_a_dead_worker_instead_of_reporting_it(service, session, world):
    add_active_run(session, world, pid=DEAD_PID)
    assert service.active_run() is None
    assert service.list_runs()[0].status == RunStatus.FAILED


def test_active_run_leaves_a_run_that_has_no_pid_yet(service, session, world):
    add_active_run(session, world, pid=None)
    assert service.active_run() is not None


def test_launcher_failing_with_a_non_os_error_still_fails_the_run_and_frees_the_slot(session_factory, world):
    def broken(run_id: int) -> int:
        raise ValueError("bad interpreter path")

    service = RunService(session_factory, Settings(), broken, sleep=lambda s: None)
    run = start(service, world)
    assert run.status == RunStatus.FAILED and run.active_slot is None
    assert "bad interpreter" not in run.error
    assert start(service, world).id != run.id


def test_watchdog_has_already_marked_the_run_failed_when_it_kills_but_the_slot_stays_held_until_after(
        session_factory, world, launcher):
    seen = {}

    def kill_and_try_to_start(pid: int) -> None:
        with session_factory() as s:
            run = s.query(Run).one()
            seen["status"] = run.status
        try:
            start(service, world)
        except RunAlreadyActive:
            seen["slot_held"] = True

    service = RunService(session_factory, Settings(), launcher, kill=kill_and_try_to_start, sleep=lambda s: None)
    run = start(service, world)
    service.wait_for_watchdogs()
    assert seen == {"status": RunStatus.FAILED, "slot_held": True}
    assert service.get_run(run.id).active_slot is None


def test_watchdog_does_not_kill_when_the_worker_finished_just_before(session_factory, world, killed, launcher):
    def sleep_then_worker_finishes_during_check(seconds: float) -> None:
        with session_factory() as s:
            run = s.query(Run).one()
            run.status, run.active_slot, run.output = RunStatus.SUCCEEDED, None, "done"
            s.commit()

    service = RunService(session_factory, Settings(), launcher, kill=killed.append,
                         sleep=sleep_then_worker_finishes_during_check)
    run = start(service, world)
    service.wait_for_watchdogs()
    stored = service.get_run(run.id)
    assert (stored.status, stored.output, stored.error) == (RunStatus.SUCCEEDED, "done", "")
    assert killed == []


def test_watchdog_still_kills_the_worker_when_the_database_fails(session_factory, world, killed, launcher):
    calls = {"count": 0}

    def flaky_factory():
        calls["count"] += 1
        if calls["count"] > 1:
            raise RuntimeError("database went away")
        return session_factory()

    service = RunService(flaky_factory, Settings(), launcher, kill=killed.append, sleep=lambda s: None,
                         pid_alive=lambda pid: pid not in killed)
    run = start(service, world)
    service.wait_for_watchdogs()
    assert killed == [4000 + run.id]


def test_recover_stale_runs_kills_and_fails_a_live_worker_past_the_timeout(session_factory, session, world, killed):
    service = RunService(session_factory, Settings(run_timeout_seconds=60), kill=killed.append,
                         pid_alive=lambda pid: True)
    run = add_active_run(session, world, pid=4242, queued_at=utcnow() - timedelta(seconds=61))
    assert service.recover_stale_runs() == 1
    stored = service.get_run(run.id)
    assert stored.status == RunStatus.FAILED and "timed out" in stored.error and stored.active_slot is None
    assert killed == [4242]


def test_recover_stale_runs_keeps_a_live_worker_within_the_timeout(session_factory, session, world, killed):
    service = RunService(session_factory, Settings(run_timeout_seconds=60), kill=killed.append,
                         pid_alive=lambda pid: True)
    add_active_run(session, world, pid=4242, queued_at=utcnow() - timedelta(seconds=30))
    assert service.recover_stale_runs() == 0
    assert killed == []


def test_returned_runs_carry_workflow_portal_and_script_version_without_further_queries(service, session, world):
    portal, credential, workflow = world
    version = ScriptVersion(workflow_id=workflow.id, version=3, steps_revision=1, code="x")
    session.add(version)
    session.commit()
    run = Run(workflow_id=workflow.id, credential_id=credential.id, credential_label="main",
              script_version_id=version.id, status=RunStatus.SUCCEEDED)
    session.add(run)
    session.commit()

    for found in (service.get_run(run.id), service.list_runs()[0]):
        assert found.workflow.name == "Login"
        assert found.workflow.portal.name == "Demo"
        assert found.script_version.version == 3
