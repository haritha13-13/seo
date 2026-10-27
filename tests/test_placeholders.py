import pytest

from portal_auto.runtime.interfaces import GenerationFailed
from portal_auto.runtime.placeholders import to_engine_steps


def test_credential_placeholders_become_env_placeholders():
    steps = ["Type {{username}} into the user field", "Type {{password}} into the password field"]
    assert to_engine_steps(steps) == [
        "Type {{ENV:PORTAL_USERNAME}} into the user field",
        "Type {{ENV:PORTAL_PASSWORD}} into the password field",
    ]


def test_steps_without_placeholders_are_unchanged():
    assert to_engine_steps(["Click Save"]) == ["Click Save"]


def test_placeholders_tolerate_inner_spaces():
    assert to_engine_steps(["{{ username }}"]) == ["{{ENV:PORTAL_USERNAME}}"]


def test_placeholders_are_case_insensitive():
    assert to_engine_steps(["{{Username}} {{ PASSWORD }}"]) == ["{{ENV:PORTAL_USERNAME}} {{ENV:PORTAL_PASSWORD}}"]


@pytest.mark.parametrize("step", ["Type {{1token}}", "Use {{ENV:PORTAL_AUTO_VAULT_KEY}}"])
def test_unknown_placeholder_fails_generation_early(step):
    with pytest.raises(GenerationFailed, match="Unknown placeholder .*only {{username}}, {{password}} and Inputs"):
        to_engine_steps(["Click Save", step])
