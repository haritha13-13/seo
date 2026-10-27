"""Executor screen: pick Portal, Workflow and Credential, verify, start a Run."""
from dataclasses import dataclass, field

from flask import Blueprint, flash, redirect, render_template, request, url_for

from portal_auto.db.models import Credential, Portal, Workflow
from portal_auto.inputs import MAX_TEXT_CHARS
from portal_auto.services.errors import DomainError, RunAlreadyActive
from portal_auto.services.runs import credential_fits
from portal_auto.web.deps import credentials, portals, runs, workflows
from portal_auto.web.forms import integer
from portal_auto.web.input_form import InputForm, input_form, submitted_files, submitted_texts

bp = Blueprint("executor", __name__, url_prefix="/executor")

MASKED_PASSWORD = "••••••••"
VERIFICATION_REQUIRED = "Tick Verification (I have verified these details) before executing."
FILES_NOT_KEPT = "Uploads are not kept after a rejected Execute: choose the files again."


@dataclass
class Selection:
    portal: Portal | None = None
    workflow: Workflow | None = None
    credential: Credential | None = None
    portal_options: list[Portal] = field(default_factory=list)
    workflow_options: list[Workflow] = field(default_factory=list)
    credential_options: list[Credential] = field(default_factory=list)
    force_generate: bool = False

    @property
    def complete(self) -> bool:
        return None not in (self.portal, self.workflow, self.credential)


def _pick(options: list, wanted_id: int | None):
    return next((option for option in options if option.id == wanted_id), None)


def _credential_options(portal: Portal, workflow: Workflow | None) -> list[Credential]:
    active = credentials().list_for_portal(portal.id, active_only=True)
    if workflow is None:
        return [credential for credential in active if credential.usable]
    return [credential for credential in active if credential_fits(workflow, credential)]


def _selection(source) -> Selection:
    chosen = Selection(
        portal_options=portals().list(active_only=True), force_generate=bool(source.get("force_generate"))
    )
    chosen.portal = _pick(chosen.portal_options, integer(source.get("portal_id")))
    if chosen.portal:
        chosen.workflow_options = workflows().list(portal_id=chosen.portal.id, active_only=True)
        chosen.workflow = _pick(chosen.workflow_options, integer(source.get("workflow_id")))
        chosen.credential_options = _credential_options(chosen.portal, chosen.workflow)
        chosen.credential = _pick(chosen.credential_options, integer(source.get("credential_id")))
    return chosen


def _input_form(chosen: Selection, typed: dict[str, str] | None = None) -> InputForm:
    return input_form(chosen.workflow.id, typed) if chosen.workflow else InputForm()


def _render(chosen: Selection, form: InputForm | None = None):
    script = workflows().current_script(chosen.workflow.id) if chosen.workflow else None
    lacks_verify = bool(chosen.workflow) and not workflows().has_verify_step(chosen.workflow.id)
    last_run = next(iter(runs().list_runs(workflow_id=chosen.workflow.id, limit=1)), None) if chosen.workflow else None
    form = _input_form(chosen) if form is None else form
    return render_template(
        "executor/index.html", chosen=chosen, script=script, lacks_verify=lacks_verify, last_run=last_run,
        masked_password=MASKED_PASSWORD, input_form=form, max_text_chars=MAX_TEXT_CHARS,
    )


def _reject(chosen: Selection, message: str):
    """Back to the same Executor page with the selection and typed text kept; files must be chosen again."""
    form = _input_form(chosen, submitted_texts())
    flash(message, "error")
    if form.has_uploads:
        flash(FILES_NOT_KEPT, "warning")
    return _render(chosen, form)


@bp.get("/")
def index():
    return _render(_selection(request.args))


@bp.post("/")
def execute():
    chosen = _selection(request.form)
    if not chosen.complete:
        return _reject(chosen, "Choose a Portal, Workflow and Credential first.")
    if not request.form.get("verified"):
        return _reject(chosen, VERIFICATION_REQUIRED)
    try:
        run = runs().start_run(
            chosen.workflow.id, chosen.credential.id,
            show_browser=bool(request.form.get("show_browser")),
            force_generate=chosen.force_generate,
            inputs=submitted_texts(), files=submitted_files(),
        )
    except RunAlreadyActive as error:
        active = runs().active_run()
        if active is None:  # it finished between the two requests; nothing to point at
            return _reject(chosen, str(error))
        flash(f"{error}. Showing Run #{active.id}.", "warning")
        return redirect(url_for("runs.detail", run_id=active.id))
    except DomainError as error:
        return _reject(chosen, str(error))
    return redirect(url_for("runs.detail", run_id=run.id))
