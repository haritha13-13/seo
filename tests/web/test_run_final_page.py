"""S5: a finished Run's final page address (the published URL) is shown as a result, not buried in the log."""
import pytest
from helpers import add_run

from portal_auto.db.models import RunStatus

PUBLISHED = "https://www.sharepresentation.com/presentation/example-deck-123"


def page(client, run_id: int) -> str:
    return client.get(f"/runs/{run_id}").get_data(as_text=True)


def test_a_successful_runs_final_page_is_shown_as_a_link(app, client, world):
    run_id = add_run(app, world, output=f"OK: generated completed\nFinal page: {PUBLISHED}\n")

    html = page(client, run_id)

    assert f'href="{PUBLISHED}"' in html
    assert "Published page" in html
    assert 'rel="noopener noreferrer"' in html


def test_the_last_final_page_line_wins(app, client, world):
    output = f"Final page: https://old.example/x\nOK\nFinal page: {PUBLISHED}\n"
    assert f'href="{PUBLISHED}"' in page(client, add_run(app, world, output=output))


@pytest.mark.parametrize("output", [
    "OK: generated completed\n",
    "Final page: javascript:alert(1)\n",
    "Final page: file:///etc/passwd\n",
    "Final page: not a url\n",
    "the Final page: https://x.example/ is mentioned mid-line\n",
])
def test_nothing_is_linked_unless_it_is_a_plain_web_address(app, client, world, output):
    html = page(client, add_run(app, world, output=output))
    assert "Published page" not in html


def test_a_failed_run_has_no_published_page_even_if_its_output_mentions_one(app, client, world):
    run_id = add_run(app, world, status=RunStatus.FAILED, output=f"Final page: {PUBLISHED}\n")
    assert "Published page" not in page(client, run_id)


def test_the_address_is_escaped(app, client, world):
    html = page(client, add_run(app, world, output='Final page: https://x.example/a"><script>alert(1)</script>\n'))
    assert "<script>alert(1)</script>" not in html


def log(status_line: str) -> str:
    return f"OK: generated completed\nFinal page: {PUBLISHED}\n{status_line}\n"


@pytest.mark.parametrize("line, label", [
    ("Publication status: published - the page loads without signing in (HTTP 200)", "Published"),
    ("Publication status: not_public - HTTP 404 without signing in", "Not publicly accessible"),
    ("Publication status: unreachable - the site did not answer", "Could not check"),
])
def test_the_public_check_result_is_shown_next_to_the_published_link(app, client, world, line, label):
    html = page(client, add_run(app, world, output=log(line)))

    assert f"Status: {label}" in html
    assert f'href="{PUBLISHED}"' in html


def test_no_status_is_shown_when_the_run_has_no_public_check(app, client, world):
    html = page(client, add_run(app, world, output=f"OK\nFinal page: {PUBLISHED}\n"))
    assert "Status:" not in html and f'href="{PUBLISHED}"' in html


def test_an_unknown_status_code_in_the_log_is_ignored(app, client, world):
    html = page(client, add_run(app, world, output=log("Publication status: whatever - x")))
    assert "Status:" not in html


def test_the_runs_list_marks_published_runs(app, client, world):
    published = add_run(app, world, output=log("Publication status: published - ok"))
    add_run(app, world, output=log("Publication status: not_public - HTTP 404 without signing in"))
    html = client.get("/runs/").get_data(as_text=True)

    assert html.count("badge-published") == 1
    assert f"/runs/{published}" in html
