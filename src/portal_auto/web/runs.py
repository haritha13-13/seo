"""Runs screen: filterable list, Run Log detail, failure screenshot, regenerate."""
from dataclasses import dataclass
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for

from portal_auto.db.models import Run, RunStatus
from portal_auto.inputs import input_kind
from portal_auto.services.errors import DomainError, Invalid, NotFound
from portal_auto.web import forms
from portal_auto.web.deps import portals, runs, workflows
from portal_auto.web.timefmt import format_duration

bp = Blueprint("runs", __name__, url_prefix="/runs")

REFRESH_SECONDS = 3
REFILL_INPUTS = "This Workflow has Inputs: fill them in again and press Execute."
EXPIRED_SESSION_HINT = "This Run used a saved session; it may have expired. Capture the sign-in again."
SCREENSHOT_SUFFIXES = (".png",)


@dataclass(frozen=True)
class RunRow:
    run: Run
    portal_name: str
    workflow_name: str
    script_version: int | None

    @property
    def duration(self) -> str:
        return format_duration(self.run.started_at, self.run.finished_at)

    @property
    def active(self) -> bool:
        return self.run.status in RunStatus.ACTIVE


def _row(run: Run) -> RunRow:
    script = run.script_version
    return RunRow(run, run.workflow.portal.name, run.workflow.name, script.version if script else None)


def _get_run(run_id: int) -> Run:
    try:
        return runs().get_run(run_id)
    except NotFound:
        abort(404)


@bp.get("/")
def index():
    portal_id = forms.integer(request.args.get("portal_id"))
    workflow_id = forms.integer(request.args.get("workflow_id"))
    status = request.args.get("status") or None
    found = runs().list_runs(portal_id=portal_id, workflow_id=workflow_id, status=status)
    return render_template(
        "runs/index.html",
        rows=[_row(run) for run in found],
        portals=portals().list(),
        workflows=workflows().list(portal_id=portal_id),
        statuses=RunStatus.ACTIVE + RunStatus.FINISHED,
        portal_id=portal_id, workflow_id=workflow_id, status=status,
    )


@dataclass(frozen=True)
class FailureAdvice:
    hint: str
    offer_regenerate: bool


def _failure_advice(row: RunRow) -> FailureAdvice | None:
    advice = _script_advice(row)
    if advice is None or not row.run.used_session:
        return advice
    return FailureAdvice(f"{advice.hint} {EXPIRED_SESSION_HINT}", advice.offer_regenerate)


def _script_advice(row: RunRow) -> FailureAdvice | None:
    run = row.run
    if run.status != RunStatus.FAILED:
        return None
    latest = next(iter(runs().list_runs(workflow_id=run.workflow_id, status=RunStatus.FAILED, limit=1)), None)
    is_latest = latest is not None and latest.id == run.id
    if run.script_version_id is None:
        hint = f"The Run failed before a script was available: {run.error or 'no reason recorded'}"
        return FailureAdvice(hint, True)
    if not run.generated:
        return FailureAdvice("The stored script failed. The portal may have changed. Use Regenerate script.", True)
    hint = "A script was generated in this Run but failed when it executed. See the Run Log below."
    return FailureAdvice(hint, is_latest)


@dataclass(frozen=True)
class InputUsed:
    name: str
    kind: str
    shown: str


def _inputs_used(run: Run) -> list[InputUsed]:
    """Text values and original file names only: the stored path and file contents are never shown."""
    return [
        InputUsed(name, entry["kind"], entry["filename"] if input_kind(entry["kind"]).is_upload else entry["value"])
        for name, entry in run.input_values.items()
    ]


@bp.get("/<int:run_id>")
def detail(run_id: int):
    row = _row(_get_run(run_id))
    advice = _failure_advice(row)
    return render_template(
        "runs/detail.html", row=row, refresh_seconds=REFRESH_SECONDS, advice=advice, inputs_used=_inputs_used(row.run)
    )


@bp.get("/<int:run_id>/screenshot")
def screenshot(run_id: int):
    run = _get_run(run_id)
    if not run.screenshot_path:
        abort(404)
    root = current_app.extensions["settings"].artifacts_dir.resolve()
    path = (root / run.screenshot_path).resolve()
    if path.suffix.lower() not in SCREENSHOT_SUFFIXES or not path.is_relative_to(root) or not path.is_file():
        abort(404)
    return send_file(path, mimetype="image/png")


def _executor_for_regeneration(run: Run):
    """Inputs cannot be replayed from a past Run (files are gone), so the operator fills them in again."""
    return redirect(url_for(
        "executor.index", portal_id=run.workflow.portal_id, workflow_id=run.workflow_id,
        credential_id=run.credential_id, force_generate=1,
    ))


@bp.post("/<int:run_id>/regenerate")
def regenerate(run_id: int):
    run = _get_run(run_id)
    try:
        if run.status != RunStatus.FAILED:
            raise Invalid("Only a failed Run can regenerate its script")
        if workflows().inputs(run.workflow_id):
            flash(REFILL_INPUTS, "warning")
            return _executor_for_regeneration(run)
        new_run = runs().start_run(
            run.workflow_id, run.credential_id, show_browser=run.show_browser, force_generate=True
        )
    except DomainError as error:
        flash(str(error), "error")
        return redirect(url_for("runs.detail", run_id=run_id))
    return redirect(url_for("runs.detail", run_id=new_run.id))
