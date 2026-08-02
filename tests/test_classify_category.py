"""Tests for the description precedence logic in scripts.classify_category."""

from scripts.classify_category import _resolve_description


def test_explicit_description_overrides_fixture_file():
    descriptions = {"Chipsmain": "Baked potato chips, savoury snack"}
    description, source = _resolve_description("Chipsmain", "Hand-typed description", descriptions)
    assert description == "Hand-typed description"
    assert source == "cli"


def test_fixture_file_used_when_no_cli_description():
    descriptions = {"Chipsmain": "Baked potato chips, savoury snack"}
    description, source = _resolve_description("Chipsmain", None, descriptions)
    assert description == "Baked potato chips, savoury snack"
    assert source == "descriptions_file"


def test_none_when_neither_source_has_a_description():
    description, source = _resolve_description("UnknownLabel", None, {})
    assert description is None
    assert source == "none"
