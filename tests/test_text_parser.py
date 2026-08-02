"""Tests for src/extract/text_parser.py -- pure deterministic parsing, no
fixture files, no API calls (this module never touches the network at all).
"""

from src.extract.text_parser import parse_additive_list, parse_declaration


def test_group_heading_two_codes_same_position():
    result = parse_declaration("Colour (INS 143, INS 110)")
    assert len(result.items) == 2
    for item in result.items:
        assert item.role_source == "group_heading"
        assert item.declared_role == "Colour"
        assert item.position == 0
    codes = {item.declared_code for item in result.items}
    assert codes == {"INS 143", "INS 110"}


def test_explicit_prefix_colon():
    result = parse_declaration("Preservative: Potassium Sorbate")
    assert len(result.items) == 1
    item = result.items[0]
    assert item.role_source == "explicit_prefix"
    assert item.declared_role == "Preservative"
    assert item.name_as_declared == "Potassium Sorbate"
    assert item.declared_code is None


def test_ingredient_with_breakdown_nests():
    result = parse_declaration("Chocolate (Sugar, Cocoa Butter)")
    assert len(result.items) == 3
    parent = next(i for i in result.items if i.name_as_declared == "Chocolate")
    assert parent.nesting_depth == 0
    assert parent.parent_item_id is None

    children = [i for i in result.items if i.parent_item_id == parent.item_id]
    assert len(children) == 2
    assert {c.name_as_declared for c in children} == {"Sugar", "Cocoa Butter"}
    for child in children:
        assert child.nesting_depth == 1
        assert child.parent_item_id == parent.item_id


def test_bare_additive_list_both_depth_zero_codes_captured():
    result = parse_additive_list("INS 143, INS 330")
    assert len(result.items) == 2
    for item in result.items:
        assert item.nesting_depth == 0
        assert item.parent_item_id is None
    codes = {item.declared_code for item in result.items}
    assert codes == {"INS 143", "INS 330"}
    systems = {item.code_system for item in result.items}
    assert systems == {"INS"}


def test_bare_name_no_code():
    result = parse_additive_list("soy lecithin")
    assert len(result.items) == 1
    item = result.items[0]
    assert item.declared_code is None
    assert item.code_system == "none"
    assert item.name_as_declared == "soy lecithin"


def test_nested_brackets_do_not_split_on_inner_comma():
    result = parse_declaration("Colour (INS 143, INS 110), Sugar")
    # Without bracket-depth-aware splitting this would produce 4 top-level
    # entries instead of 2 ("Colour (INS 143" / " INS 110)" / " Sugar").
    positions = {item.position for item in result.items}
    assert positions == {0, 1}
    colour_items = [i for i in result.items if i.declared_role == "Colour"]
    assert len(colour_items) == 2
    sugar_items = [i for i in result.items if i.name_as_declared == "Sugar"]
    assert len(sugar_items) == 1
    assert sugar_items[0].position == 1


def test_unparseable_fragment_lands_in_unparsed_fragments():
    # A truncated paste with an unmatched opening bracket -- must not be
    # dropped, and must not be force-guessed into a fake item.
    result = parse_declaration("Sugar, Chocolate (Cocoa, Milk")
    assert result.unparsed_fragments == ["Chocolate (Cocoa, Milk"]
    assert len(result.items) == 1
    assert result.items[0].name_as_declared == "Sugar"


def test_inline_code_on_a_named_ingredient_is_not_nesting():
    # "Citric Acid (INS 330)" is the ingredient's OWN code, not a
    # bracketed breakdown -- one item, not a parent+child pair.
    result = parse_declaration("Citric Acid (INS 330)")
    assert len(result.items) == 1
    item = result.items[0]
    assert item.name_as_declared == "Citric Acid"
    assert item.declared_code == "INS 330"
    assert item.code_system == "INS"
    assert item.parent_item_id is None


def test_code_forms_and_systems():
    result = parse_additive_list("E322, E 143, 470(i), 472e, 1101(ii), INS 202")
    by_code = {item.declared_code: item.code_system for item in result.items}
    assert by_code == {
        "E322": "E",
        "E 143": "E",
        "470(i)": "bare",
        "472e": "bare",
        "1101(ii)": "bare",
        "INS 202": "INS",
    }


def test_confidence_is_always_one_and_no_styling_fields():
    result = parse_declaration("Sugar, Colour (INS 143)")
    for item in result.items:
        assert item.confidence == 1.0
        assert item.emphasis is False
        assert item.footnote_marker is None
