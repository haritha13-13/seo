"""Parity: the rendered standalone script, run for real, starts from the Saved Session file."""
import json
import subprocess
import sys

import pytest
from cookie_site import COOKIE_NAME, COOKIE_VALUE, serve_cookie_gated

from portal_auto.browser.launch import browser_channel_env
from portal_auto.codegen.renderer import render_script
from portal_auto.recording import ActionRecorder, AssertText, Goto
from portal_auto.workflow import Workflow


@pytest.fixture
def site():
    server, url = serve_cookie_gated()
    yield url
    server.shutdown()


def render(url: str) -> str:
    recorder = ActionRecorder()
    recorder.add(Goto(url))
    recorder.add(AssertText("signed in"))
    return render_script(Workflow(name="parity", url=url, steps=["s"]), recorder.actions)


def run_script(tmp_path, code: str, **env: str) -> subprocess.CompletedProcess:
    script = tmp_path / "script.py"
    script.write_text(code)
    return subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=120, cwd=tmp_path,
                          env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "HEADLESS": "1",
                              **browser_channel_env("google-chrome"), **env})


def state_file(tmp_path) -> str:
    cookie = {"name": COOKIE_NAME, "value": COOKIE_VALUE, "domain": "127.0.0.1", "path": "/",
              "expires": -1, "httpOnly": False, "secure": False, "sameSite": "Lax"}
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"cookies": [cookie], "origins": []}))
    return str(path)


def test_script_with_the_state_file_is_signed_in(tmp_path, site):
    result = run_script(tmp_path, render(site), PORTAL_STORAGE_STATE=state_file(tmp_path))
    assert result.returncode == 0, result.stderr


def test_script_without_the_state_file_is_not_signed_in(tmp_path, site):
    assert run_script(tmp_path, render(site)).returncode != 0


def test_script_saves_the_session_to_the_out_file_after_success(tmp_path, site):
    out = tmp_path / "out.json"
    result = run_script(tmp_path, render(site), PORTAL_STORAGE_STATE=state_file(tmp_path),
                        PORTAL_STORAGE_STATE_OUT=str(out))
    assert result.returncode == 0, result.stderr
    saved = json.loads(out.read_text())
    assert {"cookies", "origins"} <= saved.keys()


def test_script_does_not_save_the_session_after_failure(tmp_path, site):
    out = tmp_path / "out.json"
    result = run_script(tmp_path, render(site), PORTAL_STORAGE_STATE_OUT=str(out))
    assert result.returncode != 0 and not out.exists()


def test_rendered_script_never_contains_session_contents(tmp_path, site):
    assert COOKIE_VALUE not in render(site)
