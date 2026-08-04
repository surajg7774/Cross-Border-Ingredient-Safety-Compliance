"""Tests for src/horizon/news.py -- no network, no key. classify_and_quote
and build_news_signal are covered via a monkeypatched _call_model, the
same pattern tests/test_narrator.py uses for src/report/narrator.py;
nothing here calls a real model."""

import json

import pytest
from pydantic import ValidationError

from src.horizon import news as news_module
from src.horizon.news import (
    build_news_signal,
    check_modality_backstop,
    classify_and_quote,
    is_relevant,
    render_news_sentence,
)
from src.horizon.schemas import NewsSignal
from src.horizon.search import SearchResult


def _result(title="EFSA opens review of Titanium dioxide", content="The panel will assess safety data.", **overrides):
    base = {
        "title": title,
        "url": "https://efsa.europa.eu/example",
        "content": content,
        "published_date": "2026-06-01",
        "score": 0.9,
        "flags": [],
    }
    base.update(overrides)
    return SearchResult(**base)


def _mock_response(category, quoted_span):
    return json.dumps({"category": category, "quoted_span": quoted_span})


# --------------------------------------------------------------------------- #
# is_relevant -- pure, no network
# --------------------------------------------------------------------------- #
def test_is_relevant_true_when_name_appears_in_title():
    result = _result(title="EFSA opens review of Titanium dioxide")
    assert is_relevant(result, "Titanium dioxide") is True


def test_is_relevant_true_when_stem_word_appears_in_content_only():
    result = _result(title="Regulatory update", content="Titanium dioxide safety data under review.")
    assert is_relevant(result, "Titanium dioxide") is True


def test_is_relevant_false_when_neither_title_nor_content_mentions_it():
    result = _result(title="EU updates sugar tax rules", content="No mention of the substance at all.")
    assert is_relevant(result, "Titanium dioxide") is False


def test_is_relevant_short_name_falls_back_to_exact_whole_name_match():
    # "gum" has no word >= 4 chars, so relevance requires the exact name,
    # not a substring of some longer unrelated word.
    result = _result(title="Chewing gum additives reviewed", content="")
    assert is_relevant(result, "gum") is True

    result2 = _result(title="Legume prices rise", content="")
    assert is_relevant(result2, "gum") is False  # "legume" contains "gum" as a substring, not a real match


# --------------------------------------------------------------------------- #
# classify_and_quote -- monkeypatched model call, no network
# --------------------------------------------------------------------------- #
def test_classify_and_quote_accepts_a_verbatim_quote(monkeypatch):
    result = _result(title="EFSA opens review of Titanium dioxide")
    monkeypatch.setattr(
        news_module, "_call_model", lambda prompt, model_id: _mock_response("regulatory_review", "EFSA opens review")
    )

    classified = classify_and_quote(result, "Titanium dioxide", "fake-model")

    assert classified == ("regulatory_review", "EFSA opens review")


def test_classify_and_quote_rejects_a_non_verbatim_quote(monkeypatch):
    result = _result(title="EFSA opens review of Titanium dioxide")
    monkeypatch.setattr(
        news_module,
        "_call_model",
        lambda prompt, model_id: _mock_response("regulatory_review", "EFSA may soon ban this additive"),
    )

    assert classify_and_quote(result, "Titanium dioxide", "fake-model") is None


def test_classify_and_quote_returns_none_for_unrelated(monkeypatch):
    result = _result()
    monkeypatch.setattr(news_module, "_call_model", lambda prompt, model_id: _mock_response("unrelated", ""))
    assert classify_and_quote(result, "Titanium dioxide", "fake-model") is None


def test_classify_and_quote_returns_none_for_unrecognised_category(monkeypatch):
    result = _result()
    monkeypatch.setattr(
        news_module, "_call_model", lambda prompt, model_id: _mock_response("catastrophic", "EFSA opens review")
    )
    assert classify_and_quote(result, "Titanium dioxide", "fake-model") is None


def test_classify_and_quote_returns_none_for_empty_quoted_span(monkeypatch):
    result = _result()
    monkeypatch.setattr(
        news_module, "_call_model", lambda prompt, model_id: _mock_response("regulatory_review", "")
    )
    assert classify_and_quote(result, "Titanium dioxide", "fake-model") is None


def test_classify_and_quote_returns_none_on_malformed_json(monkeypatch):
    result = _result()
    monkeypatch.setattr(news_module, "_call_model", lambda prompt, model_id: "not json at all")
    assert classify_and_quote(result, "Titanium dioxide", "fake-model") is None


def test_classify_and_quote_returns_none_on_model_failure(monkeypatch):
    def _raise(prompt, model_id):
        raise RuntimeError("network down")

    monkeypatch.setattr(news_module, "_call_model", _raise)
    assert classify_and_quote(_result(), "Titanium dioxide", "fake-model") is None


def test_classify_and_quote_tolerates_whitespace_differences(monkeypatch):
    result = _result(title="EFSA opens\nreview of Titanium dioxide", content="")
    monkeypatch.setattr(
        news_module,
        "_call_model",
        lambda prompt, model_id: _mock_response("regulatory_review", "EFSA opens review of Titanium dioxide"),
    )
    assert classify_and_quote(result, "Titanium dioxide", "fake-model") is not None


def test_classify_and_quote_is_case_sensitive(monkeypatch):
    result = _result(title="efsa opens review", content="")
    monkeypatch.setattr(
        news_module, "_call_model", lambda prompt, model_id: _mock_response("regulatory_review", "EFSA Opens Review")
    )
    assert classify_and_quote(result, "Titanium dioxide", "fake-model") is None


# --------------------------------------------------------------------------- #
# render_news_sentence -- pure
# --------------------------------------------------------------------------- #
def test_render_news_sentence_includes_quote_url_and_date():
    result = _result(published_date="2026-06-01")
    sentence = render_news_sentence("Titanium dioxide", "regulatory_review", "EFSA opens review", result)
    assert "EFSA opens review" in sentence
    assert result.url in sentence
    assert "2026-06-01" in sentence
    assert "Under regulatory review" in sentence


def test_render_news_sentence_omits_date_when_absent():
    result = _result(published_date=None)
    sentence = render_news_sentence("Titanium dioxide", "regulatory_review", "EFSA opens review", result)
    assert "None" not in sentence


# --------------------------------------------------------------------------- #
# check_modality_backstop -- pure, sibling to narrator.py's _faithfulness_check
# --------------------------------------------------------------------------- #
def test_modality_backstop_allows_a_word_present_in_the_source():
    sentence = 'Titanium dioxide: Referenced in a safety opinion -- "the substance is banned in some markets" (url).'
    source = "The panel noted the substance is banned in some markets already."
    assert check_modality_backstop(sentence, source) == []


def test_modality_backstop_blocks_a_word_absent_from_the_source():
    sentence = "Titanium dioxide: this additive may soon be banned in the EU."
    source = "EFSA opened a routine review of titanium dioxide."
    assert "banned" in check_modality_backstop(sentence, source)


def test_modality_backstop_no_violations_when_no_modality_words_present():
    sentence = "Titanium dioxide: Under regulatory review."
    source = "EFSA opened a routine review."
    assert check_modality_backstop(sentence, source) == []


def test_modality_backstop_reports_each_violating_word_once():
    sentence = "This additive is banned and also prohibited and banned again."
    source = "Nothing about that here."
    violations = check_modality_backstop(sentence, source)
    assert violations.count("banned") == 1
    assert "prohibited" in violations


# --------------------------------------------------------------------------- #
# build_news_signal -- ties everything together
# --------------------------------------------------------------------------- #
def test_build_news_signal_happy_path(monkeypatch):
    result = _result(flags=["no_published_date"])
    monkeypatch.setattr(
        news_module, "_call_model", lambda prompt, model_id: _mock_response("regulatory_review", "EFSA opens review")
    )

    signal = build_news_signal(result, "171", "Titanium dioxide", "titanium dioxide EU regulation", "2026-08-04", "fake-model")

    assert isinstance(signal, NewsSignal)
    assert signal.eu_canonical_id == "171"
    assert signal.substance_name == "Titanium dioxide"
    assert signal.category == "regulatory_review"
    assert signal.quoted_span == "EFSA opens review"
    assert signal.source_url == result.url
    assert signal.search_query == "titanium dioxide EU regulation"
    assert signal.retrieved_at == "2026-08-04"
    assert signal.flags == ["no_published_date"]  # carried forward from the SearchResult


def test_build_news_signal_none_when_classification_rejects(monkeypatch):
    monkeypatch.setattr(news_module, "_call_model", lambda prompt, model_id: _mock_response("unrelated", ""))
    signal = build_news_signal(_result(), "171", "Titanium dioxide", "q", "2026-08-04", "fake-model")
    assert signal is None


def test_build_news_signal_none_on_modality_backstop_violation(monkeypatch):
    # Force a violation via the FIXED TEMPLATE, not the model's quote --
    # quoted_span is always verified as a substring of the source, so it
    # can never itself introduce a word the source lacks. This proves the
    # WIRING: build_news_signal actually calls the backstop and refuses
    # to build a signal when it fires, exactly as it would if some future
    # change added free wording to a category label.
    monkeypatch.setitem(news_module._CATEGORY_LABELS, "regulatory_review", "This additive may soon be banned")
    result = _result(title="EFSA opens review", content="Routine assessment.")
    monkeypatch.setattr(
        news_module, "_call_model", lambda prompt, model_id: _mock_response("regulatory_review", "EFSA opens review")
    )

    signal = build_news_signal(result, "171", "Titanium dioxide", "q", "2026-08-04", "fake-model")

    assert signal is None


# --------------------------------------------------------------------------- #
# NewsSignal invariants -- tested INDEPENDENTLY of HorizonSignal's own
# (see tests/test_horizon.py's test_affects_verdict_is_always_false and
# test_severity_is_always_advisory for the curated-source equivalents;
# neither file imports the other, so neither guarantee depends on the
# other source's code being correct).
# --------------------------------------------------------------------------- #
def test_news_signal_affects_verdict_is_always_false(monkeypatch):
    monkeypatch.setattr(
        news_module, "_call_model", lambda prompt, model_id: _mock_response("market_action", "banned in Country X")
    )
    result = _result(title="Country X bans additive", content="banned in Country X after review.")
    signal = build_news_signal(result, "171", "Titanium dioxide", "q", "2026-08-04", "fake-model")
    assert signal.affects_verdict is False


def test_news_signal_severity_is_always_advisory(monkeypatch):
    monkeypatch.setattr(
        news_module, "_call_model", lambda prompt, model_id: _mock_response("regulatory_review", "EFSA opens review")
    )
    signal = build_news_signal(_result(), "171", "Titanium dioxide", "q", "2026-08-04", "fake-model")
    assert signal.severity == "advisory"


def test_news_signal_severity_field_rejects_any_other_value():
    with pytest.raises(ValidationError):
        NewsSignal(
            eu_canonical_id="171",
            substance_name="Titanium dioxide",
            category="regulatory_review",
            quoted_span="x",
            source_url="https://example.com",
            published_date=None,
            search_query="q",
            retrieved_at="2026-08-04",
            severity="urgent",
            affects_verdict=False,
            flags=[],
        )


def test_news_signal_category_rejects_unrelated():
    # "unrelated" is a valid classifier OUTPUT but never a valid
    # NewsSignal.category -- see schemas.py's NewsSignal docstring.
    with pytest.raises(ValidationError):
        NewsSignal(
            eu_canonical_id="171",
            substance_name="Titanium dioxide",
            category="unrelated",
            quoted_span="x",
            source_url="https://example.com",
            published_date=None,
            search_query="q",
            retrieved_at="2026-08-04",
            severity="advisory",
            affects_verdict=False,
            flags=[],
        )


def test_news_module_does_not_import_lane_or_construct_horizon_signal():
    # Structural guard for "constructed in THIS schema's own path, not
    # inherited from HorizonSignal's" -- news.py must never import
    # src.horizon.lane (prose in comments/docstrings MAY still mention
    # HorizonSignal by name for explanatory purposes, as this module's own
    # docstring does -- what matters is no import and no constructor call).
    import pathlib
    import re

    source = pathlib.Path("src/horizon/news.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import)\s+src\.horizon\.lane\b", source, re.MULTILINE)
    assert not re.search(r"\bHorizonSignal\(", source)  # never constructed here
