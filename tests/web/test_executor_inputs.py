"""S5: the Executor's Inputs form, built from the chosen Workflow's Steps at request time."""
import io
import re
from pathlib import Path

import pytest
from helpers import csrf_token

from portal_auto.db.models import Run
from portal_auto.services.workflows import WorkflowService

TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)
PUBLISH_STEPS = [
    "Upload {{deck:file}} with the Choose file button",
    "Enter {{description}} in the Description box",
    "Upload {{cover:image}} as the cover",
    "Select {{category}} in the Category dropdown, then enter {{description}} again",
]


def set_steps(app, workflow_id: int, steps: list[str]) -> None:
    with app.extensions["session_factory"]() as session:
        WorkflowService(session).replace_steps(workflow_id, steps)


@pytest.fixture
def publish(app, world) -> int:
    with app.extensions["session_factory"]() as session:
        return WorkflowService(session).create(world.bank, "Publish deck", PUBLISH_STEPS).id


def selection(world, workflow_id: int) -> dict:
    return {"portal_id": world.bank, "workflow_id": workflow_id, "credential_id": world.main}


def page(client, world, workflow_id: int) -> str:
    return client.get("/executor/", query_string=selection(world, workflow_id)).get_data(as_text=True)


def execute(client, world, workflow_id: int, **fields):
    data = selection(world, workflow_id) | {"csrf_token": csrf_token(client), "verified": "1"} | fields
    return client.post("/executor/", data=data, content_type="multipart/form-data")


def complete_inputs(**overrides) -> dict:
    return {
        "input__description": "Quarterly results",
        "input__category": "Finance",
        "file__deck": (io.BytesIO(b"slides"), "q3.pptx"),
        "file__cover": (io.BytesIO(TINY_PNG), "cover.png"),
    } | overrides


def runs_of(app) -> list[Run]:
    with app.extensions["session_factory"]() as session:
        return session.query(Run).order_by(Run.id).all()


def input_tags(html: str) -> list[str]:
    return re.findall(r"<input[^>]*name=\"(?:input|file)__[^\"]+\"[^>]*>", html)


def test_form_has_one_required_field_per_input_in_order_of_first_appearance(client, world, publish):
    tags = input_tags(page(client, world, publish))
    assert [re.search(r'name="([^"]+)"', tag).group(1) for tag in tags] == [
        "file__deck", "input__description", "file__cover", "input__category",
    ]
    assert all("required" in tag for tag in tags)


def test_text_inputs_are_text_boxes_and_uploads_are_file_choosers_with_the_registry_accept(client, world, publish):
    deck, description, cover, _ = input_tags(page(client, world, publish))
    assert 'type="text"' in description and 'maxlength="5000"' in description
    assert 'type="file"' in deck and 'accept=""' in deck
    assert 'type="file"' in cover and 'accept="image/*"' in cover


def test_labels_read_as_words_with_a_kind_hint(client, world, publish):
    html = page(client, world, publish)
    assert re.search(r"Deck\s*<span class=\"muted input-kind\">file</span>", html)
    assert re.search(r"Cover\s*<span class=\"muted input-kind\">image</span>", html)
    assert re.search(r"Description\s*<span class=\"muted input-kind\">text</span>", html)


def test_label_turns_underscores_into_spaces(app, client, world, publish):
    set_steps(app, publish, ["Enter {{due_date}} in the Due box"])
    assert re.search(r"Due date\s*<span", page(client, world, publish))


def test_execute_form_is_multipart(client, world, publish):
    assert 'enctype="multipart/form-data"' in page(client, world, publish)


def test_changing_the_steps_changes_the_form_on_the_next_load(app, client, world, publish):
    set_steps(app, publish, ["Enter {{title}} in the Title box"])
    html = page(client, world, publish)
    assert 'name="input__title"' in html
    assert "input__description" not in html and "file__deck" not in html


def test_workflow_without_inputs_shows_no_input_fields(client, world):
    html = page(client, world, world.pay)
    assert input_tags(html) == []
    assert "Inputs" not in html


def test_workflow_without_inputs_still_executes_as_before(app, client, world):
    response = execute(client, world, world.pay)
    assert response.status_code == 302 and response.headers["Location"].endswith("/runs/1")
    assert runs_of(app)[0].input_values == {}


def test_execute_passes_text_and_files_to_the_run(app, client, world, publish):
    response = execute(client, world, publish, **complete_inputs())
    assert response.status_code == 302, response.get_data(as_text=True)
    values = runs_of(app)[0].input_values
    assert values["description"] == {"kind": "text", "value": "Quarterly results"}
    assert values["category"]["value"] == "Finance"
    assert values["deck"]["filename"] == "q3.pptx" and values["cover"]["filename"] == "cover.png"


def test_uploaded_file_lands_in_the_runs_private_folder(app, client, world, publish):
    execute(client, world, publish, **complete_inputs())
    run = runs_of(app)[0]
    folder = (app.extensions["settings"].uploads_dir / f"run-{run.id}").resolve()
    deck, cover = (Path(run.input_values[name]["path"]).resolve() for name in ("deck", "cover"))
    assert deck.is_relative_to(folder) and cover.is_relative_to(folder)
    assert deck.read_bytes() == b"slides" and cover.read_bytes() == TINY_PNG
    assert (deck.name, cover.name) == ("q3.pptx", "cover.png")
    assert oct(folder.stat().st_mode & 0o777) == "0o700"


def test_uploaded_file_is_gone_from_the_page(app, client, world, publish):
    execute(client, world, publish, **complete_inputs())
    html = page(client, world, publish)
    assert "slides" not in html and str(app.extensions["settings"].uploads_dir) not in html


def test_missing_inputs_list_every_problem_and_start_no_run(app, client, world, publish):
    html = execute(client, world, publish).get_data(as_text=True)
    assert "flash-error" in html
    for name in ("deck", "description", "cover", "category"):
        assert f"Input {name} is required" in html
    assert runs_of(app) == []


def test_blank_text_is_rejected_server_side(app, client, world, publish):
    html = execute(client, world, publish, **complete_inputs(input__category="   ")).get_data(as_text=True)
    assert "Input category: Text must not be blank" in html
    assert runs_of(app) == []


def test_rejected_execute_keeps_the_selection_and_typed_text_and_asks_for_files_again(app, client, world, publish):
    fields = complete_inputs(input__category="")
    html = execute(client, world, publish, **fields).get_data(as_text=True)
    assert "Executor" in html and 'name="workflow_id" value="%d"' % publish in html
    assert 'value="Quarterly results"' in html
    assert "choose the files again" in html


def test_missing_verification_keeps_typed_text(client, world, publish):
    data = selection(world, publish) | {"csrf_token": csrf_token(client), "input__description": "Kept text"}
    html = client.post("/executor/", data=data, content_type="multipart/form-data").get_data(as_text=True)
    assert "Verification" in html and 'value="Kept text"' in html


def test_unknown_input_is_rejected(app, client, world, publish):
    html = execute(client, world, publish, **complete_inputs(input__extra="x")).get_data(as_text=True)
    assert "Unknown Input extra" in html
    assert runs_of(app) == []


def test_text_file_named_png_is_rejected_as_an_image(app, client, world, publish):
    fields = complete_inputs(file__cover=(io.BytesIO(b"just text"), "fake.png"))
    html = execute(client, world, publish, **fields).get_data(as_text=True)
    assert "Input cover: The file must be a PNG, JPEG, GIF or WebP image" in html
    assert runs_of(app) == []


def test_text_is_prefilled_from_the_last_run_with_inputs_but_files_never_are(app, client, world, publish):
    execute(client, world, publish, **complete_inputs())
    html = page(client, world, publish)
    deck, description, _, category = input_tags(html)
    assert 'value="Quarterly results"' in description and 'value="Finance"' in category
    assert "value=" not in deck and "q3.pptx" not in html


def test_prefill_matches_names_whatever_their_case(app, client, world, publish):
    execute(client, world, publish, **complete_inputs())
    set_steps(app, publish, ["Enter {{Description}} in the Description box"])
    assert 'value="Quarterly results"' in input_tags(page(client, world, publish))[0]


def test_values_and_filenames_are_escaped(app, client, world, publish):
    fields = complete_inputs(input__description="<script>alert(1)</script>")
    execute(client, world, publish, **fields)
    html = page(client, world, publish)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_malformed_steps_show_the_problem_instead_of_a_500(app, client, world, publish):
    with app.extensions["session_factory"]() as session:
        from portal_auto.db.models import WorkflowStep

        step = session.query(WorkflowStep).filter_by(workflow_id=publish).first()
        step.instruction = "Enter {{1bad}} and {{x:video}}"
        session.commit()
    response = client.get("/executor/", query_string=selection(world, publish))
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "is not a valid Input name" in html and "unknown kind" in html
    assert execute(client, world, publish).status_code == 200
    assert runs_of(app) == []
