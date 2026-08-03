"""Tests for src/ui/components.py's pure render helpers -- no Streamlit
runtime required for these, since they are plain string/dict functions."""

from src.ui.components import (
    _blocking_reason,
    _conditions_notes,
    _display_name,
    _group_note,
    _is_ancestor_code,
    _out_of_scope_reason,
    _period_of_application_note,
    _permitted_group_key,
    _primary_candidate,
    _short_category_name,
    _substitute_flag_label,
    _verdict_caveats,
    _verdict_label,
    count_buckets,
    verdict_strip_html,
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


def test_single_candidate_renders_one_block_no_divergence():
    item = {
        "eu_canonical_id": "300",
        "component_label": None,
        "flags": ["category_confirmed_by_user"],
        "confirmed_fcs_code": "1.5",
        "by_category": [_candidate("1.5", "permitted_qs")],
    }
    html_out = verdict_strip_html(item, "Ascorbic acid")
    assert html_out.count("eu-block'") == 1
    assert "eu-divergence" not in html_out
    assert "confirmed" in html_out  # the confirmed tag


def test_agreeing_candidates_collapse_to_one_block():
    """Where all candidates agree, render one block and omit the
    divergence line -- even when there is more than one candidate."""
    item = {
        "eu_canonical_id": "300",
        "component_label": None,
        "flags": [],
        "confirmed_fcs_code": None,
        "by_category": [
            _candidate("1.5", "permitted_qs"),
            _candidate("7.2", "permitted_qs"),
        ],
    }
    html_out = verdict_strip_html(item, "Ascorbic acid")
    assert html_out.count("eu-block'") == 1
    assert "eu-divergence" not in html_out


def test_divergent_candidates_show_all_blocks_confirmed_and_dimmed():
    """MEASURED PROBLEM this covers: E551 permitted under the confirmed
    12.2.2, not permitted under 15.1 -- the user must see both."""
    item = {
        "eu_canonical_id": "551",
        "component_label": None,
        "flags": ["category_confirmed_by_user"],
        "confirmed_fcs_code": "12.2.2",
        "by_category": [
            _candidate("12.2.2", "permitted_with_conditions"),
            _candidate("12.1.1", "permitted_with_conditions"),
            _candidate("12.1", "not_permitted_in_category"),
        ],
    }
    html_out = verdict_strip_html(item, "Silicon dioxide")

    assert html_out.count("eu-block'") == 3
    assert "eu-divergence" in html_out
    assert "verdict depends on category" in html_out
    # The confirmed block: outlined/full-colour, tagged, never dimmed.
    assert "data-bucket='permitted' data-confirmed='true'>" in html_out
    assert "<span class='eu-code'>12.2.2</span>" in html_out
    # The other two: dimmed, not tagged confirmed.
    assert html_out.count("data-dimmed='true'") == 2
    assert html_out.count("eu-block-confirmed-tag") == 1


def test_unconfirmed_multi_candidate_strip_has_no_dimming():
    """No confirmed_fcs_code at all (e.g. a preview-only render) -- nothing
    is "the" answer yet, so nothing should be dimmed relative to it."""
    item = {
        "eu_canonical_id": "551",
        "component_label": None,
        "flags": ["category_unconfirmed"],
        "confirmed_fcs_code": None,
        "by_category": [
            _candidate("12.2.2", "permitted_with_conditions"),
            _candidate("12.1", "not_permitted_in_category"),
        ],
    }
    html_out = verdict_strip_html(item, "Silicon dioxide")
    assert "data-dimmed" not in html_out
    assert "eu-block-confirmed-tag" not in html_out


def test_duplicate_fcs_codes_are_deduped():
    item = {
        "eu_canonical_id": "551",
        "component_label": None,
        "flags": ["category_confirmed_by_user"],
        "confirmed_fcs_code": "12.2.2",
        "by_category": [
            _candidate("12.2.2", "permitted_qs"),
            _candidate("12.2.2", "permitted_qs"),  # same code from the confirmed-candidate merge
        ],
    }
    html_out = verdict_strip_html(item, "Silicon dioxide")
    assert html_out.count("eu-block'") == 1


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
    assert "flavouring" in _out_of_scope_reason({"flags": ["governing_regulation: Reg 1334/2008"]})
    assert "enzyme" in _out_of_scope_reason({"flags": ["governing_regulation: Reg 1332/2008"]})
    assert "food ingredient" in _out_of_scope_reason({"flags": ["governing_regulation: not an additive"]})
    assert _out_of_scope_reason({"flags": []}) == "not an additive"


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


def test_khusmain_real_shape_ancestor_candidates_render_as_parent_not_divergence():
    # THE REAL CASE: Khusmain's three retrieved candidates were 14.1.4,
    # 14.1 and 14 -- not alternatives, 14.1.4 is INSIDE 14.1 is INSIDE 14.
    # eu_fip has no row for the parents, so they would otherwise render
    # "not permitted in this category": true, meaningless, and it falsely
    # claims a divergence that does not exist.
    item = {
        "eu_canonical_id": "330",
        "component_label": None,
        "flags": ["category_confirmed_by_user"],
        "confirmed_fcs_code": "14.1.4",
        "by_category": [
            _candidate("14.1.4", "permitted_with_conditions", category_name="Flavoured drinks"),
            _candidate("14.1", "not_permitted_in_category", category_name="Non-alcoholic beverages"),
            _candidate("14", "not_permitted_in_category", category_name="Beverages"),
        ],
    }
    html_out = verdict_strip_html(item, "Citric acid")

    # One real block (the confirmed one) -- no competing verdict.
    assert "verdict depends on category" not in html_out
    assert "eu-divergence" not in html_out
    assert "not permitted in this category" not in html_out
    # The parents are still present, just relabelled and unstyled.
    assert html_out.count("parent category") == 2
    assert "<span class='eu-code'>14.1</span>" in html_out
    assert "<span class='eu-code'>14</span>" in html_out
    assert "Flavoured drinks" in html_out  # the confirmed block's category name
    assert "confirmed" in html_out


def test_confirmed_block_shows_category_name_alongside_code():
    item = {
        "eu_canonical_id": "551",
        "component_label": None,
        "flags": ["category_confirmed_by_user"],
        "confirmed_fcs_code": "12.2.2",
        "by_category": [_candidate("12.2.2", "permitted_qs", category_name="Seasonings and condiments")],
    }
    html_out = verdict_strip_html(item, "Silicon dioxide")
    assert "eu-block-category" in html_out
    assert "Seasonings and condiments" in html_out


def test_genuine_divergence_between_non_ancestor_candidates_still_shown():
    # Regression guard: the ancestor fix must not suppress a REAL
    # divergence between two candidates that are not in the same branch
    # (Chipsmain's E551: permitted under 12.2.2, not under 15.1).
    item = {
        "eu_canonical_id": "551",
        "component_label": None,
        "flags": ["category_confirmed_by_user"],
        "confirmed_fcs_code": "12.2.2",
        "by_category": [
            _candidate("12.2.2", "permitted_with_conditions", category_name="Seasonings"),
            _candidate("15.1", "not_permitted_in_category", category_name="Savoury snacks"),
        ],
    }
    html_out = verdict_strip_html(item, "Silicon dioxide")
    assert "verdict depends on category" in html_out
    assert html_out.count("eu-block'") == 2
    assert "parent category" not in html_out


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


def test_verdict_label_plain_english_wording():
    assert _verdict_label("permitted_with_conditions") == ("Allowed — conditions to check", "permitted")
    assert _verdict_label("not_permitted_in_category") == ("Not allowed in this kind of food", "blocked")
    assert _verdict_label("not_authorised_eu") == ("Not authorised in the EU", "blocked")
    assert _verdict_label("permitted_qs") == ("Allowed — no fixed limit", "permitted")


def test_verdict_label_with_limit_includes_the_actual_level():
    label, bucket = _verdict_label("permitted_with_limit", 5000.0)
    assert label == "Allowed — up to 5000 mg/kg"
    assert bucket == "permitted"


def test_verdict_label_with_limit_missing_level_falls_back():
    label, _bucket = _verdict_label("permitted_with_limit", None)
    assert label == "Allowed — no fixed limit"


def test_permitted_group_key_same_for_items_sharing_the_whole_block():
    item_a = {
        "component_label": "SEASONING",
        "by_category": [_candidate("5.1", "permitted_with_conditions", conditions="Group I text", category_name="Cocoa")],
        "confirmed_fcs_code": "5.1",
    }
    item_b = {
        "component_label": "SEASONING",
        "by_category": [_candidate("5.1", "permitted_with_conditions", conditions="Group I text", category_name="Cocoa")],
        "confirmed_fcs_code": "5.1",
    }
    assert _permitted_group_key(item_a) == _permitted_group_key(item_b)


def test_permitted_group_key_differs_when_conditions_differ():
    item_a = {
        "component_label": "SEASONING",
        "by_category": [_candidate("5.1", "permitted_with_limit", conditions=None, max_level_mg_kg=5000.0, category_name="Cocoa")],
        "confirmed_fcs_code": "5.1",
    }
    item_b = {
        "component_label": "SEASONING",
        "by_category": [_candidate("5.1", "permitted_with_conditions", conditions="Group I text", category_name="Cocoa")],
        "confirmed_fcs_code": "5.1",
    }
    assert _permitted_group_key(item_a) != _permitted_group_key(item_b)
