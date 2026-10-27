import pytest

from portal_auto.config import Settings
from portal_auto.db.models import (
    Credential, Portal, PublicationStatus, Run, RunStatus, ScriptVersion, Workflow, WorkflowStep,
)
from portal_auto.runtime.fakes import FakeScriptExecutor, FakeScriptGenerator
from portal_auto.runtime.interfaces import ExecutionResult
from portal_auto.runtime.publication import PublicationCheck
from portal_auto.services.vault import CredentialVault
from portal_auto.worker import RunNotFound, execute_run

PASSWORD = "hunter2-pass"


@pytest.fixture
def vault(vault_key):
    return CredentialVault(vault_key)


@pytest.fixture
def generator():
    return FakeScriptGenerator(code="print('script')")


@pytest.fixture
def executor():
    return FakeScriptExecutor()


@pytest.fixture
def world(session, vault):
    portal = Portal(name="Demo", url="https://demo.test")
    credential = Credential(portal=portal, label="main", username="alice", password_encrypted=vault.encrypt(PASSWORD))
    workflow = Workflow(portal=portal, name="Login", steps=[
        WorkflowStep(position=1, instruction="Type {{username}} into user"),
        WorkflowStep(position=2, instruction="Type {{password}} into password"),
        WorkflowStep(position=3, instruction="Verify welcome text"),
    ])
    session.add_all([portal, credential, workflow])
    session.commit()
    return workflow, credential


def new_run(session, world, **fields) -> Run:
    workflow, credential = world
    run = Run(workflow_id=workflow.id, credential_id=credential.id, credential_label=credential.label,
              active_slot=1, **fields)
    session.add(run)
    session.commit()
    return run


def go(session, run, generator, executor, vault, **settings) -> Run:
    return execute_run(session, run.id, generator=generator, executor=executor, vault=vault,
                       settings=Settings(**settings))


def finish_run(session, world, generator, executor, vault, **fields) -> Run:
    return go(session, new_run(session, world, **fields), generator, executor, vault)


@pytest.fixture
def stored_script(session, world) -> ScriptVersion:
    """A Current Script Version, so the next Run replays it."""
    workflow = world[0]
    version = ScriptVersion(workflow_id=workflow.id, version=1, steps_revision=workflow.steps_revision,
                            code="print('stored')")
    session.add(version)
    session.commit()
    return version


def test_first_run_is_the_live_generation_and_stores_version_one_without_executing_it(
        session, world, generator, executor, vault):
    run = finish_run(session, world, generator, executor, vault)
    assert run.status == RunStatus.SUCCEEDED and run.error == ""
    assert len(generator.calls) == 1 and executor.calls == []
    version = session.get(ScriptVersion, run.script_version_id)
    assert (version.version, version.steps_revision, version.code) == (1, 1, "print('script')")
    assert run.output.startswith("Generated live: this Run carried out the Steps in the agent's browser.")
    assert "The stored script (v1) is used from the next Run." in run.output
    assert run.started_at is not None and run.finished_at is not None and run.active_slot is None


def test_a_live_generation_reports_the_page_the_agent_ended_on(session, world, executor, vault):
    generator = FakeScriptGenerator(code="print('script')", final_page_url="https://demo.test/decks/42")
    run = finish_run(session, world, generator, executor, vault)
    assert run.output.splitlines()[-1] == "Final page: https://demo.test/decks/42"
    assert run.final_page_url == "https://demo.test/decks/42"


def test_a_live_generation_without_a_final_page_says_nothing_about_one(session, world, generator, executor, vault):
    assert "Final page" not in finish_run(session, world, generator, executor, vault).output


def test_the_final_page_of_a_live_generation_is_redacted(session, world, executor, vault):
    generator = FakeScriptGenerator(code="print('script')", final_page_url="https://demo.test/home?user=alice")
    run = finish_run(session, world, generator, executor, vault)
    assert "alice" not in run.output and "Final page: https://demo.test/home?user=***" in run.output


def test_generator_receives_engine_steps_url_and_env(session, world, generator, executor, vault):
    finish_run(session, world, generator, executor, vault)
    call = generator.calls[0]
    assert call["url"] == "https://demo.test"
    assert call["steps"][:2] == ["Type {{ENV:PORTAL_USERNAME}} into user", "Type {{ENV:PORTAL_PASSWORD}} into password"]
    assert call["headless"] is True
    assert call["env"]["PORTAL_PASSWORD"] == PASSWORD


def test_executor_gets_decrypted_credentials_timeout_and_headed_flag(
        session, world, generator, executor, vault, stored_script):
    run = new_run(session, world, show_browser=True)
    execute_run(session, run.id, generator=generator, executor=executor, vault=vault,
                settings=Settings(run_timeout_seconds=77))
    call = executor.calls[0]
    assert call["code"] == "print('stored')" and generator.calls == []
    assert call["env"]["PORTAL_USERNAME"] == "alice"
    assert call["env"]["PORTAL_PASSWORD"] == PASSWORD
    assert "PORTAL_ARTIFACTS_DIR" in call["env"]
    assert call["headless"] is False
    assert 0 < call["timeout_seconds"] < 77


def test_second_run_replays_without_generating(session, world, generator, executor, vault):
    first = finish_run(session, world, generator, executor, vault)
    second = finish_run(session, world, generator, executor, vault)
    assert len(generator.calls) == 1 and len(executor.calls) == 1
    assert executor.calls[0]["code"] == "print('script')"
    assert second.status == RunStatus.SUCCEEDED and second.output == "OK"
    assert second.script_version_id == first.script_version_id


def test_editing_steps_makes_next_run_generate_version_two(session, world, generator, executor, vault):
    finish_run(session, world, generator, executor, vault)
    world[0].steps_revision += 1
    session.commit()
    run = finish_run(session, world, generator, executor, vault)
    version = session.get(ScriptVersion, run.script_version_id)
    assert (version.version, version.steps_revision) == (2, 2)
    assert len(generator.calls) == 2 and executor.calls == []
    assert run.status == RunStatus.SUCCEEDED and "(v2)" in run.output


def test_force_generate_regenerates_current_workflow(session, world, generator, executor, vault):
    finish_run(session, world, generator, executor, vault)
    run = finish_run(session, world, generator, executor, vault, force_generate=True)
    assert len(generator.calls) == 2 and executor.calls == []
    assert session.get(ScriptVersion, run.script_version_id).version == 2
    assert run.status == RunStatus.SUCCEEDED and run.generated


def test_failed_replay_marks_failed_and_never_calls_generator(session, world, generator, executor, vault):
    finish_run(session, world, generator, executor, vault)
    executor.result = ExecutionResult(1, "boom", "/tmp/shot.png")
    run = finish_run(session, world, generator, executor, vault)
    assert run.status == RunStatus.FAILED
    assert run.error and PASSWORD not in run.error
    assert run.screenshot_path == "/tmp/shot.png"
    assert len(generator.calls) == 1


def test_generation_failure_fails_run_with_error_and_skips_execution(session, world, generator, executor, vault):
    generator.error = "agent gave up"
    run = finish_run(session, world, generator, executor, vault)
    assert run.status == RunStatus.FAILED
    assert "agent gave up" in run.error
    assert executor.calls == []
    assert run.active_slot is None and run.finished_at is not None


def test_password_is_redacted_from_output(session, world, generator, executor, vault, stored_script):
    executor.result = ExecutionResult(0, f"typed {PASSWORD} ok")
    run = finish_run(session, world, generator, executor, vault)
    assert PASSWORD not in run.output
    assert run.output == "typed *** ok"


@pytest.mark.parametrize("result", [ExecutionResult(0, "ok"), ExecutionResult(1, "bad")])
def test_active_slot_cleared_at_terminal_states(session, world, generator, executor, vault, result, stored_script):
    executor.result = result
    assert finish_run(session, world, generator, executor, vault).active_slot is None


def test_a_generating_run_goes_from_generating_straight_to_succeeded(session, world, executor, vault):
    seen: list[str] = []
    run = new_run(session, world)

    class Spy(FakeScriptGenerator):
        def generate(self, *args, **kwargs):
            seen.append(session.get(Run, run.id).status)
            return super().generate(*args, **kwargs)

    go(session, run, Spy(), executor, vault)
    assert seen == [RunStatus.GENERATING] and run.status == RunStatus.SUCCEEDED and executor.calls == []


def test_replay_skips_generating_status(session, world, generator, executor, vault):
    finish_run(session, world, generator, executor, vault)
    run = new_run(session, world)
    seen: list[str] = []

    class SpyExecutor(FakeScriptExecutor):
        def execute(self, *args, **kwargs):
            seen.append(session.get(Run, run.id).status)
            return super().execute(*args, **kwargs)

    go(session, run, generator, SpyExecutor(), vault)
    assert seen == [RunStatus.RUNNING]


def test_undecryptable_password_fails_run(session, world, generator, executor):
    from cryptography.fernet import Fernet
    run = finish_run(session, world, generator, executor, CredentialVault(Fernet.generate_key().decode()))
    assert run.status == RunStatus.FAILED and run.error
    assert run.active_slot is None
    assert generator.calls == [] and executor.calls == []


class CrashingExecutor:
    def execute(self, code, env, headless, timeout_seconds):
        raise RuntimeError(f"adapter bug, password was {env['PORTAL_PASSWORD']}")


def test_unexpected_crash_fails_the_run_frees_the_slot_and_hides_the_password(
        session, world, generator, vault, stored_script):
    run = finish_run(session, world, generator, CrashingExecutor(), vault)

    assert run.status == RunStatus.FAILED
    assert run.active_slot is None
    assert run.finished_at is not None
    assert "Unexpected error" in run.error
    assert PASSWORD not in run.error


def test_unknown_run_id_raises_run_not_found(session, generator, executor, vault):
    with pytest.raises(RunNotFound):
        execute_run(session, 999, generator=generator, executor=executor, vault=vault, settings=Settings())


def test_username_and_repr_escaped_password_are_redacted_from_output(
        session, world, generator, executor, vault, stored_script):
    executor.result = ExecutionResult(0, f"user alice sent {PASSWORD!r}")
    run = finish_run(session, world, generator, executor, vault)
    assert PASSWORD not in run.output and "alice" not in run.output


def test_password_is_redacted_from_a_failure_message(session, world, generator, executor, vault):
    generator.error = f"agent typed {PASSWORD} into the wrong field"
    run = finish_run(session, world, generator, executor, vault)
    assert run.status == RunStatus.FAILED
    assert PASSWORD not in run.error and "agent typed" in run.error


@pytest.mark.parametrize("secret_in_code", [PASSWORD, "alice"])
def test_generated_script_embedding_a_credential_fails_the_run_and_is_not_stored(
        session, world, executor, vault, secret_in_code):
    generator = FakeScriptGenerator(code=f"page.fill('#u', '{secret_in_code}')")
    run = finish_run(session, world, generator, executor, vault)
    assert run.status == RunStatus.FAILED
    assert "{{username}}/{{password}}" in run.error
    assert secret_in_code not in run.error.replace("{{username}}/{{password}}", "")
    assert session.query(ScriptVersion).count() == 0
    assert executor.calls == [] and run.script_version_id is None and run.active_slot is None


def test_script_timeout_leaves_room_for_the_time_the_run_already_used(
        session, world, generator, executor, vault, stored_script):
    clock = iter([0.0, 30.0])
    run = new_run(session, world)
    execute_run(session, run.id, generator=generator, executor=executor, vault=vault,
                settings=Settings(run_timeout_seconds=100), monotonic=lambda: next(clock))
    assert executor.calls[0]["timeout_seconds"] == 60


def test_a_run_the_watchdog_already_failed_is_not_overwritten_by_the_worker(session, world, generator, vault, stored_script):
    run = new_run(session, world)

    class WatchdogWinsExecutor(FakeScriptExecutor):
        def execute(self, *args, **kwargs):
            session.query(Run).update({Run.status: RunStatus.FAILED, Run.error: "Run timed out after 5 seconds"})
            session.commit()
            return super().execute(*args, **kwargs)

    go(session, run, generator, WatchdogWinsExecutor(), vault)
    assert (run.status, run.error, run.output) == (RunStatus.FAILED, "Run timed out after 5 seconds", "")


def test_a_run_the_watchdog_failed_during_generation_stays_failed(session, world, vault):
    run = new_run(session, world)

    class WatchdogWinsGenerator(FakeScriptGenerator):
        def generate(self, *args, **kwargs):
            session.query(Run).update({Run.status: RunStatus.FAILED, Run.error: "Run timed out after 5 seconds"})
            session.commit()
            return super().generate(*args, **kwargs)

    go(session, run, WatchdogWinsGenerator(), FakeScriptExecutor(), vault)
    assert (run.status, run.error, run.output) == (RunStatus.FAILED, "Run timed out after 5 seconds", "")


def test_worker_does_not_resurrect_a_run_failed_before_it_started_work(session, world, generator, executor, vault):
    run = new_run(session, world, status=RunStatus.FAILED)
    go(session, run, generator, executor, vault)
    assert run.status == RunStatus.FAILED and executor.calls == [] and generator.calls == []


def test_crash_is_logged_with_secrets_redacted(session, world, generator, vault, caplog, stored_script):
    with caplog.at_level("ERROR", logger="portal_auto.worker"):
        finish_run(session, world, generator, CrashingExecutor(), vault)
    assert "RuntimeError" in caplog.text and "adapter bug" in caplog.text
    assert PASSWORD not in caplog.text


def test_run_records_whether_it_generated_its_script(session, world, generator, executor, vault):
    first = finish_run(session, world, generator, executor, vault)
    replay = finish_run(session, world, generator, executor, vault)
    forced = finish_run(session, world, generator, executor, vault, force_generate=True)

    assert (first.generated, replay.generated, forced.generated) == (True, False, True)


def test_generator_is_told_the_portals_declared_hosts(session, world, generator, executor, vault):
    world[0].portal.extra_hosts = "adobe.com, auth.example.org"
    session.commit()

    finish_run(session, world, generator, executor, vault)

    assert generator.calls[0]["allowed_hosts"] == ["adobe.com", "auth.example.org"]


# --- public-access check of the final page (S3) ---
PUBLISHED_PAGE = "https://demo.test/decks/42"
PUBLIC = PublicationCheck(PublicationStatus.PUBLISHED, "the page loads without signing in (HTTP 200)")


class RecordingCheck:
    def __init__(self, result: PublicationCheck = PUBLIC, error: Exception | None = None):
        self.result, self.error, self.urls = result, error, []

    def __call__(self, url: str) -> PublicationCheck:
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.result


def run_with_check(session, world, generator, executor, vault, check, **fields):
    run = new_run(session, world, **fields)
    return execute_run(session, run.id, generator=generator, executor=executor, vault=vault,
                       settings=Settings(), public_check=check)


def test_a_live_generation_that_ends_on_a_page_is_checked_and_reported(session, world, executor, vault):
    check = RecordingCheck()
    generator = FakeScriptGenerator(code="print('script')", final_page_url=PUBLISHED_PAGE)

    run = run_with_check(session, world, generator, executor, vault, check)

    assert check.urls == [PUBLISHED_PAGE]
    assert f"Final page: {PUBLISHED_PAGE}" in run.output
    assert f"Publication status: published - {PUBLIC.detail}" in run.output
    assert run.publication_status == "published"
    assert run.status == RunStatus.SUCCEEDED


def test_a_replay_that_ends_on_a_page_is_checked_and_reported(session, world, generator, vault, stored_script):
    check = RecordingCheck(PublicationCheck(PublicationStatus.NOT_PUBLIC, "HTTP 404 without signing in"))
    executor = FakeScriptExecutor(result=ExecutionResult(0, f"OK: generated completed\nFinal page: {PUBLISHED_PAGE}\n"))

    run = run_with_check(session, world, generator, executor, vault, check)

    assert check.urls == [PUBLISHED_PAGE]
    assert "Publication status: not_public - HTTP 404 without signing in" in run.output
    assert run.publication_status == "not_public" and run.status == RunStatus.SUCCEEDED


def test_no_page_means_no_check(session, world, executor, vault):
    check = RecordingCheck()

    run_with_check(session, world, FakeScriptGenerator(code="print('script')"), executor, vault, check)

    assert check.urls == []


def test_a_failed_replay_is_not_checked(session, world, generator, vault, stored_script):
    check = RecordingCheck()
    executor = FakeScriptExecutor(result=ExecutionResult(1, f"FAILED\nFinal page: {PUBLISHED_PAGE}\n"))

    run = run_with_check(session, world, generator, executor, vault, check)

    assert run.status == RunStatus.FAILED and check.urls == []
    assert "Publication status" not in run.output


def test_without_a_checker_nothing_is_added(session, world, executor, vault):
    generator = FakeScriptGenerator(code="print('script')", final_page_url=PUBLISHED_PAGE)

    run = run_with_check(session, world, generator, executor, vault, None)

    assert "Publication status" not in run.output and run.publication_status is None


def test_a_crashing_checker_never_fails_the_run(session, world, executor, vault):
    check = RecordingCheck(error=RuntimeError("boom with secret-ish text"))
    generator = FakeScriptGenerator(code="print('script')", final_page_url=PUBLISHED_PAGE)

    run = run_with_check(session, world, generator, executor, vault, check)

    assert run.status == RunStatus.SUCCEEDED
    assert "Publication status: unreachable" in run.output
    assert "boom" not in run.output
