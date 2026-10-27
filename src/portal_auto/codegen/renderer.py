"""Recorded actions -> standalone Playwright script. Knows nothing about crewai or the LLM."""
import re
from collections.abc import Iterable
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from portal_auto.literals import env_names
from portal_auto.recording import Action
from portal_auto.workflow import Workflow

_ENV = Environment(
    loader=FileSystemLoader(Path(__file__).parent),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
)
_ENVIRON_LOOKUP = re.compile(r'os\.environ\["(\w+)"\]')


def render_script(workflow: Workflow, actions: list[Action], allowed_env: Iterable[str] | None = None) -> str:
    """`allowed_env` lists the environment names the script may read; by default exactly the
    `{{ENV:NAME}}` names the workflow's own Steps declare."""
    allowed = set(allowed_env) if allowed_env is not None else {n for s in workflow.steps for n in env_names(s)}
    lines = [line for action in actions for line in action.to_code()] or ["pass"]
    for line in lines:
        for name in _ENVIRON_LOOKUP.findall(line):
            if name not in allowed:
                raise ValueError(f"Generated script would read environment variable {name}, which is not allowed")
    return _ENV.get_template("script.py.j2").render(name=workflow.name, headless=workflow.headless, lines=lines)
