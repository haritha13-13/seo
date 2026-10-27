"""Parity: a script generated with one Run's Inputs, run for real, uses whatever values the next Run supplies
through its environment, and its text never holds a value."""
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from site_server import serve

from portal_auto.browser.launch import browser_channel_env
from portal_auto.browser.session import BrowserSession
from portal_auto.config import Settings
from portal_auto.recording import ActionRecorder
from portal_auto.runtime.generator import AgentScriptGenerator

PAGE = """
<label for="title">Title</label><input id="title" type="text">
<label for="deck">Attach deck</label><input id="deck" type="file" hidden>
<label for="logo">Logo</label><input id="logo" type="file">
<button onclick="send()">Send</button>
<p id="status"></p>
<script>
async function describe(id) {
  const file = document.getElementById(id).files[0];
  return file.name + ':' + await file.text();
}
async function send() {
  const query = new URLSearchParams({
    title: document.getElementById('title').value, deck: await describe('deck'), logo: await describe('logo'),
  });
  await fetch('/sent?' + query);
  document.getElementById('status').textContent = 'Inputs sent';
}
</script>
"""
GENERATION_VALUES = {"title": "Q3 results", "deck": ("q3-deck.pdf", "q3 deck"), "logo": ("q3-logo.png", "q3 logo")}
REPLAY_VALUES = {"title": "Q4 outlook", "deck": ("q4-deck.pdf", "q4 deck"), "logo": ("q4-logo.png", "q4 logo")}


@pytest.fixture
def site():
    requested: list[str] = []
    server, base_url = serve({"/": PAGE}, requested)
    yield f"{base_url}/", requested
    server.shutdown()


def input_env(folder: Path, values: dict) -> dict[str, str]:
    env = {"PORTAL_INPUT_TITLE": values["title"]}
    for name in ("deck", "logo"):
        filename, content = values[name]
        path = folder / name / filename
        path.parent.mkdir(parents=True)
        path.write_text(content)
        env[f"PORTAL_INPUT_{name.upper()}"] = str(path)
    return env


def ref_of(session: BrowserSession, description: str, tag: str) -> int:
    session.snapshot()
    return next(e.ref for e in session._snapshot.elements.values() if e.description == description and e.tag == tag)


def scripted_agent(workflow, settings, secrets, input_files=()) -> ActionRecorder:
    """Plays the agent: does what the Steps say through a real browser, passing only placeholders."""
    recorder = ActionRecorder()
    session = BrowserSession(recorder, workflow.url, secrets, workflow.allowed_hosts, input_files=input_files)
    session.start(workflow.url, workflow.headless)
    try:
        session.fill(ref_of(session, "Title", "input"), "{{ENV:PORTAL_INPUT_TITLE}}")
        session.upload_file(ref_of(session, "Attach deck", "label"), "{{ENV:PORTAL_INPUT_DECK}}")
        session.upload_file(ref_of(session, "Logo", "input"), "{{ENV:PORTAL_INPUT_LOGO}}")
        session.click(ref_of(session, "Send", "button"))
        session.wait_until_present("Inputs sent", timeout_seconds=15)
    finally:
        session.close()
    return recorder


def sent(requested: list[str]) -> list[dict[str, str]]:
    return [{key: values[0] for key, values in parse_qs(urlsplit(path).query).items()}
            for path in requested if path.startswith("/sent")]


def test_the_generated_script_uploads_and_types_the_next_runs_values(site, tmp_path):
    url, requested = site
    settings = Settings(root=tmp_path)
    generation_env = {"PORTAL_USERNAME": "alice", **input_env(settings.uploads_dir / "run-1", GENERATION_VALUES)}
    generation = AgentScriptGenerator(settings, run_workflow=scripted_agent).generate(
        url, ["Type the title, attach the deck and logo, then send"], True, generation_env)
    code = generation.code
    assert generation.final_page_url == url
    assert sent(requested) == [{"title": "Q3 results", "deck": "q3-deck.pdf:q3 deck", "logo": "q3-logo.png:q3 logo"}]

    replay_env = input_env(tmp_path / "elsewhere", REPLAY_VALUES)
    script = tmp_path / "script.py"
    script.write_text(code)
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=120, cwd=tmp_path,
                            env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "HEADLESS": "1",
                                 **browser_channel_env("google-chrome"), **replay_env})

    assert result.returncode == 0, result.stderr
    assert sent(requested)[1:] == [{"title": "Q4 outlook", "deck": "q4-deck.pdf:q4 deck", "logo": "q4-logo.png:q4 logo"}]
    for value in [*generation_env.values(), *replay_env.values(), "q3-deck.pdf", "q3-logo.png", "Q3 results"]:
        assert value not in code
    assert "expect_file_chooser" in code and "set_input_files" in code
