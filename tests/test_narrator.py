"""Tests for src/report/narrator.py -- hand-built ProductVerdict, mocked
model calls, no network."""

import json

from src.horizon.schemas import HorizonResult
from src.report.narrator import narrate
from src.rules.schemas import CategoryVerdict, ItemVerdict, ProductVerdict
from src.substitutes.schemas import SubstituteResult


def _verdict() -> ProductVerdict:
    item = ItemVerdict(
        item_id=1,
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
                note_codes=[],
                source_url="https://ec.europa.eu/food/food-feed-portal/screen/food-additives",
                retrieved_date="2011-12-01",
            )
        ],
        headline="permitted_with_conditions",
        category_sensitive=False,
        verdict_certainty="certain",
        flags=["category_confirmed_by_user"],
    )
    return ProductVerdict(
        items=[item],
        blocking=[],
        category_conflict=[],
        review_required=[],
        category_sensitive_items=[],
        summary="1 item(s) evaluated. No item was found not permitted.",
        category_used={"Seasoning": "12.2.2"},
        category_source={"Seasoning": "user"},
        warnings=[],
        data_version="test",
    )


def _substitutes() -> SubstituteResult:
    return SubstituteResult(suggestions=[], warnings=[])


def _horizon() -> HorizonResult:
    return HorizonResult(
        signals=[],
        checked_ids=["551"],
        warnings=["Coverage is PARTIAL: ..."],
        data_version="test",
        data_retrieved=None,
    )


def test_invented_e_number_lands_in_unfaithful_claims(monkeypatch):
    fake_response = json.dumps(
        {
            "summary": (
                "Silicon dioxide (E551) is permitted with conditions under food category 12.2.2. "
                "Sucralose (E955) was also reviewed and found permitted."
            ),
            "detail": {"What is permitted": ["E551 carries a maximum level of 20000 mg/kg."]},
        }
    )
    monkeypatch.setattr("src.report.narrator._call_model", lambda prompt, model_id: fake_response)

    result = narrate(_verdict(), _substitutes(), _horizon(), "fake-model")

    assert result.model_id == "fake-model"
    assert any("955" in claim for claim in result.unfaithful_claims)
    # The real E-number and the real level must NOT be flagged.
    assert not any("551" in claim for claim in result.unfaithful_claims)
    assert not any("20000" in claim for claim in result.unfaithful_claims)


def test_invented_category_code_lands_in_unfaithful_claims(monkeypatch):
    fake_response = json.dumps(
        {
            "summary": "This item is permitted under category 12.2.2.",
            "detail": {"What is permitted": ["It is also cleared under category 99.9.9, which does not exist here."]},
        }
    )
    monkeypatch.setattr("src.report.narrator._call_model", lambda prompt, model_id: fake_response)

    result = narrate(_verdict(), _substitutes(), _horizon(), "fake-model")

    assert any("99.9.9" in claim for claim in result.unfaithful_claims)
    assert not any("12.2.2" in claim for claim in result.unfaithful_claims)


def test_faithful_narration_has_no_unfaithful_claims(monkeypatch):
    fake_response = json.dumps(
        {
            "summary": "Silicon dioxide (E551) is permitted with conditions under category 12.2.2.",
            "detail": {"What is permitted": ["The maximum level is 20000 mg/kg."]},
        }
    )
    monkeypatch.setattr("src.report.narrator._call_model", lambda prompt, model_id: fake_response)

    result = narrate(_verdict(), _substitutes(), _horizon(), "fake-model")

    assert result.unfaithful_claims == []


def test_detail_renders_as_structured_sections_not_a_stringified_dict(monkeypatch):
    # MEASURED BUG: detail used to be typed `str`, so a model returning a
    # JSON object for it (reasonable, given the topic-per-key prompt) got
    # silently coerced via str(dict) -- the screen showed the literal
    # Python repr "{'What is blocked': [...], ...}". Asserting the TYPE
    # here is the regression guard: a str would mean the bug is back.
    fake_response = json.dumps(
        {
            "summary": "Fast Green FCF is not authorised.",
            "detail": {
                "What is blocked": ["Fast Green FCF (INS 143) is not authorised."],
                "What is permitted": ["Silicon dioxide (E551) is permitted with conditions."],
            },
        }
    )
    monkeypatch.setattr("src.report.narrator._call_model", lambda prompt, model_id: fake_response)

    result = narrate(_verdict(), _substitutes(), _horizon(), "fake-model")

    assert isinstance(result.detail, dict)
    assert result.detail["What is blocked"] == ["Fast Green FCF (INS 143) is not authorised."]
    assert result.detail["What is permitted"] == ["Silicon dioxide (E551) is permitted with conditions."]


def test_detail_section_with_a_single_string_is_wrapped_not_dropped(monkeypatch):
    # Tolerated model drift: one string instead of a one-item list under a
    # topic. Coerced, not discarded and not a fallback trigger.
    fake_response = json.dumps(
        {"summary": "No blocking issues found.", "detail": {"What is permitted": "Silicon dioxide is permitted."}}
    )
    monkeypatch.setattr("src.report.narrator._call_model", lambda prompt, model_id: fake_response)

    result = narrate(_verdict(), _substitutes(), _horizon(), "fake-model")

    assert result.model_id == "fake-model"
    assert result.detail == {"What is permitted": ["Silicon dioxide is permitted."]}


def test_detail_as_plain_string_falls_back_to_deterministic_summary(monkeypatch):
    # The OLD shape (a flat markdown string) is now treated as a format
    # break, not silently accepted -- falls back rather than risk
    # re-introducing the stringified-dict bug in the other direction.
    fake_response = json.dumps({"summary": "No blocking issues found.", "detail": "Silicon dioxide is permitted."})
    monkeypatch.setattr("src.report.narrator._call_model", lambda prompt, model_id: fake_response)

    verdict = _verdict()
    result = narrate(verdict, _substitutes(), _horizon(), "fake-model")

    assert result.model_id == "unavailable"
    assert result.summary == verdict.summary


def test_model_failure_falls_back_to_deterministic_summary(monkeypatch):
    def _raise(prompt, model_id):
        raise RuntimeError("network down")

    monkeypatch.setattr("src.report.narrator._call_model", _raise)

    verdict = _verdict()
    result = narrate(verdict, _substitutes(), _horizon(), "fake-model")

    assert result.model_id == "unavailable"
    assert result.summary == verdict.summary
    assert result.unfaithful_claims == []


def test_malformed_json_response_falls_back(monkeypatch):
    monkeypatch.setattr("src.report.narrator._call_model", lambda prompt, model_id: "not json at all")

    verdict = _verdict()
    result = narrate(verdict, _substitutes(), _horizon(), "fake-model")

    assert result.model_id == "unavailable"
    assert result.summary == verdict.summary
