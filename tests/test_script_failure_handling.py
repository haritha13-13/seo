"""S1: a rendered script, run for real, reports failures usefully."""
import os
import subprocess
import sys
from dataclasses import dataclass

from portal_auto.codegen.renderer import render_script
from portal_auto.recording import Action, Goto
from portal_auto.workflow import Workflow

WORKFLOW = Workflow(name="failing_demo", url="https://example.com", steps=["x"])


@dataclass(frozen=True)
class RawCode(Action):
    """Test-only action emitting arbitrary script lines."""
    code: str
    kind = "raw"

    def to_code(self):
        return [self.code]


def run_script(tmp_path, actions):
    script = tmp_path / "script.py"
    script.write_text(render_script(WORKFLOW, actions, allowed_env=()))
    artifacts = tmp_path / "artifacts"
    env = {**os.environ, "PORTAL_ARTIFACTS_DIR": str(artifacts), "HEADLESS": "1"}
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env=env, timeout=60)
    return result, artifacts


def test_failure_reports_the_original_error_and_saves_a_screenshot(tmp_path):
    actions = [Goto("data:text/html,<p>hi</p>"), RawCode('raise RuntimeError("boom")')]

    result, artifacts = run_script(tmp_path, actions)

    assert result.returncode == 1
    assert "FAILED: boom" in result.stderr
    assert list(artifacts.glob("failing_demo_*.png"))


def test_original_error_is_reported_even_when_the_page_is_already_gone(tmp_path):
    actions = [Goto("data:text/html,<p>hi</p>"), RawCode("page.close()"), RawCode('page.get_by_text("x").click()')]

    result, _ = run_script(tmp_path, actions)

    assert result.returncode == 1
    assert "FAILED:" in result.stderr
    assert "Traceback" not in result.stderr
