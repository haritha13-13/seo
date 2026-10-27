"""Inputs: named values the operator supplies for each Run, written as placeholders in the Steps (ADR 0005).

Pure and dependency-free so services, runtime and web share one definition. Adding a kind means adding
one entry to `KINDS`."""
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from portal_auto.services.errors import Invalid

MB = 1024 * 1024
MAX_TEXT_CHARS = 5000
MAX_FILENAME_BYTES = 255
DEFAULT_KIND = "text"
CREDENTIAL_NAMES = ("username", "password")
INPUT_ENV_PREFIX = "PORTAL_INPUT_"
PLACEHOLDER = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")
_ENGINE_PREFIX = "ENV:"
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_PATH_SEPARATORS = re.compile(r"[\\/]")
_IMAGE_SIGNATURES = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")


@dataclass(frozen=True)
class UploadedFile:
    filename: str
    content: bytes = field(repr=False)


@dataclass(frozen=True)
class InputSpec:
    name: str
    kind: str


@dataclass(frozen=True)
class InputKind:
    name: str
    is_upload: bool
    accept: str
    max_bytes: int | None
    check: Callable[["InputKind", "str | UploadedFile"], None] = field(repr=False)

    def validate(self, value: str | UploadedFile) -> None:
        """Raise Invalid with an operator-safe message (never the value) unless `value` suits this kind."""
        self.check(self, value)


def _check_text(kind: InputKind, value: str | UploadedFile) -> None:
    if not isinstance(value, str):
        raise Invalid("Text must be typed in, not uploaded")
    if not value.strip():
        raise Invalid("Text must not be blank")
    if len(value) > MAX_TEXT_CHARS:
        raise Invalid(f"Text must be at most {MAX_TEXT_CHARS} characters")


def _check_file(kind: InputKind, value: str | UploadedFile) -> None:
    if not isinstance(value, UploadedFile):
        raise Invalid("A file must be uploaded")
    safe_filename(value.filename)
    if not value.content:
        raise Invalid("The file is empty")
    if kind.max_bytes is not None and len(value.content) > kind.max_bytes:
        raise Invalid(f"The file is larger than {kind.max_bytes // MB} MB")


def _check_image(kind: InputKind, value: str | UploadedFile) -> None:
    _check_file(kind, value)
    if not _is_image(value.content):
        raise Invalid("The file must be a PNG, JPEG, GIF or WebP image (checked by its content, not its name)")


def _is_image(content: bytes) -> bool:
    is_webp = content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    return is_webp or content.startswith(_IMAGE_SIGNATURES)


KINDS: Mapping[str, InputKind] = MappingProxyType({kind.name: kind for kind in (
    InputKind("text", is_upload=False, accept="", max_bytes=None, check=_check_text),
    InputKind("file", is_upload=True, accept="", max_bytes=100 * MB, check=_check_file),
    InputKind("image", is_upload=True, accept="image/*", max_bytes=20 * MB, check=_check_image),
)})


def input_kind(name: str) -> InputKind:
    try:
        return KINDS[name]
    except KeyError:
        raise Invalid(f"Unknown Input kind '{name}'; use one of: {', '.join(KINDS)}") from None


def input_key(name: str) -> str:
    """Input names are case-insensitive: names that differ only by case are the same Input."""
    return name.upper()


def env_name(name: str) -> str:
    return INPUT_ENV_PREFIX + input_key(name)


def is_input_env(name: str) -> bool:
    return name.startswith(INPUT_ENV_PREFIX)


def engine_placeholder(name: str) -> str:
    return "{{ENV:%s}}" % env_name(name)


def safe_filename(filename: str) -> str:
    """The uploaded file's own base name, with any folders stripped; Invalid when nothing usable is left."""
    base = _PATH_SEPARATORS.split(filename)[-1].strip()
    unusable = base in ("", ".", "..") or any(ord(char) < 32 for char in base)
    if unusable or len(base.encode()) > MAX_FILENAME_BYTES:
        raise Invalid("The file has no usable name")
    return base


def placeholder_spec(inner: str) -> InputSpec | None:
    """The Input declared by a placeholder's inner text; None for the Credential's and the engine's own."""
    if inner.startswith(_ENGINE_PREFIX):
        return None
    name, colon, kind = (part.strip() for part in inner.partition(":"))
    if name.lower() in CREDENTIAL_NAMES:
        if colon:
            raise Invalid(f"{{{{{name}}}}} is the Credential's sign-in and takes no kind")
        return None
    if not _NAME.fullmatch(name):
        raise Invalid(
            f"{{{{{inner}}}}} is not a valid Input name; use letters, digits and underscores, starting with a letter"
        )
    kind = kind.lower() if colon else DEFAULT_KIND
    if kind not in KINDS:
        raise Invalid(f"{{{{{inner}}}}} has an unknown kind '{kind}'; use one of: {', '.join(KINDS)}")
    return InputSpec(name, kind)


def parse_inputs(steps: Sequence[str]) -> list[InputSpec]:
    """Each Input the Steps declare, once, in order of first appearance; Invalid lists every problem."""
    specs: dict[str, InputSpec] = {}
    problems: list[str] = []
    for inner in (found for step in steps for found in PLACEHOLDER.findall(step)):
        try:
            spec = placeholder_spec(inner)
        except Invalid as error:
            problems.append(str(error))
            continue
        if spec is None:
            continue
        known = specs.setdefault(input_key(spec.name), spec)
        if known.kind != spec.kind:
            problems.append(f"Input {known.name} is used as both {known.kind} and {spec.kind}; give it one kind")
    if problems:
        raise Invalid("; ".join(dict.fromkeys(problems)))
    return list(specs.values())


def match_inputs(
    specs: Sequence[InputSpec], texts: Mapping[str, str], files: Mapping[str, UploadedFile],
) -> list[tuple[InputSpec, str | UploadedFile]]:
    """Pairs each declared Input with the value supplied for it, matching names whatever their case.

    Invalid lists every problem at once: missing, unknown, given twice, or not valid for its kind."""
    supplied: dict[str, list[tuple[str, str | UploadedFile]]] = {}
    for name, value in [*texts.items(), *files.items()]:
        supplied.setdefault(input_key(name), []).append((name, value))
    problems: list[str] = []
    pairs: list[tuple[InputSpec, str | UploadedFile]] = []
    for spec in specs:
        given = supplied.pop(input_key(spec.name), [])
        problem = _supply_problem(spec, [value for _, value in given])
        if problem:
            problems.append(problem)
        else:
            pairs.append((spec, given[0][1]))
    problems += [f"Unknown Input {name}" for given in supplied.values() for name, _ in given]
    if problems:
        raise Invalid("; ".join(problems))
    return pairs


def _supply_problem(spec: InputSpec, values: list[str | UploadedFile]) -> str | None:
    if not values:
        return f"Input {spec.name} is required"
    if len(values) > 1:
        return f"Input {spec.name} was given more than once"
    try:
        input_kind(spec.kind).validate(values[0])
    except Invalid as error:
        return f"Input {spec.name}: {error}"
    return None


def recorded_text(spec: InputSpec, value: str) -> dict[str, str]:
    return {"kind": spec.kind, "value": value}


def recorded_file(spec: InputSpec, path: str) -> dict[str, str]:
    return {"kind": spec.kind, "filename": _PATH_SEPARATORS.split(path)[-1], "path": path}


def input_env(recorded: Mapping[str, Mapping[str, str]]) -> dict[str, str]:
    """`PORTAL_INPUT_<NAME>` for each recorded Input: the text itself, or the uploaded file's path."""
    return {
        env_name(name): entry["path"] if input_kind(entry["kind"]).is_upload else entry["value"]
        for name, entry in recorded.items()
    }


def upload_paths(recorded: Mapping[str, Mapping[str, str]]) -> list[str]:
    return [entry["path"] for entry in recorded.values() if input_kind(entry["kind"]).is_upload]
