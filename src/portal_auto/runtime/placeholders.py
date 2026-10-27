"""Maps operator-facing Step placeholders to the engine's environment placeholders (ADR 0002, ADR 0005)."""
import re

from portal_auto.inputs import CREDENTIAL_NAMES, PLACEHOLDER, engine_placeholder, placeholder_spec
from portal_auto.runtime.interfaces import GenerationFailed
from portal_auto.services.errors import Invalid

_CREDENTIALS = {name: "{{ENV:PORTAL_%s}}" % name.upper() for name in CREDENTIAL_NAMES}
UNKNOWN_PLACEHOLDER = (
    "Unknown placeholder {{{{{placeholder}}}}}; only {{{{username}}}}, {{{{password}}}} and Inputs such as "
    "{{{{name}}}}, {{{{name:file}}}} or {{{{name:image}}}} are supported"
)


def _to_engine(match: re.Match) -> str:
    inner = match.group(1)
    if inner.lower() in _CREDENTIALS:
        return _CREDENTIALS[inner.lower()]
    try:
        spec = placeholder_spec(inner)
    except Invalid:
        spec = None
    if spec is None:
        raise GenerationFailed(UNKNOWN_PLACEHOLDER.format(placeholder=inner))
    return engine_placeholder(spec.name)


def to_engine_steps(steps: list[str]) -> list[str]:
    return [PLACEHOLDER.sub(_to_engine, step) for step in steps]
