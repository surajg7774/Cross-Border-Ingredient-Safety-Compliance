"""Tests for src/ui/components.py's pure render helpers -- no Streamlit
runtime required for these, since they are plain string/dict functions."""

from src.ui.components import (
    _additive_row,
    _blocking_reason,
    _conditions_notes,
    _display_name,
    _group_note,
    _max_amount_cell,
    _order_news_signals,
    _out_of_scope_reason,
    _period_of_application_note,
    _primary_candidate,
    _shared_family_word,
    _short_category_name,
    _status_label,
    _substitute_flag_label,
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


def test_candidates_phrase_names_the_family_for_many_real_candidates():
    # MEASURED (data/reference/codex_ins.json): the real 17 candidates
    # behind "MODIFIED CORNSTARCH" -- a code range ("E1400-E1452") says
    # what these are numbered, not what they are; the shared family word
    # from their real names says what they are.
    codes = [
        "1400", "1401", "1402", "1403", "1404", "1405", "1410", "1412", "1413",
        "1414", "1420", "1422", "1440", "1442", "1450", "1451", "1452",
    ]
    ins_names = {
        "1400": "Dextrins, roasted starch",
        "1401": "Acid-treated starch",
        "1402": "Alkaline-treated starch",
        "1403": "Bleached starch",
        "1404": "Oxidized starch",
        "1405": "Starches, enzyme treated",
        "1410": "Monostarch phosphate",
        "1412": "Distarch phosphate",
        "1413": "Phosphated distarch phosphate",
        "1414": "Acetylated distarch phosphate",
        "1420": "Starch acetate",
        "1422": "Acetylated distarch adipate",
        "1440": "Hydroxypropyl starch",
        "1442": "Hydroxypropyl distarch phosphate",
        "1450": "Starch sodium octenyl succinate",
        "1451": "Acetylated oxidized starch",
        "1452": "Starch aluminium octenyl succinate",
    }
    assert candidates_phrase(codes, ins_names) == "17 different starches"


def test_candidates_phrase_falls_back_to_plain_count_with_no_names():
    # No ins_names available -- there is nothing to derive a family word
    # from, but the count alone is still never a code range.
    codes = ["1400", "1401", "1402", "1403", "1404", "1405", "1410"]
    assert candidates_phrase(codes, {}) == "7 possible matches"


def test_shared_family_word_ignores_short_incidental_matches():
    # "Starch acetate" and "Acid-treated starch" share "starch" (>= 4
    # letters) but would also share short incidental substrings if the
    # length floor were removed -- the longest qualifying match wins.
    assert _shared_family_word(["Starch acetate", "Acid-treated starch"]) == "starch"


def test_shared_family_word_none_when_names_share_nothing():
    assert _shared_family_word(["Curcumin", "Riboflavin"]) is None


def test_candidates_phrase_empty_for_no_candidates():
    assert candidates_phrase([], {}) == ""


def test_substitute_flag_label_translates_known_flags_and_passes_through_unknown():
    assert _substitute_flag_label("classes_from_subtypes") == "function inferred from sub-types"
    assert _substitute_flag_label("adds_labelling_obligation") == "requires a warning label"
    assert _substitute_flag_label("under_efsa_review") == "under EFSA review"
    assert _substitute_flag_label("some_future_flag") == "some_future_flag"



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


def test_additive_row_where_shows_component_not_category_when_both_exist():
    # MEASURED BUG this replaces: "Seasoning: Seasonings and condiments
    # (12.2.2)" showed both, near-duplicates of each other -- component
    # wins outright, the category name/code do not appear at all.
    item = {
        "eu_canonical_id": "551",
        "additive_name": "Silicon dioxide",
        "component_label": "SEASONING",
        "confirmed_fcs_code": "12.2.2",
        "flags": [],
        "by_category": [_candidate("12.2.2", "permitted_qs", category_name="Seasonings and condiments")],
    }
    row = _additive_row(item, None)
    assert row["where"] == "SEASONING"


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


def test_first_sentence_style_split_would_barely_touch_real_eu_fip_conditions_text():
    # MEASURED: terminal punctuation is rare across eu_fip's distinct
    # conditions strings, and where a split WOULD occur it is often a
    # Regulation citation's or a date's own period, not a genuine clause
    # end -- a sentence-boundary heuristic would not have been a reliable
    # way to find a clause boundary in this data (the conditions-splitting
    # machinery this originally documented has since been removed along
    # with the results screen's per-additive detail; the underlying
    # measurement is still worth keeping as a locked-in fact about the
    # reference data).
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
