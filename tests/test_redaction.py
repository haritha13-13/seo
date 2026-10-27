import json

import pytest

from portal_auto.redaction import REDACTED, redact


def test_plain_secret_is_replaced():
    assert redact("login with hunter22 ok", ["hunter22"]) == f"login with {REDACTED} ok"


def test_json_escaped_form_is_replaced():
    secret = 'pa"ss\\word'
    assert secret not in redact(f'fill({__import__("json").dumps(secret)})', [secret])


def test_repr_escaped_form_is_replaced():
    secret = "it's\nsecret"
    assert "secret" not in redact(f"value={secret!r}", [secret])


def test_url_encoded_form_is_replaced():
    assert "p%40ss%21" not in redact("GET /login?p=p%40ss%21", ["p@ss!"])


def test_every_secret_in_the_list_is_replaced():
    cleaned = redact("user=alice pass=hunter22", ["alice", "hunter22"])
    assert "alice" not in cleaned and "hunter22" not in cleaned


def test_empty_secrets_are_ignored_and_text_is_unchanged():
    assert redact("nothing to hide", ["", None]) == "nothing to hide"


def test_longer_secret_wins_when_one_contains_another():
    assert redact("abcdef", ["abc", "abcdef"]) == REDACTED


def test_session_secrets_are_long_cookie_and_local_storage_values_only():
    from portal_auto.redaction import session_secrets
    state = json.dumps({
        "cookies": [{"name": "a", "value": "long-cookie-value"}, {"name": "b", "value": "short"}],
        "origins": [{"origin": "https://x.test", "localStorage": [{"name": "t", "value": "long-local-value"}]},
                    {"origin": "https://y.test"}],
    })
    assert sorted(session_secrets(state)) == ["long-cookie-value", "long-local-value"]


@pytest.mark.parametrize("text", ["not json", "[]", '{"cookies": 1, "origins": []}', '{"cookies": [1], "origins": []}'])
def test_session_secrets_of_garbage_is_empty(text):
    from portal_auto.redaction import session_secrets
    assert session_secrets(text) == []


def test_one_malformed_entry_does_not_stop_the_others_from_being_redacted():
    from portal_auto.redaction import session_secrets
    state = json.dumps({
        "cookies": [{"name": "no-value"}, "junk", {"name": "n", "value": 12345678901}, {"name": "ok", "value": "good-cookie-value"}],
        "origins": [{"origin": "https://x.test", "localStorage": [{"name": "t"}, {"name": "u", "value": "good-local-value"}]}, 7],
    })
    assert sorted(session_secrets(state)) == ["good-cookie-value", "good-local-value"]


def test_string_leaves_of_json_valued_local_storage_are_redacted_too():
    from portal_auto.redaction import session_secrets
    nested = json.dumps({"auth": {"token": "inner-token-value", "ids": ["list-leaf-value", 5]}, "short": "x"})
    state = json.dumps({"cookies": [], "origins": [{"origin": "https://x.test", "localStorage": [{"name": "k", "value": nested}]}]})
    secrets = session_secrets(state)
    assert {"inner-token-value", "list-leaf-value", nested} <= set(secrets)
    assert "x" not in secrets
