"""Single place where screens are registered."""
from flask import Flask, redirect, url_for

from portal_auto.web import credentials, executor, portals, runs, workflows

SCREENS = (portals, credentials, workflows, executor, runs)


def register_blueprints(app: Flask) -> None:
    for screen in SCREENS:
        app.register_blueprint(screen.bp)

    @app.get("/")
    def home():
        return redirect(url_for("executor.index"))
