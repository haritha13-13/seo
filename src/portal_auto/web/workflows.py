"""Workflows screen: Workflow list and the editor with its ordered Steps child table.

Row actions (add, remove, move) post the whole form and re-render it without saving;
only the Save action persists, through WorkflowService.replace_steps.
"""
from collections.abc import Callable
from dataclasses import dataclass

from flask import Blueprint, flash, redirect, render_template, request, url_for

from portal_auto.inputs import InputSpec
from portal_auto.services.errors import Invalid
from portal_auto.web import deps, forms

bp = Blueprint("workflows", __name__, url_prefix="/workflows")

SAVE = "save"
StepRows = list[str]


@dataclass
class Draft:
    name: str
    portal_id: int | None
    active: bool
    starts_signed_in: bool
    steps: StepRows


def _swap(steps: StepRows, i: int, j: int) -> StepRows:
    if not (0 <= i < len(steps) and 0 <= j < len(steps)):
        return steps
    moved = list(steps)
    moved[i], moved[j] = moved[j], moved[i]
    return moved


def _remove(steps: StepRows, i: int) -> StepRows:
    return (steps[:i] + steps[i + 1 :]) or [""]


ROW_ACTIONS: dict[str, Callable[[StepRows, int], StepRows]] = {
    "remove": _remove,
    "up": lambda steps, i: _swap(steps, i, i - 1),
    "down": lambda steps, i: _swap(steps, i, i + 1),
}


def apply_row_action(steps: StepRows, action: str) -> StepRows:
    kind, _, position = action.partition(":")
    if kind == "add":
        return steps + [""]
    index = forms.integer(position)
    handler = ROW_ACTIONS.get(kind)
    if handler is None or index is None or not 0 <= index < len(steps):
        return steps
    return handler(steps, index)


def _submitted_draft() -> Draft:
    return Draft(
        name=forms.text("name"),
        portal_id=forms.integer(forms.text("portal_id")),
        active=forms.checked("active"),
        starts_signed_in=forms.checked("starts_signed_in"),
        steps=request.form.getlist("step") or [""],
    )


@dataclass(frozen=True)
class StoredInputs:
    specs: list[InputSpec]
    error: str | None = None


def _stored_inputs(workflow_id: int) -> StoredInputs:
    try:
        return StoredInputs(deps.workflows().inputs(workflow_id))
    except Invalid as error:
        return StoredInputs([], str(error))


def _render_editor(workflow, draft: Draft):
    return render_template(
        "workflows/form.html",
        workflow=workflow,
        draft=draft,
        portals=deps.portals().list(active_only=True),
        script=deps.workflows().current_script(workflow.id) if workflow else None,
        stored_inputs=_stored_inputs(workflow.id) if workflow else None,
    )


def _report_saved_steps(workflow_id: int, changed: bool, had_script: bool, flipped: bool = False) -> None:
    if changed and had_script:
        flash("Steps changed: existing Script Versions are now Stale and a new Generation is needed.", "warning")
    elif flipped and had_script:
        flash("Starts signed in changed: existing Script Versions are now Stale and a new Generation is needed.", "warning")
    if not deps.workflows().has_verify_step(workflow_id):
        flash(
            "No Step verifies the outcome, so a successful Run only means the Steps did not error.", "warning"
        )


def _back_to_editor(workflow_id: int):
    return redirect(url_for("workflows.edit", workflow_id=workflow_id))


@bp.get("/")
def index():
    return render_template("workflows/list.html", workflows=deps.workflows().list())


@bp.route("/new", methods=["GET", "POST"])
def create():
    draft = Draft(name="", portal_id=forms.integer(request.args.get("portal_id")), active=True,
                  starts_signed_in=False, steps=[""])
    if request.method == "POST":
        draft = _submitted_draft()
        action = forms.text("action")
        if action != SAVE:
            draft.steps = apply_row_action(draft.steps, action)
        else:
            with forms.reporting_errors() as done:
                workflow = _create_from(draft)
                forms.success(f"Workflow {workflow.name} created")
                _report_saved_steps(workflow.id, False, False)
            if done.ok:
                return _back_to_editor(workflow.id)
    return _render_editor(None, draft)


def _create_from(draft: Draft):
    service = deps.workflows()
    workflow = service.create(draft.portal_id or 0, draft.name, draft.steps, draft.starts_signed_in)
    if not draft.active:
        service.set_active(workflow.id, False)
    return workflow


def _save_edit(workflow_id: int, draft: Draft) -> None:
    service = deps.workflows()
    had_script = service.current_script(workflow_id) is not None
    service.update(workflow_id, draft.name)
    service.set_active(workflow_id, draft.active)
    flipped = service.get(workflow_id).starts_signed_in != draft.starts_signed_in
    service.set_starts_signed_in(workflow_id, draft.starts_signed_in)
    changed = service.replace_steps(workflow_id, draft.steps)
    forms.success(f"Workflow {draft.name} saved")
    _report_saved_steps(workflow_id, changed, had_script, flipped)


@bp.route("/<int:workflow_id>/edit", methods=["GET", "POST"])
def edit(workflow_id: int):
    workflow = deps.workflows().get(workflow_id)
    draft = Draft(
        name=workflow.name,
        portal_id=workflow.portal_id,
        active=workflow.active,
        starts_signed_in=workflow.starts_signed_in,
        steps=[step.instruction for step in workflow.steps],
    )
    if request.method == "POST":
        draft = _submitted_draft()
        action = forms.text("action")
        if action != SAVE:
            draft.steps = apply_row_action(draft.steps, action)
        else:
            with forms.reporting_errors() as done:
                _save_edit(workflow_id, draft)
            if done.ok:
                return _back_to_editor(workflow_id)
    return _render_editor(workflow, draft)


@bp.post("/<int:workflow_id>/active")
def set_active(workflow_id: int):
    forms.toggle_active(deps.workflows().set_active, workflow_id, "Workflow")
    return redirect(url_for("workflows.index"))


@bp.route("/<int:workflow_id>/delete", methods=["GET", "POST"])
def delete(workflow_id: int):
    """A Workflow with Runs or stored scripts is only deleted after a confirmation page lists what goes with it."""
    service = deps.workflows()
    workflow = service.get(workflow_id)
    usage = service.usage(workflow_id)
    has_history = bool(usage.runs or usage.script_versions)
    if request.method == "GET":
        return render_template("workflows/delete.html", workflow=workflow, usage=usage)
    confirmed = forms.checked("confirm")
    if has_history and not confirmed:
        return redirect(url_for("workflows.delete", workflow_id=workflow_id))
    with forms.reporting_errors():
        service.delete(workflow_id, with_history=confirmed)
        forms.success(f"Workflow {workflow.name} deleted")
    return redirect(url_for("workflows.index"))
