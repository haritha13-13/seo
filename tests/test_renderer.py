import ast

import pytest

from portal_auto.codegen.renderer import render_script
from portal_auto.recording import AssertText, Click, Fill, Goto
from portal_auto.workflow import Workflow

WORKFLOW = Workflow(name="demo_login", url="https://example.com", steps=["x"])
ALLOWED = ("DEMO_PASS",)
ACTIONS = [
    Goto("https://example.com"),
    Fill('page.get_by_label("Password", exact=True)', "{{ENV:DEMO_PASS}}"),
    Click('page.get_by_role("button", name="Login")'),
    AssertText("secure area"),
]


def test_rendered_script_is_valid_python():
    ast.parse(render_script(WORKFLOW, ACTIONS, allowed_env=ALLOWED))


def test_rendered_script_uses_env_not_literal_secret():
    script = render_script(WORKFLOW, ACTIONS, allowed_env=ALLOWED)
    assert 'os.environ["DEMO_PASS"]' in script
    assert "SuperSecret" not in script


def test_rendered_script_contains_every_action():
    script = render_script(WORKFLOW, ACTIONS, allowed_env=ALLOWED)
    for fragment in ('page.goto("https://example.com")', ".click()", 'get_by_text("secure area")'):
        assert fragment in script


def test_no_actions_still_produces_valid_script():
    ast.parse(render_script(WORKFLOW, [], allowed_env=ALLOWED))


def test_script_refuses_environment_names_that_were_not_available():
    actions = [Fill("page.locator('#x')", "{{ENV:PORTAL_AUTO_VAULT_KEY}}")]
    with pytest.raises(ValueError, match="PORTAL_AUTO_VAULT_KEY"):
        render_script(WORKFLOW, actions, allowed_env=("PORTAL_PASSWORD",))


def test_allowed_names_default_to_those_the_workflow_declares():
    workflow = Workflow(name="w", url="https://x.test", steps=["Enter {{ENV:DEMO_USER}}"])
    ok = [Fill("page.locator('#x')", "{{ENV:DEMO_USER}}")]
    assert 'os.environ["DEMO_USER"]' in render_script(workflow, ok)
    with pytest.raises(ValueError):
        render_script(workflow, [Fill("page.locator('#x')", "{{ENV:OTHER}}")])


def test_docstring_and_timeout_are_accurate():
    script = render_script(WORKFLOW, [], allowed_env=ALLOWED)
    assert "workflows/" not in script and ".yaml" not in script
    assert "Runs without any LLM" in script
    assert "DEFAULT_TIMEOUT_MS = 15000" in script
    assert "set_default_timeout(DEFAULT_TIMEOUT_MS)" in script
