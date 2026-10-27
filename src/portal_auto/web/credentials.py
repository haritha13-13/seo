"""Credentials screen: Credentials are managed per Portal; passwords are never rendered."""
from flask import Blueprint, flash, redirect, render_template, request, url_for

from portal_auto.services.errors import Invalid
from portal_auto.services.saved_sessions import SOURCE_CAPTURED
from portal_auto.web import deps, forms

bp = Blueprint("credentials", __name__, url_prefix="/credentials")


SESSION_FILE_FIELD = "session_file"


def _back_to_list(portal_id: int):
    return redirect(url_for("credentials.index", portal_id=portal_id))


def _back_to_edit(credential_id: int):
    return redirect(url_for("credentials.edit", credential_id=credential_id))


def _uploaded_session_file() -> bytes:
    uploaded = request.files.get(SESSION_FILE_FIELD)
    if uploaded is None or not uploaded.filename:
        raise Invalid("Choose a session file")
    return uploaded.read()


def _capture_for(credential_id: int):
    """The open Capture for this Credential, plus the Credential id blocking Start when another is open."""
    active = deps.capture().active()
    mine = active if active and active.credential_id == credential_id else None
    return mine, active if active and not mine else None


def _open_capture_id(credential_id: int) -> str:
    mine, _ = _capture_for(credential_id)
    if mine is None and deps.capture().expired_for(credential_id):
        raise Invalid("The capture expired; start it again")
    if mine is None:
        raise Invalid("No capture is open for this Credential; start one first")
    return mine.id


@bp.get("/")
def index():
    portal_id = forms.integer(request.args.get("portal_id"))
    portal = deps.portals().get(portal_id) if portal_id else None
    credentials = deps.credentials().list_for_portal(portal.id) if portal else []
    return render_template(
        "credentials/list.html", portals=deps.portals().list(), portal=portal, credentials=credentials
    )


@bp.route("/new", methods=["GET", "POST"])
def create():
    portal_id = forms.integer(request.values.get("portal_id"))
    portal = deps.portals().get(portal_id) if portal_id else None
    values = {"label": "", "username": ""}
    if portal and request.method == "POST":
        values = {"label": forms.text("label"), "username": forms.text("username")}
        with forms.reporting_errors() as done:
            deps.credentials().create(portal.id, password=forms.raw("password"), **values)
            forms.success(f"Credential {values['label']} created")
        if done.ok:
            return _back_to_list(portal.id)
    return render_template(
        "credentials/form.html", portal=portal, credential=None, values=values, vault_ready=deps.vault_ready()
    )


@bp.route("/<int:credential_id>/edit", methods=["GET", "POST"])
def edit(credential_id: int):
    credential = deps.credentials().get(credential_id)
    values = {"label": credential.label, "username": credential.username}
    if request.method == "POST":
        values = {"label": forms.text("label"), "username": forms.text("username")}
        with forms.reporting_errors() as done:
            deps.credentials().update(credential_id, password=forms.raw("password"), **values)
            forms.success(f"Credential {values['label']} saved")
        if done.ok:
            return _back_to_list(credential.portal_id)
    capture, other_capture = _capture_for(credential_id)
    return render_template(
        "credentials/form.html", portal=credential.portal, credential=credential, values=values,
        vault_ready=deps.vault_ready(), capture=capture, other_capture=other_capture,
    )


@bp.post("/<int:credential_id>/active")
def set_active(credential_id: int):
    portal_id = deps.credentials().get(credential_id).portal_id
    forms.toggle_active(deps.credentials().set_active, credential_id, "Credential")
    return _back_to_list(portal_id)


@bp.post("/<int:credential_id>/delete")
def delete(credential_id: int):
    portal_id = deps.credentials().get(credential_id).portal_id
    with forms.reporting_errors():
        deps.credentials().delete(credential_id)
        forms.success("Credential deleted")
    return _back_to_list(portal_id)


@bp.post("/<int:credential_id>/session")
def import_session(credential_id: int):
    deps.credentials().get(credential_id)
    with forms.reporting_errors():
        result = deps.saved_sessions().import_session(credential_id, _uploaded_session_file())
        forms.success("Saved session imported")
        for warning in result.warnings:
            flash(warning, "warning")
    return _back_to_edit(credential_id)


@bp.post("/<int:credential_id>/session/remove")
def remove_session(credential_id: int):
    with forms.reporting_errors():
        deps.saved_sessions().clear(credential_id)
        forms.success("Saved session removed")
    return _back_to_edit(credential_id)


@bp.post("/<int:credential_id>/capture/start")
def start_capture(credential_id: int):
    credential = deps.credentials().get(credential_id)
    with forms.reporting_errors():
        deps.capture().start(credential_id, credential.portal.url)
        forms.success(
            "Chrome is open. Sign in by hand, then click Save session here "
            "(it closes itself after 15 minutes)."
        )
    return _back_to_edit(credential_id)


@bp.post("/<int:credential_id>/capture/save")
def save_capture(credential_id: int):
    deps.credentials().get(credential_id)
    with forms.reporting_errors():
        state = deps.capture().finish(_open_capture_id(credential_id))
        result = deps.saved_sessions().import_session(credential_id, state.encode(), source=SOURCE_CAPTURED)
        forms.success("Saved session captured")
        for warning in result.warnings:
            flash(warning, "warning")
    return _back_to_edit(credential_id)


@bp.post("/<int:credential_id>/capture/cancel")
def cancel_capture(credential_id: int):
    deps.credentials().get(credential_id)
    with forms.reporting_errors():
        deps.capture().cancel(_open_capture_id(credential_id))
        forms.success("Capture cancelled")
    return _back_to_edit(credential_id)
