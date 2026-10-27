"""Single home for value handling shared by the live browser run and code generation:
Python literal quoting and the `{{ENV:NAME}}` secret placeholder."""
import json
import re
from collections.abc import Mapping

ENV_PLACEHOLDER = re.compile(r"\{\{ENV:(\w+)\}\}")


class UnknownPlaceholder(ValueError):
    """A `{{ENV:NAME}}` placeholder names a value that was not made available to this run."""


def py_literal(value: str) -> str:
    """Quote a string as a Python literal for generated code."""
    return json.dumps(value)


def env_names(value: str) -> list[str]:
    """Names of the `{{ENV:NAME}}` placeholders in `value`."""
    return ENV_PLACEHOLDER.findall(value)


def resolve_env(value: str, secrets: Mapping[str, str]) -> str:
    """Replace each `{{ENV:NAME}}` with its value from `secrets` (live run).

    Only the given mapping is consulted, never the process environment."""
    def lookup(match: re.Match) -> str:
        name = match.group(1)
        if name not in secrets:
            raise UnknownPlaceholder(f"Unknown placeholder {{{{ENV:{name}}}}}; it is not available to this run")
        return secrets[name]

    return ENV_PLACEHOLDER.sub(lookup, value)


def env_expression(value: str) -> str:
    """Python expression for `value` in generated code; placeholders become os.environ lookups."""
    parts = ENV_PLACEHOLDER.split(value)  # [text, NAME, text, NAME, ..., text]
    exprs = []
    for index, part in enumerate(parts):
        if index % 2:
            exprs.append(f'os.environ["{part}"]')
        elif part:
            exprs.append(py_literal(part))
    return " + ".join(exprs) or '""'
