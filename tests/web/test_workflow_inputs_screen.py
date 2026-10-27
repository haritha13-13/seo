"""S5: the Workflow editor lists the Inputs its Steps ask for and rejects invalid placeholders."""
import re

import pytest
from helpers import csrf_token

from portal_auto.services.workflows import WorkflowService

STEPS = ["Upload {{deck:file}}", "Enter {{description}} and {{username}}", "Upload {{cover:image}}"]


@pytest.fixture
def publish(app, world) -> int:
    with app.extensions["session_factory"]() as session:
        return WorkflowService(session).create(world.bank, "Publish deck", STEPS).id


def save(client, workflow_id: int, steps: list[str]):
    data = {"csrf_token": csrf_token(client), "name": "Publish deck", "active": "1", "action": "save", "step": steps}
    return client.post(f"/workflows/{workflow_id}/edit", data=data)


def inputs_section(html: str) -> str:
    start = html.index("Inputs this Workflow asks for at run time")
    return html[start:html.index("</section>", start)]


def test_editor_lists_each_input_with_its_kind_in_order(client, publish):
    section = inputs_section(client.get(f"/workflows/{publish}/edit").get_data(as_text=True))
    found = re.findall(r"<li>(\w+) <span class=\"muted input-kind\">(\w+)</span></li>", section)
    assert found == [("deck", "file"), ("description", "text"), ("cover", "image")]
    assert "username" not in section


def test_editor_says_none_for_a_workflow_without_inputs(client, world):
    section = inputs_section(client.get(f"/workflows/{world.pay}/edit").get_data(as_text=True))
    assert "None" in section


def test_editor_help_text_explains_the_placeholder_syntax(client, publish):
    html = client.get(f"/workflows/{publish}/edit").get_data(as_text=True)
    for syntax in ("{{name}}", "{{name:file}}", "{{name:image}}", "{{username}}", "{{password}}"):
        assert syntax in html


@pytest.mark.parametrize("bad_step, message", [
    ("Enter {{1st}}", "is not a valid Input name"),
    ("Upload {{deck:video}}", "has an unknown kind"),
    ("Upload {{deck:file}} then enter {{deck}}", "Input deck is used as both file and text"),
])
def test_saving_invalid_placeholders_shows_the_error_on_the_editor(app, client, publish, bad_step, message):
    response = save(client, publish, [bad_step])
    html = response.get_data(as_text=True)
    assert response.status_code == 200 and "flash-error" in html and message in html
    assert "Edit Workflow" in html and bad_step.replace("{", "") in html.replace("{", "")
    with app.extensions["session_factory"]() as session:
        assert [step.instruction for step in WorkflowService(session).get(publish).steps] == STEPS


def test_saved_inputs_list_follows_the_new_steps(client, publish):
    assert save(client, publish, ["Enter {{title}}"]).status_code == 302
    section = inputs_section(client.get(f"/workflows/{publish}/edit").get_data(as_text=True))
    assert "title" in section and "deck" not in section
