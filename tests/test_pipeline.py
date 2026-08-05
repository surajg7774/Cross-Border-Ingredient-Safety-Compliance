"""Tests for the bbox sanitiser and the redundant-code-child dedup in
src.pipeline."""

from src.pipeline import _drop_redundant_code_children, _sanitise_bbox, run_extraction
from src.schemas import ExtractedItem, ExtractionResult, GateResult


def test_swapped_x_coordinates_are_repaired():
    result = _sanitise_bbox([0.8, 0.1, 0.2, 0.6])
    assert result == [0.2, 0.1, 0.8, 0.6]


def test_values_outside_unit_range_are_clamped():
    result = _sanitise_bbox([-0.5, -0.2, 1.5, 1.2])
    assert result == [0.0, 0.0, 1.0, 1.0]


def test_degenerate_sliver_box_returns_none():
    assert _sanitise_bbox([0.1, 0.1, 0.11, 0.9]) is None


def test_none_passes_through_as_none():
    assert _sanitise_bbox(None) is None


def test_valid_box_returned_unchanged():
    bbox = [0.1, 0.2, 0.8, 0.9]
    assert _sanitise_bbox(bbox) == bbox


# --------------------------------------------------------------------------- #
# _drop_redundant_code_children -- REGRESSION (MEASURED, Khusmain): the
# extractor sometimes emits "Citric Acid (INS 330)" as one item AND a
# separate nested child "INS 330" whose only content is that same code,
# restated -- two ItemVerdict rows, two additives-table rows, for one real
# ingredient.
# --------------------------------------------------------------------------- #
def _item(item_id, **overrides):
    base = {
        "item_id": item_id,
        "position": item_id,
        "nesting_depth": 0,
        "parent_item_id": None,
        "verbatim": f"item {item_id}",
        "name_as_declared": None,
        "declared_role": None,
        "role_source": "none",
        "declared_code": None,
        "code_system": "none",
        "percentage": None,
        "emphasis": False,
        "footnote_marker": None,
        "confidence": 0.95,
    }
    base.update(overrides)
    return ExtractedItem(**base)


def test_drops_nested_child_whose_verbatim_is_just_the_parents_code():
    # The exact real Khusmain shape.
    parent = _item(2, verbatim="Citric Acid (INS 330)", name_as_declared="Citric Acid")
    child = _item(
        3,
        nesting_depth=1,
        parent_item_id=2,
        verbatim="INS 330",
        declared_code="INS 330",
        code_system="INS",
    )
    result = _drop_redundant_code_children([parent, child])
    assert result == [parent]


def test_keeps_a_descriptive_nested_child_with_no_code():
    # The OTHER nested pair on the same real label -- "Natural-Nature
    # Identical Flavouring Substances" nested under "Added Khus Flavour
    # (...)" -- genuine classification detail, not a redundant code.
    parent = _item(
        6, verbatim="Added Khus Flavour (Natural-Nature Identical Flavouring Substances)",
        name_as_declared="Added Khus Flavour",
    )
    child = _item(
        7,
        nesting_depth=1,
        parent_item_id=6,
        verbatim="Natural-Nature Identical Flavouring Substances",
        name_as_declared="Natural-Nature Identical Flavouring Substances",
    )
    result = _drop_redundant_code_children([parent, child])
    assert result == [parent, child]


def test_keeps_a_top_level_item_that_happens_to_carry_a_code():
    # nesting_depth == 0 -- not a nested child at all, regardless of what
    # its verbatim/declared_code look like.
    item = _item(4, verbatim="INS 440", declared_code="INS 440", code_system="INS")
    assert _drop_redundant_code_children([item]) == [item]


def test_keeps_a_nested_code_child_whose_code_does_not_appear_in_the_parent():
    # The declared_code must actually be found in the PARENT's verbatim --
    # a nested item that merely carries some code, unrelated to the parent
    # label text, is not proven redundant and must never be dropped.
    parent = _item(2, verbatim="Citric Acid", name_as_declared="Citric Acid")
    child = _item(
        3, nesting_depth=1, parent_item_id=2, verbatim="INS 330", declared_code="INS 330", code_system="INS"
    )
    result = _drop_redundant_code_children([parent, child])
    assert result == [parent, child]


def test_keeps_a_nested_child_whose_verbatim_is_more_than_just_the_code():
    parent = _item(2, verbatim="Citric Acid (INS 330)", name_as_declared="Citric Acid")
    child = _item(
        3,
        nesting_depth=1,
        parent_item_id=2,
        verbatim="INS 330 acidity regulator",
        declared_code="INS 330",
        code_system="INS",
    )
    result = _drop_redundant_code_children([parent, child])
    assert result == [parent, child]


def test_empty_list_returns_empty():
    assert _drop_redundant_code_children([]) == []


def test_keeps_every_code_only_child_of_a_compound_bracket_with_many_children():
    # REGRESSION, MEASURED (Chipsmain): "Seasoning [+Spices and Condiments
    # (...), Iodised Salt, Sugar, ..., Anticaking Agent (INS 470(i), INS
    # 551)), ..., Acidity Regulator (INS 330), ...]" is ONE parent with
    # TWELVE children, six of them code-only -- each the SOLE declaration
    # of a real, distinct additive (grouped by functional class, not
    # named individually), not a restatement of anything. Every one of
    # them matches "verbatim is exactly its own code, and that code is a
    # substring of the parent's verbatim" -- the SAME shape as the true
    # Khusmain bug -- so without the only-child guard this dropped six
    # real additives from the assessment entirely, not a duplicate row.
    parent = _item(
        3,
        verbatim=(
            "Seasoning [+Spices and Condiments (Contains Chilli), Iodised Salt, Sugar, Maltodextrin, "
            "Natural and Nature Identical Flavouring Substances, Anticaking Agent (INS 470(i), INS 551)), "
            "Starch, Flavour Enhancers (INS 627, INS 631), Acidity Regulator (INS 330), "
            "Emulsifying and Stabilizing Agent (INS 471)]"
        ),
        name_as_declared="Seasoning",
    )
    non_code_children = [
        _item(n, nesting_depth=1, parent_item_id=3, verbatim=text, name_as_declared=text)
        for n, text in (
            (4, "+Spices and Condiments (Contains Chilli)"),
            (5, "Iodised Salt"),
            (6, "Sugar"),
            (7, "Maltodextrin"),
            (8, "Natural and Nature Identical Flavouring Substances"),
            (11, "Starch"),
        )
    ]
    code_children = [
        _item(n, nesting_depth=1, parent_item_id=3, verbatim=code, declared_code=code, code_system="INS")
        for n, code in (
            (9, "INS 470(i)"),
            (10, "INS 551"),
            (12, "INS 627"),
            (13, "INS 631"),
            (14, "INS 330"),
            (15, "INS 471"),
        )
    ]
    items = [parent, *non_code_children, *code_children]
    assert _drop_redundant_code_children(items) == items


class _FakeExtractor:
    """A minimal LabelExtractor -- gate always says "yes, extract this",
    extract returns a fixed ExtractionResult carrying the exact redundant-
    child shape above, so run_extraction's OWN wiring of the dedup (not
    just the pure function) is under test."""

    def gate(self, image_bytes):
        return GateResult(
            is_food_label=True,
            has_ingredients_declaration=True,
            evidence_found=["ingredients_keyword"],
            reject_reason=None,
            product_name="Test product",
            product_descriptor=None,
            languages_detected=["en"],
            language_selected="en",
        )

    def extract(self, image_bytes, language):
        parent = _item(2, verbatim="Citric Acid (INS 330)", name_as_declared="Citric Acid")
        child = _item(
            3, nesting_depth=1, parent_item_id=2, verbatim="INS 330", declared_code="INS 330", code_system="INS"
        )
        return ExtractionResult(
            declaration_verbatim="Citric Acid (INS 330)",
            language=language,
            items=[parent, child],
            unparsed_fragments=[],
            warnings=[],
            allergen_statements=[],
            footnotes={},
            declaration_statements=[],
        )


def test_run_extraction_applies_the_dedup_end_to_end(tmp_path):
    image_path = tmp_path / "label.jpg"
    image_path.write_bytes(b"not a real image -- gate/extract are faked, never decoded")

    result = run_extraction(image_path, _FakeExtractor())

    item_ids = [item["item_id"] for item in result["extraction"]["items"]]
    assert item_ids == [2]
