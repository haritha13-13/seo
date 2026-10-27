"""Recording test doubles for the runtime seams."""
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from portal_auto.runtime.interfaces import ExecutionResult, GenerationFailed, GenerationResult


@dataclass
class FakeScriptGenerator:
    code: str = "print('generated')\n"
    final_page_url: str | None = None
    error: str | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def generate(
        self, url: str, steps: list[str], headless: bool, env: dict[str, str], allowed_hosts: Sequence[str] = ()
    ) -> GenerationResult:
        self.calls.append(
            {"url": url, "steps": steps, "headless": headless, "env": env, "allowed_hosts": list(allowed_hosts)}
        )
        if self.error:
            raise GenerationFailed(self.error)
        return GenerationResult(self.code, self.final_page_url)


@dataclass
class FakeScriptExecutor:
    result: ExecutionResult = field(default_factory=lambda: ExecutionResult(0, "OK"))
    calls: list[dict[str, Any]] = field(default_factory=list)
    on_execute: Callable[[dict[str, str]], None] | None = None

    def execute(self, code: str, env: dict[str, str], headless: bool, timeout_seconds: int) -> ExecutionResult:
        self.calls.append({"code": code, "env": env, "headless": headless, "timeout_seconds": timeout_seconds})
        if self.on_execute:
            self.on_execute(env)
        return self.result
