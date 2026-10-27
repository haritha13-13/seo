import pytest

from portal_auto.literals import UnknownPlaceholder, env_expression, py_literal, resolve_env


def test_py_literal_quotes_and_escapes():
    assert py_literal('say "hi"') == '"say \\"hi\\""'


def test_env_expression_plain_text_is_a_literal():
    assert env_expression("hello") == '"hello"'


def test_env_expression_whole_placeholder():
    assert env_expression("{{ENV:DEMO_PASS}}") == 'os.environ["DEMO_PASS"]'


def test_env_expression_mixed_text_and_placeholder():
    assert env_expression("user-{{ENV:ID}}!") == '"user-" + os.environ["ID"] + "!"'


def test_resolve_env():
    assert resolve_env("pw={{ENV:DEMO_PASS}}", {"DEMO_PASS": "s3cret"}) == "pw=s3cret"


def test_resolve_env_ignores_process_environment(monkeypatch):
    monkeypatch.setenv("PORTAL_AUTO_VAULT_KEY", "vault-key")
    with pytest.raises(UnknownPlaceholder, match="PORTAL_AUTO_VAULT_KEY"):
        resolve_env("{{ENV:PORTAL_AUTO_VAULT_KEY}}", {"DEMO_PASS": "x"})


def test_resolve_env_uses_only_the_given_mapping():
    assert resolve_env("a={{ENV:X}}", {"X": "1"}) == "a=1"
