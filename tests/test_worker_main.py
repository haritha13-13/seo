import pytest

from portal_auto import worker
from portal_auto.db.models import Credential, Portal, Run, RunStatus, ScriptVersion, Workflow, WorkflowStep
from portal_auto.runtime.fakes import FakeScriptExecutor, FakeScriptGenerator
from portal_auto.services.vault import CredentialVault

PASSWORD = "hunter2-pass"


@pytest.fixture
def env(tmp_path, monkeypatch, vault_key):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PORTAL_AUTO_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("PORTAL_AUTO_VAULT_KEY", vault_key)
    return tmp_path


@pytest.fixture
def queued_run(session, vault_key, env) -> Run:
    portal = Portal(name="Demo", url="https://demo.test")
    credential = Credential(portal=portal, label="main", username="alice",
                            password_encrypted=CredentialVault(vault_key).encrypt(PASSWORD))
    workflow = Workflow(portal=portal, name="Login", steps=[WorkflowStep(position=1, instruction="Verify")])
    session.add_all([portal, credential, workflow])
    session.commit()
    run = Run(workflow_id=workflow.id, credential_id=credential.id, credential_label="main", active_slot=1, pid=123)
    session.add(run)
    session.commit()
    return run


def reload(session, run: Run) -> Run:
    session.expire_all()
    return session.get(Run, run.id)


def test_main_runs_the_run_to_completion(queued_run, session, monkeypatch):
    monkeypatch.setattr(worker, "AgentScriptGenerator", lambda settings: FakeScriptGenerator(code="print('ok')"))
    monkeypatch.setattr(worker, "SubprocessScriptExecutor", FakeScriptExecutor)
    assert worker.main([str(queued_run.id)]) == 0
    assert reload(session, queued_run).status == RunStatus.SUCCEEDED


def test_main_fails_the_run_and_frees_the_slot_when_setup_crashes(queued_run, session, monkeypatch, env):
    def broken_generator(settings):
        raise RuntimeError(f"cannot reach llm with key {PASSWORD}")

    monkeypatch.setattr(worker, "AgentScriptGenerator", broken_generator)
    assert worker.main([str(queued_run.id)]) != 0
    run = reload(session, queued_run)
    assert run.status == RunStatus.FAILED and run.active_slot is None and run.finished_at is not None
    assert "RuntimeError" in run.error and "cannot reach llm" not in run.error and PASSWORD not in run.error
    log = (env / "artifacts" / "logs" / f"run-{run.id}.log").read_text()
    assert "RuntimeError" in log


def test_main_log_never_contains_the_password(queued_run, session, monkeypatch, env):
    session.add(ScriptVersion(workflow_id=queued_run.workflow_id, version=1, steps_revision=1, code="print('ok')"))
    session.commit()

    class LeakyExecutor:
        def execute(self, code, env, headless, timeout_seconds):
            raise RuntimeError(f"leaked {env['PORTAL_PASSWORD']}")

    monkeypatch.setattr(worker, "AgentScriptGenerator", lambda settings: FakeScriptGenerator(code="print('ok')"))
    monkeypatch.setattr(worker, "SubprocessScriptExecutor", LeakyExecutor)
    worker.main([str(queued_run.id)])
    log = (env / "artifacts" / "logs" / f"run-{queued_run.id}.log").read_text()
    assert "RuntimeError" in log and PASSWORD not in log


def test_main_fails_the_run_when_the_vault_key_is_missing(queued_run, session, monkeypatch):
    monkeypatch.delenv("PORTAL_AUTO_VAULT_KEY")
    worker.main([str(queued_run.id)])
    run = reload(session, queued_run)
    assert run.status == RunStatus.FAILED and run.active_slot is None and run.error


def test_main_exits_cleanly_with_a_message_for_an_unknown_run(env, capsys):
    assert worker.main(["999"]) != 0
    assert "Run 999 not found" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [[], ["abc"]])
def test_main_exits_with_usage_for_a_bad_run_id(argv, capsys):
    assert worker.main(argv) != 0
    assert "usage" in capsys.readouterr().err.lower()


def test_main_keeps_a_generation_that_embedded_a_credential_out_of_the_database(queued_run, session, monkeypatch):
    monkeypatch.setattr(worker, "AgentScriptGenerator", lambda settings: FakeScriptGenerator(code=f"x = '{PASSWORD}'"))
    monkeypatch.setattr(worker, "SubprocessScriptExecutor", FakeScriptExecutor)
    worker.main([str(queued_run.id)])
    assert session.query(ScriptVersion).count() == 0


def test_worker_enforces_its_own_deadline_during_generation(queued_run, session, monkeypatch):
    import time

    class StuckGenerator:
        def generate(self, *args, **kwargs):
            time.sleep(30)

    monkeypatch.setenv("PORTAL_AUTO_RUN_TIMEOUT_SECONDS", "2")
    monkeypatch.setattr(worker, "AgentScriptGenerator", lambda settings: StuckGenerator())
    started = time.monotonic()
    worker.main([str(queued_run.id)])
    run = reload(session, queued_run)
    assert time.monotonic() - started < 10
    assert run.status == RunStatus.FAILED and "timed out" in run.error and run.active_slot is None


def test_main_sweeps_session_dirs_older_than_the_run_timeout_plus_a_minute(queued_run, monkeypatch):
    import os
    import tempfile
    import time
    from pathlib import Path
    monkeypatch.setenv("PORTAL_AUTO_RUN_TIMEOUT_SECONDS", "600")
    monkeypatch.setattr(worker, "AgentScriptGenerator", lambda settings: FakeScriptGenerator(code="print('ok')"))
    monkeypatch.setattr(worker, "SubprocessScriptExecutor", FakeScriptExecutor)
    root = Path(tempfile.gettempdir())
    stale, recent = root / "portal_session_test_stale", root / "portal_session_test_recent"
    stale.mkdir(), recent.mkdir()
    try:
        os.utime(stale, (time.time() - 661 - 10, time.time() - 661 - 10))
        os.utime(recent, (time.time() - 500, time.time() - 500))
        worker.main([str(queued_run.id)])
        assert not stale.exists() and recent.exists()
    finally:
        for directory in (stale, recent):
            directory.rmdir() if directory.exists() else None
