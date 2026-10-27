"""Starting a Run with Inputs: every declared Input is checked first, uploads are stored privately before the
worker starts, and the Run records values and file names but never file contents."""
import json
import stat
import threading
from pathlib import Path

import pytest

from portal_auto.config import Settings
from portal_auto.db.models import Credential, Portal, RunStatus
from portal_auto.inputs import UploadedFile
from portal_auto.services.errors import Invalid
from portal_auto.services.runs import RunService
from portal_auto.services.workflows import WorkflowService

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
DECK = b"%PDF-1.7 quarterly deck content"
STEPS = ["Type {{title}} into Title", "Upload {{deck:file}}", "Upload {{logo:image}} as the logo"]


class InspectingLauncher:
    """Records what the per-Run uploads folder held at the moment the worker would have started."""

    def __init__(self, root: Path, error: Exception | None = None):
        self._root = root
        self._error = error
        self.seen: dict[str, tuple[int, bytes]] = {}
        self.folder_mode: int | None = None

    def __call__(self, run_id: int) -> int:
        folder = self._root / "uploads" / f"run-{run_id}"
        self.folder_mode = stat.S_IMODE(folder.stat().st_mode) if folder.exists() else None
        for path in folder.rglob("*"):
            if path.is_file():
                self.seen[str(path)] = (stat.S_IMODE(path.stat().st_mode), path.read_bytes())
        if self._error:
            raise self._error
        return 4000 + run_id


@pytest.fixture
def timeout_elapsed():
    event = threading.Event()
    yield event
    event.set()


def run_service(session_factory, tmp_path, launcher, timeout_elapsed) -> RunService:
    return RunService(session_factory, Settings(root=tmp_path, run_timeout_seconds=5), launcher,
                      kill=lambda pid: None, sleep=lambda seconds: timeout_elapsed.wait(5), pid_alive=lambda pid: True)


@pytest.fixture
def launcher(tmp_path):
    return InspectingLauncher(tmp_path)


@pytest.fixture
def service(session_factory, tmp_path, launcher, timeout_elapsed):
    service = run_service(session_factory, tmp_path, launcher, timeout_elapsed)
    yield service
    timeout_elapsed.set()
    service.wait_for_watchdogs()


@pytest.fixture
def ids(session):
    portal = Portal(name="Demo", url="https://demo.test")
    credential = Credential(portal=portal, label="main", username="alice", password_encrypted="x")
    session.add_all([portal, credential])
    session.commit()
    workflow = WorkflowService(session).create(portal.id, "Publish", STEPS)
    return workflow.id, credential.id


def good_files() -> dict[str, UploadedFile]:
    return {"deck": UploadedFile("deck.pdf", DECK), "logo": UploadedFile("logo.png", PNG)}


def start(service, ids, inputs=None, files=None):
    return service.start_run(*ids, inputs=inputs, files=files)


def test_uploads_are_private_and_in_place_before_the_worker_starts(service, ids, launcher, tmp_path):
    run = start(service, ids, {"title": "Q3 results"}, good_files())

    folder = tmp_path / "uploads" / f"run-{run.id}"
    assert launcher.folder_mode == 0o700
    contents = {Path(path).name: seen for path, seen in launcher.seen.items()}
    assert contents == {"deck.pdf": (0o600, DECK), "logo.png": (0o600, PNG)}
    assert all(Path(path).is_relative_to(folder) for path in launcher.seen)


def test_the_run_records_text_values_and_file_names_but_never_contents(service, ids, launcher):
    run = start(service, ids, {"title": "Q3 results"}, good_files())

    stored = service.get_run(run.id)
    recorded = json.loads(stored.inputs)
    assert recorded["title"] == {"kind": "text", "value": "Q3 results"}
    assert {name: (entry["kind"], entry["filename"]) for name, entry in recorded.items() if name != "title"} == {
        "deck": ("file", "deck.pdf"), "logo": ("image", "logo.png")}
    assert {recorded["deck"]["path"], recorded["logo"]["path"]} == set(launcher.seen)
    assert DECK.decode() not in stored.inputs
    assert stored.input_values == recorded


def test_every_problem_is_listed_at_once_and_nothing_is_started(service, ids, launcher, tmp_path):
    files = {"logo": UploadedFile("logo.png", b"plain text pretending to be an image"), "extra": UploadedFile("x", b"1")}
    with pytest.raises(Invalid) as caught:
        start(service, ids, {"title": "   ", "colour": "crimson-shade"}, files)

    message = str(caught.value)
    for fragment in ("title", "deck", "logo", "colour", "extra", "PNG, JPEG, GIF or WebP"):
        assert fragment in message
    assert "crimson-shade" not in message and "plain text pretending" not in message
    assert service.list_runs() == [] and launcher.seen == {}
    assert not (tmp_path / "uploads").exists()


def test_input_names_match_whatever_their_case(service, ids):
    files = {"DECK": UploadedFile("deck.pdf", DECK), "Logo": UploadedFile("logo.png", PNG)}
    run = start(service, ids, {"TITLE": "Q3"}, files)

    assert set(service.get_run(run.id).input_values) == {"title", "deck", "logo"}


def test_one_input_given_twice_in_different_case_is_invalid(service, ids):
    with pytest.raises(Invalid, match="title"):
        start(service, ids, {"title": "a", "Title": "b"}, good_files())


def test_a_file_input_given_as_text_and_text_given_as_a_file_are_invalid(service, ids):
    with pytest.raises(Invalid) as caught:
        start(service, ids, {"deck": "deck.pdf"}, {"title": UploadedFile("t.txt", b"x"), "logo": UploadedFile("l.png", PNG)})
    assert "title" in str(caught.value) and "deck" in str(caught.value)


def test_an_uploaded_file_name_cannot_escape_the_run_folder(service, ids, launcher, tmp_path):
    files = {**good_files(), "logo": UploadedFile("../../etc/x.png", PNG)}
    run = start(service, ids, {"title": "Q3"}, files)

    logo = service.get_run(run.id).input_values["logo"]
    assert logo["filename"] == "x.png"
    assert Path(logo["path"]).name == "x.png"
    assert Path(logo["path"]).resolve().is_relative_to((tmp_path / "uploads" / f"run-{run.id}").resolve())
    assert not (tmp_path / "etc").exists()


def test_two_inputs_with_the_same_file_name_keep_both_files(session, service, ids, launcher):
    workflow_id, credential_id = ids
    WorkflowService(session).replace_steps(workflow_id, ["Upload {{front:image}}", "Upload {{back:image}}"])
    files = {"front": UploadedFile("photo.png", PNG), "back": UploadedFile("photo.png", PNG + b"back")}
    run = service.start_run(workflow_id, credential_id, files=files)

    values = service.get_run(run.id).input_values
    assert values["front"]["path"] != values["back"]["path"]
    assert sorted(content for _, content in launcher.seen.values()) == sorted([PNG, PNG + b"back"])


def test_the_uploads_are_removed_when_the_worker_cannot_be_started(session_factory, ids, tmp_path, timeout_elapsed):
    launcher = InspectingLauncher(tmp_path, error=OSError("no python"))
    service = run_service(session_factory, tmp_path, launcher, timeout_elapsed)

    run = start(service, ids, {"title": "Q3"}, good_files())

    assert run.status == RunStatus.FAILED and run.active_slot is None
    assert launcher.seen and not any(Path(path).exists() for path in launcher.seen)
    assert not (tmp_path / "uploads" / f"run-{run.id}").exists()


def test_a_workflow_without_inputs_starts_without_any(session, service, ids, launcher):
    workflow_id, credential_id = ids
    WorkflowService(session).replace_steps(workflow_id, ["Click Save"])
    run = service.start_run(workflow_id, credential_id)
    assert service.get_run(run.id).input_values == {}
    assert launcher.folder_mode is None
