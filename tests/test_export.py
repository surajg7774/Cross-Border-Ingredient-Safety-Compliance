"""Tests for src/report/export.py -- hand-built result objects, no files,
no network."""

import csv
import io
import json

from src.horizon.schemas import HorizonResult, HorizonSignal
from src.report.export import (
    ReportIdentity,
    _blocking_reason,
    _out_of_scope_reason,
    _substitute_flag_label,
    to_csv,
    to_json,
    to_pdf,
)
from src.report.narrator import Narration
from src.rules.schemas import CategoryVerdict, ItemVerdict, ProductVerdict
from src.substitutes.schemas import SubstituteCandidate, SubstituteResult, SubstituteSuggestion


def _blocked_item() -> ItemVerdict:
    return ItemVerdict(
        item_id=1,
        eu_canonical_id="171",
        additive_name="Titanium dioxide",
        component_label=None,
        by_category=[],
        headline="not_authorised_eu",
        category_sensitive=False,
        verdict_certainty="certain",
        flags=["prohibited"],
    )


def _permitted_item() -> ItemVerdict:
    return ItemVerdict(
        item_id=2,
        eu_canonical_id="551",
        additive_name="Silicon dioxide",
        component_label="Seasoning",
        by_category=[
            CategoryVerdict(
                fcs_code="12.2.2",
                category_name="Seasonings and condiments",
                rank=1,
                verdict="permitted_with_conditions",
                max_level_mg_kg=20000,
                max_level_basis="mg/kg",
                conditions="Permitted subject to conditions.",
                note_codes=["18"],
                source_url="https://ec.europa.eu/food/food-feed-portal/screen/food-additives",
                retrieved_date="2011-12-01",
            )
        ],
        headline="permitted_with_conditions",
        category_sensitive=False,
        verdict_certainty="certain",
        flags=["category_confirmed_by_user"],
    )


def _uncitable_item() -> ItemVerdict:
    return ItemVerdict(
        item_id=3,
        eu_canonical_id="300",
        additive_name="Ascorbic acid",
        component_label=None,
        by_category=[
            CategoryVerdict(
                fcs_code="1.5",
                category_name="Dehydrated milk",
                rank=1,
                verdict="permitted_qs",
                max_level_mg_kg=None,
                max_level_basis="gmp",
                conditions=None,
                note_codes=[],
                source_url=None,
                retrieved_date=None,
            )
        ],
        headline="permitted_qs",
        category_sensitive=False,
        verdict_certainty="certain",
        flags=["category_confirmed_by_user", "uncitable_verdict"],
    )


def _unresolved_item() -> ItemVerdict:
    return ItemVerdict(
        item_id=4,
        eu_canonical_id=None,
        additive_name=None,
        component_label=None,
        by_category=[],
        headline="unresolved",
        category_sensitive=False,
        verdict_certainty="certain",
        flags=["candidate: 621", "candidate: 627"],
    )


def _verdict() -> ProductVerdict:
    items = [_blocked_item(), _permitted_item(), _uncitable_item(), _unresolved_item()]
    return ProductVerdict(
        items=items,
        blocking=[1],
        category_conflict=[],
        review_required=[4],
        category_sensitive_items=[],
        summary=(
            "4 item(s) evaluated. 1 item(s) NOT PERMITTED (item_id [1]) -- see flags. "
            "Some additives are permitted only up to a maximum level. This does not clear "
            "the product for export."
        ),
        category_used={"(product)": "1.5", "Seasoning": "12.2.2"},
        category_source={"(product)": "user", "Seasoning": "user"},
        warnings=[],
        data_version="test",
    )


def _substitutes() -> SubstituteResult:
    return SubstituteResult(
        suggestions=[
            SubstituteSuggestion(
                item_id=1,
                blocked_eu_canonical_id="171",
                blocked_name="Titanium dioxide",
                blocked_reason="not_authorised_eu",
                fcs_code="15.1",
                candidates=[
                    SubstituteCandidate(
                        eu_canonical_id="170",
                        additive_name="Calcium carbonate",
                        shared_functional_classes=["Colour"],
                        fcs_code="15.1",
                        verdict="permitted_qs",
                        max_level_mg_kg=None,
                        conditions=None,
                        source_url="https://example.com",
                        flags=[],
                    )
                ],
                no_candidates_reason=None,
            )
        ],
        warnings=[],
    )


def _horizon() -> HorizonResult:
    return HorizonResult(
        signals=[
            HorizonSignal(
                eu_canonical_id="171",
                substance_name="Titanium dioxide",
                stage="efsa_opinion",
                title="Safety assessment of titanium dioxide (E171)",
                publication_date="2021",
                doi=None,
                doi_verified=False,
                source_url=None,
                years_old=5,
                severity="advisory",
                affects_verdict=False,
                flags=["doi_unverified"],
            )
        ],
        checked_ids=["171", "551", "300"],
        warnings=["Coverage is PARTIAL: ... see docs/findings.md F-12."],
        data_version="test",
        data_retrieved="2026-08-01",
    )


def _narration() -> Narration:
    return Narration(
        summary="Titanium dioxide is not authorised as a food additive in the EU.",
        detail={
            "What is blocked": ["Titanium dioxide (E171) is not authorised."],
            "What is permitted": ["Silicon dioxide is permitted with conditions in 12.2.2."],
        },
        model_id="fake-model",
        unfaithful_claims=["E999 (not found in the assessment)"],
    )


def _identity() -> ReportIdentity:
    return ReportIdentity(
        product_name="Chipsmain",
        source="Chipsmain.jpeg",
        category="Product: 15.1 (Savoury snacks); Seasoning: 12.2.2 (Seasonings and condiments)",
        timestamp="2026-08-02 10:00 UTC",
    )


def test_to_json_includes_narration_and_nothing_dropped():
    result = json.loads(to_json(_verdict(), _substitutes(), _horizon(), _narration()))
    assert result["verdict"]["summary"] == _verdict().summary
    assert result["narration"]["model_id"] == "fake-model"
    assert result["narration"]["unfaithful_claims"] == ["E999 (not found in the assessment)"]
    assert result["substitutes"]["suggestions"][0]["blocked_eu_canonical_id"] == "171"
    assert result["horizon"]["signals"][0]["eu_canonical_id"] == "171"
    assert "agent_review" not in result  # omitted entirely, not null/{}, when nothing was asked


def test_to_json_includes_agent_review_when_provided():
    # The review-queue assistant's full trace (tool_calls, evidence,
    # reasoning) lives only in app.py's session state -- app.py builds and
    # passes this dict explicitly; to_json itself never reaches into
    # session state. JSON-only: see to_json's own docstring for why CSV/PDF
    # deliberately do not receive this.
    agent_review = {
        4: {
            "decision": "accepted",
            "proposed_classification": "additive",
            "confidence": "high",
            "evidence": ["list_family_members returned 3 candidates"],
            "tool_calls": [{"tool": "list_family_members", "args": {}, "result_summary": "3 found"}],
            "declined": False,
            "decline_reason": None,
        }
    }
    result = json.loads(to_json(_verdict(), _substitutes(), _horizon(), _narration(), agent_review=agent_review))
    assert result["agent_review"]["4"]["decision"] == "accepted"
    assert result["agent_review"]["4"]["tool_calls"][0]["tool"] == "list_family_members"
    assert result["agent_review"]["4"]["evidence"] == ["list_family_members returned 3 candidates"]


def test_to_csv_has_one_row_per_item_including_unresolved():
    rows = list(csv.DictReader(io.StringIO(to_csv(_verdict()))))
    assert len(rows) == 4
    by_id = {row["eu_id"]: row for row in rows if row["eu_id"]}
    assert by_id["551"]["category"] == "12.2.2"
    assert by_id["551"]["verdict"] == "permitted_with_conditions"
    assert by_id["551"]["level_mg_kg"] == "20000.0"
    assert by_id["551"]["source_url"] == "https://ec.europa.eu/food/food-feed-portal/screen/food-additives"
    assert by_id["551"]["component"] == "Seasoning"
    # Unresolved item has no eu_id -- still present as its own row.
    unresolved_rows = [row for row in rows if row["verdict"] == "unresolved"]
    assert len(unresolved_rows) == 1


def test_to_csv_without_identity_has_no_comment_rows():
    text = to_csv(_verdict())
    assert not text.startswith("#")
    assert text.splitlines()[0].startswith("item,eu_id,component")


def test_to_csv_with_identity_writes_leading_comment_rows():
    text = to_csv(_verdict(), _identity())
    lines = text.splitlines()
    assert lines[0] == "# Product: Chipsmain"
    assert lines[1] == "# Source: Chipsmain.jpeg"
    assert lines[2] == "# Category: Product: 15.1 (Savoury snacks); Seasoning: 12.2.2 (Seasonings and condiments)"
    assert lines[3] == "# Generated: 2026-08-02 10:00 UTC"
    assert lines[4] == "item,eu_id,component,category,verdict,level_mg_kg,conditions,source_url,flags"


def test_blocking_reason_distinguishes_prohibited_from_absent():
    prohibited = _blocked_item()  # flags=["prohibited"]
    assert _blocking_reason(prohibited).startswith("prohibited")

    absent = ItemVerdict(
        item_id=8, eu_canonical_id=None, additive_name=None, component_label=None, by_category=[],
        headline="not_authorised_eu", category_sensitive=False, verdict_certainty="certain",
        flags=["not_in_eu_list"],
    )
    assert _blocking_reason(absent).startswith("not authorised")


def test_out_of_scope_reason_maps_governing_regulation():
    flavouring = ItemVerdict(
        item_id=9, eu_canonical_id=None, additive_name=None, component_label=None, by_category=[],
        headline="out_of_scope", category_sensitive=False, verdict_certainty="certain",
        flags=["governing_regulation: Reg 1334/2008"],
    )
    assert "flavouring" in _out_of_scope_reason(flavouring)

    enzyme = flavouring.model_copy(update={"flags": ["governing_regulation: Reg 1332/2008"]})
    assert "enzyme" in _out_of_scope_reason(enzyme)

    ingredient = flavouring.model_copy(update={"flags": ["governing_regulation: not an additive"]})
    assert "food ingredient" in _out_of_scope_reason(ingredient)


def test_substitute_flag_label_translates_known_flags_and_passes_through_unknown():
    assert _substitute_flag_label("classes_from_subtypes") == "function inferred from sub-types"
    assert _substitute_flag_label("adds_labelling_obligation") == "requires a warning label"
    assert _substitute_flag_label("under_efsa_review") == "under EFSA review"
    assert _substitute_flag_label("some_future_flag") == "some_future_flag"


def test_to_pdf_produces_valid_pdf_bytes():
    pdf_bytes = to_pdf(_verdict(), _substitutes(), _horizon(), _narration(), _identity())
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 500


def test_to_pdf_works_without_identity():
    pdf_bytes = to_pdf(_verdict(), _substitutes(), _horizon(), _narration())
    assert pdf_bytes.startswith(b"%PDF-")
