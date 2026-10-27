import tempfile
from pathlib import Path

from portal_auto.runtime.executor import SubprocessScriptExecutor
from process_helpers import is_gone

ENV = {"PORTAL_USERNAME": "alice", "PORTAL_PASSWORD": "s3cret", "PORTAL_ARTIFACTS_DIR": "/tmp/art"}


def run(code: str, timeout: int = 10, headless: bool = True, env: dict[str, str] = ENV):
    return SubprocessScriptExecutor().execute(code, env, headless, timeout)


def test_success_captures_stdout_and_stderr():
    result = run("import sys\nprint('out')\nprint('err', file=sys.stderr)\n")
    assert result.exit_code == 0
    assert "out" in result.output and "err" in result.output
    assert result.screenshot_path is None


def test_nonzero_exit_code_is_reported():
    assert run("import sys\nsys.exit(3)\n").exit_code == 3


def test_script_receives_credentials_artifacts_dir_and_headless_flag():
    code = "import os\nprint(*(os.environ[k] for k in ('PORTAL_USERNAME','PORTAL_PASSWORD','PORTAL_ARTIFACTS_DIR','HEADLESS')))\n"
    assert "alice s3cret /tmp/art 1" in run(code).output
    assert "alice s3cret /tmp/art 0" in run(code, headless=False).output


def test_screenshot_path_is_taken_from_output():
    result = run("print('Screenshot: /tmp/art/shot.png')\nraise SystemExit(1)\n")
    assert result.screenshot_path == "/tmp/art/shot.png"


def test_timeout_kills_script_and_reports_124():
    result = run("import time\nprint('start', flush=True)\ntime.sleep(30)\n", timeout=1)
    assert result.exit_code == 124
    assert "timed out" in result.output.lower()
    assert "start" in result.output


def test_temp_files_are_deleted(monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    run("print('x')")
    run("import time\ntime.sleep(30)\n", timeout=1)
    assert list(Path(tmp_path).iterdir()) == []


def test_script_does_not_inherit_the_parents_secrets(monkeypatch):
    for name in ("PORTAL_AUTO_VAULT_KEY", "PORTAL_AUTO_LLM_API_KEY", "PORTAL_AUTO_SECRET_KEY", "DATABASE_PASSWORD"):
        monkeypatch.setenv(name, "parent-secret")
    code = "import os\nprint(sorted(k for k, v in os.environ.items() if v == 'parent-secret'))\n"
    assert "[]" in run(code).output


def test_script_keeps_the_environment_it_needs_to_run(monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/opt/browsers")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    code = "import os\nprint('|'.join(os.environ.get(k, 'MISSING') for k in ('PATH', 'PLAYWRIGHT_BROWSERS_PATH', 'LC_ALL')))\n"
    path, browsers, locale = run(code).output.strip().rsplit('|', 2)
    assert path != "MISSING" and browsers == "/opt/browsers" and locale == "C.UTF-8"


def test_huge_output_is_capped_keeping_the_tail():
    code = "for i in range(300000):\n    print('line-%07d' % i)\nprint('THE-END')\n"
    result = run(code, timeout=60)
    assert result.exit_code == 0
    assert len(result.output) < 1_100_000
    assert result.output.startswith("[output truncated]")
    assert result.output.rstrip().endswith("THE-END")


def test_small_output_is_not_marked_truncated():
    assert "[output truncated]" not in run("print('small')").output


def test_timeout_leaves_no_descendant_of_the_script_alive(tmp_path):
    pidfile = tmp_path / "pid"
    code = (
        "import subprocess, time\n"
        f"child = subprocess.Popen(['sleep', '60'])\nopen({str(pidfile)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    assert run(code, timeout=2).exit_code == 124
    assert is_gone(int(pidfile.read_text()))


def test_descendant_that_outlives_a_successful_script_is_killed(tmp_path):
    pidfile = tmp_path / "pid"
    code = (
        "import subprocess\n"
        f"child = subprocess.Popen(['sleep', '60'])\nopen({str(pidfile)!r}, 'w').write(str(child.pid))\n"
    )
    result = run(code, timeout=20)
    assert result.exit_code == 0
    assert is_gone(int(pidfile.read_text()))
