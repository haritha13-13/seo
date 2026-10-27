import pytest

from portal_auto.browser.locators import NoStableLocator, locator_candidates, locator_expression
from portal_auto.browser.snapshot import Element


def element(**overrides):
    base = dict(ref=1, tag="input", type="text", role="", id="", testid="", label="", placeholder="", name="", text="")
    return Element(**{**base, **overrides})


def test_testid_wins_over_everything():
    el = element(testid="login", label="User", id="u")
    assert locator_expression(el) == 'page.get_by_test_id("login")'


def test_label_preferred_for_form_fields():
    assert locator_expression(element(label="Username", id="u")) == 'page.get_by_label("Username", exact=True)'


def test_id_then_name():
    assert locator_expression(element(id="username")) == 'page.locator("#username")'
    assert locator_expression(element(name="user")) == "page.locator(\"input[name='user']\")"


def test_button_uses_role_and_text():
    el = element(tag="button", type="submit", text="Login")
    assert locator_expression(el) == 'page.get_by_role("button", name="Login")'


def test_link_uses_role_and_text():
    assert locator_expression(element(tag="a", text="Logout")) == 'page.get_by_role("link", name="Logout")'




def test_generated_ids_are_not_trusted_so_the_visible_text_is_used():
    el = element(tag="button", type="button", text="Continue", id="react-aria5577084414-:ra:")
    assert locator_expression(el) == 'page.get_by_role("button", name="Continue")'


@pytest.mark.parametrize("unstable", ["react-aria123-:r1:", "radix-:r4:", "ember98765", "form:input:3"])
def test_generated_id_shapes_are_recognised(unstable):
    el = element(tag="button", type="button", text="Next", id=unstable)
    assert "#" not in locator_expression(el)


def test_a_normal_id_is_still_used():
    assert locator_expression(element(id="username")) == 'page.locator("#username")'


def test_an_element_with_no_stable_identity_cannot_be_recorded():
    with pytest.raises(NoStableLocator, match="no stable"):
        locator_expression(element(tag="div", ref=7, id="react-aria123-:r1:"))


def codes(el) -> list[str]:
    return [candidate.to_code() for candidate in locator_candidates(el)]


def test_every_applicable_strategy_is_a_candidate_in_priority_order():
    el = element(placeholder="Type something and hit enter", id="tags-tokenfield", name="tags", label="Tags")
    assert codes(el) == [
        'page.get_by_label("Tags", exact=True)',
        'page.get_by_placeholder("Type something and hit enter", exact=True)',
        'page.locator("#tags-tokenfield")',
        "page.locator(\"input[name='tags']\")",
    ]


def test_a_button_can_be_found_by_test_id_role_or_text():
    el = element(tag="button", type="submit", testid="go", text="Login")
    assert codes(el) == [
        'page.get_by_test_id("go")', 'page.get_by_role("button", name="Login")', 'page.get_by_text("Login", exact=True)',
    ]


def test_the_best_candidate_is_the_locator_expression():
    el = element(placeholder="Search", id="q")
    assert locator_expression(el) == locator_candidates(el)[0].to_code()


def test_a_candidate_can_stand_for_its_first_match():
    candidate = locator_candidates(element(placeholder="Note"))[0]
    assert candidate.at_first().to_code() == 'page.get_by_placeholder("Note", exact=True).first'


def test_an_element_with_no_stable_identity_has_no_candidates():
    assert locator_candidates(element(tag="div", id="react-aria123-:r1:")) == []
