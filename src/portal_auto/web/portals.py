"""Portals screen: list, create, edit, activate/deactivate and delete."""
from flask import Blueprint, flash, redirect, render_template, request, url_for

from portal_auto.web import deps, forms

bp = Blueprint("portals", __name__, url_prefix="/portals")


def _back_to_list():
    return redirect(url_for("portals.index"))


def _submitted() -> dict[str, str]:
    return {"name": forms.text("name"), "url": forms.text("url"), "extra_hosts": forms.raw("extra_hosts")}


@bp.get("/")
def index():
    return render_template("portals/list.html", portals=deps.portals().list())


@bp.route("/new", methods=["GET", "POST"])
def create():
    values = {"name": "", "url": "", "extra_hosts": ""}
    if request.method == "POST":
        values = _submitted()
        with forms.reporting_errors() as done:
            deps.portals().create(**values)
            forms.success(f"Portal {values['name']} created")
        if done.ok:
            return _back_to_list()
    return render_template("portals/form.html", portal=None, values=values)


@bp.route("/<int:portal_id>/edit", methods=["GET", "POST"])
def edit(portal_id: int):
    portal = deps.portals().get(portal_id)
    old_url = portal.url
    values = {"name": portal.name, "url": portal.url, "extra_hosts": portal.extra_hosts}
    if request.method == "POST":
        values = _submitted()
        with forms.reporting_errors() as done:
            deps.portals().update(portal_id, **values)
            forms.success(f"Portal {values['name']} saved")
            _warn_if_sessions_may_be_for_the_old_site(portal, old_url)
        if done.ok:
            return _back_to_list()
    return render_template("portals/form.html", portal=portal, values=values)


def _warn_if_sessions_may_be_for_the_old_site(portal, old_url: str) -> None:
    if portal.url != old_url and any(credential.has_session for credential in portal.credentials):
        flash(
            "The Start URL changed, so this Portal's saved sessions may belong to the old site. "
            "Capture the sign-in again for each Credential that has one.",
            "warning",
        )


@bp.post("/<int:portal_id>/active")
def set_active(portal_id: int):
    forms.toggle_active(deps.portals().set_active, portal_id, "Portal")
    return _back_to_list()


@bp.post("/<int:portal_id>/delete")
def delete(portal_id: int):
    with forms.reporting_errors():
        deps.portals().delete(portal_id)
        forms.success("Portal deleted")
    return _back_to_list()
