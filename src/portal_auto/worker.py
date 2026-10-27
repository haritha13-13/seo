"""Process entry for one Run: `python -m portal_auto.worker <run_id>` (ADR 0003)."""
import logging
import signal
import sys
import time
import traceback
from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from portal_auto.browser.launch import browser_channel_env
from portal_auto.config import Settings
from portal_auto.db.models import Credential, Run, RunStatus, ScriptVersion, Workflow, final_page_url_in, utcnow
from portal_auto.db.session import init_schema, make_engine, make_session_factory
from portal_auto.inputs import input_env, upload_paths
from portal_auto.redaction import redact, session_secrets
from portal_auto.runtime.executor import SubprocessScriptExecutor
from portal_auto.runtime.generator import AgentScriptGenerator
from portal_auto.runtime.interfaces import GenerationFailed, ScriptExecutor, ScriptGenerator
from portal_auto.runtime.placeholders import to_engine_steps
from portal_auto.runtime.processes import install_sigterm_exit
from portal_auto.runtime.publication import UNREACHABLE, PublicationCheck, check_public_page
from portal_auto.runtime.session_files import SessionFiles, session_files, sweep_stale_session_dirs
from portal_auto.runtime.uploads import remove_uploads, run_uploads_dir, sweep_stale_uploads
from portal_auto.services.errors import DomainError, Invalid, NotFound
from portal_auto.services.saved_sessions import SavedSessionService
from portal_auto.services.vault import CredentialVault
from portal_auto.services.workflows import WorkflowService

logger = logging.getLogger(__name__)
USAGE = "usage: python -m portal_auto.worker <run_id>"
CREDENTIAL_IN_SCRIPT = "Generated script contains a credential value; use {{username}}/{{password}}"
UPLOAD_IN_SCRIPT = "Generated script contains an uploaded file's location; it must use the Input's placeholder"
NO_SAVED_SESSION = "This Credential has no saved session"
NOT_REFRESHED = "[saved session not refreshed: {reason}]"
REASON_UNUSABLE = "no usable state was saved"
REASON_LOGGED_OUT = "it looked logged out"
REASON_CHANGED = "the session changed during the Run"
GENERATED_LIVE = (
    "Generated live: this Run carried out the Steps in the agent's browser. "
    "The stored script (v{version}) is used from the next Run."
)
FINAL_PAGE = "Final page: {url}"
STALE_DIR_GRACE_SECONDS = 60
EXIT_USAGE = 2
EXIT_FAILED = 1
EXIT_TERMINATED = 143


class RunNotFound(Exception):
    """The Run id given to the worker does not exist."""


class RunStopped(DomainError):
    """The Run was ended by someone else (the watchdog) while the worker was still busy."""


class NoSavedSession(DomainError):
    """A Run asked to start from a Saved Session the Credential does not have."""


class DeadlineExceeded(DomainError):
    """The Run used up its whole time allowance."""


def execute_run(
    session: Session, run_id: int, *, generator: ScriptGenerator, executor: ScriptExecutor,
    vault: CredentialVault, settings: Settings, secrets: list[str] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    public_check: Callable[[str], PublicationCheck] | None = None,
) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise RunNotFound(f"Run {run_id} not found")
    secrets = [] if secrets is None else secrets
    run.started_at = utcnow()
    try:
        _carry_out(session, run, generator, executor, vault, settings, secrets, monotonic(), monotonic, public_check)
    except DomainError as error:
        _finish(session, run, RunStatus.FAILED, error=redact(str(error), [*secrets, *upload_paths(run.input_values)]))
    except Exception as error:
        session.rollback()
        log_crash(run_id, secrets)
        _finish(session, run, RunStatus.FAILED, error=crash_message(error))
    finally:
        remove_uploads(run_uploads_dir(settings.uploads_dir, run_id))
    return run


def crash_message(error: BaseException) -> str:
    return f"Unexpected error ({type(error).__name__}); see the Run's log file"


def log_crash(run_id: int, secrets: list[str]) -> None:
    logger.error("Run %s crashed:\n%s", run_id, redact(traceback.format_exc(), secrets))


def _carry_out(
    session: Session, run: Run, generator: ScriptGenerator, executor: ScriptExecutor, vault: CredentialVault,
    settings: Settings, secrets: list[str], started: float, monotonic: Callable[[], float],
    public_check: Callable[[str], PublicationCheck] | None,
) -> None:
    workflow = session.get(Workflow, run.workflow_id)
    credential = session.get(Credential, run.credential_id)
    env = {**_credential_env(credential, vault, settings, secrets), **input_env(run.input_values)}
    state_json = _saved_session(session, run, vault)
    secrets += session_secrets(state_json) if state_json else []
    credential_id = credential.id
    loaded_at = SavedSessionService(session, vault).updated_at(credential_id) if state_json else None
    files = session_files(state_json) if state_json else nullcontext()
    with files as session_dir:
        env.update({**session_dir.env(), **browser_channel_env(settings.chrome_command)} if session_dir else {})
        headless = not run.show_browser
        script = WorkflowService(session).current_script(workflow.id)
        if script is None or run.force_generate:
            _generate_live(session, run, workflow, generator, headless, env, secrets, public_check)
            return
        _use_script(session, run, script)
        _set_status(session, run, RunStatus.RUNNING)
        budget = script_budget(settings.run_timeout_seconds, monotonic() - started)
        result = executor.execute(script.code, env, headless, budget)
        succeeded = result.exit_code == 0
        note = None
        if succeeded and session_dir:
            note = _refresh_session(session, vault, credential_id, state_json, loaded_at, session_dir, secrets)
    output = _with_note(redact(result.output, secrets), note)
    if succeeded:
        output = _with_publication(output, final_page_url_in(output), public_check)
    _finish(
        session, run, RunStatus.SUCCEEDED if succeeded else RunStatus.FAILED,
        output=output, screenshot_path=result.screenshot_path,
        error="" if succeeded else f"Script exited with code {result.exit_code}",
    )


def _credential_env(credential: Credential, vault: CredentialVault, settings: Settings, secrets: list[str]) -> dict[str, str]:
    env = {"PORTAL_USERNAME": credential.username, "PORTAL_ARTIFACTS_DIR": str(settings.artifacts_dir)}
    secrets.append(credential.username)
    if credential.has_password:
        password = vault.decrypt(credential.password_encrypted)
        env["PORTAL_PASSWORD"] = password
        secrets.append(password)
    return env


def _saved_session(session: Session, run: Run, vault: CredentialVault) -> str | None:
    if not run.used_session:
        return None
    state_json = SavedSessionService(session, vault).load(run.credential_id)
    if state_json is None:
        raise NoSavedSession(NO_SAVED_SESSION)
    return state_json


def _refresh_session(
    session: Session, vault: CredentialVault, credential_id: int, previous_json: str, loaded_at: datetime | None,
    files: SessionFiles, secrets: list[str],
) -> str | None:
    """Stores the state the script saved unless it looks wrong; returns the Run Log note when it was kept out."""
    refreshed_json = files.read_out()
    if refreshed_json is None:
        return NOT_REFRESHED.format(reason=REASON_UNUSABLE)
    secrets += session_secrets(refreshed_json)
    service = SavedSessionService(session, vault)
    try:
        if service.looks_logged_out(credential_id, previous_json, refreshed_json):
            return NOT_REFRESHED.format(reason=REASON_LOGGED_OUT)
        stored = service.refresh_if_unchanged(credential_id, refreshed_json, loaded_at)
    except Invalid:
        return NOT_REFRESHED.format(reason=REASON_UNUSABLE)
    except NotFound:
        return NOT_REFRESHED.format(reason=REASON_CHANGED)
    return None if stored else NOT_REFRESHED.format(reason=REASON_CHANGED)


def _with_publication(output: str, url: str | None, public_check: Callable[[str], PublicationCheck] | None) -> str:
    """Appends whether the final page is publicly reachable; a failing check never fails the Run."""
    if public_check is None or not url:
        return output
    try:
        line = public_check(url).log_line
    except Exception:
        line = PublicationCheck(UNREACHABLE, "the check could not run").log_line
    return _with_note(output, line)


def _with_note(output: str, note: str | None) -> str:
    return output if note is None else f"{output}\n{note}" if output else note


def deadline_margin(timeout_seconds: int) -> int:
    return max(1, min(30, timeout_seconds // 10))


def script_budget(timeout_seconds: int, elapsed: float) -> int:
    return max(1, int(timeout_seconds - elapsed) - deadline_margin(timeout_seconds))


def _use_script(session: Session, run: Run, script: ScriptVersion) -> None:
    run.script_version_id = script.id
    session.commit()


def _generate_live(
    session: Session, run: Run, workflow: Workflow, generator: ScriptGenerator, headless: bool,
    env: dict[str, str], secrets: list[str], public_check: Callable[[str], PublicationCheck] | None,
) -> None:
    """The agent's Generation is this Run's real work (ADR 0001), so the new script is stored, not executed."""
    script, final_page_url = _generate(session, run, workflow, generator, headless, env, secrets)
    run.generated = True
    _use_script(session, run, script)
    output = redact(_live_output(script.version, final_page_url), secrets)
    _finish(session, run, RunStatus.SUCCEEDED, output=_with_publication(output, final_page_url, public_check))


def _live_output(version: int, final_page_url: str | None) -> str:
    lines = [GENERATED_LIVE.format(version=version)]
    if final_page_url:
        lines.append(FINAL_PAGE.format(url=final_page_url))
    return "\n".join(lines)


def _generate(
    session: Session, run: Run, workflow: Workflow, generator: ScriptGenerator, headless: bool,
    env: dict[str, str], secrets: list[str],
) -> tuple[ScriptVersion, str | None]:
    _set_status(session, run, RunStatus.GENERATING)
    steps = to_engine_steps([step.instruction for step in workflow.steps])
    result = generator.generate(workflow.portal.url, steps, headless, env, workflow.portal.allowed_hosts)
    code = result.code
    if redact(code, secrets) != code:
        raise GenerationFailed(CREDENTIAL_IN_SCRIPT)
    if any(path in code for path in upload_paths(run.input_values)):
        raise GenerationFailed(UPLOAD_IN_SCRIPT)
    version = ScriptVersion(
        workflow_id=workflow.id, version=_next_version(workflow), steps_revision=workflow.steps_revision, code=code
    )
    session.add(version)
    session.commit()
    return version, result.final_page_url


def _next_version(workflow: Workflow) -> int:
    return max((v.version for v in workflow.script_versions), default=0) + 1


def _set_status(session: Session, run: Run, status: str) -> None:
    changed = _update_if_active(session, run, status=status)
    if not changed:
        raise RunStopped("Run was stopped before it finished")


def _finish(session: Session, run: Run, status: str, *, output: str | None = None, error: str = "",
            screenshot_path: str | None = None) -> None:
    values = {
        "status": status, "error": error, "screenshot_path": screenshot_path, "finished_at": utcnow(),
        "active_slot": None,
    }
    if output is not None:
        values["output"] = output
    _update_if_active(session, run, **values)


def _update_if_active(session: Session, run: Run, **values: object) -> bool:
    matched = session.execute(
        update(Run).where(Run.id == run.id, Run.status.in_(RunStatus.ACTIVE)).values(**values)
    ).rowcount
    session.commit()
    session.refresh(run)
    return matched > 0


def main(argv: list[str] | None = None) -> int:
    run_id = _parse_run_id(sys.argv[1:] if argv is None else argv)
    if run_id is None:
        print(USAGE, file=sys.stderr)
        return EXIT_USAGE
    install_sigterm_exit()
    secrets: list[str] = []
    try:
        load_dotenv()
        settings = Settings.from_env()
        engine = make_engine(settings.database_url)
        init_schema(engine)
        factory = make_session_factory(engine)
        _attach_run_log(settings, run_id)
        sweep_stale_session_dirs(settings.run_timeout_seconds + STALE_DIR_GRACE_SECONDS)
        sweep_stale_uploads(settings.uploads_dir, settings.run_timeout_seconds + STALE_DIR_GRACE_SECONDS)
    except Exception as error:
        print(f"Run {run_id}: worker could not start ({type(error).__name__})", file=sys.stderr)
        return EXIT_FAILED
    return _work(factory, settings, run_id, secrets)


def _work(factory: sessionmaker[Session], settings: Settings, run_id: int, secrets: list[str]) -> int:
    with factory() as session:
        if session.get(Run, run_id) is None:
            print(f"Run {run_id} not found", file=sys.stderr)
            return EXIT_FAILED
        try:
            _with_deadline(settings, lambda: execute_run(
                session, run_id, generator=AgentScriptGenerator(settings), executor=SubprocessScriptExecutor(),
                vault=CredentialVault(settings.vault_key), settings=settings, secrets=secrets,
                public_check=check_public_page,
            ))
        except BaseException as error:
            _fail_after_crash(session, run_id, error, secrets)
            return EXIT_TERMINATED if isinstance(error, SystemExit) else EXIT_FAILED
        finally:
            remove_uploads(run_uploads_dir(settings.uploads_dir, run_id))
    return 0


def _fail_after_crash(session: Session, run_id: int, error: BaseException, secrets: list[str]) -> None:
    log_crash(run_id, secrets)
    try:
        session.rollback()
        run = session.get(Run, run_id)
        _finish(session, run, RunStatus.FAILED, error=crash_message(error))
    except Exception:
        logger.error("Run %s could not be marked failed:\n%s", run_id, redact(traceback.format_exc(), secrets))


def _with_deadline(settings: Settings, work: Callable[[], object]) -> None:
    def raise_deadline(signum: int, frame: object) -> None:
        raise DeadlineExceeded(f"Run timed out after {settings.run_timeout_seconds} seconds")

    signal.signal(signal.SIGALRM, raise_deadline)
    signal.setitimer(signal.ITIMER_REAL, settings.run_timeout_seconds - deadline_margin(settings.run_timeout_seconds) / 2)
    try:
        work()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def _parse_run_id(args: list[str]) -> int | None:
    try:
        return int(args[0])
    except (IndexError, ValueError):
        return None


def _attach_run_log(settings: Settings, run_id: int) -> None:
    log_dir = settings.artifacts_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_dir / f"run-{run_id}.log")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


if __name__ == "__main__":
    sys.exit(main())
