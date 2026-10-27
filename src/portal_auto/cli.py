"""portal-auto serve            # web app on http://127.0.0.1:5001
portal-auto generate workflows/demo_login.yaml   # standalone agent run (no database)
portal-auto explore https://example.com --goal "find the contact page" --name contact   # draft a workflow"""
import argparse
import os
import signal
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import yaml
from dotenv import load_dotenv

from portal_auto.agent.explorer import explore_site
from portal_auto.agent.runner import run_workflow
from portal_auto.codegen.renderer import render_script
from portal_auto.config import Settings
from portal_auto.services.errors import Invalid
from portal_auto.services.vault import CredentialVault
from portal_auto.workflow import load_workflow

SERVE_HOST = "127.0.0.1"
SERVE_PORT = 5001


def generate(workflow_path: Path, settings: Settings) -> Path:
    workflow = load_workflow(workflow_path)
    recorder = run_workflow(workflow, settings)

    settings.actions_dir.mkdir(parents=True, exist_ok=True)
    settings.scripts_dir.mkdir(parents=True, exist_ok=True)
    recorder.save(settings.actions_dir / f"{workflow.name}.json")
    script_path = settings.scripts_dir / f"{workflow.name}.py"
    script_path.write_text(render_script(workflow, recorder.actions))
    print(f"Wrote {script_path} ({len(recorder.actions)} recorded actions)")
    return script_path


def explore(url: str, goal: str, name: str, settings: Settings, show_browser: bool = False) -> Path:
    """Let the agent look around `url` for `goal` and write the Steps it proposes as a draft workflow file."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise SystemExit("--name must be lowercase letters, digits and underscores, starting with a letter.")
    steps = explore_site(url, goal, settings, headless=not show_browser)
    path = Path("workflows") / f"{name}.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(yaml.safe_dump({"name": name, "url": url, "steps": steps}, sort_keys=False))
    print(f"Wrote {path} with {len(steps)} proposed steps. Review and edit them before you run it.")
    return path


def check_vault_key(settings: Settings) -> None:
    """Fail fast: a missing or invalid vault key would otherwise only surface when saving a password."""
    try:
        CredentialVault(settings.vault_key)
    except Invalid:
        raise SystemExit(
            "PORTAL_AUTO_VAULT_KEY is missing or invalid. Generate one with:\n"
            '  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"\n'
            "and set it in the environment or .env, then run serve again."
        ) from None


@contextmanager
def _shutdown_on_sigterm(shutdown: Callable[[], None]) -> Iterator[None]:
    """SIGTERM would otherwise skip the cleanup in `finally` and orphan a signed-in Chrome."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def terminate(signum, frame):
        shutdown()
        raise SystemExit(128 + signum)

    previous = signal.signal(signal.SIGTERM, terminate)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def serve(settings: Settings, port: int = SERVE_PORT) -> None:
    from portal_auto.web import create_app, deps

    check_vault_key(settings)
    if not os.environ.get("PORTAL_AUTO_SECRET_KEY"):
        print("PORTAL_AUTO_SECRET_KEY is not set: sessions reset on restart (set it to keep them).")

    app = create_app(settings)
    with app.app_context():
        recovered = deps.runs().recover_stale_runs()
        capture = deps.capture()
    capture.sweep_stale_profiles()
    if recovered:
        print(f"Marked {recovered} interrupted run(s) as failed")
    with _shutdown_on_sigterm(capture.shutdown):
        try:
            app.run(host=SERVE_HOST, port=port)
        finally:
            capture.shutdown()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="portal-auto")
    commands = parser.add_subparsers(dest="command", required=True)
    gen = commands.add_parser("generate", help="Explore a workflow with the agent and write a script")
    gen.add_argument("workflow", type=Path)
    exp = commands.add_parser("explore", help="Let the agent explore a website and draft a workflow for a goal")
    exp.add_argument("url")
    exp.add_argument("--goal", required=True)
    exp.add_argument("--name", required=True)
    exp.add_argument("--show-browser", action="store_true")
    web = commands.add_parser("serve", help="Run the web app (localhost only)")
    web.add_argument("--port", type=int, default=SERVE_PORT)
    args = parser.parse_args()
    if args.command == "serve":
        serve(Settings.from_env(), args.port)
    elif args.command == "explore":
        explore(args.url, args.goal, args.name, Settings.from_env(), args.show_browser)
    else:
        generate(args.workflow, Settings.from_env())


if __name__ == "__main__":
    main()
