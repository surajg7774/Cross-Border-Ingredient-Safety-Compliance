"""Tests for src/horizon/search.py -- no network. TavilySearchProvider is
covered via a monkeypatched httpx.post (same pattern as
tests/test_email.py's _FakeSmtp); nothing here makes a real request."""

from datetime import date

import httpx
import pytest

from src.horizon import search as search_module
from src.horizon.search import (
    FixtureSearchProvider,
    SearchResult,
    TavilySearchProvider,
    validate_search_result,
)

# The EXACT literal Tavily returns for published_date with topic="news" --
# MEASURED against a live response, RFC 2822, not ISO.
_TAVILY_RFC2822_DATE = "Tue, 10 Mar 2026 00:00:00 GMT"


def _result(url="https://example.com/article", published_date=date(2026, 7, 1), published_date_raw="2026-07-01", **overrides):
    base = {
        "title": "Test headline",
        "url": url,
        "content": "Test content",
        "published_date": published_date,
        "published_date_raw": published_date_raw,
        "score": 0.9,
    }
    base.update(overrides)
    return SearchResult(**base)


# --------------------------------------------------------------------------- #
# FixtureSearchProvider -- no network, ever
# --------------------------------------------------------------------------- #
def test_fixture_provider_returns_results_for_a_known_query():
    fixture_results = [_result(title="Titanium dioxide under review")]
    provider = FixtureSearchProvider({"titanium dioxide EU regulation": fixture_results})

    results = provider.search("titanium dioxide EU regulation")

    assert results == fixture_results


def test_fixture_provider_returns_empty_list_for_unknown_query():
    provider = FixtureSearchProvider({"titanium dioxide EU regulation": [_result()]})

    assert provider.search("some other additive EU regulation") == []


def test_fixture_provider_with_no_fixtures_returns_empty_list():
    provider = FixtureSearchProvider()
    assert provider.search("anything") == []


def test_fixture_provider_accepts_include_domains_and_days_without_using_them():
    # Same Protocol signature as TavilySearchProvider -- callers should be
    # able to pass these unconditionally regardless of which provider is
    # in use.
    provider = FixtureSearchProvider({"q": [_result()]})
    assert provider.search("q", include_domains=["efsa.europa.eu"], days=7) == [_result()]


# --------------------------------------------------------------------------- #
# validate_search_result -- pure, no network
# --------------------------------------------------------------------------- #
def test_validate_well_formed_result_gets_no_flags():
    result = validate_search_result(_result())
    assert result.flags == []


def test_validate_flags_malformed_url():
    result = validate_search_result(_result(url="not-a-url"))
    assert "malformed_source_url" in result.flags


def test_validate_flags_empty_url():
    result = validate_search_result(_result(url=""))
    assert "malformed_source_url" in result.flags


def test_validate_flags_missing_published_date():
    result = validate_search_result(_result(published_date=None, published_date_raw=None))
    assert "no_published_date" in result.flags


def test_validate_flags_unparseable_published_date_distinctly():
    # A date that is PRESENT (published_date_raw set) but failed to parse
    # is a different failure from a date that was never returned at all --
    # must NOT be flagged "no_published_date".
    result = validate_search_result(_result(published_date=None, published_date_raw="not a real date"))
    assert "unparseable_published_date" in result.flags
    assert "no_published_date" not in result.flags


def test_validate_flags_both_problems_at_once():
    result = validate_search_result(_result(url="ftp://example.com/x", published_date=None, published_date_raw=None))
    assert "malformed_source_url" in result.flags
    assert "no_published_date" in result.flags


def test_validate_never_drops_a_flagged_result():
    # The whole point: flag, don't silently drop.
    original = _result(url="garbage", published_date=None, published_date_raw=None)
    result = validate_search_result(original)
    assert result.title == original.title
    assert result.url == original.url


def test_validate_does_not_mutate_the_original_result():
    original = _result()
    validate_search_result(_result(url="garbage"))
    assert original.flags == []


# --------------------------------------------------------------------------- #
# TavilySearchProvider -- monkeypatched httpx.post, never the real network
# --------------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json_data


def test_tavily_provider_sends_basic_search_depth_news_topic_and_no_answer(monkeypatch):
    captured = {}

    def _fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse({"results": [], "answer": None})

    monkeypatch.setattr(search_module.httpx, "post", _fake_post)

    provider = TavilySearchProvider(api_key="fake-key")
    provider.search("titanium dioxide EU regulation")

    assert captured["json"]["search_depth"] == "basic"
    assert captured["json"]["topic"] == "news"  # required for published_date to be present at all
    assert captured["json"]["include_answer"] is False
    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["headers"]["Authorization"] == "Bearer fake-key"


def test_tavily_provider_forwards_include_domains_and_days(monkeypatch):
    captured = {}

    def _fake_post(url, json, headers, timeout):
        captured["json"] = json
        return _FakeResponse({"results": [], "answer": None})

    monkeypatch.setattr(search_module.httpx, "post", _fake_post)

    provider = TavilySearchProvider(api_key="fake-key")
    provider.search("x", include_domains=["efsa.europa.eu"], days=7)

    assert captured["json"]["include_domains"] == ["efsa.europa.eu"]
    assert captured["json"]["days"] == 7


def test_tavily_provider_omits_include_domains_and_days_when_not_given(monkeypatch):
    captured = {}

    def _fake_post(url, json, headers, timeout):
        captured["json"] = json
        return _FakeResponse({"results": [], "answer": None})

    monkeypatch.setattr(search_module.httpx, "post", _fake_post)

    TavilySearchProvider(api_key="fake-key").search("x")

    assert "include_domains" not in captured["json"]
    assert "days" not in captured["json"]


def test_tavily_provider_parses_the_real_rfc2822_published_date(monkeypatch):
    # The exact literal MEASURED against a live Tavily response with
    # topic="news" -- not ISO, not assumed.
    def _fake_post(url, json, headers, timeout):
        return _FakeResponse(
            {
                "answer": None,
                "results": [
                    {
                        "title": "EFSA reviews titanium dioxide",
                        "url": "https://efsa.europa.eu/example",
                        "content": "The panel concluded...",
                        "published_date": _TAVILY_RFC2822_DATE,
                        "score": 0.87,
                    }
                ],
            }
        )

    monkeypatch.setattr(search_module.httpx, "post", _fake_post)

    results = TavilySearchProvider(api_key="fake-key").search("titanium dioxide")

    assert len(results) == 1
    assert results[0].published_date == date(2026, 3, 10)
    assert results[0].published_date_raw == _TAVILY_RFC2822_DATE


def test_tavily_provider_flags_never_dropped_for_an_unparseable_date(monkeypatch):
    def _fake_post(url, json, headers, timeout):
        return _FakeResponse(
            {
                "answer": None,
                "results": [
                    {
                        "title": "Some article",
                        "url": "https://efsa.europa.eu/example",
                        "content": "...",
                        "published_date": "not a real date at all",
                        "score": 0.87,
                    }
                ],
            }
        )

    monkeypatch.setattr(search_module.httpx, "post", _fake_post)

    results = TavilySearchProvider(api_key="fake-key").search("x")

    assert len(results) == 1  # never dropped
    assert results[0].published_date is None  # failed to parse
    assert results[0].published_date_raw == "not a real date at all"  # raw text kept
    validated = validate_search_result(results[0])
    assert "unparseable_published_date" in validated.flags


def test_tavily_provider_tolerates_missing_optional_fields(monkeypatch):
    def _fake_post(url, json, headers, timeout):
        return _FakeResponse({"results": [{"url": "https://example.com/x"}], "answer": None})

    monkeypatch.setattr(search_module.httpx, "post", _fake_post)

    results = TavilySearchProvider(api_key="fake-key").search("x")

    assert results[0].title == ""
    assert results[0].published_date is None
    assert results[0].published_date_raw is None
    assert results[0].score is None


def test_tavily_provider_raises_on_http_error(monkeypatch):
    def _fake_post(url, json, headers, timeout):
        return _FakeResponse({}, status_code=500)

    monkeypatch.setattr(search_module.httpx, "post", _fake_post)

    with pytest.raises(httpx.HTTPStatusError):
        TavilySearchProvider(api_key="fake-key").search("x")


def test_tavily_provider_raises_if_answer_is_non_null(monkeypatch):
    # constraint 3 (horizon-news design report): this lane must never
    # receive Tavily's own generated summary. Not requesting it is not
    # enough on its own -- the response is asserted too.
    def _fake_post(url, json, headers, timeout):
        return _FakeResponse({"results": [], "answer": "Titanium dioxide is a food additive used in..."})

    monkeypatch.setattr(search_module.httpx, "post", _fake_post)

    with pytest.raises(RuntimeError, match="answer"):
        TavilySearchProvider(api_key="fake-key").search("x")


def test_tavily_provider_is_never_constructed_or_called_anywhere_else():
    # Structural guard for the Stage 1 constraint: nothing in the live
    # codebase should reference TavilySearchProvider outside this test file
    # and src/horizon/search.py itself -- it must not be wired to a
    # factory, a settings key, or a graph node yet.
    import pathlib
    import re

    hits = []
    for path in pathlib.Path("src").rglob("*.py"):
        if path == pathlib.Path("src/horizon/search.py"):
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"\bTavilySearchProvider\b", text):
            hits.append(str(path))
    assert hits == [], f"TavilySearchProvider referenced outside search.py: {hits}"
