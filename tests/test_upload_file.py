"""Generation with Inputs: files are uploaded through the page's own controls, and Input values are only ever
passed as placeholders so the recorded script works with the next Run's values."""
import pytest
from site_server import serve

from portal_auto.agent.tools import build_tools
from portal_auto.browser.session import BrowserSession
from portal_auto.recording import ActionRecorder, Fill, Select, UploadFile

PAGE = """
<label for="title">Title</label><input id="title" type="text">
<label for="passport">Passport</label><input id="passport" type="text">
<label for="category">Category</label>
<select id="category"><option>Finance</option><option>Quarterly report</option></select>
<label for="deck" style="border:1px solid">Attach deck</label>
<input id="deck" type="file" hidden onchange="show('deck', this)">
<label for="logo">Logo</label><input id="logo" type="file" onchange="show('logo', this)">
<p id="status"></p>
<script>
function show(name, input) {
  const file = input.files[0];
  file.text().then(text => document.getElementById('status').textContent += ` ${name} got ${file.name} saying ${text};`);
}
</script>
"""
DECK = "{{ENV:PORTAL_INPUT_DECK}}"
LOGO = "{{ENV:PORTAL_INPUT_LOGO}}"
TITLE = "{{ENV:PORTAL_INPUT_TITLE}}"


@pytest.fixture
def site():
    server, base_url = serve({"/": PAGE})
    yield f"{base_url}/"
    server.shutdown()


@pytest.fixture
def files(tmp_path):
    deck, logo = tmp_path / "deck" / "q3-deck.pdf", tmp_path / "logo" / "brand.png"
    for path, text in ((deck, "deck-bytes"), (logo, "logo-bytes")):
        path.parent.mkdir()
        path.write_text(text)
    return str(deck), str(logo)


@pytest.fixture
def secrets(files):
    deck, logo = files
    return {
        "PORTAL_USERNAME": "alice", "PORTAL_INPUT_TITLE": "Q3 results", "PORTAL_INPUT_CATEGORY": "Quarterly report",
        "PORTAL_INPUT_DECK": deck, "PORTAL_INPUT_LOGO": logo, "PORTAL_INPUT_PASSPORT": "X1234567",
        "PORTAL_INPUT_CODE": "ab",
    }


@pytest.fixture
def session(site, secrets, files):
    browser = BrowserSession(ActionRecorder(), site, secrets, input_files=files)
    browser.start(site)
    yield browser
    browser.close()


def ref_of(session, description: str, tag: str) -> int:
    session.snapshot()
    return next(e.ref for e in session._snapshot.elements.values() if e.description == description and e.tag == tag)


def recorded(session, kind) -> list:
    return [action for action in session.recorder.actions if isinstance(action, kind)]


def test_the_snapshot_lists_a_hidden_file_input_and_marks_it(session):
    lines = session.snapshot().splitlines()
    assert any("<input type=file hidden>" in line and "Attach deck" in line for line in lines)
    assert any("<input type=file>" in line and "Logo" in line for line in lines)


def test_a_hidden_file_input_behind_a_visible_label_gets_the_file_through_the_chooser(session, files):
    session.upload_file(ref_of(session, "Attach deck", "label"), DECK)

    assert session.assert_text("deck got q3-deck.pdf saying deck-bytes")
    [upload] = recorded(session, UploadFile)
    assert upload.value == DECK and "expect_file_chooser" in "\n".join(upload.to_code())
    assert files[0] not in session.snapshot()


def test_a_visible_file_input_gets_the_file_set_directly(session):
    session.upload_file(ref_of(session, "Logo", "input"), LOGO)

    assert session.assert_text("logo got brand.png saying logo-bytes")
    [upload] = recorded(session, UploadFile)
    assert upload.to_code() == [f'{upload.trigger_locator}.set_input_files(os.environ["PORTAL_INPUT_LOGO"])']


@pytest.mark.parametrize("value", ["Q3 results", "Quarterly report", "X1234567"])
def test_typing_an_input_value_literally_is_refused_with_its_placeholder_named(session, value):
    with pytest.raises(ValueError, match=r"\{\{ENV:PORTAL_INPUT_"):
        session.fill(ref_of(session, "Title", "input"), value)
    assert recorded(session, Fill) == []


def test_choosing_an_input_value_literally_is_refused(session):
    with pytest.raises(ValueError, match=r"\{\{ENV:PORTAL_INPUT_CATEGORY\}\}"):
        session.select_option(ref_of(session, "Category", "select"), "Quarterly report")
    assert recorded(session, Select) == []


@pytest.mark.parametrize("which", ["path", "file name"])
def test_uploading_an_input_file_by_its_path_or_name_is_refused(session, files, which):
    value = files[0] if which == "path" else "q3-deck.pdf"
    with pytest.raises(ValueError, match=r"\{\{ENV:PORTAL_INPUT_DECK\}\}"):
        session.upload_file(ref_of(session, "Logo", "input"), value)
    assert recorded(session, UploadFile) == []


@pytest.mark.parametrize("value", [TITLE, "{{ENV:PORTAL_USERNAME}}", "/etc/hostname", f"x{DECK}"])
def test_only_an_input_files_placeholder_can_be_uploaded(session, value):
    with pytest.raises(ValueError):
        session.upload_file(ref_of(session, "Logo", "input"), value)
    assert recorded(session, UploadFile) == []
    assert not session.assert_text("logo got")


def test_short_input_values_are_not_mistaken_for_literals(session):
    session.fill(ref_of(session, "Title", "input"), "ab")
    assert [fill.value for fill in recorded(session, Fill)] == ["ab"]


def test_input_placeholders_are_filled_and_chosen_live_but_recorded_as_placeholders(session):
    session.fill(ref_of(session, "Title", "input"), TITLE)
    session.fill(ref_of(session, "Passport", "input"), "{{ENV:PORTAL_INPUT_PASSPORT}}")
    session.select_option(ref_of(session, "Category", "select"), "{{ENV:PORTAL_INPUT_CATEGORY}}")

    page = session._page
    assert session.worker.call(page.input_value, "#title") == "Q3 results"
    assert session.worker.call(page.input_value, "#category") == "Quarterly report"
    assert [select.option for select in recorded(session, Select)] == ["{{ENV:PORTAL_INPUT_CATEGORY}}"]
    assert [fill.value for fill in recorded(session, Fill)] == [TITLE, "{{ENV:PORTAL_INPUT_PASSPORT}}"]


def test_the_agent_tools_upload_and_explain_a_refusal(session):
    tools = {tool.name: tool for tool in build_tools(session)}

    assert tools["upload_file"].run(ref=ref_of(session, "Attach deck", "label"), value=DECK).startswith("Uploaded")
    refusal = tools["fill"].run(ref=ref_of(session, "Title", "input"), value="Q3 results")
    assert refusal.startswith("ERROR") and "{{ENV:PORTAL_INPUT_TITLE}}" in refusal
