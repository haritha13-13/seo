"""Flask app factory. Blueprints live in sibling modules and are registered in blueprints.py."""
import os
import secrets

from flask import Flask, g, jsonify, render_template, request, session
from sqlalchemy import text
from werkzeug.exceptions import BadRequest

from portal_auto.config import Settings
from portal_auto.db.session import init_schema, make_engine, make_session_factory
from portal_auto.services.errors import DomainError, NotFound
from portal_auto.web.blueprints import register_blueprints
from portal_auto.web.timefmt import format_utc, time_ago

CSRF_FIELD = "csrf_token"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
MAX_REQUEST_BYTES = 3 * 1024 * 1024
LARGE_UPLOAD_ENDPOINTS = {"executor.execute": 120 * 1024 * 1024}
SESSION_FILE_TOO_LARGE = "The upload is too large. A session file may be at most 2 MB."
INPUT_UPLOAD_TOO_LARGE = "The upload is too large (limit 100 MB for a file, 20 MB for an image)."
NAV_ITEMS = [
    ("portals.index", "Portals"),
    ("credentials.index", "Credentials"),
    ("workflows.index", "Workflows"),
    ("executor.index", "Executor"),
    ("runs.index", "Runs"),
]


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or Settings.from_env()
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("PORTAL_AUTO_SECRET_KEY") or secrets.token_hex(32)
    app.config.update(
        SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True, ALLOWED_HOSTS=("127.0.0.1", "localhost"),
        MAX_CONTENT_LENGTH=MAX_REQUEST_BYTES,
    )
    app.extensions["settings"] = settings
    app.jinja_env.filters["utc"] = format_utc
    app.jinja_env.filters["ago"] = time_ago

    engine = make_engine(settings.database_url)
    init_schema(engine)
    app.extensions["engine"] = engine
    app.extensions["session_factory"] = make_session_factory(engine)

    _register_request_hooks(app)
    _register_error_handlers(app)
    _register_health(app)
    register_blueprints(app)
    return app


def _csrf_token() -> str:
    if CSRF_FIELD not in session:
        session[CSRF_FIELD] = secrets.token_urlsafe(32)
    return session[CSRF_FIELD]


def _register_request_hooks(app: Flask) -> None:
    app.jinja_env.globals["csrf_token"] = _csrf_token

    @app.context_processor
    def inject_nav_items():
        return {"nav_items": [(endpoint, label) for endpoint, label in NAV_ITEMS if endpoint in app.view_functions]}

    @app.before_request
    def allow_large_uploads():
        """Must run before anything reads the form, so the per-route limit applies while it is parsed."""
        limit = LARGE_UPLOAD_ENDPOINTS.get(request.endpoint or "")
        if limit is not None:
            request.max_content_length = limit

    @app.before_request
    def open_db_session_and_check_csrf():
        if request.host.partition(":")[0] not in app.config["ALLOWED_HOSTS"]:
            raise BadRequest("This app only answers on localhost.")
        g.db = app.extensions["session_factory"]()
        _csrf_token()
        if request.method not in SAFE_METHODS:
            sent = request.form.get(CSRF_FIELD, "")
            if not sent or not secrets.compare_digest(sent, session.get(CSRF_FIELD, "")):
                raise BadRequest("Invalid or missing CSRF token. Reload the page and try again.")

    @app.teardown_request
    def close_db_session(_error):
        db = g.pop("db", None)
        if db is not None:
            db.close()


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(404)
    def not_found(_error):
        return render_template("errors/error.html", title="Not found", message="That page does not exist."), 404

    @app.errorhandler(400)
    def bad_request(error):
        return render_template("errors/error.html", title="Bad request", message=error.description), 400

    @app.errorhandler(413)
    def too_large(_error):
        message = INPUT_UPLOAD_TOO_LARGE if request.endpoint in LARGE_UPLOAD_ENDPOINTS else SESSION_FILE_TOO_LARGE
        return render_template("errors/error.html", title="File too large", message=message), 413

    @app.errorhandler(NotFound)
    def unknown_record(error):
        return render_template("errors/error.html", title="Not found", message=str(error)), 404

    @app.errorhandler(DomainError)
    def domain_error(error):
        return render_template("errors/error.html", title="Cannot do that", message=str(error)), 422


def _register_health(app: Flask) -> None:
    @app.get("/health")
    def health():
        g.db.execute(text("SELECT 1"))
        return jsonify(status="ok")
