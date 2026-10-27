"""A Run with used_session starts both Generation and the script from the Credential's Saved Session."""
import json
import stat
from pathlib import Path

import pytest
from sqlalchemy import text

from portal_auto.config import Settings
from portal_auto.db.models import Credential, Portal, Run, RunStatus, ScriptVersion, Workflow, WorkflowStep
from portal_auto.runtime.fakes import FakeScriptExecutor, FakeScriptGenerator
from portal_auto.runtime.interfaces import ExecutionResult
from portal_auto.services.saved_sessions import SavedSessionService
from portal_auto.services.vault import CredentialVault
from portal_auto.worker import execute_run

COOKIE_VALUE = "cookie-secret-value-123"
LOCAL_VALUE = "local-storage-token-456"
STATE = {
    "cookies": [{"name": "sid", "value": COOKIE_VALUE, "domain": "demo.test", "path": "/"}],
    "origins": [{"origin": "https://demo.test", "localStorage": [{"name": "t", "value": LOCAL_VALUE}]}],
}
REFRESHED = {"cookies": [{"name": "sid", "value": "rotated-cookie-value", "domain": "demo.test", "path": "/"}], "origins": []}


@pytest.fixture
def vault(vault_key):
    return CredentialVault(vault_key)


@pytest.fixture
def credential(session, vault):
    portal = Portal(name="Demo", url="https://demo.test")
    credential = Credential(portal=portal, label="main", username="alice", password_encrypted=None)
    session.add_all([portal, credential, Workflow(portal=portal, name="W", steps=[
        WorkflowStep(position=1, instruction="Verify welcome text")])])
    session.commit()
    SavedSessionService(session, vault).import_session(credential.id, json.dumps(STATE).encode())
    return credential


@pytest.fixture
def stored_script(session, credential) -> ScriptVersion:
    """A Current Script Version, so Runs replay it."""
    workflow = session.query(Workflow).one()
    version = ScriptVersion(workflow_id=workflow.id, version=1, steps_revision=workflow.steps_revision,
                            code="print('stored')")
    session.add(version)
    session.commit()
    return version


def run_with(session, credential, vault, generator=None, executor=None, used_session=True, settings=None) -> Run:
    workflow = session.query(Workflow).one()
    run = Run(workflow_id=workflow.id, credential_id=credential.id, credential_label="main", active_slot=1,
              used_session=used_session)
    session.add(run)
    session.commit()
    return execute_run(session, run.id, generator=generator or FakeScriptGenerator(),
                       executor=executor or FakeScriptExecutor(), vault=vault, settings=settings or Settings())


def stored(session, vault, credential) -> dict:
    return json.loads(SavedSessionService(session, vault).load(credential.id))


def test_both_generation_and_execution_get_the_state_file_in_a_private_directory(session, credential, vault):
    seen = []

    def inspect(env):
        path = Path(env["PORTAL_STORAGE_STATE"])
        out = Path(env["PORTAL_STORAGE_STATE_OUT"])
        seen.append((json.loads(path.read_text()), path.parent == out.parent,
                     stat.S_IMODE(path.stat().st_mode), stat.S_IMODE(path.parent.stat().st_mode)))

    generator, executor = FakeScriptGenerator(), FakeScriptExecutor(on_execute=inspect)
    original_generate = generator.generate

    def generate(url, steps, headless, env, allowed_hosts=()):
        inspect(env)
        return original_generate(url, steps, headless, env, allowed_hosts)

    generator.generate = generate
    run_with(session, credential, vault, generator, executor)
    run_with(session, credential, vault, generator, executor)
    assert len(generator.calls) == len(executor.calls) == 1
    assert len(seen) == 2
    for state, same_dir, file_mode, dir_mode in seen:
        assert state == STATE and same_dir and file_mode == 0o600 and dir_mode == 0o700


def test_without_used_session_no_storage_variables_are_passed(session, credential, vault):
    generator, executor = FakeScriptGenerator(), FakeScriptExecutor()
    run_with(session, credential, vault, generator, executor, used_session=False)
    run_with(session, credential, vault, generator, executor, used_session=False)
    for env in (generator.calls[0]["env"], executor.calls[0]["env"]):
        assert not {"PORTAL_STORAGE_STATE", "PORTAL_STORAGE_STATE_OUT"} & env.keys()


def test_a_credential_without_a_session_fails_the_run_before_generating(session, credential, vault):
    SavedSessionService(session, vault).clear(credential.id)
    generator, executor = FakeScriptGenerator(), FakeScriptExecutor()
    run = run_with(session, credential, vault, generator, executor)
    assert run.status == RunStatus.FAILED and run.error == "This Credential has no saved session"
    assert not generator.calls and not executor.calls


def test_session_only_credential_omits_the_password_but_keeps_the_username(session, credential, vault, stored_script):
    executor = FakeScriptExecutor()
    run = run_with(session, credential, vault, executor=executor)
    assert run.status == RunStatus.SUCCEEDED
    assert executor.calls[0]["env"]["PORTAL_USERNAME"] == "alice"
    assert "PORTAL_PASSWORD" not in executor.calls[0]["env"]


def capture_dir(into: list):
    return lambda env: into.append(Path(env["PORTAL_STORAGE_STATE"]).parent)


def test_directory_is_removed_after_success_and_after_a_failing_script(session, credential, vault, stored_script):
    dirs: list[Path] = []
    run_with(session, credential, vault, executor=FakeScriptExecutor(on_execute=capture_dir(dirs)))
    run_with(session, credential, vault, executor=FakeScriptExecutor(
        ExecutionResult(1, "boom"), on_execute=capture_dir(dirs)))
    assert len(dirs) == 2 and not any(d.exists() for d in dirs)


def test_directory_is_removed_when_generation_fails_or_crashes(session, credential, vault):
    dirs: list[Path] = []
    failing = FakeScriptGenerator(error="nope")
    crashing = FakeScriptGenerator()
    crashing.generate = lambda url, steps, headless, env, allowed_hosts=(): (
        dirs.append(Path(env["PORTAL_STORAGE_STATE"]).parent), 1 / 0)
    original = failing.generate
    failing.generate = lambda url, steps, headless, env, allowed_hosts=(): (
        dirs.append(Path(env["PORTAL_STORAGE_STATE"]).parent), original(url, steps, headless, env, allowed_hosts))
    run_with(session, credential, vault, generator=failing)
    run_with(session, credential, vault, generator=crashing)
    assert len(dirs) == 2 and not any(d.exists() for d in dirs)


def test_directory_is_removed_when_the_worker_is_terminated(session, credential, vault, stored_script):
    dirs: list[Path] = []

    def terminate(env):
        dirs.append(Path(env["PORTAL_STORAGE_STATE"]).parent)
        raise SystemExit(143)

    with pytest.raises(SystemExit):
        run_with(session, credential, vault, executor=FakeScriptExecutor(on_execute=terminate))
    assert len(dirs) == 1 and not dirs[0].exists()


def write_out(state: dict | str):
    def write(env):
        Path(env["PORTAL_STORAGE_STATE_OUT"]).write_text(state if isinstance(state, str) else json.dumps(state))
    return write


def test_successful_run_refreshes_the_stored_session(session, credential, vault, stored_script):
    run_with(session, credential, vault, executor=FakeScriptExecutor(on_execute=write_out(REFRESHED)))
    assert stored(session, vault, credential) == REFRESHED


def test_failed_run_never_overwrites_the_stored_session(session, credential, vault, stored_script):
    executor = FakeScriptExecutor(ExecutionResult(1, "x"), on_execute=write_out(REFRESHED))
    run_with(session, credential, vault, executor=executor)
    assert stored(session, vault, credential) == STATE


@pytest.mark.parametrize("content", ["not json", '{"cookies": 1}', None])
def test_missing_or_invalid_out_file_is_ignored(session, credential, vault, stored_script, content):
    on_execute = write_out(content) if content else None
    run = run_with(session, credential, vault, executor=FakeScriptExecutor(on_execute=on_execute))
    assert run.status == RunStatus.SUCCEEDED
    assert stored(session, vault, credential) == STATE


def test_session_values_printed_by_the_script_are_redacted_from_output_and_error(session, credential, vault, stored_script):
    result = ExecutionResult(1, f"cookie={COOKIE_VALUE} local={LOCAL_VALUE} ok")
    run = run_with(session, credential, vault, executor=FakeScriptExecutor(result))
    assert COOKIE_VALUE not in run.output and LOCAL_VALUE not in run.output
    assert COOKIE_VALUE not in run.error and "ok" in run.output


def test_session_values_in_a_generated_script_are_refused(session, credential, vault):
    generator = FakeScriptGenerator(code=f"print('{COOKIE_VALUE}')")
    run = run_with(session, credential, vault, generator=generator)
    assert run.status == RunStatus.FAILED and COOKIE_VALUE not in run.error


def test_parent_environment_is_not_leaked_but_storage_paths_reach_the_script(monkeypatch):
    from portal_auto.runtime.executor import SubprocessScriptExecutor
    monkeypatch.setenv("PORTAL_AUTO_VAULT_KEY", "parent-secret")
    code = "import os; print(os.environ.get('PORTAL_AUTO_VAULT_KEY'), os.environ['PORTAL_STORAGE_STATE'], "\
           "os.environ['PORTAL_STORAGE_STATE_OUT'])"
    env = {"PORTAL_STORAGE_STATE": "/a/state.json", "PORTAL_STORAGE_STATE_OUT": "/a/out.json"}
    result = SubprocessScriptExecutor().execute(code, env, True, 30)
    assert result.output.split() == ["None", "/a/state.json", "/a/out.json"]




def test_a_run_finishing_after_the_session_was_removed_does_not_restore_it(session, credential, vault, stored_script):
    clear = lambda env: SavedSessionService(session, vault).clear(credential.id)
    run = run_with(session, credential, vault, executor=FakeScriptExecutor(
        on_execute=lambda env: (write_out(REFRESHED)(env), clear(env))))
    assert SavedSessionService(session, vault).load(credential.id) is None
    assert run.status == RunStatus.SUCCEEDED and "[saved session not refreshed" in run.output


def test_a_run_finishing_after_a_re_import_does_not_overwrite_it(session, credential, vault, stored_script):
    newer = {"cookies": [{"name": "sid", "value": "operator-new-value", "domain": "demo.test", "path": "/"}], "origins": []}
    reimport = lambda env: SavedSessionService(session, vault).import_session(credential.id, json.dumps(newer).encode())
    run_with(session, credential, vault, executor=FakeScriptExecutor(
        on_execute=lambda env: (write_out(REFRESHED)(env), reimport(env))))
    assert stored(session, vault, credential) == newer


def test_a_credential_deleted_during_the_run_does_not_fail_a_successful_run(session, credential, vault, stored_script):
    def delete(env):
        write_out(REFRESHED)(env)
        session.commit()
        session.execute(text("PRAGMA foreign_keys=OFF"))
        session.execute(text("DELETE FROM credential"))
        session.commit()

    run = run_with(session, credential, vault, executor=FakeScriptExecutor(on_execute=delete))
    assert run.status == RunStatus.SUCCEEDED


def test_a_logged_out_looking_state_keeps_the_old_session_and_says_so_in_the_run_log(session, credential, vault, stored_script):
    elsewhere = {"cookies": [{"name": "sid", "value": "other-site-value", "domain": "other.test", "path": "/"}], "origins": []}
    run = run_with(session, credential, vault, executor=FakeScriptExecutor(
        ExecutionResult(0, "done"), on_execute=write_out(elsewhere)))
    assert stored(session, vault, credential) == STATE
    assert run.status == RunStatus.SUCCEEDED
    assert "[saved session not refreshed: it looked logged out]" in run.output and "other-site-value" not in run.output


def test_a_state_with_far_fewer_cookies_keeps_the_old_session(session, vault, credential, stored_script):
    many = {**STATE, "cookies": [{"name": f"c{i}", "value": f"cookie-value-{i}-xx", "domain": "demo.test", "path": "/"}
                                 for i in range(4)]}
    SavedSessionService(session, vault).import_session(credential.id, json.dumps(many).encode())
    run = run_with(session, credential, vault, executor=FakeScriptExecutor(on_execute=write_out(REFRESHED)))
    assert stored(session, vault, credential) == many and "it looked logged out" in run.output


@pytest.mark.parametrize("write", [
    lambda env: Path(env["PORTAL_STORAGE_STATE_OUT"]).write_bytes(b"\xff\xfe\x00bad"),
    lambda env: Path(env["PORTAL_STORAGE_STATE_OUT"]).write_text(" " * (2 * 1024 * 1024 + 1)),
    lambda env: None,
])
def test_an_unusable_out_file_is_logged_and_does_not_crash(session, credential, vault, stored_script, write):
    run = run_with(session, credential, vault, executor=FakeScriptExecutor(on_execute=write))
    assert run.status == RunStatus.SUCCEEDED and "[saved session not refreshed" in run.output
    assert stored(session, vault, credential) == STATE


def test_generation_and_script_get_the_same_browser_channel_from_the_configured_chrome_command(session, credential, vault):
    generator, executor = FakeScriptGenerator(), FakeScriptExecutor()
    for _ in range(2):
        run_with(session, credential, vault, generator, executor, settings=Settings(chrome_command="python3"))
    assert generator.calls[0]["env"]["PORTAL_BROWSER_CHANNEL"] == executor.calls[0]["env"]["PORTAL_BROWSER_CHANNEL"] == "chrome"


def test_no_channel_is_set_when_the_chrome_command_is_not_installed(session, credential, vault):
    generator, executor = FakeScriptGenerator(), FakeScriptExecutor()
    for _ in range(2):
        run_with(session, credential, vault, generator, executor, settings=Settings(chrome_command="no-such-browser"))
    assert "PORTAL_BROWSER_CHANNEL" not in generator.calls[0]["env"] | executor.calls[0]["env"]


def test_a_live_generation_leaves_the_stored_session_as_it_was_and_removes_its_directory(session, credential, vault):
    dirs: list[Path] = []
    generator = FakeScriptGenerator()
    original = generator.generate

    def generate(url, steps, headless, env, allowed_hosts=()):
        write_out(REFRESHED)(env)
        dirs.append(Path(env["PORTAL_STORAGE_STATE"]).parent)
        return original(url, steps, headless, env, allowed_hosts)

    generator.generate = generate
    run = run_with(session, credential, vault, generator=generator)
    assert run.status == RunStatus.SUCCEEDED and run.generated
    assert stored(session, vault, credential) == STATE
    assert "[saved session not refreshed" not in run.output
    assert len(dirs) == 1 and not dirs[0].exists()
