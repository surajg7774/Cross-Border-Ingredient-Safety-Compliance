"""Tests for src/ui/components.py's pure render helpers -- no Streamlit
runtime required for these, since they are plain string/dict functions."""

from src.ui.components import (
    _additive_row,
    _blocking_reason,
    _conditions_notes,
    _detail_lead_line,
    _display_name,
    _diverging_candidates,
    _group_conditions_map,
    _group_note,
    _is_ancestor_code,
    _max_amount_cell,
    _normalize_conditions,
    _order_news_signals,
    _out_of_scope_reason,
    _period_of_application_note,
    _primary_candidate,
    _pure_group_match,
    _short_category_name,
    _split_merged_clauses,
    _status_label,
    _substitute_flag_label,
    _verdict_caveats,
    candidates_phrase,
    count_buckets,
)


def _candidate(fcs_code, verdict, **overrides):
    base = {
        "fcs_code": fcs_code,
        "category_name": f"Category {fcs_code}",
        "rank": 1,
        "verdict": verdict,
        "max_level_mg_kg": None,
        "max_level_basis": None,
        "conditions": None,
        "note_codes": [],
        "source_url": None,
        "retrieved_date": None,
    }
    base.update(overrides)
    return base


def test_diverging_candidates_empty_for_a_single_candidate():
    item = {
        "confirmed_fcs_code": "1.5",
        "by_category": [_candidate("1.5", "permitted_qs")],
    }
    assert _diverging_candidates(item) == []


def test_diverging_candidates_empty_when_all_candidates_agree():
    """Where all candidates agree, there is nothing to explain -- even
    when there is more than one candidate."""
    item = {
        "confirmed_fcs_code": None,
        "by_category": [
            _candidate("1.5", "permitted_qs"),
            _candidate("7.2", "permitted_qs"),
        ],
    }
    assert _diverging_candidates(item) == []


def test_diverging_candidates_returns_all_when_they_genuinely_disagree():
    """MEASURED PROBLEM this covers: E551 permitted under the confirmed
    12.2.2, not permitted under 15.1 (here modelled as 12.1.1 vs 12.1,
    which are NOT ancestor-related to each other or to 12.2.2) -- the
    reader must see all three, confirmed one first."""
    item = {
        "confirmed_fcs_code": "12.2.2",
        "by_category": [
            _candidate("12.2.2", "permitted_with_conditions"),
            _candidate("12.1.1", "permitted_with_conditions"),
            _candidate("12.1", "not_permitted_in_category"),
        ],
    }
    diverging = _diverging_candidates(item)
    assert [cv["fcs_code"] for cv in diverging] == ["12.2.2", "12.1.1", "12.1"]


def test_diverging_candidates_unconfirmed_still_returns_the_real_divergence():
    """No confirmed_fcs_code at all (e.g. a preview-only render) -- still
    a genuine divergence, just with nothing marked "confirmed"."""
    item = {
        "confirmed_fcs_code": None,
        "by_category": [
            _candidate("12.2.2", "permitted_with_conditions"),
            _candidate("12.1", "not_permitted_in_category"),
        ],
    }
    diverging = _diverging_candidates(item)
    assert len(diverging) == 2


def test_diverging_candidates_deduped_fcs_codes_collapse_to_no_divergence():
    item = {
        "confirmed_fcs_code": "12.2.2",
        "by_category": [
            _candidate("12.2.2", "permitted_qs"),
            _candidate("12.2.2", "permitted_qs"),  # same code from the confirmed-candidate merge
        ],
    }
    assert _diverging_candidates(item) == []


def test_primary_candidate_prefers_confirmed_code():
    item = {
        "confirmed_fcs_code": "12.1",
        "by_category": [_candidate("12.2.2", "permitted_qs"), _candidate("12.1", "not_permitted_in_category")],
    }
    assert _primary_candidate(item)["fcs_code"] == "12.1"


def test_primary_candidate_falls_back_to_first_when_unconfirmed():
    item = {
        "confirmed_fcs_code": None,
        "by_category": [_candidate("12.2.2", "permitted_qs"), _candidate("12.1", "not_permitted_in_category")],
    }
    assert _primary_candidate(item)["fcs_code"] == "12.2.2"


def test_primary_candidate_none_when_no_by_category():
    assert _primary_candidate({"by_category": []}) is None
    assert _primary_candidate({}) is None


def test_display_name_prefers_enriched_additive_name():
    """Section A: app.py already backfills additive_name (eu_fip, else
    Codex, else the label's own wording) before this module ever sees the
    item -- so as long as additive_name is set, _display_name must use it
    first, never falling through to E{id}/item N."""
    item = {"item_id": 8, "eu_canonical_id": None, "additive_name": "Fast Green FCF"}
    assert _display_name(item, names={8: "some label text"}) == "Fast Green FCF"


def test_display_name_falls_back_to_extraction_name_then_id():
    assert _display_name({"item_id": 4, "additive_name": None, "eu_canonical_id": None}, names={4: "Natural Khus Flavour"}) == "Natural Khus Flavour"
    assert _display_name({"item_id": 4, "additive_name": None, "eu_canonical_id": "551"}, names=None) == "E551"
    assert _display_name({"item_id": 4, "additive_name": None, "eu_canonical_id": None}, names=None) == "item 4"


def test_blocking_reason_distinguishes_prohibited_from_absent():
    assert _blocking_reason({"flags": ["prohibited"]}).startswith("prohibited")
    assert _blocking_reason({"flags": ["not_in_eu_list"]}).startswith("not authorised")
    assert _blocking_reason({"flags": []}).startswith("not authorised")


def test_out_of_scope_reason_maps_governing_regulation():
    # Regulation numbers are dropped from this default, reader-facing text
    # -- the real citation stays on the item's own flag, in the export.
    assert _out_of_scope_reason({"flags": ["governing_regulation: Reg 1334/2008"]}) == (
        "a flavouring, covered by different rules"
    )
    assert _out_of_scope_reason({"flags": ["governing_regulation: Reg 1332/2008"]}) == (
        "an enzyme, covered by different rules"
    )
    assert _out_of_scope_reason({"flags": ["governing_regulation: not an additive"]}) == "an ordinary food"
    assert _out_of_scope_reason({"flags": []}) == "an ordinary food"
    assert "Reg " not in _out_of_scope_reason({"flags": ["governing_regulation: Reg 1334/2008"]})


def test_candidates_phrase_shows_real_names_for_a_few_candidates():
    # MEASURED (data/reference/label_aliases.json): "stevia" resolves to
    # exactly these 4 bare INS codes -- a reader with no regulatory
    # training cannot act on "960a, 960b, 960c, 960d" alone.
    codes = ["960a", "960b", "960c", "960d"]
    ins_names = {
        "960a": "Steviol glycosides from Stevia rebaudiana Bertoni",
        "960b": "Steviol glycosides from fermentation",
        "960c": "Enzymatically produced steviol glycosides",
        "960d": "Glucosylated steviol glycosides",
    }
    phrase = candidates_phrase(codes, ins_names)
    assert phrase == (
        "Steviol glycosides from Stevia rebaudiana Bertoni, Steviol glycosides from fermentation, "
        "Enzymatically produced steviol glycosides, Glucosylated steviol glycosides"
    )


def test_candidates_phrase_falls_back_to_e_number_for_an_unnamed_code():
    assert candidates_phrase(["960a"], {}) == "E960a"
    assert candidates_phrase(["960a"], None) == "E960a"


def test_candidates_phrase_shows_count_and_range_for_many_candidates():
    # MEASURED: "modified starch"/"modified cornstarch" both resolve to
    # 17 candidates spanning E1400-E1452 -- 17 long INS names is not
    # something anyone scans; a count and the range is.
    codes = ["1400", "1401", "1402", "1403", "1404", "1405", "1410"]
    assert candidates_phrase(codes, {}) == "7 possible matches (E1400–E1410)"


def test_candidates_phrase_empty_for_no_candidates():
    assert candidates_phrase([], {}) == ""


def test_substitute_flag_label_translates_known_flags_and_passes_through_unknown():
    assert _substitute_flag_label("classes_from_subtypes") == "function inferred from sub-types"
    assert _substitute_flag_label("adds_labelling_obligation") == "requires a warning label"
    assert _substitute_flag_label("under_efsa_review") == "under EFSA review"
    assert _substitute_flag_label("some_future_flag") == "some_future_flag"


def test_is_ancestor_code():
    assert _is_ancestor_code("14.1", "14.1.4") is True
    assert _is_ancestor_code("14", "14.1.4") is True
    assert _is_ancestor_code("14.1.4", "14.1") is False  # directional
    assert _is_ancestor_code("14.1.4", "14.1.4") is False  # not its own ancestor
    assert _is_ancestor_code("14.2", "14.1.4") is False  # sibling branch, not ancestor
    assert _is_ancestor_code("1", "14.1.4") is False  # prefix string, not a real dotted ancestor


def test_khusmain_real_shape_ancestor_candidates_produce_no_divergence():
    # THE REAL CASE: Khusmain's three retrieved candidates were 14.1.4,
    # 14.1 and 14 -- not alternatives, 14.1.4 is INSIDE 14.1 is INSIDE 14.
    # eu_fip has no row for the parents, so they would otherwise evaluate
    # "not permitted in this category": true, meaningless, and it would
    # falsely claim a divergence that does not exist.
    item = {
        "confirmed_fcs_code": "14.1.4",
        "by_category": [
            _candidate("14.1.4", "permitted_with_conditions", category_name="Flavoured drinks"),
            _candidate("14.1", "not_permitted_in_category", category_name="Non-alcoholic beverages"),
            _candidate("14", "not_permitted_in_category", category_name="Beverages"),
        ],
    }
    # The parents are filtered out entirely -- one real candidate left,
    # nothing to diverge against.
    assert _diverging_candidates(item) == []


def test_genuine_divergence_between_non_ancestor_candidates_still_shown():
    # Regression guard: the ancestor fix must not suppress a REAL
    # divergence between two candidates that are not in the same branch
    # (Chipsmain's E551: permitted under 12.2.2, not under 15.1).
    item = {
        "confirmed_fcs_code": "12.2.2",
        "by_category": [
            _candidate("12.2.2", "permitted_with_conditions", category_name="Seasonings"),
            _candidate("15.1", "not_permitted_in_category", category_name="Savoury snacks"),
        ],
    }
    diverging = _diverging_candidates(item)
    assert [cv["fcs_code"] for cv in diverging] == ["12.2.2", "15.1"]


def test_group_note_derives_group_name_from_conditions_text():
    note = _group_note("Permitted via Group I, Additives; E 420, E 421 may not be used")
    assert note is not None
    assert "Group I, Additives" in note
    assert "may not be in your product" in note

    note2 = _group_note("Permitted via Group II, Colours at quantum satis; excluding chocolate milk")
    assert note2 is not None
    assert "Group II, Colours" in note2

    # A numbered, merged multi-clause text (src/rules/engine.py's
    # _merge_conditions) still matches on its first clause.
    merged = "1) Permitted via Group I, Additives; E 420 may not be used\n\n2) Permitted via Group I, Additives; ML = quantum satis"
    note3 = _group_note(merged)
    assert note3 is not None
    assert "Group I, Additives" in note3


def test_group_note_none_when_not_a_group_permission():
    assert _group_note("Permitted via Silicon dioxide - silicates; Period of application: from 1 February 2014") is None
    assert _group_note("only tuna") is None


def test_period_of_application_note_detects_transition_dates():
    note = _period_of_application_note("Permitted via Silicon dioxide - silicates; Period of application: until 31 July 2014")
    assert note is not None
    assert "no separate expiry field" in note
    assert _period_of_application_note("only tuna") is None


def test_conditions_notes_stacks_both_when_both_apply():
    text = "Permitted via Group I, Additives; Period of application: until 31 July 2014"
    notes = _conditions_notes(text)
    assert len(notes) == 2
    assert any("Group I" in n for n in notes)
    assert any("period of application" in n.lower() for n in notes)


def test_conditions_notes_empty_for_plain_conditions():
    assert _conditions_notes("only tuna") == []
    assert _conditions_notes(None) == []


def test_count_buckets_are_mutually_exclusive_and_sum_to_item_total():
    # MEASURED BUG this covers: the count strip used to read
    # ProductVerdict.review_required directly, which deliberately also
    # includes permitted_with_conditions items -- so on a real product
    # (Chipsmain: 16 items) "Blocked" + "Permitted" + "Review" + "Out of
    # scope" summed to 22, not 16. One item dict per possible headline,
    # covering every value ItemVerdict.headline can take.
    headlines = [
        "not_authorised_eu",
        "not_permitted_in_category",
        "permitted_qs",
        "permitted_with_limit",
        "permitted_with_conditions",
        "unresolved",
        "category_unknown",
        "out_of_scope",
    ]
    items = [{"item_id": i, "headline": h} for i, h in enumerate(headlines)]

    counts = count_buckets(items)

    assert sum(counts.values()) == len(items)
    assert counts == {
        "Blocked": 2,
        "Permitted, conditions to check": 3,
        "Needs review": 2,
        "Out of scope": 1,
    }


def test_count_buckets_empty_list_sums_to_zero():
    assert sum(count_buckets([]).values()) == 0


def _item_with_flags(flags, by_category=None):
    return {"by_category": by_category or [], "flags": flags}


def test_verdict_caveats_dosage_fires_when_any_max_level_present():
    items = [_item_with_flags([], [_candidate("12.2.2", "permitted_with_limit", max_level_mg_kg=20000.0)])]
    caveats = _verdict_caveats(items)
    assert any("dosage" in c.lower() for c in caveats)


def test_verdict_caveats_dosage_absent_when_no_item_has_a_level():
    items = [_item_with_flags([], [_candidate("12.2.2", "permitted_qs")])]
    assert _verdict_caveats(items) == []


def test_verdict_caveats_unconfirmed_wins_over_confirmed_when_mixed():
    # This app always confirms every category before computing a verdict at
    # all (see count_buckets' own docstring), so a genuine mix should not
    # occur in practice -- but the function must still resolve it safely,
    # and the uncertain case is the one worth surfacing, not the reassuring
    # one, so "not confirmed" wins if both flags appear anywhere.
    items = [
        _item_with_flags(["category_confirmed_by_user"]),
        _item_with_flags(["category_unconfirmed"]),
    ]
    caveats = _verdict_caveats(items)
    assert any("not been confirmed" in c for c in caveats)
    assert not any("you confirmed" in c.lower() for c in caveats)


def test_verdict_caveats_confirmed_when_no_item_is_unconfirmed():
    items = [_item_with_flags(["category_confirmed_by_user"])]
    caveats = _verdict_caveats(items)
    assert any("you confirmed" in c.lower() for c in caveats)


def test_verdict_caveats_empty_when_nothing_applies():
    assert _verdict_caveats([]) == []
    assert _verdict_caveats([_item_with_flags([])]) == []


def test_verdict_caveats_never_reads_narration():
    # THE property tests/test_app_ui.py's AppTest-based tests prove end to
    # end: _verdict_caveats takes item dicts only, no narration parameter
    # exists to pass one, so its output cannot depend on whether narrate()
    # succeeded or fell back -- a prompt is a request, not a guarantee, and
    # this is deterministic code instead. Locked in here at the signature
    # level: calling it twice with the SAME items, nothing else in scope,
    # must be idempotent.
    items = [_item_with_flags([], [_candidate("3", "permitted_with_limit", max_level_mg_kg=100.0)])]
    assert _verdict_caveats(items) == _verdict_caveats(items)


def test_short_category_name_strips_trailing_legal_citation():
    assert (
        _short_category_name("Cocoa and chocolate products as covered by Directive 2000/36/EC")
        == "Cocoa and chocolate products"
    )


def test_short_category_name_strips_citation_in_the_middle_keeps_the_rest():
    assert (
        _short_category_name("Fruit juices as defined by Directive 2001/112/EC and vegetable juices")
        == "Fruit juices and vegetable juices"
    )


def test_short_category_name_unchanged_when_no_citation():
    assert _short_category_name("Seasonings and condiments") == "Seasonings and condiments"


def test_short_category_name_none_passthrough():
    assert _short_category_name(None) is None


# ---- UI REDESIGN (ui-simplify): _status_label/_max_amount_cell/
# _additive_row/_detail_lead_line -- the additives table's Status/Maximum
# amount columns and per-row expander lead line. Four plain-English status
# strings only, never a raw verdict string. ------------------------------
def test_status_label_the_two_blocking_verdicts():
    assert _status_label("not_permitted_in_category", None) == ("Not allowed in this food", "blocked")
    assert _status_label("not_authorised_eu", None) == ("Not allowed in the EU", "blocked")


def test_status_label_permitted_qs_reads_allowed_with_no_limit():
    assert _status_label("permitted_qs", None) == ("Allowed", "permitted")


def test_status_label_driven_by_max_level_not_the_verdict_string():
    # MEASURED: permitted_with_conditions can ALSO carry a real mg/kg cap
    # (96% of eu_fip rows carry conditions text; src/rules/engine.py's own
    # dosage caveat already fires off max_level_mg_kg directly for exactly
    # this reason) -- status must reflect that, not treat every
    # permitted_with_conditions row as bare "Allowed".
    assert _status_label("permitted_with_conditions", 500.0) == ("Allowed with limits", "permitted")
    assert _status_label("permitted_with_conditions", None) == ("Allowed", "permitted")
    assert _status_label("permitted_with_limit", 5000.0) == ("Allowed with limits", "permitted")


def test_max_amount_cell_real_figure_or_no_fixed_limit_never_quantum_satis():
    assert _max_amount_cell("permitted_qs", None) == "No fixed limit"
    assert _max_amount_cell("permitted_with_limit", 5000.0) == "Up to 5000 mg/kg"
    assert _max_amount_cell("permitted_with_conditions", 500.0) == "Up to 500 mg/kg"
    assert "quantum satis" not in _max_amount_cell("permitted_qs", None)


def test_max_amount_cell_dash_for_a_blocking_verdict():
    assert _max_amount_cell("not_permitted_in_category", None) == "—"
    assert _max_amount_cell("not_authorised_eu", None) == "—"


def test_additive_row_permitted_qs():
    item = {
        "eu_canonical_id": "300",
        "additive_name": "Ascorbic acid",
        "component_label": None,
        "confirmed_fcs_code": "1.5",
        "flags": [],
        "by_category": [_candidate("1.5", "permitted_qs", category_name="Dehydrated milk")],
    }
    row = _additive_row(item, None)
    assert row["additive"] == "Ascorbic acid (E300)"
    assert row["status"] == "Allowed"
    assert row["bucket"] == "permitted"
    assert row["max_amount"] == "No fixed limit"
    assert row["where"] == "Dehydrated milk (1.5)"


def test_additive_row_where_includes_component_label():
    item = {
        "eu_canonical_id": "551",
        "additive_name": "Silicon dioxide",
        "component_label": "SEASONING",
        "confirmed_fcs_code": "12.2.2",
        "flags": [],
        "by_category": [_candidate("12.2.2", "permitted_qs", category_name="Seasonings and condiments")],
    }
    row = _additive_row(item, None)
    assert row["where"] == "SEASONING: Seasonings and condiments (12.2.2)"


def test_additive_row_headline_only_blocking_item_has_no_category_columns():
    # not_authorised_eu with no by_category at all -- absence/prohibition
    # is jurisdiction-wide, not category-dependent, so there is no
    # category row to fill Maximum amount/In force since/Where from.
    item = {
        "eu_canonical_id": "999",
        "additive_name": "Fast Green FCF",
        "component_label": None,
        "confirmed_fcs_code": None,
        "flags": ["prohibited"],
        "by_category": [],
    }
    row = _additive_row(item, None)
    assert row["status"] == "Not allowed in the EU"
    assert row["max_amount"] == "—"
    assert row["in_force"] == "—"
    assert row["where"] == "—"


def test_additive_row_no_eu_id_omits_the_parenthetical():
    item = {
        "eu_canonical_id": None,
        "additive_name": "Something unidentified",
        "component_label": None,
        "confirmed_fcs_code": None,
        "flags": [],
        "by_category": [],
    }
    assert _additive_row(item, None)["additive"] == "Something unidentified"


def test_detail_lead_line_not_permitted_in_category_names_the_category():
    item = {
        "eu_canonical_id": "551",
        "additive_name": "Silicon dioxide",
        "component_label": None,
        "confirmed_fcs_code": "15.1",
        "flags": [],
        "by_category": [_candidate("15.1", "not_permitted_in_category", category_name="Savoury snacks")],
    }
    row = _additive_row(item, None)
    assert _detail_lead_line(row) == "Silicon dioxide is not permitted for use in Savoury snacks (15.1)."


def test_detail_lead_line_allowed_with_conditions_points_at_the_text_below():
    item = {
        "eu_canonical_id": "330",
        "additive_name": "Citric acid",
        "component_label": None,
        "confirmed_fcs_code": "14.1.4",
        "flags": [],
        "by_category": [
            _candidate(
                "14.1.4", "permitted_with_conditions", category_name="Flavoured drinks", conditions="only sport drinks"
            )
        ],
    }
    row = _additive_row(item, None)
    assert _detail_lead_line(row) == (
        "Citric acid is allowed in Flavoured drinks (14.1.4), subject to the condition below — "
        "read it before using this ingredient here."
    )


def test_detail_lead_line_allowed_no_conditions_is_a_short_statement():
    item = {
        "eu_canonical_id": "300",
        "additive_name": "Ascorbic acid",
        "component_label": None,
        "confirmed_fcs_code": "1.5",
        "flags": [],
        "by_category": [_candidate("1.5", "permitted_qs", category_name="Dehydrated milk")],
    }
    row = _additive_row(item, None)
    assert _detail_lead_line(row) == "Ascorbic acid is allowed in Dehydrated milk (1.5)."


def test_detail_lead_line_eu_wide_block_uses_blocking_reason():
    item = {
        "eu_canonical_id": "171",
        "additive_name": "Titanium dioxide",
        "component_label": None,
        "confirmed_fcs_code": None,
        "flags": ["prohibited"],
        "by_category": [],
    }
    row = _additive_row(item, None)
    lead = _detail_lead_line(row)
    assert lead.startswith("Titanium dioxide is prohibited")


# ---- Group I/II/... shared-block dedup (_normalize_conditions,
# _pure_group_match, _group_conditions_map) -----------------------------

_QS_CLAUSE_ONE_LINE = (
    "Permitted via Group I, Additives; ML = quantum satis; except E 425 ML = 10000 mg/kg."
)
_QS_CLAUSE_WITH_NEWLINE = (
    "Permitted via Group I, Additives; ML = quantum satis; except E 425 ML =\n10000 mg/kg."
)
_QS_CLAUSE_NO_PERIOD = (
    "Permitted via Group I, Additives; ML = quantum satis; except E 425 ML = 10000 mg/kg"
)
# A real merged shape (MEASURED in data/outputs/verdict/GraphTestChips.json,
# INS 627/631): Group I is clause 1 of 2, the second clause (Ribonucleotides)
# is NOT a Group permission and must not be dropped.
_MIXED_GROUP_AND_OTHER = (
    f"1) {_QS_CLAUSE_ONE_LINE}\n\n2) Permitted via Ribonucleotides"
)


def test_normalize_conditions_collapses_whitespace_and_trailing_period_variants():
    normalized = {
        _normalize_conditions(_QS_CLAUSE_ONE_LINE),
        _normalize_conditions(_QS_CLAUSE_WITH_NEWLINE),
        _normalize_conditions(_QS_CLAUSE_NO_PERIOD),
    }
    assert len(normalized) == 1


def test_pure_group_match_matches_a_plain_group_clause():
    match = _pure_group_match(_QS_CLAUSE_ONE_LINE)
    assert match is not None
    assert match.group(1) == "Group I, Additives"


def test_pure_group_match_none_for_non_group_conditions():
    assert _pure_group_match("Permitted via Silicon dioxide - silicates; only tuna") is None


def test_pure_group_match_none_for_merged_multi_clause_conditions():
    # Group I is only ONE of two co-applicable clauses here -- collapsing
    # this to a reference would silently drop the Ribonucleotides clause,
    # so it must NOT be treated as a pure Group match.
    assert _pure_group_match(_MIXED_GROUP_AND_OTHER) is None


def test_group_conditions_map_dedupes_whitespace_variants_of_the_same_clause():
    item_a = {"by_category": [_candidate("14.1.4", "permitted_qs", conditions=_QS_CLAUSE_ONE_LINE)]}
    item_b = {"by_category": [_candidate("1.4", "permitted_qs", conditions=_QS_CLAUSE_WITH_NEWLINE)]}
    item_c = {"by_category": [_candidate("5.2", "permitted_qs", conditions=_QS_CLAUSE_NO_PERIOD)]}
    reps = _group_conditions_map([item_a, item_b, item_c])
    assert len(reps) == 1


def test_group_conditions_map_excludes_merged_multi_clause_items():
    # The whole point of the guard: an item whose conditions merges Group I
    # with a non-Group clause must never enter the shared-block registry,
    # even though the text starts with "1) Permitted via Group I..." --
    # entering it would mean this item's own per-item render collapses to
    # a reference that omits the Ribonucleotides clause entirely.
    pure_item = {"by_category": [_candidate("14.1.4", "permitted_qs", conditions=_QS_CLAUSE_ONE_LINE)]}
    mixed_item = {"by_category": [_candidate("14.1.4", "permitted_qs", conditions=_MIXED_GROUP_AND_OTHER)]}
    reps = _group_conditions_map([pure_item, mixed_item])
    assert len(reps) == 1
    (only_entry,) = reps.values()
    assert only_entry["conditions"] == _QS_CLAUSE_ONE_LINE


def test_group_conditions_map_spans_items_regardless_of_category_or_component():
    # The same clause under a DIFFERENT category/component still collapses
    # to one shared-block entry.
    item_a = {
        "component_label": "CRISPS",
        "by_category": [_candidate("14.1.4", "permitted_qs", conditions=_QS_CLAUSE_ONE_LINE)],
    }
    item_b = {
        "component_label": "SEASONING",
        "by_category": [_candidate("12.2.2", "permitted_qs", conditions=_QS_CLAUSE_ONE_LINE)],
    }
    reps = _group_conditions_map([item_a, item_b])
    assert len(reps) == 1


def test_group_conditions_map_empty_when_no_item_carries_a_group_clause():
    item = {"by_category": [_candidate("5.2", "permitted_qs", conditions="only tuna")]}
    assert _group_conditions_map([item]) == {}


# --------------------------------------------------------------------------- #
# _order_news_signals
# --------------------------------------------------------------------------- #
def _news_signal(eu_id, category, published_date, **overrides):
    base = {
        "eu_canonical_id": eu_id,
        "substance_name": f"Additive {eu_id}",
        "category": category,
        "quoted_span": "quote",
        "source_url": "https://example.com",
        "published_date": published_date,
        "flags": [],
    }
    base.update(overrides)
    return base


def test_order_news_signals_sorts_one_additives_signals_newest_first():
    # MEASURED (Pepsimain, E950): a "regulatory_review" and a
    # "safety_opinion" signal for the SAME additive rendered in whatever
    # order find_news_signals returned them -- no cue for which was
    # current. Newest published_date must come first.
    older = _news_signal("950", "regulatory_review", "2024-01-01")
    newer = _news_signal("950", "safety_opinion", "2026-08-01")
    ordered = _order_news_signals([older, newer])
    assert ordered == [newer, older]


def test_order_news_signals_missing_date_sorts_last_within_its_additive():
    dated = _news_signal("950", "safety_opinion", "2026-08-01")
    undated = _news_signal("950", "regulatory_review", None)
    ordered = _order_news_signals([undated, dated])
    assert ordered == [dated, undated]


def test_order_news_signals_keeps_different_additives_grouped_and_in_first_seen_order():
    # Sorting is per-additive, not a single global sort by date -- an
    # older signal for the FIRST additive seen must not be pushed after a
    # newer signal for a different additive; each additive's own group
    # stays contiguous.
    e968_old = _news_signal("968", "regulatory_review", "2023-01-01")
    e955_new = _news_signal("955", "safety_opinion", "2026-08-01")
    e968_older = _news_signal("968", "safety_opinion", "2022-01-01")
    ordered = _order_news_signals([e968_old, e955_new, e968_older])
    assert [s["eu_canonical_id"] for s in ordered] == ["968", "968", "955"]
    assert ordered[0] == e968_old and ordered[1] == e968_older  # newest-first WITHIN the 968 group


def test_order_news_signals_empty_list_returns_empty():
    assert _order_news_signals([]) == []


# --------------------------------------------------------------------------- #
# _split_merged_clauses -- the four MEASURED real strings from A4/A5
# (data/outputs/verdict/{Chipsmain,Ice-creammain,Pepsimain}.json), not
# constructed ones.
# --------------------------------------------------------------------------- #
_PEPSI_E150D_CONDITIONS = (
    "Permitted via Group II, Colours at quantum satis; excluding chocolate milk and malt "
    "products  Period of application:\nuntil 31 July 2014"
)
_PEPSI_E330_CONDITIONS = (
    "Permitted via Group I, Additives; E 420, E 421, E 953, E 965, E 966 and E 967 may not be "
    "used  E 968 may\nnot be used except where specifically provided for in this food category"
)
_CHIPS_E551_CONDITIONS = (
    "Permitted via Silicon dioxide - silicates; only seasoning  Period of application:  from 1 "
    "February 2014; Note 1: The additives may be added individually or in combination"
)
_ICE_CREAM_GROUP_I_CONDITIONS = (
    "1) Permitted via Group I, Additives; ML = quantum satis; except E 425 ML = 10000 mg/kg; "
    "E 620 to E 625, ML =\n10000 mg/kg individually or in combination, expressed as glutamic "
    "acid;\nE 626 to E 635, ML = 500 mg/kg individually or in combination, expressed\nas "
    "guanylic acid.\n\n2) Permitted via Group IV, Polyols; only energy-reduced or with no "
    "added sugar"
)


def test_split_merged_clauses_none_for_pepsi_e150d_no_confident_boundary():
    # A raw "\n" mid-clause ("...Period of application:\nuntil 31 July
    # 2014") is NOT a clause marker -- must not split.
    assert _split_merged_clauses(_PEPSI_E150D_CONDITIONS) is None


def test_split_merged_clauses_none_for_pepsi_e330_no_confident_boundary():
    assert _split_merged_clauses(_PEPSI_E330_CONDITIONS) is None


def test_split_merged_clauses_none_for_chips_e551_no_confident_boundary():
    assert _split_merged_clauses(_CHIPS_E551_CONDITIONS) is None


def test_split_merged_clauses_splits_ice_cream_group_i_at_the_numbered_marker():
    clauses = _split_merged_clauses(_ICE_CREAM_GROUP_I_CONDITIONS)
    assert clauses is not None
    assert len(clauses) == 2
    # The "N) " marker is stripped -- native <ol> numbering supplies it.
    assert clauses[0].startswith("Permitted via Group I, Additives; ML = quantum satis")
    assert clauses[1] == "Permitted via Group IV, Polyols; only energy-reduced or with no added sugar"
    # Clause 1's own internal word-wrap newlines are preserved, not
    # treated as a further boundary -- only the merge marker is confident.
    assert "E 620 to E 625, ML =\n10000 mg/kg" in clauses[0]


def test_split_merged_clauses_finds_no_pre_merged_marker_in_raw_eu_fip_rows():
    # merge_conditions's "N) " marker (src/rules/row_selection.py) is
    # synthesized at evaluate() time when 2+ rows combine -- it does not
    # pre-exist in eu_fip.json's own per-row conditions text, which is
    # always a single row's own clause. Confirms _split_merged_clauses
    # correctly returns None across the WHOLE real dataset, not just the
    # four measured single-clause strings above.
    import json

    from config import settings

    with open(settings.REFERENCE_DIR / "eu_fip.json", encoding="utf-8") as f:
        eu_fip = json.load(f)
    distinct = {row["conditions"] for row in eu_fip if row.get("conditions")}
    assert len(distinct) > 1000  # sanity: this is the real, full dataset
    assert all(_split_merged_clauses(c) is None for c in distinct)


def test_first_sentence_style_split_would_barely_touch_real_eu_fip_conditions_text():
    # Answers A4 investigation question 1: first_sentence (src/category/
    # corpus.py) was never actually the mechanism behind the reported
    # truncation -- _render_conditions_body used conditions.partition
    # ("\n") instead (see _split_merged_clauses' own docstring) -- but the
    # question is worth answering directly: would a sentence-boundary
    # heuristic have done any better? MEASURED: no. Terminal punctuation
    # is rare across eu_fip's distinct conditions strings, and where a
    # split WOULD occur it is often a Regulation citation's or a date's
    # own period, not a genuine clause end (see the four measured cases
    # in tests/test_app_ui.py) -- confirming neither a raw newline nor a
    # sentence-boundary heuristic is a confident boundary on this data;
    # only merge_conditions's own numbered marker is.
    import json

    from config import settings
    from src.category.corpus import first_sentence

    with open(settings.REFERENCE_DIR / "eu_fip.json", encoding="utf-8") as f:
        eu_fip = json.load(f)
    distinct = {row["conditions"] for row in eu_fip if row.get("conditions")}
    assert len(distinct) > 1000  # sanity: this is the real, full dataset

    would_split = [c for c in distinct if first_sentence(c) != c.strip()]
    # MEASURED: 96 of 1194 (8.0%) -- comfortably under 10%, locked in with
    # margin so a small data update does not make this test flaky.
    assert len(would_split) < len(distinct) * 0.10
