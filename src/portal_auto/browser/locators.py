"""Ways to find a snapshot element again, as Playwright locators that render themselves as script code.
Priority: test id > label > placeholder > id > name > role+text > text. When a way matches several elements, the
recorder narrows it to the visible ones (a hidden copy in another dialog is common), then to a position among the
matches; an element none of these can identify is refused: a script that cannot find it again on replay is worse
than no script."""
import re
from dataclasses import dataclass, replace

from playwright.sync_api import Locator, Page

from portal_auto.browser.snapshot import Element
from portal_auto.literals import py_literal

_FORM_TAGS = ("input", "textarea", "select")
# Ids minted by UI frameworks (react-aria, radix, ember...) change on every page load.
_GENERATED_ID = re.compile(r"[:]|\d{5,}|^(react|radix|ember|headlessui|mui)", re.IGNORECASE)

TEST_ID = "test_id"
LABEL = "label"
PLACEHOLDER = "placeholder"
CSS = "css"
ROLE = "role"
TEXT = "text"
_EXACT = {"exact": True}
_CALLS: dict[str, tuple[str, dict[str, bool]]] = {
    TEST_ID: ("get_by_test_id", {}),
    LABEL: ("get_by_label", _EXACT),
    PLACEHOLDER: ("get_by_placeholder", _EXACT),
    CSS: ("locator", {}),
    TEXT: ("get_by_text", _EXACT),
}


class NoStableLocator(ValueError):
    """The element has nothing a later page load could find it by."""


@dataclass(frozen=True)
class LocatorSpec:
    """One way to find an element: `to_code()` is what a script runs and `resolve(page)` is the same locator live,
    both built from one Playwright call so they cannot drift apart."""
    strategy: str
    value: str
    role: str = ""
    first: bool = False
    visible: bool = False
    nth: int | None = None

    def to_code(self) -> str:
        method, args, options = self._call()
        rendered = [*map(py_literal, args), *(f"{key}={_option_code(value)}" for key, value in options.items())]
        code = f"page.{method}({', '.join(rendered)})"
        if self.visible:
            code += ".filter(visible=True)"
        if self.first:
            code += ".first"
        elif self.nth is not None:
            code += f".nth({self.nth})"
        return code

    def resolve(self, page: Page) -> Locator:
        method, args, options = self._call()
        locator = getattr(page, method)(*args, **options)
        if self.visible:
            locator = locator.filter(visible=True)
        if self.first:
            return locator.first
        return locator.nth(self.nth) if self.nth is not None else locator

    def at_first(self) -> "LocatorSpec":
        return replace(self, first=True)

    def only_visible(self) -> "LocatorSpec":
        return replace(self, visible=True)

    def at_position(self, index: int) -> "LocatorSpec":
        return self.at_first() if index == 0 else replace(self, nth=index)

    @property
    def positional(self) -> bool:
        return self.first or self.nth is not None

    def _call(self) -> tuple[str, tuple[str, ...], dict[str, str | bool]]:
        if self.strategy == ROLE:
            return "get_by_role", (self.role,), {"name": self.value}
        method, options = _CALLS[self.strategy]
        return method, (self.value,), options


def _option_code(value: str | bool) -> str:
    return repr(value) if isinstance(value, bool) else py_literal(value)


def _stable_id(el: Element) -> bool:
    return bool(el.id) and not _GENERATED_ID.search(el.id)


def _is_button(el: Element) -> bool:
    return el.tag == "button" or el.role == "button" or el.type in ("submit", "button")


def _role(el: Element) -> str:
    if el.tag == "a":
        return "link"
    return "button" if _is_button(el) else ""


def locator_candidates(el: Element) -> list[LocatorSpec]:
    """Every way to find `el`, best first."""
    candidates = [
        LocatorSpec(TEST_ID, el.testid) if el.testid else None,
        LocatorSpec(LABEL, el.label) if el.tag in _FORM_TAGS and el.label else None,
        LocatorSpec(PLACEHOLDER, el.placeholder) if el.placeholder else None,
        LocatorSpec(CSS, f"#{el.id}") if _stable_id(el) else None,
        LocatorSpec(CSS, f"{el.tag}[name={el.name!r}]") if el.name else None,
        LocatorSpec(ROLE, el.text, role=_role(el)) if _role(el) and el.text else None,
        LocatorSpec(TEXT, el.text) if el.text else None,
    ]
    return [candidate for candidate in candidates if candidate]


def _no_stable_locator(el: Element) -> NoStableLocator:
    return NoStableLocator(
        f"Element [{el.ref}] <{el.tag}> has no stable label, id, name or text to find it by again; choose another element"
    )


def _ambiguous(el: Element) -> NoStableLocator:
    return NoStableLocator(
        f"Element [{el.ref}] <{el.tag}> cannot be told apart from other elements on the page; choose another element"
    )


def locator_expression(el: Element) -> str:
    candidates = locator_candidates(el)
    if not candidates:
        raise _no_stable_locator(el)
    return candidates[0].to_code()


MAX_POSITIONS = 20  # how many matches are searched for the target's position


def choose_locator(el: Element, page: Page, target: Locator) -> LocatorSpec:
    """The best candidate that finds `target` and nothing else on the live page, hidden elements included (strict
    mode counts them on replay); failing that the best one narrowed to visible elements; failing that the best one
    narrowed to the target's position among its matches."""
    candidates = locator_candidates(el)
    if not candidates:
        raise _no_stable_locator(el)
    for candidate in candidates:
        if _finds_only(candidate.resolve(page), target):
            return candidate
    for candidate in (candidate.only_visible() for candidate in candidates):
        if _finds_only(candidate.resolve(page), target):
            return candidate
    for candidate in candidates:
        position = _by_position(candidate, page, target)
        if position:
            return position
    raise _ambiguous(el)


def _by_position(candidate: LocatorSpec, page: Page, target: Locator) -> LocatorSpec | None:
    """`candidate` (visible-only when that removes matches) pinned to the index at which `target` appears."""
    visible = candidate.only_visible()
    narrowed = visible if visible.resolve(page).count() < candidate.resolve(page).count() else candidate
    matches = narrowed.resolve(page)
    for index in range(min(matches.count(), MAX_POSITIONS)):
        if matches.nth(index).and_(target).count() == 1:
            return narrowed.at_position(index)
    return None


def _finds_only(locator: Locator, target: Locator) -> bool:
    return locator.count() == 1 and locator.and_(target).count() == 1
