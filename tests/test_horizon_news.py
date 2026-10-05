"""Tests for src/horizon/news.py -- no network, no key. classify_and_quote
and build_news_signal are covered via a monkeypatched _call_model, the
same pattern tests/test_narrator.py uses for src/report/narrator.py;
nothing here calls a real model."""

import json
from datetime import date

import pytest
from pydantic import ValidationError

from src.horizon import news as news_module
from src.horizon.news import (
    MAX_ADDITIVES_PER_RUN,
    MAX_RESULTS_CLASSIFIED_PER_ADDITIVE,
    MAX_RESULTS_CLASSIFIED_PER_ROUTE,
    NEWS_INITIAL_BACKOFF_SECONDS,
    NEWS_MAX_BACKOFF_SECONDS,
    NEWS_MAX_RETRIES,
    build_news_signal,
    build_route_news_signal,
    build_route_query,
    check_category_consistency,
    check_modality_backstop,
    check_route_category_consistency,
    classify_and_quote,
    classify_and_quote_route,
    find_news_signals,
    find_route_news_signals,
    is_relevant,
    meets_score_threshold,
    passes_prefilter,
    render_news_sentence,
    render_route_news_sentence,
)
from src.horizon.schemas import NewsSignal, RouteNewsSignal
from src.horizon.search import FixtureSearchProvider, SearchResult


def _result(title="EFSA opens review of Titanium dioxide", content="The panel will assess safety data.", **overrides):
    base = {
        "title": title,
        "url": "https://efsa.europa.eu/example",
        "content": content,
        "published_date": date(2026, 6, 1),
        "published_date_raw": "Mon, 01 Jun 2026 00:00:00 GMT",
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
# meets_score_threshold / passes_prefilter -- pure, no network
# --------------------------------------------------------------------------- #
def test_meets_score_threshold_true_above_the_minimum():
    result = _result(score=0.6)
    assert meets_score_threshold(result) is True


def test_meets_score_threshold_false_below_the_minimum():
    result = _result(score=0.59)
    assert meets_score_threshold(result) is False


def test_meets_score_threshold_false_when_score_is_none():
    result = _result(score=None)
    assert meets_score_threshold(result) is False


def test_meets_score_threshold_rejects_the_measured_efsa_minutes_case():
    # MEASURED against a live Tavily response (query: "Titanium dioxide"):
    # an EFSA sweeteners working-group minutes PDF scored 0.52 and
    # mentions titanium dioxide only as the reason another agenda item
    # was deprioritised -- on an allowed domain, containing the additive's
    # name, so it survives is_relevant() unchanged. Score is what actually
    # catches it.
    result = _result(
        title="Minutes of the EFSA Working Group on Sweeteners",
        content="Discussion of titanium dioxide was deprioritised in favour of other agenda items.",
        url="https://efsa.europa.eu/sites/default/files/wg-sweeteners-minutes.pdf",
        score=0.52,
    )
    assert is_relevant(result, "Titanium dioxide") is True  # passes keyword relevance...
    assert meets_score_threshold(result) is False  # ...but fails the score threshold
    assert passes_prefilter(result, "Titanium dioxide") is False  # so the combined gate rejects it


def test_passes_prefilter_true_when_both_checks_pass():
    result = _result(title="EFSA opens review of Titanium dioxide", score=0.9)
    assert passes_prefilter(result, "Titanium dioxide") is True


def test_passes_prefilter_false_when_only_relevance_passes():
    result = _result(title="EFSA opens review of Titanium dioxide", score=0.3)
    assert passes_prefilter(result, "Titanium dioxide") is False


def test_passes_prefilter_false_when_only_score_passes():
    result = _result(title="Unrelated sugar tax update", content="", score=0.9)
    assert passes_prefilter(result, "Titanium dioxide") is False


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


def test_classify_and_quote_rejects_the_measured_e968_case(monkeypatch):
    # MEASURED against a live retrieval (.cache/horizon_news_cache.json,
    # additive 968): the model badged "regulatory_review" while quoting
    # its own source's "Re-evaluation completed in 2023" -- a table row
    # that says the review is DONE. The quote is a real verbatim
    # substring (so the verbatim check alone would accept it); this is
    # exactly the pair check_category_consistency exists to catch.
    result = _result(
        title="Sweeteners | EFSA - European Union",
        content="| E 968 | Erythritol | Re-evaluation completed in 2023 as a food additive |",
    )
    monkeypatch.setattr(
        news_module,
        "_call_model",
        lambda prompt, model_id: _mock_response("regulatory_review", "Re-evaluation completed in 2023"),
    )
    assert classify_and_quote(result, "Erythritol", "fake-model") is None


# --------------------------------------------------------------------------- #
# check_category_consistency -- pure
# --------------------------------------------------------------------------- #
def test_category_consistency_rejects_regulatory_review_quoting_completion():
    assert check_category_consistency("regulatory_review", "Re-evaluation completed in 2023") is False
    assert check_category_consistency("regulatory_review", "EFSA adopted its opinion") is False


def test_category_consistency_rejects_safety_opinion_quoting_ongoing():
    assert check_category_consistency("safety_opinion", "The re-evaluation is ongoing") is False
    assert check_category_consistency("safety_opinion", "Review still underway") is False


def test_category_consistency_allows_matching_pairs():
    assert check_category_consistency("regulatory_review", "Re-evaluation ongoing") is True
    assert check_category_consistency("safety_opinion", "EFSA concludes it is safe") is True


def test_category_consistency_does_not_judge_other_categories():
    # market_action/consumer_alert carry no process-vs-output ambiguity --
    # a completion/ongoing word in their quote is not a contradiction.
    assert check_category_consistency("market_action", "Re-evaluation completed in 2023") is True
    assert check_category_consistency("consumer_alert", "Review still ongoing") is True


# --------------------------------------------------------------------------- #
# render_news_sentence -- pure
# --------------------------------------------------------------------------- #
def test_render_news_sentence_includes_quote_url_and_date():
    result = _result(published_date=date(2026, 6, 1))
    sentence = render_news_sentence("Titanium dioxide", "regulatory_review", "EFSA opens review", result)
    assert "EFSA opens review" in sentence
    assert result.url in sentence
    assert "2026-06-01" in sentence
    assert "Under regulatory review" in sentence


def test_render_news_sentence_omits_date_when_absent():
    result = _result(published_date=None, published_date_raw=None)
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


# --------------------------------------------------------------------------- #
# find_news_signals -- FixtureSearchProvider or a counting test double, never
# a real network call. classify_and_quote calls are monkeypatched throughout.
# --------------------------------------------------------------------------- #
class _CountingProvider:
    """A SearchProvider that records every query it was asked and can be
    told to raise for specific ones -- for testing caps and failure
    handling precisely, which FixtureSearchProvider's simpler contract
    does not support."""

    def __init__(self, results_by_query=None, raise_for=()):
        self.results_by_query = results_by_query or {}
        self.raise_for = set(raise_for)
        self.calls: list[str] = []

    def search(self, query, *, include_domains=None, days=None):
        self.calls.append(query)
        if query in self.raise_for:
            raise RuntimeError("provider down")
        return list(self.results_by_query.get(query, []))


def test_find_news_signals_returns_empty_unchanged_when_provider_is_none():
    cache = {"171": "sentinel"}
    signals, updated_cache = find_news_signals(["171"], None, cache, "fake-model", date(2026, 8, 4))
    assert signals == []
    assert updated_cache is cache


def test_find_news_signals_happy_path(monkeypatch):
    query = news_module._build_query("Titanium dioxide")
    provider = FixtureSearchProvider({query: [_result(title="EFSA opens review of Titanium dioxide")]})
    monkeypatch.setattr(
        news_module, "_call_model", lambda prompt, model_id: _mock_response("regulatory_review", "EFSA opens review")
    )

    signals, updated_cache = find_news_signals(
        ["171"], provider, {}, "fake-model", date(2026, 8, 4), additive_names={"171": "Titanium dioxide"}
    )

    assert len(signals) == 1
    assert signals[0].eu_canonical_id == "171"
    assert signals[0].substance_name == "Titanium dioxide"
    assert signals[0].search_query == query
    assert signals[0].retrieved_at == "2026-08-04"
    assert "171" in updated_cache  # cache was updated on the miss


def test_find_news_signals_falls_back_to_additive_id_when_name_missing(monkeypatch):
    query = news_module._build_query("171")
    provider = FixtureSearchProvider({query: [_result(title="171 opens review")]})
    monkeypatch.setattr(
        news_module, "_call_model", lambda prompt, model_id: _mock_response("regulatory_review", "171 opens review")
    )

    signals, _cache = find_news_signals(["171"], provider, {}, "fake-model", date(2026, 8, 4))

    assert len(signals) == 1
    assert signals[0].substance_name == "171"


def test_find_news_signals_uses_cache_on_second_call_no_new_search():
    query = news_module._build_query("Titanium dioxide")
    provider = _CountingProvider({query: [_result()]})

    _signals1, cache_after_first = find_news_signals(
        ["171"], provider, {}, "fake-model", date(2026, 8, 4), additive_names={"171": "Titanium dioxide"}
    )
    assert len(provider.calls) == 1

    find_news_signals(
        ["171"], provider, cache_after_first, "fake-model", date(2026, 8, 4), additive_names={"171": "Titanium dioxide"}
    )
    assert len(provider.calls) == 1  # second call served entirely from cache -- no new search


def test_find_news_signals_caps_additives_searched_per_run():
    additive_ids = [str(n) for n in range(MAX_ADDITIVES_PER_RUN + 5)]
    provider = _CountingProvider({})

    find_news_signals(additive_ids, provider, {}, "fake-model", date(2026, 8, 4))

    assert len(provider.calls) == MAX_ADDITIVES_PER_RUN


def test_find_news_signals_caps_results_classified_per_additive(monkeypatch):
    query = news_module._build_query("Titanium dioxide")
    many_results = [_result(title=f"EFSA review {i} of Titanium dioxide", url=f"https://efsa.europa.eu/{i}") for i in range(10)]
    provider = FixtureSearchProvider({query: many_results})

    call_count = {"n": 0}

    def _counting_call_model(prompt, model_id):
        call_count["n"] += 1
        return _mock_response("regulatory_review", "EFSA review")

    monkeypatch.setattr(news_module, "_call_model", _counting_call_model)

    signals, _cache = find_news_signals(
        ["171"], provider, {}, "fake-model", date(2026, 8, 4), additive_names={"171": "Titanium dioxide"}
    )

    assert call_count["n"] <= MAX_RESULTS_CLASSIFIED_PER_ADDITIVE
    assert len(signals) <= MAX_RESULTS_CLASSIFIED_PER_ADDITIVE


def test_find_news_signals_filters_out_results_failing_the_prefilter(monkeypatch):
    query = news_module._build_query("Titanium dioxide")
    low_score_result = _result(title="EFSA opens review of Titanium dioxide", score=0.1)
    provider = FixtureSearchProvider({query: [low_score_result]})
    monkeypatch.setattr(
        news_module, "_call_model", lambda prompt, model_id: _mock_response("regulatory_review", "EFSA opens review")
    )

    signals, _cache = find_news_signals(
        ["171"], provider, {}, "fake-model", date(2026, 8, 4), additive_names={"171": "Titanium dioxide"}
    )

    assert signals == []


def test_find_news_signals_skips_additive_on_search_failure_but_continues_the_rest(monkeypatch):
    query_a = news_module._build_query("A")
    query_b = news_module._build_query("B")
    provider = _CountingProvider(
        results_by_query={query_b: [_result(title="B opens review")]}, raise_for={query_a}
    )
    monkeypatch.setattr(
        news_module, "_call_model", lambda prompt, model_id: _mock_response("regulatory_review", "B opens review")
    )

    signals, _cache = find_news_signals(
        ["a", "b"], provider, {}, "fake-model", date(2026, 8, 4), additive_names={"a": "A", "b": "B"}
    )

    assert len(signals) == 1
    assert signals[0].substance_name == "B"


def test_find_news_signals_carries_stale_cache_served_flag(monkeypatch):
    query = news_module._build_query("Titanium dioxide")
    stale_cache = {
        "171": {
            "fetched_at": "2026-01-01",  # long past the 7-day TTL
            "results": [
                {
                    "title": "EFSA opens review of Titanium dioxide",
                    "url": "https://efsa.europa.eu/example",
                    "content": "The panel will assess safety data.",
                    "published_date": "2026-01-01",
                    "published_date_raw": "Thu, 01 Jan 2026 00:00:00 GMT",
                    "score": 0.9,
                    "flags": [],
                }
            ],
        }
    }
    provider = _CountingProvider(raise_for={query})  # provider is down -- forces a stale-serve
    monkeypatch.setattr(
        news_module, "_call_model", lambda prompt, model_id: _mock_response("regulatory_review", "EFSA opens review")
    )

    signals, _cache = find_news_signals(
        ["171"], provider, stale_cache, "fake-model", date(2026, 8, 4), additive_names={"171": "Titanium dioxide"}
    )

    assert len(signals) == 1
    assert "stale_cache_served" in signals[0].flags


# --------------------------------------------------------------------------- #
# _call_model's retry budget -- the news lane is advisory only
# (NewsSignal.affects_verdict is always False), so it gets its OWN,
# deliberately smaller retry budget than the compliance-bearing paths.
# --------------------------------------------------------------------------- #
def test_call_model_passes_the_news_retry_budget_to_the_text_generator(monkeypatch):
    captured: dict = {}

    class _FakeGenerator:
        def complete(self, prompt, model_id, **kwargs):
            captured.update(kwargs)
            return "ok"

    monkeypatch.setattr(news_module, "get_text_generator", lambda: _FakeGenerator())

    result = news_module._call_model("prompt", "fake-model")

    assert result == "ok"
    assert captured == {
        "max_retries": NEWS_MAX_RETRIES,
        "initial_backoff_seconds": NEWS_INITIAL_BACKOFF_SECONDS,
        "max_backoff_seconds": NEWS_MAX_BACKOFF_SECONDS,
    }


def test_news_retry_budget_is_smaller_than_the_extraction_paths():
    # OBSERVED: repeated 503 UNAVAILABLE "high demand" errors made the news
    # lane (advisory only, up to MAX_RESULTS_CLASSIFIED_PER_ADDITIVE calls
    # per additive) cost more wall-clock time than the compliance path
    # itself. This locks the RELATIONSHIP in, not just the news lane's own
    # value, so a future change to either constant cannot silently
    # re-equalise them and reintroduce the same problem. Extraction is the
    # compliance-bearing path this must stay smaller than -- it should wait
    # as long as it takes; the news lane should not.
    from src.extractors.gemini import MAX_RATE_LIMIT_RETRIES as EXTRACTION_MAX_RETRIES

    assert NEWS_MAX_RETRIES < EXTRACTION_MAX_RETRIES


# =========================================================================== #
# ROUTE-SCOPED NEWS -- India -> EU, one query per screening, never per
# additive. Same monkeypatched-_call_model discipline as the additive tests
# above; nothing here calls a real model or a real SearchProvider either.
# =========================================================================== #
def _route_result(
    title="EU tightens checks on Indian spice imports",
    content="Officials say checks on Indian spice consignments have increased.",
    **overrides,
):
    base = {
        "title": title,
        "url": "https://ec.europa.eu/example-route",
        "content": content,
        "published_date": date(2026, 5, 1),
        "published_date_raw": "Fri, 01 May 2026 00:00:00 GMT",
        "score": 0.9,
        "flags": [],
    }
    base.update(overrides)
    return SearchResult(**base)


# --------------------------------------------------------------------------- #
# build_route_query -- pure
# --------------------------------------------------------------------------- #
def test_build_route_query_uses_the_plain_category_name():
    assert build_route_query("Herbs and spices") == "Indian Herbs and spices exports EU import rules"


def test_build_route_query_falls_back_to_a_generic_food_query():
    # Never skipped when no product-level category is confirmed.
    assert build_route_query(None) == "Indian food exports EU import rules"


def test_build_route_query_cleans_underscores_and_citation_clauses():
    # MEASURED (data/reference/food_categories.json): some category names
    # are stored with underscores instead of spaces, and 25 of 154 carry a
    # trailing legal citation -- neither belongs in a search query.
    assert build_route_query("Flavoured_drinks") == "Indian Flavoured drinks exports EU import rules"
    assert build_route_query("Cocoa and chocolate products as covered by Directive 2000/36/EC") == (
        "Indian Cocoa and chocolate products exports EU import rules"
    )


def test_build_route_query_never_uses_a_bare_fcs_code():
    query = build_route_query("Herbs and spices")
    assert "12.2.1" not in query


# --------------------------------------------------------------------------- #
# check_route_category_consistency -- pure
# --------------------------------------------------------------------------- #
def test_route_consistency_rejects_import_control_quoting_an_incident():
    assert check_route_category_consistency("import_control", "the shipment was rejected at the border") is False
    assert check_route_category_consistency("import_control", "consignments were destroyed on arrival") is False


def test_route_consistency_rejects_border_rejection_quoting_a_future_plan():
    assert check_route_category_consistency("border_rejection", "the EU proposed new rules for spice imports") is False
    assert check_route_category_consistency("border_rejection", "checks will apply from January 2027") is False


def test_route_consistency_allows_matching_pairs():
    assert check_route_category_consistency("import_control", "official checks have been increased") is True
    assert check_route_category_consistency("border_rejection", "the shipment was rejected at the border") is True


def test_route_consistency_does_not_judge_other_categories():
    assert check_route_category_consistency("trade_agreement", "the shipment was rejected at the border") is True
    assert check_route_category_consistency("consumer_alert", "the EU proposed new rules") is True


# --------------------------------------------------------------------------- #
# classify_and_quote_route -- monkeypatched model call, no network
# --------------------------------------------------------------------------- #
def test_classify_and_quote_route_accepts_a_verbatim_quote(monkeypatch):
    result = _route_result()
    monkeypatch.setattr(
        news_module,
        "_call_model",
        lambda prompt, model_id: _mock_response("import_control", "checks on Indian spice consignments have increased"),
    )

    classified = classify_and_quote_route(result, "India", "Herbs and spices", "fake-model")

    assert classified == ("import_control", "checks on Indian spice consignments have increased")


def test_classify_and_quote_route_rejects_a_non_verbatim_quote(monkeypatch):
    result = _route_result()
    monkeypatch.setattr(
        news_module,
        "_call_model",
        lambda prompt, model_id: _mock_response("import_control", "spices will soon be banned entirely"),
    )
    assert classify_and_quote_route(result, "India", "Herbs and spices", "fake-model") is None


def test_classify_and_quote_route_returns_none_for_unrelated(monkeypatch):
    result = _route_result()
    monkeypatch.setattr(news_module, "_call_model", lambda prompt, model_id: _mock_response("unrelated", ""))
    assert classify_and_quote_route(result, "India", "Herbs and spices", "fake-model") is None


def test_classify_and_quote_route_returns_none_for_an_additive_category(monkeypatch):
    # "regulatory_review"/"safety_opinion" are the ADDITIVE lane's
    # categories, not valid route categories -- proves the two category
    # sets are genuinely separate, not just differently labelled.
    result = _route_result()
    monkeypatch.setattr(
        news_module, "_call_model", lambda prompt, model_id: _mock_response("regulatory_review", "checks on Indian spice consignments have increased")
    )
    assert classify_and_quote_route(result, "India", "Herbs and spices", "fake-model") is None


def test_classify_and_quote_route_rejects_a_category_contradicted_by_its_own_quote(monkeypatch):
    # MEASURED-STYLE case this exists to catch: "import_control" badged
    # over a quote that is actually about a specific rejected shipment.
    monkeypatch.setattr(
        news_module,
        "_call_model",
        lambda prompt, model_id: _mock_response("import_control", "the consignment was rejected at the border"),
    )
    result = _route_result(content="the consignment was rejected at the border after failing checks")
    assert classify_and_quote_route(result, "India", "Herbs and spices", "fake-model") is None


def test_classify_and_quote_route_returns_none_on_malformed_json(monkeypatch):
    result = _route_result()
    monkeypatch.setattr(news_module, "_call_model", lambda prompt, model_id: "not json at all")
    assert classify_and_quote_route(result, "India", "Herbs and spices", "fake-model") is None


def test_classify_and_quote_route_does_not_affect_the_additive_lane(monkeypatch):
    # classify_and_quote itself is untouched -- calling the route sibling
    # never routes through it or shares any mutable state with it.
    result = _result()
    monkeypatch.setattr(
        news_module, "_call_model", lambda prompt, model_id: _mock_response("regulatory_review", "EFSA opens review")
    )
    assert classify_and_quote(result, "Titanium dioxide", "fake-model") == ("regulatory_review", "EFSA opens review")


# --------------------------------------------------------------------------- #
# render_route_news_sentence -- pure
# --------------------------------------------------------------------------- #
def test_render_route_news_sentence_includes_quote_url_date_and_origin():
    result = _route_result(published_date=date(2026, 5, 1))
    sentence = render_route_news_sentence("India", "Herbs and spices", "import_control", "checks increased", result)
    assert "checks increased" in sentence
    assert result.url in sentence
    assert "2026-05-01" in sentence
    assert "EU import control" in sentence
    assert "India" in sentence and "Herbs and spices" in sentence


def test_render_route_news_sentence_omits_date_when_absent():
    result = _route_result(published_date=None, published_date_raw=None)
    sentence = render_route_news_sentence("India", "Herbs and spices", "import_control", "checks increased", result)
    assert "None" not in sentence


# --------------------------------------------------------------------------- #
# build_route_news_signal -- ties everything together
# --------------------------------------------------------------------------- #
def test_build_route_news_signal_happy_path(monkeypatch):
    result = _route_result(flags=["no_published_date"])
    monkeypatch.setattr(
        news_module,
        "_call_model",
        lambda prompt, model_id: _mock_response("import_control", "checks on Indian spice consignments have increased"),
    )

    signal = build_route_news_signal(
        result, "India", "Herbs and spices", "Indian Herbs and spices exports EU import rules", "2026-08-04", "fake-model"
    )

    assert isinstance(signal, RouteNewsSignal)
    assert signal.origin == "India"
    assert signal.category_name == "Herbs and spices"
    assert signal.category == "import_control"
    assert signal.quoted_span == "checks on Indian spice consignments have increased"
    assert signal.source_url == result.url
    assert signal.search_query == "Indian Herbs and spices exports EU import rules"
    assert signal.retrieved_at == "2026-08-04"
    assert signal.flags == ["no_published_date"]


def test_build_route_news_signal_none_when_classification_rejects(monkeypatch):
    monkeypatch.setattr(news_module, "_call_model", lambda prompt, model_id: _mock_response("unrelated", ""))
    signal = build_route_news_signal(_route_result(), "India", "Herbs and spices", "q", "2026-08-04", "fake-model")
    assert signal is None


def test_build_route_news_signal_none_on_modality_backstop_violation(monkeypatch):
    # Forced via the FIXED TEMPLATE, same proof-of-wiring shape as the
    # additive lane's own test_build_news_signal_none_on_modality_
    # backstop_violation -- check_modality_backstop is REUSED, unchanged.
    monkeypatch.setitem(news_module._ROUTE_CATEGORY_LABELS, "import_control", "This category may soon be banned")
    result = _route_result(title="EU tightens checks", content="Routine control update.")
    monkeypatch.setattr(
        news_module, "_call_model", lambda prompt, model_id: _mock_response("import_control", "EU tightens checks")
    )

    signal = build_route_news_signal(result, "India", "Herbs and spices", "q", "2026-08-04", "fake-model")

    assert signal is None


def test_route_news_signal_affects_verdict_is_always_false(monkeypatch):
    monkeypatch.setattr(
        news_module,
        "_call_model",
        lambda prompt, model_id: _mock_response("border_rejection", "consignments were rejected"),
    )
    result = _route_result(
        title="Indian spice consignments rejected", content="EU border officials say consignments were rejected."
    )
    signal = build_route_news_signal(result, "India", "Herbs and spices", "q", "2026-08-04", "fake-model")
    assert signal.affects_verdict is False


def test_route_news_signal_severity_is_always_advisory(monkeypatch):
    monkeypatch.setattr(
        news_module,
        "_call_model",
        lambda prompt, model_id: _mock_response("import_control", "checks on Indian spice consignments have increased"),
    )
    signal = build_route_news_signal(_route_result(), "India", "Herbs and spices", "q", "2026-08-04", "fake-model")
    assert signal.severity == "advisory"


# --------------------------------------------------------------------------- #
# find_route_news_signals -- FixtureSearchProvider or a counting test
# double, never a real network call.
# --------------------------------------------------------------------------- #
def test_find_route_news_signals_returns_empty_unchanged_when_provider_is_none():
    cache = {"sentinel": "value"}
    signals, updated_cache, category = find_route_news_signals(
        "12.2.1", "Herbs and spices", None, cache, "fake-model", date(2026, 8, 4)
    )
    assert signals == []
    assert updated_cache is cache
    assert category == "Herbs and spices"  # still resolved, even though nothing ran


def test_find_route_news_signals_happy_path(monkeypatch):
    query = build_route_query("Herbs and spices")
    provider = FixtureSearchProvider({query: [_route_result()]})
    monkeypatch.setattr(
        news_module,
        "_call_model",
        lambda prompt, model_id: _mock_response("import_control", "checks on Indian spice consignments have increased"),
    )

    signals, updated_cache, category = find_route_news_signals(
        "12.2.1", "Herbs and spices", provider, {}, "fake-model", date(2026, 8, 4)
    )

    assert len(signals) == 1
    assert signals[0].category == "import_control"
    assert category == "Herbs and spices"
    assert "route:india:12.2.1" in updated_cache  # namespaced, never collides with an additive id


def test_find_route_news_signals_falls_back_to_generic_query_when_uncategorised(monkeypatch):
    query = build_route_query(None)
    provider = FixtureSearchProvider({query: [_route_result()]})
    monkeypatch.setattr(
        news_module,
        "_call_model",
        lambda prompt, model_id: _mock_response("import_control", "checks on Indian spice consignments have increased"),
    )

    signals, updated_cache, category = find_route_news_signals(
        None, None, provider, {}, "fake-model", date(2026, 8, 4)
    )

    assert len(signals) == 1  # the lane still ran -- never skipped for lacking a confirmed category
    assert category == "food"
    assert "route:india:unknown" in updated_cache


def test_find_route_news_signals_search_failure_degrades_instead_of_raising(monkeypatch):
    # A route search failing must not break the additive lane or the run
    # -- degrades to empty, same discipline find_news_signals applies per
    # additive, just for the whole (single) route query here.
    class _RaisingProvider:
        def search(self, q, *, include_domains=None, days=None):
            raise RuntimeError("Tavily quota exhausted")

    signals, updated_cache, category = find_route_news_signals(
        "12.2.1", "Herbs and spices", _RaisingProvider(), {}, "fake-model", date(2026, 8, 4)
    )
    assert signals == []
    assert updated_cache == {}
    assert category == "Herbs and spices"


def test_find_route_news_signals_caps_results_classified(monkeypatch):
    query = build_route_query("Herbs and spices")
    provider = FixtureSearchProvider(
        {query: [_route_result(url=f"https://ec.europa.eu/example-{i}") for i in range(6)]}
    )
    calls = {"n": 0}

    def _counting_call_model(prompt, model_id):
        calls["n"] += 1
        return _mock_response("import_control", "checks on Indian spice consignments have increased")

    monkeypatch.setattr(news_module, "_call_model", _counting_call_model)
    find_route_news_signals("12.2.1", "Herbs and spices", provider, {}, "fake-model", date(2026, 8, 4))

    assert calls["n"] == MAX_RESULTS_CLASSIFIED_PER_ROUTE


def test_find_route_news_signals_reuses_the_cache_on_a_second_screening_of_a_different_product(monkeypatch):
    # Two screenings of different biscuit brands, SAME confirmed category
    # -- must share one cache entry, never re-search.
    query = build_route_query("Biscuits")
    provider = _CountingProvider({query: [_route_result()]})
    monkeypatch.setattr(
        news_module, "_call_model", lambda prompt, model_id: _mock_response("import_control", "checks on Indian spice consignments have increased")
    )

    _signals_a, cache_after_first, _category = find_route_news_signals(
        "7.2", "Biscuits", provider, {}, "fake-model", date(2026, 8, 4)
    )
    find_route_news_signals("7.2", "Biscuits", provider, cache_after_first, "fake-model", date(2026, 8, 4))

    assert provider.calls == [query]  # the SECOND screening never called search() again


def test_find_route_news_signals_stale_serve_flag_carries_onto_signals(monkeypatch):
    query = build_route_query("Herbs and spices")
    stale_cache = {
        "route:india:12.2.1": {
            "fetched_at": "2026-01-01",  # well past the 7-day TTL
            "results": [
                {
                    "title": "EU tightens checks on Indian spice imports",
                    "url": "https://ec.europa.eu/example-route",
                    "content": "Officials say checks on Indian spice consignments have increased.",
                    "published_date": "2026-05-01",
                    "published_date_raw": "Fri, 01 May 2026 00:00:00 GMT",
                    "score": 0.9,
                    "flags": [],
                }
            ],
        }
    }
    provider = _CountingProvider(raise_for={query})  # provider is down -- forces a stale-serve
    monkeypatch.setattr(
        news_module, "_call_model", lambda prompt, model_id: _mock_response("import_control", "checks on Indian spice consignments have increased")
    )

    signals, _cache, _category = find_route_news_signals(
        "12.2.1", "Herbs and spices", provider, stale_cache, "fake-model", date(2026, 8, 4)
    )

    assert len(signals) == 1
    assert "stale_cache_served" in signals[0].flags


def test_route_lane_shares_no_module_state_with_additive_lane(monkeypatch):
    # find_route_news_signals and find_news_signals must be independently
    # callable in the same run without interfering -- proves the shared
    # cache dict is genuinely namespace-safe (additive ids vs "route:...").
    additive_query = news_module._build_query("Titanium dioxide")
    route_query = build_route_query("Herbs and spices")
    provider = FixtureSearchProvider(
        {
            additive_query: [_result()],
            route_query: [_route_result()],
        }
    )
    monkeypatch.setattr(
        news_module,
        "_call_model",
        lambda prompt, model_id: _mock_response("regulatory_review", "EFSA opens review"),
    )

    additive_signals, cache = find_news_signals(
        ["171"], provider, {}, "fake-model", date(2026, 8, 4), additive_names={"171": "Titanium dioxide"}
    )
    monkeypatch.setattr(
        news_module,
        "_call_model",
        lambda prompt, model_id: _mock_response("import_control", "checks on Indian spice consignments have increased"),
    )
    route_signals, cache, _category = find_route_news_signals(
        "12.2.1", "Herbs and spices", provider, cache, "fake-model", date(2026, 8, 4)
    )

    assert len(additive_signals) == 1
    assert len(route_signals) == 1
    assert "171" in cache
    assert "route:india:12.2.1" in cache
