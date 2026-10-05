"""Tests for src/rules/engine.py -- hand-built dicts, no fixture files."""

from src.category.schemas import CategoryCandidate, CategoryQuery, CategoryResult
from src.resolve.schemas import ResolvedItem
from src.rules.engine import _select_row, evaluate
from src.ui.components import count_buckets


def _resolved(item_id, classification, eu_canonical_id=None, candidates=None, canonical_ins=None):
    return ResolvedItem(
        item_id=item_id,
        canonical_ins=canonical_ins,
        eu_canonical_id=eu_canonical_id,
        classification=classification,
        normalised_role=None,
        codex_functional_classes=[],
        resolution_method="test",
        resolution_confidence=1.0,
        flags=[],
        candidates=candidates or [],
        matched_on=None,
    )


def _row(
    canonical_id,
    food_category_raw,
    status="permitted",
    max_level_mg_kg=None,
    max_level_basis=None,
    conditions=None,
    note_codes=None,
    source_url="https://example.com/eu-fip",
    effective_date="2020-01-01",
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
        "note_codes": note_codes or [],
        "source_url": source_url,
        "effective_date": effective_date,
    }


def _candidate(code, name="Test Category", similarity=0.7, permitted=True):
    return CategoryCandidate(code=code, name=name, similarity=similarity, permitted=permitted)


def _category_result(scope, component_label, candidate_codes, additive_ids=None, names=None):
    names = names or {}
    query = CategoryQuery(
        scope=scope,
        component_label=component_label,
        text="x",
        fields_used=[],
        additive_ids=additive_ids or [],
    )
    return CategoryResult(
        component_label=component_label,
        query=query,
        top3=[_candidate(code, name=names.get(code, "Test Category")) for code in candidate_codes],
        filtered_out=[],
        empty_intersection=False,
        excluded_additives=[],
        additive_breadth={},
    )


def test_gmp_basis_no_conditions_is_permitted_qs():
    eu_fip = [_row("300", "1.5 dehydrated milk", max_level_basis="gmp")]
    resolved = [_resolved(0, "additive", eu_canonical_id="300")]
    category_results = {0: _category_result("product", None, ["1.5"], additive_ids=["300"])}
    verdict = evaluate(resolved, category_results, eu_fip)
    assert verdict.items[0].headline == "permitted_qs"


def test_numeric_max_level_is_permitted_with_limit():
    eu_fip = [_row("300", "1.5 dehydrated milk", max_level_mg_kg=300.0, max_level_basis="mg/kg")]
    resolved = [_resolved(0, "additive", eu_canonical_id="300")]
    category_results = {0: _category_result("product", None, ["1.5"], additive_ids=["300"])}
    verdict = evaluate(resolved, category_results, eu_fip)
    item = verdict.items[0]
    assert item.headline == "permitted_with_limit"
    assert item.by_category[0].max_level_mg_kg == 300.0


def test_conditions_text_is_permitted_with_conditions():
    eu_fip = [_row("300", "9.1.1 unprocessed fish", conditions="only tuna")]
    resolved = [_resolved(0, "additive", eu_canonical_id="300")]
    category_results = {0: _category_result("product", None, ["9.1.1"], additive_ids=["300"])}
    verdict = evaluate(resolved, category_results, eu_fip)
    item = verdict.items[0]
    assert item.headline == "permitted_with_conditions"
    assert item.by_category[0].conditions == "only tuna"


def test_in_eu_fip_but_not_this_category_is_not_permitted_in_category():
    eu_fip = [_row("300", "1.5 dehydrated milk")]
    resolved = [_resolved(0, "additive", eu_canonical_id="300")]
    category_results = {0: _category_result("product", None, ["9.1.1"], additive_ids=["300"])}
    verdict = evaluate(resolved, category_results, eu_fip)
    assert verdict.items[0].headline == "not_permitted_in_category"


def test_absent_from_eu_fip_entirely_is_not_authorised_eu():
    eu_fip = [_row("300", "1.5 dehydrated milk")]
    resolved = [_resolved(0, "additive", eu_canonical_id="999")]
    category_results = {0: _category_result("product", None, ["1.5"], additive_ids=["999"])}
    verdict = evaluate(resolved, category_results, eu_fip)
    assert verdict.items[0].headline == "not_authorised_eu"


def test_prohibited_status_is_not_authorised_eu_with_flag():
    eu_fip = [_row("999", "1.5 dehydrated milk", status="prohibited")]
    resolved = [_resolved(0, "additive", eu_canonical_id="999")]
    category_results = {0: _category_result("product", None, ["1.5"], additive_ids=["999"])}
    verdict = evaluate(resolved, category_results, eu_fip)
    item = verdict.items[0]
    assert item.headline == "not_authorised_eu"
    assert "prohibited" in item.flags


def test_prohibition_with_no_category_row_is_not_authorised_eu():
    # eu_fip records a jurisdiction-wide prohibition as a row with
    # food_category_raw = None -- a (canonical_id, fcs_code) lookup can
    # never find it. Must still resolve to not_authorised_eu, by_category
    # empty, category_sensitive False -- the verdict does not depend on
    # which category is correct.
    eu_fip = [_row("999", None, status="prohibited")]
    resolved = [_resolved(0, "additive", eu_canonical_id="999")]
    category_results = {0: _category_result("product", None, ["1.5"], additive_ids=["999"])}
    verdict = evaluate(resolved, category_results, eu_fip)
    item = verdict.items[0]
    assert item.headline == "not_authorised_eu"
    assert "prohibited" in item.flags
    assert item.by_category == []
    assert item.category_sensitive is False


def test_prohibition_with_no_category_available_is_not_authorised_eu_not_category_unknown():
    # Prohibition is category-independent, exactly like absence -- a
    # text-input item with a prohibited additive and NO category result at
    # all must still resolve to not_authorised_eu, not category_unknown.
    eu_fip = [_row("999", None, status="prohibited")]
    resolved = [_resolved(0, "additive", eu_canonical_id="999")]
    verdict = evaluate(resolved, {}, eu_fip)  # no category_results entry
    item = verdict.items[0]
    assert item.headline == "not_authorised_eu"
    assert "prohibited" in item.flags


def test_e171_titanium_dioxide_real_shape():
    # MEASURED bug: E171 titanium dioxide was returning not_permitted_in_category
    # instead of not_authorised_eu, because its prohibition row (like the EU's
    # real data) has food_category_raw = None. Delisted EU-wide by Commission
    # Regulation 2022/63.
    eu_fip = [_row("171", None, status="prohibited", additive_name="Titanium dioxide")]
    resolved = [_resolved(0, "additive", eu_canonical_id="171")]
    category_results = {0: _category_result("product", None, ["18"], additive_ids=["171"])}
    verdict = evaluate(resolved, category_results, eu_fip)
    item = verdict.items[0]
    assert item.headline == "not_authorised_eu"
    assert "prohibited" in item.flags
    assert item.headline != "not_permitted_in_category"


def test_prohibited_parent_widens_sub_code():
    # "160b(i)" is not itself prohibited, but its parent "160b" (annatto) is
    # -- must still resolve to not_authorised_eu, flagged as widened.
    eu_fip = [_row("160b", None, status="prohibited")]
    resolved = [_resolved(0, "additive", eu_canonical_id="160b(i)")]
    category_results = {0: _category_result("product", None, ["1.5"], additive_ids=["160b(i)"])}
    verdict = evaluate(resolved, category_results, eu_fip)
    item = verdict.items[0]
    assert item.headline == "not_authorised_eu"
    assert "prohibited" in item.flags
    assert "widened_to_parent_for_lookup" in item.flags


def test_absent_from_category_with_no_prohibited_row_anywhere_stays_fixable():
    # Regression guard: the prohibition fix must not swallow the genuinely
    # fixable case -- an additive permitted somewhere in the EU, just not
    # in THIS category, with no prohibited row anywhere for it or its
    # parents, is still a formulation problem, not a dead end.
    eu_fip = [_row("300", "1.5 dehydrated milk")]
    resolved = [_resolved(0, "additive", eu_canonical_id="300")]
    category_results = {0: _category_result("product", None, ["9.1.1"], additive_ids=["300"])}
    verdict = evaluate(resolved, category_results, eu_fip)
    item = verdict.items[0]
    assert item.headline == "not_permitted_in_category"
    assert "prohibited" not in item.flags


def test_enzyme_classification_is_out_of_scope_no_lookup():
    resolved = [_resolved(0, "enzyme")]
    verdict = evaluate(resolved, {}, [])
    item = verdict.items[0]
    assert item.headline == "out_of_scope"
    assert item.by_category == []
    assert any("1332/2008" in f for f in item.flags)


def test_unknown_classification_is_unresolved_not_not_authorised():
    resolved = [_resolved(0, "unknown", candidates=["1400", "1401"])]
    verdict = evaluate(resolved, {}, [])
    item = verdict.items[0]
    assert item.headline == "unresolved"
    assert item.headline != "not_authorised_eu"
    assert any("1400" in f for f in item.flags)


def test_no_category_available_is_category_unknown_not_a_pass():
    eu_fip = [_row("300", "1.5 dehydrated milk")]
    resolved = [_resolved(0, "additive", eu_canonical_id="300")]
    verdict = evaluate(resolved, {}, eu_fip)  # no category_results entry for item 0
    item = verdict.items[0]
    assert item.headline == "category_unknown"
    assert item.headline not in ("permitted_qs", "permitted_with_limit", "permitted_with_conditions")


def test_parent_fallback_widens_sub_notation_to_family():
    eu_fip = [
        _row("500", "7.2 fine bakery wares", max_level_basis="gmp"),
        _row("500(ii)", "1.5 dehydrated milk", max_level_basis="gmp"),  # sub-code exists, but not in 7.2
    ]
    resolved = [_resolved(0, "additive", eu_canonical_id="500(ii)")]
    category_results = {0: _category_result("product", None, ["7.2"], additive_ids=["500(ii)"])}
    verdict = evaluate(resolved, category_results, eu_fip)
    item = verdict.items[0]
    assert item.headline == "permitted_qs"
    assert "widened_to_parent_for_lookup" in item.flags


def test_duplicate_rows_merge_level_and_conditions_not_pick_one():
    # MEASURED bug (docs/build_log.md's conflicting_rows investigation):
    # picking the "most restrictive" row silently discarded whichever row
    # lost the tie-break. The numeric level is still the most restrictive
    # (lower) of the two -- but BOTH rows' status is "permitted", so this
    # is now a MERGE, and "only tuna" must survive even though it came from
    # the row that did not carry the winning level.
    eu_fip = [
        _row(
            "300", "9.1.1 unprocessed fish",
            max_level_mg_kg=300.0, max_level_basis="mg/kg", conditions="only tuna",
        ),
        _row("300", "9.1.1 unprocessed fish", max_level_basis="gmp"),
    ]
    resolved = [_resolved(0, "additive", eu_canonical_id="300")]
    category_results = {0: _category_result("product", None, ["9.1.1"], additive_ids=["300"])}
    verdict = evaluate(resolved, category_results, eu_fip)
    item = verdict.items[0]
    assert item.by_category[0].max_level_mg_kg == 300.0
    assert item.by_category[0].conditions == "only tuna"
    assert "multiple_provisions_apply: 2" in item.flags
    assert "conflicting_rows" not in item.flags


def test_two_rows_both_null_level_different_conditions_both_texts_present():
    eu_fip = [
        _row("330", "14.1.4 flavoured drinks", conditions="E 420, E 421 may not be used"),
        _row("330", "14.1.4 flavoured drinks", conditions="ML = quantum satis except E 425 ML = 10000 mg/kg"),
    ]
    resolved = [_resolved(0, "additive", eu_canonical_id="330")]
    category_results = {0: _category_result("product", None, ["14.1.4"], additive_ids=["330"])}
    verdict = evaluate(resolved, category_results, eu_fip)
    item = verdict.items[0]
    conditions = item.by_category[0].conditions
    assert "E 420, E 421 may not be used" in conditions
    assert "ML = quantum satis except E 425 ML = 10000 mg/kg" in conditions
    assert item.by_category[0].max_level_mg_kg is None
    assert "multiple_provisions_apply: 2" in item.flags


def test_two_rows_different_levels_lower_wins_and_both_conditions_present():
    eu_fip = [
        _row("224", "14.1.4 flavoured drinks", max_level_mg_kg=20.0, conditions="carry-over from concentrates only"),
        _row("224", "14.1.4 flavoured drinks", max_level_mg_kg=50.0, conditions="direct addition permitted"),
    ]
    resolved = [_resolved(0, "additive", eu_canonical_id="224")]
    category_results = {0: _category_result("product", None, ["14.1.4"], additive_ids=["224"])}
    verdict = evaluate(resolved, category_results, eu_fip)
    item = verdict.items[0]
    cv = item.by_category[0]
    assert cv.max_level_mg_kg == 20.0
    assert "carry-over from concentrates only" in cv.conditions
    assert "direct addition permitted" in cv.conditions
    assert "multiple_provisions_apply: 2" in item.flags


def test_single_row_is_unchanged_no_merge_flag():
    eu_fip = [_row("300", "9.1.1 unprocessed fish", conditions="only tuna")]
    resolved = [_resolved(0, "additive", eu_canonical_id="300")]
    category_results = {0: _category_result("product", None, ["9.1.1"], additive_ids=["300"])}
    verdict = evaluate(resolved, category_results, eu_fip)
    item = verdict.items[0]
    assert item.by_category[0].conditions == "only tuna"
    assert not any(f.startswith("multiple_provisions_apply") for f in item.flags)
    assert "conflicting_rows" not in item.flags


def test_e330_real_shape_14_1_4_group_i_two_clauses_both_shown():
    # THE REAL CASE from docs/build_log.md's investigation: E330 (citric
    # acid) in category 14.1.4, via eu_fip's actual two Group I rows --
    # both clauses apply simultaneously and neither may be dropped.
    eu_fip = [
        _row(
            "330", "14.1.4 Flavoured_drinks", max_level_basis="conditional",
            conditions=(
                "Permitted via Group I, Additives; E 420, E 421, E 953, E 965, E 966 and E 967 may not "
                "be used  E 968 may\nnot be used except where specifically provided for in this food category"
            ),
        ),
        _row(
            "330", "14.1.4 Flavoured_drinks", max_level_basis="gmp",
            conditions=(
                "Permitted via Group I, Additives; ML = quantum satis; except E 425 ML = 10000 mg/kg; "
                "E 620 to E 625, ML =\n10000 mg/kg individually or in combination, expressed as glutamic "
                "acid;\nE 626 to E 635, ML = 500 mg/kg individually or in combination, expressed\nas "
                "guanylic acid."
            ),
        ),
    ]
    resolved = [_resolved(0, "additive", eu_canonical_id="330")]
    category_results = {0: _category_result("product", None, ["14.1.4"], additive_ids=["330"])}
    verdict = evaluate(resolved, category_results, eu_fip)
    item = verdict.items[0]
    conditions = item.by_category[0].conditions
    assert "E 420, E 421, E 953, E 965, E 966 and E 967 may not be used" in conditions
    assert "ML = quantum satis; except E 425 ML = 10000 mg/kg" in conditions
    assert "multiple_provisions_apply: 2" in item.flags


def test_nbsp_only_condition_is_dropped_not_numbered_as_an_empty_clause():
    # MEASURED bug (data/reference/eu_fip.json): some rows carry a literal
    # "&nbsp;" as a scraped placeholder for an empty conditions cell --
    # truthy in Python, but renders as an empty numbered list item ("2)"
    # with nothing after it) once merged alongside a real clause. The blank
    # row must be dropped, not numbered.
    eu_fip = [
        _row("150c", "14.2.1", conditions="&nbsp;"),
        _row("150c", "14.2.1", conditions='only "Table beer"'),
    ]
    resolved = [_resolved(0, "additive", eu_canonical_id="150c")]
    category_results = {0: _category_result("product", None, ["14.2.1"], additive_ids=["150c"])}
    verdict = evaluate(resolved, category_results, eu_fip)
    item = verdict.items[0]
    assert item.by_category[0].conditions == 'only "Table beer"'
    assert "1)" not in item.by_category[0].conditions
    assert "2)" not in item.by_category[0].conditions


def test_nbsp_only_condition_on_a_single_row_normalizes_to_no_conditions():
    eu_fip = [_row("300", "6.2.1", conditions="&nbsp;")]
    resolved = [_resolved(0, "additive", eu_canonical_id="300")]
    category_results = {0: _category_result("product", None, ["6.2.1"], additive_ids=["300"])}
    verdict = evaluate(resolved, category_results, eu_fip)
    item = verdict.items[0]
    assert item.by_category[0].conditions is None
    assert item.headline == "permitted_qs"


def test_status_disagreement_keeps_old_conflicting_rows_behaviour_not_merged():
    # A genuine contradiction (one row permits, another prohibits) is not
    # two co-applicable provisions -- there is no sensible "merge" of a
    # permission with a ban. Tested directly against _select_row, not
    # through evaluate(): the full pipeline can never actually reach this
    # case for one additive, since _prohibited_id's jurisdiction-wide check
    # (evaluate() step 3) already short-circuits to not_authorised_eu, by_
    # category=[], before any category-specific lookup runs -- this guards
    # _select_row's own behaviour in isolation, should it ever be reached
    # a different way.
    rows = [
        _row("300", "9.1.1 unprocessed fish", status="permitted", conditions="only tuna"),
        _row("300", "9.1.1 unprocessed fish", status="prohibited"),
    ]
    chosen, flags = _select_row(rows)
    assert chosen["status"] == "prohibited"
    assert flags == ["conflicting_rows"]


def test_category_sensitive_true_when_candidates_disagree():
    # E551 permitted in 12.2.2, not in 15.1 -- opposite verdicts for the
    # same additive depending on which candidate category is correct.
    eu_fip = [_row("551", "12.2.2 seasonings and condiments", max_level_basis="gmp")]
    resolved = [_resolved(0, "additive", eu_canonical_id="551")]
    category_results = {0: _category_result("product", None, ["12.2.2", "15.1"], additive_ids=["551"])}
    verdict = evaluate(resolved, category_results, eu_fip)
    item = verdict.items[0]
    assert item.category_sensitive is True
    assert item.headline == "permitted_qs"  # rank-1 candidate is 12.2.2


def test_category_sensitive_false_when_all_candidates_agree():
    eu_fip = [
        _row("330", "12.2.2 seasonings and condiments", max_level_basis="gmp"),
        _row("330", "15.1 savoury snacks", max_level_basis="gmp"),
        _row("330", "5.1 cocoa and chocolate", max_level_basis="gmp"),
    ]
    resolved = [_resolved(0, "additive", eu_canonical_id="330")]
    category_results = {0: _category_result("product", None, ["12.2.2", "15.1", "5.1"], additive_ids=["330"])}
    verdict = evaluate(resolved, category_results, eu_fip)
    assert verdict.items[0].category_sensitive is False


def test_absent_additive_with_no_category_is_not_authorised_eu():
    # Absence is category-independent: computable even with NO category
    # result at all -- this is the text-input case (Mixed.json skips the
    # category stage entirely).
    eu_fip = [_row("300", "1.5 dehydrated milk")]
    resolved = [_resolved(0, "additive", eu_canonical_id="999")]
    verdict = evaluate(resolved, {}, eu_fip)  # no category_results entry
    assert verdict.items[0].headline == "not_authorised_eu"


def test_summary_states_dosage_caveat_even_when_conditions_also_present():
    # A row can carry BOTH conditions text and a numeric cap (real example:
    # INS 551, "Permitted via Silicon dioxide..." with a 20000 mg/kg cap).
    # Rule 4 checks conditions before max_level, so the verdict label is
    # permitted_with_conditions, not permitted_with_limit -- the dosage
    # caveat must still fire, since a real numeric limit is present.
    eu_fip = [
        _row(
            "551", "12.2.2 seasonings and condiments",
            max_level_mg_kg=20000.0, max_level_basis="mg/kg", conditions="Permitted via Silicon dioxide",
        )
    ]
    resolved = [_resolved(0, "additive", eu_canonical_id="551")]
    category_results = {0: _category_result("product", None, ["12.2.2"], additive_ids=["551"])}
    verdict = evaluate(resolved, category_results, eu_fip)
    assert verdict.items[0].headline == "permitted_with_conditions"
    assert "dosage" in verdict.summary.lower()


def test_identified_substance_absent_from_eu_fip_is_not_authorised_eu():
    # MEASURED false-clear bug: INS 143 (Fast Green FCF) -- canonical_ins
    # set, classification "additive", eu_canonical_id null because the EU
    # crosswalk found no entry. Absence from the Union list is a FINDING,
    # not an unknown -- this must be a blocking not_authorised_eu, never
    # "unresolved".
    eu_fip = [_row("300", "1.5 dehydrated milk")]  # unrelated additive, present
    resolved = [_resolved(0, "additive", eu_canonical_id=None, canonical_ins="143")]
    category_results = {0: _category_result("product", None, ["1.5"], additive_ids=[])}
    verdict = evaluate(resolved, category_results, eu_fip)
    item = verdict.items[0]
    assert item.headline == "not_authorised_eu"
    assert item.headline != "unresolved"
    assert "not_in_eu_list" in item.flags
    assert 0 in verdict.blocking


def test_identified_substance_absent_from_eu_fip_with_no_category_still_blocks():
    eu_fip = [_row("300", "1.5 dehydrated milk")]
    resolved = [_resolved(0, "additive", eu_canonical_id=None, canonical_ins="143")]
    verdict = evaluate(resolved, {}, eu_fip)  # no category_results entry at all
    item = verdict.items[0]
    assert item.headline == "not_authorised_eu"
    assert 0 in verdict.blocking


def test_genuinely_unidentified_substance_stays_unresolved():
    # Regression guard: the fix must not turn every "unknown" into a ban --
    # only a substance the resolver actually IDENTIFIED (canonical_ins set)
    # and found absent from eu_fip is not_authorised_eu.
    eu_fip = [_row("300", "1.5 dehydrated milk")]
    resolved = [_resolved(0, "unknown", eu_canonical_id=None, canonical_ins=None, candidates=["1400"])]
    verdict = evaluate(resolved, {}, eu_fip)
    item = verdict.items[0]
    assert item.headline == "unresolved"
    assert item.headline != "not_authorised_eu"
    assert 0 not in verdict.blocking


def test_summary_does_not_falsely_clear_when_item_is_not_authorised():
    # The exact false-clear this bug produced: a product summary claiming
    # "No item was found not permitted" while a blocking finding sat
    # unreported as "unresolved".
    eu_fip = [_row("300", "1.5 dehydrated milk")]
    resolved = [_resolved(0, "additive", eu_canonical_id=None, canonical_ins="143")]
    category_results = {0: _category_result("product", None, ["1.5"], additive_ids=[])}
    verdict = evaluate(resolved, category_results, eu_fip)
    assert "No item was found not permitted" not in verdict.summary
    assert "NOT PERMITTED" in verdict.summary


def test_rank1_blocks_rank2_permits_is_category_conflict_not_blocking():
    # MEASURED: category recall@1 is ~0.46 -- a retrieval miss must not
    # become a confident compliance failure.
    eu_fip = [_row("503", "7.2 fine bakery wares", max_level_basis="gmp")]
    resolved = [_resolved(0, "additive", eu_canonical_id="503")]
    category_results = {
        0: _category_result(
            "product", None, ["11.1", "7.2"], additive_ids=["503"],
            names={"11.1": "Sugars and syrups", "7.2": "Fine bakery wares"},
        )
    }
    verdict = evaluate(resolved, category_results, eu_fip)
    item = verdict.items[0]
    assert item.verdict_certainty == "category_dependent"
    assert 0 in verdict.category_conflict
    assert 0 not in verdict.blocking
    assert "NOT PERMITTED" not in verdict.summary
    assert "depend on the food category" in verdict.summary
    assert "11.1" in verdict.summary and "7.2" in verdict.summary


def test_all_candidates_not_permitted_is_blocking_and_certain():
    eu_fip = [_row("999", "1.5 dehydrated milk")]  # exists, but not in either candidate category
    resolved = [_resolved(0, "additive", eu_canonical_id="999")]
    category_results = {0: _category_result("product", None, ["9.1.1", "12.2.2"], additive_ids=["999"])}
    verdict = evaluate(resolved, category_results, eu_fip)
    item = verdict.items[0]
    assert item.verdict_certainty == "certain"
    assert 0 in verdict.blocking
    assert 0 not in verdict.category_conflict


def test_absence_not_authorised_is_blocking_and_certain_with_no_category():
    eu_fip = [_row("300", "1.5 dehydrated milk")]
    resolved = [_resolved(0, "additive", eu_canonical_id="999")]
    verdict = evaluate(resolved, {}, eu_fip)  # no category_results entry at all
    item = verdict.items[0]
    assert item.headline == "not_authorised_eu"
    assert item.verdict_certainty == "certain"
    assert 0 in verdict.blocking


def test_prohibition_not_authorised_is_blocking_and_certain():
    eu_fip = [_row("999", None, status="prohibited")]
    resolved = [_resolved(0, "additive", eu_canonical_id="999")]
    verdict = evaluate(resolved, {}, eu_fip)  # no category_results entry at all
    item = verdict.items[0]
    assert item.headline == "not_authorised_eu"
    assert item.verdict_certainty == "certain"
    assert 0 in verdict.blocking


def test_parle_g_real_shape_503_under_11_1_vs_7_2_is_category_conflict():
    # MEASURED: Parle-Gmain reported "3 item(s) NOT PERMITTED" (503,
    # 500(ii), 472e). Hand-calculation against eu_fip confirms all three
    # ARE permitted via Group I in 7.2 Fine bakery wares -- rank-1 was 11.1
    # Sugars and syrups; 7.2 was rank 2. Parle-G is a biscuit.
    eu_fip = [_row("503", "7.2 fine bakery wares", max_level_basis="gmp", additive_name="Ammonium carbonates")]
    resolved = [_resolved(0, "additive", eu_canonical_id="503")]
    category_results = {
        0: _category_result(
            "product", None, ["11.1", "7.2"], additive_ids=["503"],
            names={"11.1": "Sugars and syrups", "7.2": "Fine bakery wares"},
        )
    }
    verdict = evaluate(resolved, category_results, eu_fip)
    item = verdict.items[0]
    assert item.headline == "not_permitted_in_category"  # rank-1 verdict, unchanged
    assert item.verdict_certainty == "category_dependent"
    assert 0 in verdict.category_conflict
    assert 0 not in verdict.blocking
    assert "NOT PERMITTED" not in verdict.summary
    assert "Ammonium carbonates" in verdict.summary


def test_confirmed_category_yields_one_candidate_certain_and_flagged():
    # A confirmed category (as scripts/verdict.py's --category builds it)
    # is a single-candidate CategoryResult -- category_sensitive and
    # verdict_certainty fall out correctly on their own; only the flag
    # (category_confirmed_by_user, not category_unconfirmed) differs.
    eu_fip = [_row("503", "7.2 fine bakery wares", conditions="Permitted via Group I")]
    resolved = [_resolved(0, "additive", eu_canonical_id="503")]
    category_results = {
        0: _category_result("product", None, ["7.2"], additive_ids=["503"], names={"7.2": "Fine bakery wares"})
    }
    verdict = evaluate(resolved, category_results, eu_fip, confirmed_item_ids=frozenset({0}))
    item = verdict.items[0]
    assert len(item.by_category) == 1
    assert item.category_sensitive is False
    assert item.verdict_certainty == "certain"
    assert "category_confirmed_by_user" in item.flags
    assert "category_unconfirmed" not in item.flags


def test_confirmed_blocking_verdict_is_blocking_not_category_conflict():
    # The whole point of confirmation: it removes the uncertainty that
    # would otherwise force a rank-1 block into category_conflict.
    eu_fip = [_row("300", "1.5 dehydrated milk")]  # exists, but not in the confirmed category
    resolved = [_resolved(0, "additive", eu_canonical_id="300")]
    category_results = {0: _category_result("product", None, ["9.1.1"], additive_ids=["300"])}
    verdict = evaluate(resolved, category_results, eu_fip, confirmed_item_ids=frozenset({0}))
    item = verdict.items[0]
    assert item.headline == "not_permitted_in_category"
    assert item.verdict_certainty == "certain"
    assert 0 in verdict.blocking
    assert 0 not in verdict.category_conflict


def test_mixed_confirmed_and_retrieved_components_keeps_retrieval_caveat():
    # A product can have one confirmed component and one still-retrieved
    # component -- mixing is expected. The confirmation sentence AND the
    # retrieval caveat must both appear, since the caveat is genuinely
    # still true for the unconfirmed component.
    eu_fip = [
        _row("503", "7.2 fine bakery wares", max_level_basis="gmp"),
        _row("330", "11.2 other sugars and syrups", max_level_basis="gmp"),
    ]
    resolved = [
        _resolved(0, "additive", eu_canonical_id="503"),
        _resolved(1, "additive", eu_canonical_id="330"),
    ]
    confirmed_result = _category_result(
        "product", None, ["7.2"], additive_ids=["503"], names={"7.2": "Fine bakery wares"}
    )
    retrieved_result = _category_result(
        "component", "INVERT SUGAR SYRUP", ["11.2", "9.1.1"], additive_ids=["330"]
    )
    category_results = {0: confirmed_result, 1: retrieved_result}
    verdict = evaluate(resolved, category_results, eu_fip, confirmed_item_ids=frozenset({0}))

    assert "Food category confirmed by user: product = 7.2 (Fine bakery wares)" in verdict.summary
    assert "recall@1" in verdict.summary
    assert verdict.category_source == {"(product)": "user", "INVERT SUGAR SYRUP": "retrieved"}


def test_summary_does_not_call_permitted_with_conditions_items_manual_review():
    # MEASURED BUG (Pepsimain): verdict.summary said "7 item(s) require
    # manual review" while the count strip (src/ui/components.count_
    # buckets) showed "Needs review: 0" for the SAME ProductVerdict --
    # every one of the 7 was permitted_with_conditions, which count_
    # buckets deliberately buckets under "Permitted, conditions to check",
    # not "Needs review" (see count_buckets' own docstring and
    # test_count_buckets_are_mutually_exclusive_and_sum_to_item_total in
    # tests/test_components.py). The two must never disagree about the
    # SAME items, even in wording -- narration hides the fallback summary
    # on a successful run, but it must still be correct on its own.
    eu_fip = [
        _row("300", "9.1.1 unprocessed fish", conditions="only tuna"),
        _row("330", "9.1.1 unprocessed fish", conditions="excluding smoked fish"),
    ]
    resolved = [
        _resolved(0, "additive", eu_canonical_id="300"),
        _resolved(1, "additive", eu_canonical_id="330"),
    ]
    category_results = {
        0: _category_result("product", None, ["9.1.1"], additive_ids=["300"]),
        1: _category_result("product", None, ["9.1.1"], additive_ids=["330"]),
    }
    verdict = evaluate(resolved, category_results, eu_fip)

    assert [item.headline for item in verdict.items] == ["permitted_with_conditions"] * 2
    assert verdict.review_required == [0, 1]  # unchanged -- narrator.py still relies on this

    assert "require manual review" not in verdict.summary
    assert "2 item(s) are permitted subject to conditions that must be checked" in verdict.summary
    assert "[0, 1]" in verdict.summary

    item_dicts = [item.model_dump() for item in verdict.items]
    assert count_buckets(item_dicts)["Needs review"] == 0  # the strip this must not contradict


def test_summary_still_names_genuinely_unresolved_items_as_needing_review():
    # The split above must not swallow the real case: an unresolved item
    # (no identity at all) genuinely belongs in "require manual review",
    # and count_buckets agrees -- its headline is in _NEEDS_REVIEW_HEADLINES.
    eu_fip = [_row("300", "9.1.1 unprocessed fish", conditions="only tuna")]
    resolved = [
        _resolved(0, "additive", eu_canonical_id="300"),
        _resolved(1, "unknown"),
    ]
    category_results = {0: _category_result("product", None, ["9.1.1"], additive_ids=["300"])}
    verdict = evaluate(resolved, category_results, eu_fip)

    assert verdict.items[1].headline == "unresolved"
    assert "1 item(s) require manual review (item_id [1])" in verdict.summary
    assert "1 item(s) are permitted subject to conditions that must be checked" in verdict.summary

    item_dicts = [item.model_dump() for item in verdict.items]
    assert count_buckets(item_dicts)["Needs review"] == 1
