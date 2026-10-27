"""A Run's Inputs reach Generation and the script through the environment, and its uploads never outlive it."""
import os
import threading
import time
from pathlib import Path

import pytest

from portal_auto import worker
from portal_auto.config import Settings
from portal_auto.db.models import Credential, Portal, Run, RunStatus, ScriptVersion, Workflow
from portal_auto.db.session import init_schema, make_engine, make_session_factory
from portal_auto.inputs import UploadedFile
from portal_auto.runtime.fakes import FakeScriptExecutor, FakeScriptGenerator
from portal_auto.runtime.interfaces import ExecutionResult
from portal_auto.services.runs import RunService
from portal_auto.services.vault import CredentialVault
from portal_auto.services.workflows import WorkflowService
from portal_auto.worker import execute_run

DECK = b"%PDF-1.7 deck content"
STEPS = ["Type {{title}} into Title", "Upload {{deck:file}} as the deck"]
SCRIPT = 'page.fill(os.environ["PORTAL_INPUT_TITLE"])\nchooser.value.set_files(os.environ["PORTAL_INPUT_DECK"])\n'


@pytest.fixture
def vault(vault_key):
    return CredentialVault(vault_key)


@pytest.fixture
def settings(tmp_path):
    return Settings(root=tmp_path)


@pytest.fixture
def start(session, session_factory, vault, settings):
    """Starts Runs the way the Executor does, without a real worker process."""
    portal = Portal(name="Demo", url="https://demo.test")
    credential = Credential(portal=portal, label="main", username="alice", password_encrypted=vault.encrypt("pw-12345"))
    session.add_all([portal, credential])
    session.commit()
    workflow = WorkflowService(session).create(portal.id, "Publish", STEPS)
    released = threading.Event()
    service = RunService(session_factory, settings, lambda run_id: 4000 + run_id, kill=lambda pid: None,
                         sleep=lambda seconds: released.wait(), pid_alive=lambda pid: True)

    def start_run(title: str = "Q3 results", deck: UploadedFile = UploadedFile("deck.pdf", DECK)) -> int:
        return service.start_run(workflow.id, credential.id, inputs={"title": title}, files={"deck": deck}).id

    yield start_run
    released.set()
    service.wait_for_watchdogs()


@pytest.fixture
def stored_script(session, start) -> ScriptVersion:
    """A Current Script Version, so Runs replay it."""
    workflow = session.query(Workflow).one()
    version = ScriptVersion(workflow_id=workflow.id, version=1, steps_revision=workflow.steps_revision, code=SCRIPT)
    session.add(version)
    session.commit()
    return version


def go(session, run_id, settings, vault, generator=None, executor=None) -> Run:
    return execute_run(session, run_id, generator=generator or FakeScriptGenerator(code=SCRIPT),
                       executor=executor or FakeScriptExecutor(), vault=vault, settings=settings)


def uploads_of(settings, run_id) -> Path:
    return settings.uploads_dir / f"run-{run_id}"


def test_generation_and_the_script_get_each_input_as_an_environment_value(session, start, settings, vault):
    seen = []
    executor = FakeScriptExecutor(on_execute=lambda env: seen.append(Path(env["PORTAL_INPUT_DECK"]).read_bytes()))
    generator = FakeScriptGenerator(code=SCRIPT)
    go(session, start(), settings, vault, generator, executor)
    go(session, start(), settings, vault, generator, executor)

    assert len(generator.calls) == len(executor.calls) == 1
    for env in (generator.calls[0]["env"], executor.calls[0]["env"]):
        assert env["PORTAL_INPUT_TITLE"] == "Q3 results"
        assert Path(env["PORTAL_INPUT_DECK"]).name == "deck.pdf"
    assert seen == [DECK]


def test_generation_is_given_engine_placeholders_for_the_inputs(session, start, settings, vault):
    generator = FakeScriptGenerator(code=SCRIPT)
    go(session, start(), settings, vault, generator)

    assert generator.calls[0]["steps"] == [
        "Type {{ENV:PORTAL_INPUT_TITLE}} into Title", "Upload {{ENV:PORTAL_INPUT_DECK}} as the deck"]


def test_a_replay_uses_the_new_runs_values_with_the_stored_script(session, start, settings, vault):
    generator, executor = FakeScriptGenerator(code=SCRIPT), FakeScriptExecutor()
    go(session, start(), settings, vault, generator, executor)
    replay = go(session, start("Q4 outlook", UploadedFile("q4.pdf", b"%PDF q4")), settings, vault, generator, executor)

    assert replay.status == RunStatus.SUCCEEDED and len(generator.calls) == len(executor.calls) == 1
    env = executor.calls[0]["env"]
    assert env["PORTAL_INPUT_TITLE"] == "Q4 outlook" and Path(env["PORTAL_INPUT_DECK"]).name == "q4.pdf"
    assert executor.calls[0]["code"] == SCRIPT


class CrashingExecutor:
    def __init__(self, error: BaseException):
        self._error = error

    def execute(self, code, env, headless, timeout_seconds):
        raise self._error


@pytest.mark.parametrize("generator, executor", [
    (FakeScriptGenerator(code=SCRIPT), FakeScriptExecutor()),
    (FakeScriptGenerator(error="agent gave up"), FakeScriptExecutor()),
], ids=["generated-live", "generation-failed"])
def test_the_uploads_are_removed_whichever_way_a_generating_run_ends(session, start, settings, vault, generator, executor):
    run_id = start()
    assert uploads_of(settings, run_id).is_dir()

    go(session, run_id, settings, vault, generator, executor)

    assert not uploads_of(settings, run_id).exists() and executor.calls == []


@pytest.mark.parametrize("executor", [
    FakeScriptExecutor(),
    FakeScriptExecutor(ExecutionResult(1, "boom")),
    CrashingExecutor(RuntimeError("adapter bug")),
], ids=["succeeded", "script-failed", "crashed"])
def test_the_uploads_are_removed_whichever_way_a_replay_ends(session, start, settings, vault, executor, stored_script):
    run_id = start()
    assert uploads_of(settings, run_id).is_dir()

    run = go(session, run_id, settings, vault, executor=executor)

    assert run.script_version_id == stored_script.id and not run.generated
    assert not uploads_of(settings, run_id).exists()


def test_the_uploads_are_removed_when_the_worker_is_terminated(session, start, settings, vault, stored_script):
    run_id = start()
    with pytest.raises(SystemExit):
        go(session, run_id, settings, vault, executor=CrashingExecutor(SystemExit(143)))
    assert not uploads_of(settings, run_id).exists()


def test_a_generated_script_holding_an_uploaded_files_location_is_refused_and_not_stored(session, start, settings, vault):
    run_id = start()
    folder = str(uploads_of(settings, run_id).absolute())
    generator = FakeScriptGenerator(code=f'chooser.value.set_files("{folder}/deck/deck.pdf")')
    executor = FakeScriptExecutor()

    run = go(session, run_id, settings, vault, generator, executor)

    assert run.status == RunStatus.FAILED and "placeholder" in run.error and folder not in run.error
    assert session.query(ScriptVersion).count() == 0 and executor.calls == []


def test_an_uploaded_files_location_never_reaches_the_run_error(session, start, settings, vault):
    run_id = start()
    path = f"{uploads_of(settings, run_id).absolute()}/deck/deck.pdf"
    run = go(session, run_id, settings, vault, FakeScriptGenerator(error=f"could not upload {path}"))

    assert run.status == RunStatus.FAILED and "could not upload" in run.error
    assert str(settings.uploads_dir) not in run.error


@pytest.fixture
def worker_env(tmp_path, monkeypatch, vault_key):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PORTAL_AUTO_DATABASE_URL", f"sqlite:///{tmp_path / 'worker.db'}")
    monkeypatch.setenv("PORTAL_AUTO_VAULT_KEY", vault_key)
    monkeypatch.setenv("PORTAL_AUTO_RUN_TIMEOUT_SECONDS", "600")
    return tmp_path / "uploads"


def aged_folder(path: Path, age_seconds: float) -> Path:
    path.mkdir(parents=True)
    (path / "deck.pdf").write_bytes(DECK)
    then = time.time() - age_seconds
    os.utime(path, (then, then))
    return path


def test_the_worker_sweeps_only_upload_folders_older_than_any_run_can_live(worker_env, tmp_path):
    stale = aged_folder(worker_env / "run-7", 600 + 61 + 5)
    young = aged_folder(worker_env / "run-8", 600)
    other = aged_folder(worker_env / "keep-me", 10_000)
    target = aged_folder(tmp_path / "elsewhere", 10_000)
    (worker_env / "run-9").symlink_to(target)
    os.utime(worker_env / "run-9", (time.time() - 10_000,) * 2, follow_symlinks=False)

    worker.main(["999"])

    assert not stale.exists()
    assert young.exists() and other.exists()
    assert (worker_env / "run-9").is_symlink() and (target / "deck.pdf").exists()


def test_the_uploads_are_removed_when_the_worker_fails_before_the_run_begins(worker_env, tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{tmp_path / 'worker.db'}")
    init_schema(engine)
    with make_session_factory(engine)() as session:
        portal = Portal(name="Demo", url="https://demo.test")
        credential = Credential(portal=portal, label="main", username="alice", password_encrypted="x")
        session.add_all([portal, credential])
        session.commit()
        workflow = WorkflowService(session).create(portal.id, "Publish", STEPS)
        run = Run(workflow_id=workflow.id, credential_id=credential.id, credential_label="main", active_slot=1)
        session.add(run)
        session.commit()
        run_id = run.id
    folder = aged_folder(worker_env / f"run-{run_id}", 0)
    monkeypatch.setattr(worker, "AgentScriptGenerator", lambda settings: 1 / 0)

    assert worker.main([str(run_id)]) != 0
    assert not folder.exists()
