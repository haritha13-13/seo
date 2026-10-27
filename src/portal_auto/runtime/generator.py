"""Generation: the agent carries out the Steps in a live browser and the recording is rendered to a script."""
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from portal_auto.agent.runner import AgentRunFailed, run_workflow
from portal_auto.codegen.renderer import render_script
from portal_auto.config import Settings
from portal_auto.browser.launch import channel_from_env
from portal_auto.browser.origin import web_address
from portal_auto.inputs import is_input_env
from portal_auto.recording import ActionRecorder
from portal_auto.redaction import redact
from portal_auto.runtime.interfaces import GenerationFailed, GenerationResult
from portal_auto.runtime.session_files import STATE_ENV
from portal_auto.workflow import Workflow

SCRIPT_NAME = "generated"
# Besides the Run's own Inputs (ADR 0005), the only environment values a Generation may use or a generated
# script may read (ADR 0002).
ENGINE_ENV_NAMES = ("PORTAL_USERNAME", "PORTAL_PASSWORD")


class AgentScriptGenerator:
    def __init__(
        self,
        settings: Settings,
        run_workflow: Callable[..., ActionRecorder] = run_workflow,
    ):
        self._settings = settings
        self._run_workflow = run_workflow

    def generate(
        self, url: str, steps: list[str], headless: bool, env: dict[str, str], allowed_hosts: Sequence[str] = ()
    ) -> GenerationResult:
        workflow = Workflow(
            name=SCRIPT_NAME, url=url, headless=headless, steps=steps, allowed_hosts=list(allowed_hosts)
        )
        allowed = (*ENGINE_ENV_NAMES, *sorted(name for name in env if is_input_env(name)))
        secrets = {name: env[name] for name in allowed if name in env}
        options = _browser_options(env, _input_files(secrets, self._settings.uploads_dir))
        try:
            recorder = self._run_workflow(workflow, self._settings, secrets, **options)
            code = render_script(workflow, recorder.actions, allowed_env=allowed)
            return GenerationResult(code, web_address(recorder.final_page_url))
        except Exception as error:
            credentials = [env[name] for name in ENGINE_ENV_NAMES if name in env]
            raise GenerationFailed(redact(_failure_message(error), credentials)) from None


def _failure_message(error: Exception) -> str:
    message = f"Agent could not complete the steps: {error}"
    trace = getattr(error, "trace", None) if isinstance(error, AgentRunFailed) else None
    if trace:
        message += "\nLast agent actions:\n" + "\n".join(f"- {line}" for line in trace)
    return message


def _input_files(secrets: Mapping[str, str], uploads_dir: Path) -> list[str]:
    """The Input values that are files the Run's uploads folder holds; only these may be uploaded."""
    root = uploads_dir.resolve()
    return [value for name, value in secrets.items() if is_input_env(name) and _is_file_within(value, root)]


def _is_file_within(value: str, root: Path) -> bool:
    path = Path(value)
    return path.is_absolute() and path.is_file() and path.resolve().is_relative_to(root)


def _browser_options(env: dict[str, str], input_files: list[str]) -> dict[str, object]:
    options: dict[str, object] = {"storage_state_path": env[STATE_ENV]} if STATE_ENV in env else {}
    channel = channel_from_env(env)
    if channel:
        options["browser_channel"] = channel
    return {**options, "input_files": input_files} if input_files else options
