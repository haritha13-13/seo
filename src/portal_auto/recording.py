"""Actions the agent performed. Each action knows how to render itself as script code,
so the recorder and the code generator can never drift apart."""
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from portal_auto.literals import env_expression, py_literal

CHOOSER = "chooser"
FILE_INPUT = "file_input"


@dataclass(frozen=True)
class Action:
    kind = "action"

    def to_code(self) -> list[str]:
        raise NotImplementedError

    def to_dict(self) -> dict:
        return {"kind": self.kind, **asdict(self)}


@dataclass(frozen=True)
class Goto(Action):
    url: str
    kind = "goto"

    def to_code(self):
        return [f"page.goto({py_literal(self.url)})"]


@dataclass(frozen=True)
class Click(Action):
    locator: str
    kind = "click"

    def to_code(self):
        return [f"{self.locator}.click()", 'page.wait_for_load_state("domcontentloaded")']


@dataclass(frozen=True)
class Fill(Action):
    locator: str
    value: str  # may contain {{ENV:NAME}} placeholders
    kind = "fill"

    def to_code(self):
        return [f"{self.locator}.fill({env_expression(self.value)})"]


@dataclass(frozen=True)
class Select(Action):
    locator: str
    option: str
    kind = "select"

    def to_code(self):
        return [f"{self.locator}.select_option(label={env_expression(self.option)})"]


@dataclass(frozen=True)
class PressKey(Action):
    """Press a navigation key in a field, e.g. Enter to add a tag."""
    locator: str
    key: str
    kind = "press_key"

    def to_code(self):
        return [f"{self.locator}.press({py_literal(self.key)})"]


@dataclass(frozen=True)
class UploadFile(Action):
    """Answers the file chooser a visible trigger opens (`CHOOSER`), or sets the file on a file input (`FILE_INPUT`)."""
    trigger_locator: str
    value: str  # an Input placeholder such as {{ENV:PORTAL_INPUT_DECK}}
    via: str = CHOOSER
    kind = "upload_file"

    def to_code(self):
        files = env_expression(self.value)
        if self.via == FILE_INPUT:
            return [f"{self.trigger_locator}.set_input_files({files})"]
        return [
            "with page.expect_file_chooser() as chooser:",
            f"    {self.trigger_locator}.click()",
            f"chooser.value.set_files({files})",
        ]


@dataclass(frozen=True)
class AssertText(Action):
    text: str
    kind = "assert_text"

    def to_code(self):
        return [f"page.get_by_text({py_literal(self.text)}).first.wait_for()"]


@dataclass(frozen=True)
class WaitFor(Action):
    """Pause until something shows up, e.g. while a person types an emailed code into the browser."""
    text: str
    timeout_ms: int
    kind = "wait_for"

    def to_code(self):
        text = py_literal(self.text)
        target = f"page.get_by_label({text}).or_(page.get_by_placeholder({text})).or_(page.get_by_text({text})).first"
        return [f"{target}.wait_for(timeout={self.timeout_ms})"]


class ActionRecorder:
    def __init__(self):
        self.actions: list[Action] = []
        self.final_page_url: str | None = None  # where the browser was when the agent's work ended

    def add(self, action: Action) -> None:
        self.actions.append(action)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps([a.to_dict() for a in self.actions], indent=2))
