"""Inputs as written in Steps: which placeholders they are, their kinds, and what a valid value is."""
import pytest

from portal_auto.inputs import (
    KINDS, InputSpec, UploadedFile, engine_placeholder, env_name, input_kind, parse_inputs, safe_filename,
)
from portal_auto.services.errors import Invalid

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
GIF = b"GIF89a" + b"\x00" * 16
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8


def test_inputs_are_listed_once_in_order_of_first_appearance_with_their_kinds():
    steps = ["Type {{title}} into Title", "Upload {{deck:file}} and {{logo:image}}", "Type {{title}} again"]
    assert parse_inputs(steps) == [InputSpec("title", "text"), InputSpec("deck", "file"), InputSpec("logo", "image")]


def test_credential_and_engine_placeholders_are_not_inputs():
    steps = ["Type {{username}} and {{ Password }}", "Use {{ENV:DEMO_PASS}}", "Click Save"]
    assert parse_inputs(steps) == []


def test_spaces_inside_braces_and_kind_case_are_tolerated():
    assert parse_inputs(["{{ logo : IMAGE }}", "{{ title }}"]) == [InputSpec("logo", "image"), InputSpec("title", "text")]


def test_names_differing_only_by_case_are_one_input_named_as_first_written():
    assert parse_inputs(["{{Title}}", "{{TITLE}}", "{{title:text}}"]) == [InputSpec("Title", "text")]


@pytest.mark.parametrize("step", ["{{1st}}", "{{my-title}}", "{{}}", "{{two words}}", "{{_x}}"])
def test_bad_names_are_invalid(step):
    with pytest.raises(Invalid, match="name"):
        parse_inputs([step])


def test_an_unknown_kind_is_invalid_and_names_the_known_kinds():
    with pytest.raises(Invalid, match="video") as caught:
        parse_inputs(["{{clip:video}}"])
    assert all(kind in str(caught.value) for kind in KINDS)


def test_one_name_with_two_kinds_is_invalid_even_when_the_case_differs():
    with pytest.raises(Invalid, match="logo"):
        parse_inputs(["{{logo:image}}", "{{Logo:file}}"])


def test_a_credential_placeholder_with_a_kind_is_invalid():
    with pytest.raises(Invalid, match="username"):
        parse_inputs(["{{username:file}}"])


def test_every_problem_is_reported_at_once():
    with pytest.raises(Invalid) as caught:
        parse_inputs(["{{1st}} {{clip:video}}"])
    assert "1st" in str(caught.value) and "video" in str(caught.value)


def test_env_name_and_engine_placeholder_use_the_uppercase_name():
    assert env_name("deck_v2") == "PORTAL_INPUT_DECK_V2"
    assert engine_placeholder("deck_v2") == "{{ENV:PORTAL_INPUT_DECK_V2}}"


def test_kinds_describe_their_form_field():
    text, file, image = input_kind("text"), input_kind("file"), input_kind("image")
    assert (text.is_upload, file.is_upload, image.is_upload) == (False, True, True)
    assert (text.accept, file.accept, image.accept) == ("", "", "image/*")
    assert (text.max_bytes, file.max_bytes, image.max_bytes) == (None, 100 * 1024 * 1024, 20 * 1024 * 1024)


def test_an_unknown_kind_lookup_is_invalid():
    with pytest.raises(Invalid):
        input_kind("video")


def test_text_must_be_a_non_blank_string_within_the_cap():
    text = input_kind("text")
    text.validate("Quarterly report")
    text.validate("x" * 5000)
    for bad in ("", "   ", "x" * 5001, UploadedFile("a.txt", b"hi")):
        with pytest.raises(Invalid):
            text.validate(bad)


def test_a_file_accepts_any_non_empty_content_up_to_its_cap():
    file = input_kind("file")
    file.validate(UploadedFile("notes.bin", b"\x00\x01"))
    for bad in (UploadedFile("empty.txt", b""), "a text value", UploadedFile("big.bin", b"x" * (file.max_bytes + 1))):
        with pytest.raises(Invalid):
            file.validate(bad)


@pytest.mark.parametrize("content", [PNG, JPEG, GIF, WEBP])
def test_an_image_is_recognised_by_its_content_whatever_its_name(content):
    input_kind("image").validate(UploadedFile("picture.bin", content))


def test_a_text_file_renamed_png_is_not_an_image():
    with pytest.raises(Invalid, match="PNG, JPEG, GIF or WebP"):
        input_kind("image").validate(UploadedFile("photo.png", b"just some text, not an image"))


def test_an_image_larger_than_its_cap_is_refused():
    image = input_kind("image")
    with pytest.raises(Invalid, match="20 MB"):
        image.validate(UploadedFile("huge.png", PNG + b"\x00" * image.max_bytes))


def test_uploaded_file_names_keep_only_their_base_name():
    assert safe_filename("../../etc/x.png") == "x.png"
    assert safe_filename("C:\\Users\\me\\deck.pptx") == "deck.pptx"
    assert safe_filename(" report.pdf ") == "report.pdf"


@pytest.mark.parametrize("name", ["", "   ", "..", "dir/", ".", "a\x00b"])
def test_file_names_that_cannot_be_kept_are_invalid(name):
    with pytest.raises(Invalid):
        safe_filename(name)


def test_an_upload_with_an_unusable_name_is_invalid():
    with pytest.raises(Invalid):
        input_kind("file").validate(UploadedFile("../", b"content"))
