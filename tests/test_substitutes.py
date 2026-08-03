"""Tests for src/substitutes/advisor.py -- hand-built dicts, no fixture
files, no API calls."""

from src.reference_data import clean_eu_fip_rows
from src.rules.schemas import CategoryVerdict, ItemVerdict, ProductVerdict
from src.substitutes.advisor import _SUBTYPE_FLAG, find_substitutes


def _row(
    canonical_id,
    food_category_raw,
    status="permitted",
    max_level_mg_kg=None,
    max_level_basis=None,
    conditions=None,
    source_url="https://example.com/eu-fip",
    additive_name="Test Additive",
):
    return {
        "canonical_id": canonical_id,
        "additive_name": additive_name,
        "food_category_raw": food_category_raw,
        "status": status,
        "max_level_mg_kg": max_level_mg_kg,
        "max_level_basis": max_level_basis,
        "conditions": conditions,
        "note_codes": [],
        "source_url": source_url,
        "effective_date": "2020-01-01",
    }


def _codex(ins, functional_classes, parent_ins=None):
    return {
        "ins": ins,
        "parent_ins": parent_ins,
        "name": f"Additive {ins}",
        "synonyms": [],
        "functional_classes": functional_classes,
        "purposes": [],
        "is_parent_row": False,
    }


def _category_verdict(fcs_code, verdict="not_permitted_in_category", max_level_mg_kg=None, rank=1):
    return CategoryVerdict(
        fcs_code=fcs_code,
        category_name="Test Category",
        rank=rank,
        verdict=verdict,
        max_level_mg_kg=max_level_mg_kg,
        max_level_basis=None,
        conditions=None,
        note_codes=[],
        source_url=None,
        retrieved_date=None,
    )


def _item(item_id, eu_canonical_id=None, additive_name=None, by_category=None, headline="not_authorised_eu"):
    return ItemVerdict(
        item_id=item_id,
        eu_canonical_id=eu_canonical_id,
        additive_name=additive_name,
        component_label=None,
        by_category=by_category or [],
        headline=headline,
        category_sensitive=False,
        verdict_certainty="certain",
        flags=[],
    )


def _verdict(items, blocking, category_conflict=None, category_used=None):
    return ProductVerdict(
        items=items,
        blocking=blocking,
        category_conflict=category_conflict or [],
        review_required=[],
        category_sensitive_items=[],
        summary="x",
        category_used=category_used or {},
        category_source={},
        warnings=[],
        data_version="test",
    )


def test_blocked_colour_yields_only_candidates_sharing_colour_class():
    blocked = _item(1, eu_canonical_id="160a(i)", by_category=[_category_verdict("14.1.4")])
    verdict = _verdict([blocked], blocking=[1])
    eu_fip = [
        _row("133", "14.1.4 Flavoured drinks", additive_name="Brilliant Blue FCF"),
        _row("211", "14.1.4 Flavoured drinks", additive_name="Sodium benzoate"),
    ]
    codex_ins = [
        _codex("160a(i)", ["Colour"]),
        _codex("133", ["Colour"]),
        _codex("211", ["Preservative"]),
    ]
    result = find_substitutes(verdict, eu_fip, codex_ins)
    ids = [c.eu_canonical_id for c in result.suggestions[0].candidates]
    assert ids == ["133"]


def test_candidate_permitted_in_different_category_is_excluded():
    blocked = _item(1, eu_canonical_id="160a(i)", by_category=[_category_verdict("14.1.4")])
    verdict = _verdict([blocked], blocking=[1])
    eu_fip = [_row("133", "9.2 Processed fish", additive_name="Brilliant Blue FCF")]
    codex_ins = [_codex("160a(i)", ["Colour"]), _codex("133", ["Colour"])]
    result = find_substitutes(verdict, eu_fip, codex_ins)
    assert result.suggestions[0].candidates == []
    assert result.suggestions[0].no_candidates_reason == "no permitted additive in this category shares a functional class"


def test_candidate_with_lower_max_level_is_excluded():
    blocked = _item(1, eu_canonical_id="160a(i)", by_category=[_category_verdict("14.1.4", max_level_mg_kg=500.0)])
    verdict = _verdict([blocked], blocking=[1])
    eu_fip = [
        _row("133", "14.1.4 Flavoured drinks", max_level_mg_kg=100.0, max_level_basis="mg/kg"),
    ]
    codex_ins = [_codex("160a(i)", ["Colour"]), _codex("133", ["Colour"])]
    result = find_substitutes(verdict, eu_fip, codex_ins)
    assert result.suggestions[0].candidates == []


def test_quantum_satis_candidate_passes_level_check_against_any_level():
    blocked = _item(1, eu_canonical_id="160a(i)", by_category=[_category_verdict("14.1.4", max_level_mg_kg=500.0)])
    verdict = _verdict([blocked], blocking=[1])
    eu_fip = [_row("133", "14.1.4 Flavoured drinks", max_level_mg_kg=None, max_level_basis="gmp")]
    codex_ins = [_codex("160a(i)", ["Colour"]), _codex("133", ["Colour"])]
    result = find_substitutes(verdict, eu_fip, codex_ins)
    ids = [c.eu_canonical_id for c in result.suggestions[0].candidates]
    assert ids == ["133"]


def test_azo_colour_candidate_is_flagged_and_ranked_last_not_excluded():
    blocked = _item(1, eu_canonical_id="160a(i)", by_category=[_category_verdict("14.1.4")])
    verdict = _verdict([blocked], blocking=[1])
    eu_fip = [
        _row("102", "14.1.4 Flavoured drinks", additive_name="Tartrazine"),  # azo colour
        _row("133", "14.1.4 Flavoured drinks", additive_name="Brilliant Blue FCF"),  # not azo
    ]
    codex_ins = [_codex("160a(i)", ["Colour"]), _codex("102", ["Colour"]), _codex("133", ["Colour"])]
    result = find_substitutes(verdict, eu_fip, codex_ins)
    candidates = result.suggestions[0].candidates
    assert [c.eu_canonical_id for c in candidates] == ["133", "102"]
    assert candidates[-1].flags == ["adds_labelling_obligation"]
    assert candidates[0].flags == []


def test_blocked_additive_never_appears_in_its_own_candidate_list():
    blocked = _item(1, eu_canonical_id="160a(i)", by_category=[_category_verdict("14.1.4")])
    verdict = _verdict([blocked], blocking=[1])
    eu_fip = [_row("160a(i)", "14.1.4 Flavoured drinks")]
    codex_ins = [_codex("160a(i)", ["Colour"])]
    result = find_substitutes(verdict, eu_fip, codex_ins)
    assert result.suggestions[0].candidates == []


def test_prohibited_additive_is_never_a_candidate():
    blocked = _item(1, eu_canonical_id="160a(i)", by_category=[_category_verdict("14.1.4")])
    verdict = _verdict([blocked], blocking=[1])
    eu_fip = [
        _row("133", "14.1.4 Flavoured drinks", additive_name="Brilliant Blue FCF"),
        _row("133", None, status="prohibited"),  # jurisdiction-wide ban, recorded separately
    ]
    codex_ins = [_codex("160a(i)", ["Colour"]), _codex("133", ["Colour"])]
    result = find_substitutes(verdict, eu_fip, codex_ins)
    assert result.suggestions[0].candidates == []


def test_blocked_item_with_no_category_returns_no_candidates_reason():
    blocked = _item(1, eu_canonical_id="160a(i)", by_category=[])
    verdict = _verdict([blocked], blocking=[1], category_used={})
    result = find_substitutes(verdict, [], [_codex("160a(i)", ["Colour"])])
    suggestion = result.suggestions[0]
    assert suggestion.candidates == []
    assert suggestion.no_candidates_reason == "no food category available"


def test_category_conflict_item_produces_no_suggestion():
    conflicted = _item(1, eu_canonical_id="160a(i)", by_category=[_category_verdict("14.1.4")])
    verdict = _verdict([conflicted], blocking=[], category_conflict=[1])
    result = find_substitutes(verdict, [], [])
    assert result.suggestions == []


def test_real_case_ins143_blocked_in_14_1_4_yields_colour_candidates():
    # INS 143 (Fast Green FCF): resolved by Codex, absent from eu_fip
    # entirely -- eu_canonical_id is still "143" (Codex identified it), but
    # by_category is empty (absence is category-independent), so fcs_code
    # falls back to ProductVerdict.category_used.
    blocked = _item(8, eu_canonical_id="143", by_category=[], headline="not_authorised_eu")
    verdict = _verdict([blocked], blocking=[8], category_used={"(product)": "14.1.4"})
    eu_fip = [
        _row("102", "14.1.4 Flavoured drinks", additive_name="Tartrazine"),
        _row("133", "14.1.4 Flavoured drinks", additive_name="Brilliant Blue FCF"),
        _row("211", "14.1.4 Flavoured drinks", additive_name="Sodium benzoate"),
    ]
    codex_ins = [
        _codex("143", ["Colour"]),
        _codex("102", ["Colour"]),
        _codex("133", ["Colour"]),
        _codex("211", ["Preservative"]),
    ]
    result = find_substitutes(verdict, eu_fip, codex_ins)
    suggestion = result.suggestions[0]
    assert suggestion.fcs_code == "14.1.4"
    ids = {c.eu_canonical_id for c in suggestion.candidates}
    assert ids == {"102", "133"}
    assert all("Colour" in c.shared_functional_classes for c in suggestion.candidates)


def test_blocked_additive_resolves_via_canonical_ins_when_eu_id_is_null():
    # THE MEASURED BUG: eu_canonical_id is null for exactly the additives
    # that block via absence from eu_fip -- that null is WHY they blocked.
    # canonical_ins (threaded in from the resolution file) is the only way
    # to reach codex_ins's functional_classes for them.
    blocked = _item(8, eu_canonical_id=None, by_category=[], headline="not_authorised_eu")
    verdict = _verdict([blocked], blocking=[8], category_used={"(product)": "14.1.4"})
    eu_fip = [_row("133", "14.1.4 Flavoured drinks", additive_name="Brilliant Blue FCF")]
    codex_ins = [_codex("143", ["Colour"]), _codex("133", ["Colour"])]
    result = find_substitutes(verdict, eu_fip, codex_ins, canonical_ins_by_item={8: "143"})
    suggestion = result.suggestions[0]
    assert suggestion.no_candidates_reason is None
    assert [c.eu_canonical_id for c in suggestion.candidates] == ["133"]


def test_functional_class_unknown_only_when_neither_id_resolves():
    blocked = _item(9, eu_canonical_id="999", by_category=[], headline="not_authorised_eu")
    verdict = _verdict([blocked], blocking=[9], category_used={"(product)": "14.1.4"})
    codex_ins = [_codex("102", ["Colour"])]  # present, but matches neither id below
    result = find_substitutes(verdict, [], codex_ins, canonical_ins_by_item={9: "998"})
    suggestion = result.suggestions[0]
    assert suggestion.no_candidates_reason == "blocked additive's functional class is unknown (not in codex_ins)"


def test_subnotation_canonical_ins_resolves_via_parent():
    # "322(i)" is not itself in codex_ins, but its parent "322" is -- the
    # same sub-notation widening _codex_functional_classes already applies
    # to eu_canonical_id must apply to canonical_ins too.
    blocked = _item(10, eu_canonical_id=None, by_category=[], headline="not_authorised_eu")
    verdict = _verdict([blocked], blocking=[10], category_used={"(product)": "14.1.4"})
    eu_fip = [_row("442", "14.1.4 Flavoured drinks", additive_name="Ammonium salts of phosphatidic acid")]
    codex_ins = [_codex("322", ["Emulsifier"]), _codex("442", ["Emulsifier"])]
    result = find_substitutes(verdict, eu_fip, codex_ins, canonical_ins_by_item={10: "322(i)"})
    suggestion = result.suggestions[0]
    assert [c.eu_canonical_id for c in suggestion.candidates] == ["442"]


def test_parent_row_with_empty_classes_inherits_union_of_subtypes_flagged():
    # 170 Calcium carbonates: functional_classes=[] on the parent row itself
    # (CXG 36 records classes on the SUB-TYPES). 170(i) carries "Colour",
    # 170(ii) carries "Anticaking agent" -- the union of both must surface,
    # flagged classes_from_subtypes so a reader knows it was inherited.
    blocked = _item(1, eu_canonical_id="171", by_category=[_category_verdict("18")])
    verdict = _verdict([blocked], blocking=[1])
    eu_fip = [_row("170", "18 Chewing gum", additive_name="Calcium carbonates")]
    codex_ins = [
        _codex("171", ["Colour", "Stabilizer"]),
        _codex("170", []),
        _codex("170(i)", ["Colour"], parent_ins="170"),
        _codex("170(ii)", ["Anticaking agent"], parent_ins="170"),
    ]
    result = find_substitutes(verdict, eu_fip, codex_ins)
    candidates = result.suggestions[0].candidates
    assert len(candidates) == 1
    assert candidates[0].eu_canonical_id == "170"
    assert candidates[0].shared_functional_classes == ["Colour"]
    assert _SUBTYPE_FLAG in candidates[0].flags


def test_record_genuinely_absent_from_codex_ins_still_returns_none():
    blocked = _item(1, eu_canonical_id="171", by_category=[_category_verdict("18")])
    verdict = _verdict([blocked], blocking=[1])
    eu_fip = [_row("999", "18 Chewing gum", additive_name="Not in codex at all")]
    codex_ins = [_codex("171", ["Colour"])]  # "999" is absent entirely, not just empty
    result = find_substitutes(verdict, eu_fip, codex_ins)
    assert result.suggestions[0].candidates == []
    assert "functional class unknown (not in codex_ins)" in result.warnings[0]


def test_record_with_own_nonempty_classes_does_not_get_subtype_flag():
    blocked = _item(1, eu_canonical_id="171", by_category=[_category_verdict("18")])
    verdict = _verdict([blocked], blocking=[1])
    eu_fip = [_row("133", "18 Chewing gum", additive_name="Brilliant Blue FCF")]
    codex_ins = [_codex("171", ["Colour", "Stabilizer"]), _codex("133", ["Colour"])]
    result = find_substitutes(verdict, eu_fip, codex_ins)
    candidates = result.suggestions[0].candidates
    assert len(candidates) == 1
    assert _SUBTYPE_FLAG not in candidates[0].flags


def test_worked_example_calcium_carbonate_candidate_for_titanium_dioxide():
    # THE WORKED EXAMPLE from this project's own design document: 170
    # Calcium carbonates is the actual industry replacement for titanium
    # dioxide (171) after the EU's 2022 E171 ban.
    blocked = _item(3, eu_canonical_id="171", additive_name="Titanium dioxide", by_category=[])
    verdict = _verdict([blocked], blocking=[3], category_used={"(product)": "18"})
    eu_fip = [
        _row("170", "18 Chewing gum", additive_name="Calcium carbonates"),
        _row("133", "18 Chewing gum", additive_name="Brilliant Blue FCF"),
    ]
    codex_ins = [
        _codex("171", ["Colour", "Stabilizer"]),
        _codex("170", []),
        _codex("170(i)", ["Colour"], parent_ins="170"),
        _codex("170(ii)", ["Anticaking agent"], parent_ins="170"),
        _codex("133", ["Colour"]),
    ]
    result = find_substitutes(verdict, eu_fip, codex_ins)
    ids = {c.eu_canonical_id for c in result.suggestions[0].candidates}
    assert "170" in ids


def test_placeholder_only_conditions_renders_no_numbered_clause():
    # MEASURED bug: advisor.py reads eu_fip rows directly and does not
    # normalize them itself (by design -- it must not import
    # src.rules.engine, see this module's own DESIGN RULE comment), so a
    # "&nbsp;"-only conditions cell used to reach the substitutes UI/PDF/
    # narrator verbatim. The fix lives at the data layer
    # (src.reference_data.clean_eu_fip_rows, applied wherever eu_fip.json
    # is loaded into memory) -- advisor.py itself is UNCHANGED. This test
    # runs the fixture through that same cleaning step, exactly as
    # load_eu_fip does for a real file, before handing it to
    # find_substitutes -- proving the candidate that reaches
    # src/ui/components.py's render_substitutes (and the PDF/narrator
    # paths) never carries the placeholder, so nothing -- numbered or
    # otherwise -- can render from it.
    blocked = _item(1, eu_canonical_id="160a(i)", by_category=[_category_verdict("14.1.4")])
    verdict = _verdict([blocked], blocking=[1])
    raw_eu_fip = [_row("133", "14.1.4 Flavoured drinks", conditions="&nbsp;", additive_name="Brilliant Blue FCF")]
    codex_ins = [_codex("160a(i)", ["Colour"]), _codex("133", ["Colour"])]

    eu_fip = clean_eu_fip_rows(raw_eu_fip)
    result = find_substitutes(verdict, eu_fip, codex_ins)

    candidates = result.suggestions[0].candidates
    assert len(candidates) == 1
    assert candidates[0].conditions is None
