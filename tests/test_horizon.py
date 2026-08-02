"""Tests for src/horizon/lane.py and the substitute advisor's negative
filter -- hand-built dicts, no fixture files, no network."""

from datetime import UTC, datetime

from src.horizon.lane import find_horizon_signals
from src.rules.schemas import CategoryVerdict, ItemVerdict, ProductVerdict
from src.substitutes.advisor import find_substitutes

_THIS_YEAR = datetime.now(UTC).year


def _record(
    eu_canonical_id=None,
    substance_name="Test Substance",
    title="EFSA opinion on test substance",
    stage="efsa_opinion",
    doi=None,
    doi_verified=False,
    year=None,
    note=None,
):
    return {
        "eu_canonical_id": eu_canonical_id,
        "substance_name": substance_name,
        "stage": stage,
        "title": title,
        "doi": doi,
        "doi_verified": doi_verified,
        "year": year,
        "note": note,
    }


def _recent_year(years_ago=1):
    return _THIS_YEAR - years_ago


def test_assessment_inside_recency_window_produces_signal():
    horizon_signals = [_record(eu_canonical_id="E 955", year=_recent_year(1))]
    result = find_horizon_signals(["955"], horizon_signals)
    assert len(result.signals) == 1
    assert result.signals[0].eu_canonical_id == "955"


def test_assessment_outside_recency_window_excluded_but_counted_in_warnings():
    horizon_signals = [_record(eu_canonical_id="E 955", substance_name="Sucralose", year=_recent_year(10))]
    result = find_horizon_signals(["955"], horizon_signals)
    assert result.signals == []
    assert len(result.warnings) == 2  # coverage warning + the exclusion warning
    assert "Sucralose" in result.warnings[1]


def test_affects_verdict_is_always_false():
    horizon_signals = [_record(eu_canonical_id="E 955", year=_recent_year(1))]
    result = find_horizon_signals(["955"], horizon_signals)
    assert result.signals[0].affects_verdict is False


def test_severity_is_always_advisory():
    horizon_signals = [_record(eu_canonical_id="E 955", year=_recent_year(1))]
    result = find_horizon_signals(["955"], horizon_signals)
    assert result.signals[0].severity == "advisory"


def test_e_number_normalisation_matches_all_formats():
    for raw_eu_id in ("E 955", "E955", "955"):
        horizon_signals = [_record(eu_canonical_id=raw_eu_id, year=_recent_year(1))]
        result = find_horizon_signals(["955"], horizon_signals)
        assert len(result.signals) == 1, f"failed to match {raw_eu_id!r}"


def test_name_only_match_is_flagged_matched_by_name():
    horizon_signals = [_record(eu_canonical_id=None, substance_name="Sucralose", year=_recent_year(1))]
    result = find_horizon_signals(["955"], horizon_signals, additive_names={"955": "Sucralose"})
    assert len(result.signals) == 1
    assert "matched_by_name" in result.signals[0].flags


def test_coverage_warning_always_present_even_with_no_matches():
    result = find_horizon_signals(["955"], [])
    assert result.signals == []
    assert len(result.warnings) == 1
    assert "PARTIAL" in result.warnings[0]


def test_unverified_doi_is_flagged():
    horizon_signals = [_record(eu_canonical_id="E 955", year=_recent_year(1), doi=None, doi_verified=False)]
    result = find_horizon_signals(["955"], horizon_signals)
    assert "doi_unverified" in result.signals[0].flags
    assert result.signals[0].doi_verified is False


def test_verified_doi_is_not_flagged():
    horizon_signals = [
        _record(eu_canonical_id="E 955", year=_recent_year(1), doi="10.2903/j.efsa.2026.0001", doi_verified=True)
    ]
    result = find_horizon_signals(["955"], horizon_signals)
    assert "doi_unverified" not in result.signals[0].flags
    assert result.signals[0].doi_verified is True


def _item(item_id, eu_canonical_id, by_category):
    return ItemVerdict(
        item_id=item_id,
        eu_canonical_id=eu_canonical_id,
        additive_name=None,
        component_label=None,
        by_category=by_category,
        headline=by_category[0].verdict if by_category else "not_authorised_eu",
        category_sensitive=False,
        verdict_certainty="certain",
        flags=[],
    )


def _category_verdict(fcs_code, verdict="not_permitted_in_category"):
    return CategoryVerdict(
        fcs_code=fcs_code,
        category_name="Test Category",
        rank=1,
        verdict=verdict,
        max_level_mg_kg=None,
        max_level_basis=None,
        conditions=None,
        note_codes=[],
        source_url=None,
        retrieved_date=None,
    )


def _row(canonical_id, food_category_raw, additive_name="Test Additive"):
    return {
        "canonical_id": canonical_id,
        "additive_name": additive_name,
        "food_category_raw": food_category_raw,
        "status": "permitted",
        "max_level_mg_kg": None,
        "max_level_basis": "gmp",
        "conditions": None,
        "note_codes": [],
        "source_url": "https://example.com",
        "effective_date": "2020-01-01",
    }


def _codex(ins, functional_classes):
    return {
        "ins": ins,
        "parent_ins": None,
        "name": f"Additive {ins}",
        "synonyms": [],
        "functional_classes": functional_classes,
        "purposes": [],
        "is_parent_row": False,
    }


def test_horizon_flagged_candidate_is_flagged_and_ranked_last_not_excluded():
    blocked = _item(1, "160a(i)", by_category=[_category_verdict("14.1.4")])
    verdict = ProductVerdict(
        items=[blocked],
        blocking=[1],
        category_conflict=[],
        review_required=[],
        category_sensitive_items=[],
        summary="x",
        category_used={},
        category_source={},
        warnings=[],
        data_version="test",
    )
    eu_fip = [
        _row("133", "14.1.4 Flavoured drinks", additive_name="Brilliant Blue FCF"),
        _row("140", "14.1.4 Flavoured drinks", additive_name="Chlorophylls"),
    ]
    codex_ins = [_codex("160a(i)", ["Colour"]), _codex("133", ["Colour"]), _codex("140", ["Colour"])]

    result = find_substitutes(verdict, eu_fip, codex_ins, horizon_flagged=frozenset({"133"}))
    candidates = result.suggestions[0].candidates

    ids = [c.eu_canonical_id for c in candidates]
    assert "133" in ids  # not excluded
    assert ids[-1] == "133"  # ranked last
    flagged = next(c for c in candidates if c.eu_canonical_id == "133")
    assert "under_efsa_review" in flagged.flags
