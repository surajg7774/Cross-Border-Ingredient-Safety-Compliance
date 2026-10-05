"""Tests for the Pydantic models in src.schemas."""

import pytest
from pydantic import ValidationError

from src.schemas import ExtractedItem, ExtractionResult, GateResult


def _item(**overrides) -> dict:
    base = {
        "item_id": 0,
        "position": 0,
        "nesting_depth": 0,
        "parent_item_id": None,
        "verbatim": "sugar",
        "name_as_declared": "sugar",
        "declared_role": None,
        "role_source": "none",
        "declared_code": None,
        "code_system": "none",
        "percentage": None,
        "emphasis": False,
        "footnote_marker": None,
        "confidence": 0.9,
    }
    base.update(overrides)
    return base


def _gate(**overrides) -> dict:
    base = {
        "is_food_label": True,
        "has_ingredients_declaration": True,
        "evidence_found": ["ingredients_keyword"],
        "reject_reason": None,
        "product_name": "Test Product",
        "product_descriptor": None,
        "languages_detected": ["en"],
        "language_selected": "en",
    }
    base.update(overrides)
    return base


def test_valid_extraction_result_parses():
    result = ExtractionResult(
        declaration_verbatim="Sugar, cocoa mass",
        language="en",
        items=[_item(item_id=0, verbatim="Sugar"), _item(item_id=1, verbatim="cocoa mass")],
        unparsed_fragments=[],
        warnings=[],
        allergen_statements=[],
        footnotes={},
        declaration_statements=[],
    )
    assert len(result.items) == 2


def test_nesting_with_parent_item_id_validates():
    parent = _item(item_id=0, verbatim="Chocolate")
    child = _item(item_id=1, verbatim="soya lecithin", nesting_depth=1, parent_item_id=0)
    item = ExtractedItem(**child)
    assert item.parent_item_id == 0
    assert item.nesting_depth == 1
    assert ExtractedItem(**parent).parent_item_id is None


def test_declared_code_none_allowed():
    item = ExtractedItem(**_item(declared_code=None))
    assert item.declared_code is None


def test_confidence_outside_range_rejected():
    with pytest.raises(ValidationError):
        ExtractedItem(**_item(confidence=1.5))


def test_code_system_accepts_all_four_literals():
    for system in ("E", "INS", "bare", "none"):
        item = ExtractedItem(**_item(code_system=system))
        assert item.code_system == system


def test_code_system_rejects_unknown_value():
    with pytest.raises(ValidationError):
        ExtractedItem(**_item(code_system="EU"))


def test_ins_code_with_sub_notation_validates():
    item = ExtractedItem(**_item(declared_code="503(ii)", code_system="INS"))
    assert item.declared_code == "503(ii)"
    assert item.code_system == "INS"


def test_gate_result_without_ingredients_declaration_validates():
    gate = GateResult(**_gate(has_ingredients_declaration=False))
    assert gate.has_ingredients_declaration is False


def test_footnote_marker_accepts_string_and_none():
    marked = ExtractedItem(**_item(footnote_marker="#"))
    unmarked = ExtractedItem(**_item(footnote_marker=None))
    assert marked.footnote_marker == "#"
    assert unmarked.footnote_marker is None


def test_extraction_result_with_allergen_statements_validates():
    result = ExtractionResult(
        declaration_verbatim="Sugar. Contains: Wheat, Milk.",
        language="en",
        items=[_item(item_id=0, verbatim="Sugar")],
        unparsed_fragments=[],
        warnings=[],
        allergen_statements=["Contains: Wheat, Milk."],
        footnotes={},
        declaration_statements=[],
    )
    assert result.allergen_statements == ["Contains: Wheat, Milk."]


def test_extraction_result_with_footnotes_validates():
    result = ExtractionResult(
        declaration_verbatim="Invert Sugar Syrup#. +Used as natural flavouring agent.",
        language="en",
        items=[_item(item_id=0, verbatim="Invert Sugar Syrup#", footnote_marker="#")],
        unparsed_fragments=[],
        warnings=[],
        allergen_statements=[],
        footnotes={"#": "Used as natural flavouring agent"},
        declaration_statements=[],
    )
    assert result.footnotes == {"#": "Used as natural flavouring agent"}


def test_extraction_result_with_declaration_statements_validates():
    result = ExtractionResult(
        declaration_verbatim="Sugar. Contains permitted natural colour(s).",
        language="en",
        items=[_item(item_id=0, verbatim="Sugar")],
        unparsed_fragments=[],
        warnings=[],
        allergen_statements=[],
        footnotes={},
        declaration_statements=["Contains permitted natural colour(s)."],
    )
    assert result.declaration_statements == ["Contains permitted natural colour(s)."]


def test_item_nesting_under_a_component_validates():
    component = _item(item_id=0, verbatim="Inner Layer (Ice Cream 68%)", percentage=68.0)
    child = _item(item_id=1, verbatim="Milk Solids", nesting_depth=1, parent_item_id=0)
    item = ExtractedItem(**child)
    assert item.parent_item_id == 0
    assert item.nesting_depth == 1
    assert ExtractedItem(**component).percentage == 68.0
