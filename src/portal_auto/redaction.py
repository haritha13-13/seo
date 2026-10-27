"""The one place secrets are scrubbed from text that is stored, shown or logged."""
import json
from collections.abc import Iterable, Iterator
from urllib.parse import quote

REDACTED = "***"


def _spellings(secret: str) -> set[str]:
    """How a secret can appear in output: raw, JSON-escaped, repr-escaped, URL-encoded."""
    return {secret, json.dumps(secret)[1:-1], repr(secret)[1:-1], quote(secret, safe=""), quote(secret)}


def redact(text: str, secrets: Iterable[str | None]) -> str:
    spellings = {form for secret in secrets if secret for form in _spellings(secret) if form}
    for form in sorted(spellings, key=len, reverse=True):
        text = text.replace(form, REDACTED)
    return text


MIN_SESSION_VALUE_LENGTH = 8


def _entries(state: object) -> Iterator[dict]:
    """Every cookie and localStorage item that is a dict, skipping whatever is malformed."""
    if not isinstance(state, dict):
        return
    cookies, origins = state.get("cookies"), state.get("origins")
    items = [*(cookies if isinstance(cookies, list) else [])]
    for origin in origins if isinstance(origins, list) else []:
        storage = origin.get("localStorage") if isinstance(origin, dict) else None
        items.extend(storage if isinstance(storage, list) else [])
    yield from (item for item in items if isinstance(item, dict))


def _string_leaves(node: object) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for child in node.values():
            yield from _string_leaves(child)
    elif isinstance(node, list):
        for child in node:
            yield from _string_leaves(child)


def _values_in(value: str) -> Iterator[str]:
    yield value
    try:
        parsed = json.loads(value)
    except ValueError:
        return
    if isinstance(parsed, (dict, list)):
        yield from _string_leaves(parsed)


def session_secrets(state_json: str) -> list[str]:
    """Every cookie and localStorage value (and string inside a JSON one) of a Playwright storage state, long enough to redact safely."""
    try:
        state = json.loads(state_json)
    except ValueError:
        return []
    values = (leaf for entry in _entries(state) if isinstance(entry.get("value"), str) for leaf in _values_in(entry["value"]))
    return [value for value in values if len(value) >= MIN_SESSION_VALUE_LENGTH]
