"""Starting, watching and listing Runs. A Run executes in its own worker process (ADR 0003)."""
import json
import logging
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from portal_auto.config import Settings
from portal_auto.db.models import Credential, Run, RunStatus, Workflow, utcnow
from portal_auto.inputs import InputSpec, UploadedFile, match_inputs, recorded_file, recorded_text
from portal_auto.runtime.processes import pid_alive as process_alive
from portal_auto.runtime.processes import terminate_group
from portal_auto.runtime.uploads import remove_uploads, run_uploads_dir, write_uploads
from portal_auto.services.errors import Invalid, NotFound, RunAlreadyActive
from portal_auto.services.workflows import WorkflowService

ACTIVE_SLOT = 1
INTERRUPTED = "Run was interrupted before it finished"
NEEDS_SESSION = "This Workflow starts signed in, so the Credential needs a saved session"
NEEDS_PASSWORD = "This Credential has no password; use a Workflow that starts signed in, or add a password"
UPLOADS_NOT_STORED = "Could not store the uploaded files"
logger = logging.getLogger(__name__)


def spawn_worker(run_id: int) -> int:
    process = subprocess.Popen(
        [sys.executable, "-m", "portal_auto.worker", str(run_id)], start_new_session=True
    )
    return process.pid


def kill_process(pid: int) -> None:
    terminate_group(pid)


# Runs are returned detached; load what the Run screens display so callers never need a Session.
_DISPLAY_LOADS = (selectinload(Run.workflow).selectinload(Workflow.portal), selectinload(Run.script_version))

class RunService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        launcher: Callable[[int], int] | None = None,
        *,
        kill: Callable[[int], None] = kill_process,
        sleep: Callable[[float], None] = time.sleep,
        pid_alive: Callable[[int], bool] = process_alive,
        clock: Callable[[], datetime] = utcnow,
    ):
        self._session_factory = session_factory
        self._settings = settings
        self._launcher = launcher or spawn_worker
        self._kill = kill
        self._sleep = sleep
        self._pid_alive = pid_alive
        self._clock = clock
        self._watchdogs: list[threading.Thread] = []

    def start_run(
        self, workflow_id: int, credential_id: int, show_browser: bool = False, force_generate: bool = False,
        inputs: Mapping[str, str] | None = None, files: Mapping[str, UploadedFile] | None = None,
    ) -> Run:
        """`inputs` holds the text Inputs and `files` the uploaded ones, by Input name (any case)."""
        with self._session_factory() as session:
            self._reap_dead_workers(session)
            supplied = match_inputs(WorkflowService(session).inputs(workflow_id), inputs or {}, files or {})
            run = self._queue(session, workflow_id, credential_id, show_browser, force_generate)
            if self._store_inputs(session, run, supplied):
                self._launch(session, run)
            return run

    def get_run(self, run_id: int) -> Run:
        with self._session_factory() as session:
            run = session.scalars(select(Run).options(*_DISPLAY_LOADS).where(Run.id == run_id)).first()
        if run is None:
            raise NotFound("Run not found")
        return run

    def list_runs(
        self, portal_id: int | None = None, workflow_id: int | None = None, status: str | None = None,
        limit: int = 100,
    ) -> list[Run]:
        query = select(Run).options(*_DISPLAY_LOADS).join(Workflow, Run.workflow_id == Workflow.id)
        if portal_id is not None:
            query = query.where(Workflow.portal_id == portal_id)
        if workflow_id is not None:
            query = query.where(Run.workflow_id == workflow_id)
        if status is not None:
            query = query.where(Run.status == status)
        with self._session_factory() as session:
            return list(session.scalars(query.order_by(Run.id.desc()).limit(limit)))

    def active_run(self) -> Run | None:
        with self._session_factory() as session:
            self._reap_dead_workers(session)
            return session.scalars(select(Run).options(*_DISPLAY_LOADS).where(Run.status.in_(RunStatus.ACTIVE))).first()

    def recover_stale_runs(self) -> int:
        with self._session_factory() as session:
            active = list(session.scalars(select(Run).where(Run.status.in_(RunStatus.ACTIVE))))
            dead = [run for run in active if run.pid is None or not self._pid_alive(run.pid)]
            overdue = [(run.id, run.pid) for run in active if run not in dead and self._overdue(run)]
            for run in dead:
                _fail(run, INTERRUPTED)
            session.commit()
        for run_id, pid in overdue:
            self._time_out(run_id, pid)
        return len(dead) + len(overdue)

    def wait_for_watchdogs(self) -> None:
        for watchdog in self._watchdogs:
            watchdog.join()

    def _queue(self, session: Session, workflow_id: int, credential_id: int, show_browser: bool,
               force_generate: bool) -> Run:
        workflow, credential = _validated(session, workflow_id, credential_id)
        run = Run(
            workflow_id=workflow_id, credential_id=credential_id, credential_label=credential.label,
            show_browser=show_browser, force_generate=force_generate, active_slot=ACTIVE_SLOT,
            used_session=workflow.starts_signed_in,
        )
        session.add(run)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise RunAlreadyActive("Another Run is already in progress; wait for it to finish") from None
        return run

    def _store_inputs(self, session: Session, run: Run, supplied: list[tuple[InputSpec, str | UploadedFile]]) -> bool:
        """Writes the uploads into the Run's private folder and records every Input; False if that failed."""
        uploads = {spec.name: value for spec, value in supplied if isinstance(value, UploadedFile)}
        try:
            paths = write_uploads(self._uploads_of(run), uploads) if uploads else {}
        except OSError:
            logger.exception("Could not store the uploaded files of Run %s", run.id)
            remove_uploads(self._uploads_of(run))
            _fail(run, UPLOADS_NOT_STORED)
            session.commit()
            return False
        run.inputs = json.dumps({
            spec.name: recorded_file(spec, str(paths[spec.name])) if spec.name in paths else recorded_text(spec, value)
            for spec, value in supplied
        })
        session.commit()
        return True

    def _uploads_of(self, run: Run) -> Path:
        return run_uploads_dir(self._settings.uploads_dir, run.id)

    def _launch(self, session: Session, run: Run) -> None:
        try:
            run.pid = self._launcher(run.id)
        except Exception:
            logger.exception("Could not start the process for Run %s", run.id)
            remove_uploads(self._uploads_of(run))
            _fail(run, "Could not start the Run process")
            session.commit()
            return
        session.commit()
        self._start_watchdog(run.id, run.pid)

    def _reap_dead_workers(self, session: Session) -> None:
        for run in session.scalars(select(Run).where(Run.status.in_(RunStatus.ACTIVE), Run.pid.is_not(None))):
            if not self._pid_alive(run.pid):
                _fail(run, INTERRUPTED)
        session.commit()

    def _overdue(self, run: Run) -> bool:
        queued_at = run.queued_at if run.queued_at.tzinfo else run.queued_at.replace(tzinfo=timezone.utc)
        return self._clock() - queued_at > timedelta(seconds=self._settings.run_timeout_seconds)

    def _start_watchdog(self, run_id: int, pid: int) -> None:
        watchdog = threading.Thread(target=self._enforce_timeout, args=(run_id, pid), daemon=True)
        self._watchdogs.append(watchdog)
        watchdog.start()

    def _enforce_timeout(self, run_id: int, pid: int) -> None:
        self._sleep(self._settings.run_timeout_seconds)
        self._time_out(run_id, pid)

    def _time_out(self, run_id: int, pid: int) -> None:
        reason = f"Run timed out after {self._settings.run_timeout_seconds} seconds"
        try:
            still_active = self._mark_failed_if_active(run_id, reason)
        except Exception:
            logger.exception("Could not record the timeout of Run %s", run_id)
            still_active = True
        if not still_active:
            return
        try:
            self._kill(pid)
            self._release_slot(run_id)
        except Exception:
            logger.exception("Could not finish stopping Run %s", run_id)

    def _mark_failed_if_active(self, run_id: int, reason: str) -> bool:
        with self._session_factory() as session:
            matched = session.execute(
                update(Run).where(Run.id == run_id, Run.status.in_(RunStatus.ACTIVE))
                .values(status=RunStatus.FAILED, error=reason, finished_at=utcnow())
            ).rowcount
            session.commit()
        return matched > 0

    def _release_slot(self, run_id: int) -> None:
        with self._session_factory() as session:
            session.execute(update(Run).where(Run.id == run_id).values(active_slot=None))
            session.commit()


def credential_fits(workflow: Workflow, credential: Credential) -> bool:
    """A signed-in Workflow replays the Saved Session; any other types the password."""
    return credential.has_session if workflow.starts_signed_in else credential.has_password


def _validated(session: Session, workflow_id: int, credential_id: int) -> tuple[Workflow, Credential]:
    workflow = session.get(Workflow, workflow_id)
    credential = session.get(Credential, credential_id)
    if workflow is None or credential is None:
        raise NotFound("Workflow or Credential not found")
    if credential.portal_id != workflow.portal_id:
        raise Invalid("The Credential does not belong to the Workflow's Portal")
    if not (workflow.portal.active and workflow.active and credential.active):
        raise Invalid("The Portal, Workflow and Credential must all be active")
    if not credential_fits(workflow, credential):
        raise Invalid(NEEDS_SESSION if workflow.starts_signed_in else NEEDS_PASSWORD)
    return workflow, credential


def _fail(run: Run, reason: str) -> None:
    run.status = RunStatus.FAILED
    run.error = reason
    run.active_slot = None
    run.finished_at = utcnow()
