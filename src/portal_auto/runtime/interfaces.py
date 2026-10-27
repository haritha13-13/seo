"""The two seams of a Run: Generation and script execution."""
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from portal_auto.services.errors import DomainError


class GenerationFailed(DomainError):
    """The agent could not complete the Workflow's Steps."""


@dataclass(frozen=True)
class GenerationResult:
    """The script a Generation recorded, and the http(s) address of the page the agent's browser ended on."""
    code: str
    final_page_url: str | None = None


@dataclass(frozen=True)
class ExecutionResult:
    exit_code: int
    output: str
    screenshot_path: str | None = None


class ScriptGenerator(Protocol):
    def generate(
        self, url: str, steps: list[str], headless: bool, env: dict[str, str], allowed_hosts: Sequence[str] = ()
    ) -> GenerationResult: ...


class ScriptExecutor(Protocol):
    def execute(self, code: str, env: dict[str, str], headless: bool, timeout_seconds: int) -> ExecutionResult: ...
