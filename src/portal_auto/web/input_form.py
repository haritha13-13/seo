"""The Executor's Inputs form: built from the chosen Workflow's Steps on every request (ADR 0005)."""
from collections.abc import Mapping
from dataclasses import dataclass, field

from flask import request

from portal_auto.inputs import InputKind, InputSpec, UploadedFile, input_key, input_kind
from portal_auto.services.errors import Invalid
from portal_auto.web.deps import runs, workflows

TEXT_PREFIX = "input__"
FILE_PREFIX = "file__"
PREFILL_LOOKBACK = 20


@dataclass(frozen=True)
class InputField:
    spec: InputSpec
    kind: InputKind
    value: str = ""

    @property
    def label(self) -> str:
        return self.spec.name.replace("_", " ").capitalize()

    @property
    def field_name(self) -> str:
        return (FILE_PREFIX if self.kind.is_upload else TEXT_PREFIX) + self.spec.name


@dataclass(frozen=True)
class InputForm:
    fields: list[InputField] = field(default_factory=list)
    error: str | None = None

    @property
    def has_uploads(self) -> bool:
        return any(input_field.kind.is_upload for input_field in self.fields)


def input_form(workflow_id: int, typed: Mapping[str, str] | None = None) -> InputForm:
    """`typed` holds the text the operator just submitted; without it, text comes from the last Run."""
    try:
        specs = workflows().inputs(workflow_id)
    except Invalid as error:
        return InputForm(error=str(error))
    if not specs:
        return InputForm()
    texts = _by_key(_last_texts(workflow_id) if typed is None else typed)
    return InputForm([_field(spec, texts) for spec in specs])


def _field(spec: InputSpec, texts: Mapping[str, str]) -> InputField:
    kind = input_kind(spec.kind)
    return InputField(spec, kind, "" if kind.is_upload else texts.get(input_key(spec.name), ""))


def _by_key(texts: Mapping[str, str]) -> dict[str, str]:
    return {input_key(name): value for name, value in texts.items()}


def _last_texts(workflow_id: int) -> dict[str, str]:
    for run in runs().list_runs(workflow_id=workflow_id, limit=PREFILL_LOOKBACK):
        if run.input_values:
            return {name: entry["value"] for name, entry in run.input_values.items() if "value" in entry}
    return {}


def submitted_texts() -> dict[str, str]:
    return {name[len(TEXT_PREFIX):]: value for name, value in request.form.items() if name.startswith(TEXT_PREFIX)}


def submitted_files() -> dict[str, UploadedFile]:
    """Each chosen file by Input name; an empty chooser is left out so the Run reports it as required."""
    return {
        name[len(FILE_PREFIX):]: UploadedFile(upload.filename, upload.read())
        for name, upload in request.files.items()
        if name.startswith(FILE_PREFIX) and upload.filename
    }
