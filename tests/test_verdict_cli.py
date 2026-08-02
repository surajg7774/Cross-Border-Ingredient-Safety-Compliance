"""Tests for the --category override logic in scripts.verdict."""

import pytest
import typer

from scripts.verdict import _build_item_category_map, _parse_category_overrides
from src.category.schemas import CategoryCandidate, CategoryQuery, CategoryResult
from src.resolve.schemas import ResolvedItem

CATEGORY_NAMES = {"7.2": "Fine bakery wares", "12.2.2": "Seasonings and condiments", "11.1": "Sugars and syrups"}


def _resolved(item_id, eu_canonical_id):
    return ResolvedItem(
        item_id=item_id,
        canonical_ins=None,
        eu_canonical_id=eu_canonical_id,
        classification="additive",
        normalised_role=None,
        codex_functional_classes=[],
        resolution_method="test",
        resolution_confidence=1.0,
        flags=[],
        candidates=[],
        matched_on=None,
    )


def _candidate(code, name="Retrieved Category"):
    return CategoryCandidate(code=code, name=name, similarity=0.7, permitted=True)


def _category_result(scope, component_label, candidate_codes, additive_ids):
    query = CategoryQuery(
        scope=scope, component_label=component_label, text="x", fields_used=[], additive_ids=additive_ids
    )
    return CategoryResult(
        component_label=component_label,
        query=query,
        top3=[_candidate(code) for code in candidate_codes],
        filtered_out=[],
        empty_intersection=False,
        excluded_additives=[],
        additive_breadth={},
    )


def test_bare_code_applies_to_product_query_only():
    overrides = _parse_category_overrides(["7.2"], CATEGORY_NAMES)
    assert set(overrides) == {None}
    assert overrides[None].code == "7.2"
    assert overrides[None].name == "Fine bakery wares"


def test_component_equals_code_applies_to_that_component_only():
    overrides = _parse_category_overrides(["Seasoning=12.2.2"], CATEGORY_NAMES)
    assert set(overrides) == {"Seasoning"}
    assert overrides["Seasoning"].code == "12.2.2"


def test_unknown_code_raises_a_clear_error():
    with pytest.raises(typer.Exit):
        _parse_category_overrides(["999.9"], CATEGORY_NAMES)


def test_build_item_category_map_applies_bare_override_to_product_only():
    resolved = [_resolved(0, "503"), _resolved(1, "330")]
    product_result = _category_result("product", None, ["11.1"], ["503"])
    component_result = _category_result("component", "INVERT SUGAR SYRUP", ["11.1"], ["330"])
    overrides = _parse_category_overrides(["7.2"], CATEGORY_NAMES)
    item_labels = {0: None, 1: "INVERT SUGAR SYRUP"}

    mapping, confirmed = _build_item_category_map(
        resolved, [product_result, component_result], overrides, item_labels
    )

    assert [c.code for c in mapping[0].top3] == ["7.2"]  # product query overridden
    assert [c.code for c in mapping[1].top3] == ["11.1"]  # component query untouched
    assert confirmed == frozenset({0})


def test_build_item_category_map_applies_component_override_to_that_component_only():
    resolved = [_resolved(0, "503"), _resolved(1, "330")]
    product_result = _category_result("product", None, ["11.1"], ["503"])
    component_result = _category_result("component", "Seasoning", ["11.1"], ["330"])
    overrides = _parse_category_overrides(["Seasoning=12.2.2"], CATEGORY_NAMES)
    item_labels = {0: None, 1: "Seasoning"}

    mapping, confirmed = _build_item_category_map(
        resolved, [product_result, component_result], overrides, item_labels
    )

    assert [c.code for c in mapping[0].top3] == ["11.1"]  # product query untouched
    assert [c.code for c in mapping[1].top3] == ["12.2.2"]  # component query overridden
    assert confirmed == frozenset({1})


def test_build_item_category_map_routes_same_eu_id_in_two_components_separately():
    """THE MEASURED BUG this guards against: Ice-creammain declares
    maltitol (eu_canonical_id 965) in both Inner Layer and Outer Layer.
    A join keyed on eu_canonical_id collapses both items onto whichever
    component's query was processed last -- item_labels (per-item, via the
    parent_item_id ancestor walk) must keep them distinct."""
    resolved = [_resolved(2, "965"), _resolved(15, "965")]
    inner = _category_result("component", "Inner Layer", ["3", "18.2", "5.1"], ["965", "471"])
    outer = _category_result("component", "Outer Layer", ["3", "5.1", "2.3"], ["965", "322"])
    item_labels = {2: "Inner Layer", 15: "Outer Layer"}

    mapping, _confirmed = _build_item_category_map(resolved, [inner, outer], {}, item_labels)

    assert mapping[2].component_label == "Inner Layer"
    assert mapping[15].component_label == "Outer Layer"
    assert mapping[2] is not mapping[15]
