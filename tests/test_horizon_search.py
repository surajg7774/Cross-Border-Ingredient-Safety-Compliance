"""Tests for src/horizon/search.py -- no network. TavilySearchProvider is
covered via a monkeypatched httpx.post (same pattern as
tests/test_email.py's _FakeSmtp); nothing here makes a real request."""

import httpx
import pytest

from src.horizon import search as search_module
from src.horizon.search import (
    FixtureSearchProvider,
    SearchResult,
    TavilySearchProvider,
    validate_search_result,
)


def _result(url="https://example.com/article", published_date="2026-07-01", **overrides):
    base = {"title": "Test headline", "url": url, "content": "Test content", "published_date": published_date, "score": 0.9}
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
    result = validate_search_result(_result(published_date=None))
    assert "no_published_date" in result.flags


def test_validate_flags_both_problems_at_once():
    result = validate_search_result(_result(url="ftp://example.com/x", published_date=None))
    assert "malformed_source_url" in result.flags
    assert "no_published_date" in result.flags


def test_validate_never_drops_a_flagged_result():
    # The whole point: flag, don't silently drop.
    original = _result(url="garbage", published_date=None)
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


def test_tavily_provider_sends_basic_search_depth_explicitly(monkeypatch):
    captured = {}

    def _fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse({"results": []})

    monkeypatch.setattr(search_module.httpx, "post", _fake_post)

    provider = TavilySearchProvider(api_key="fake-key")
    provider.search("titanium dioxide EU regulation")

    assert captured["json"]["search_depth"] == "basic"
    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["headers"]["Authorization"] == "Bearer fake-key"


def test_tavily_provider_forwards_include_domains_and_days(monkeypatch):
    captured = {}

    def _fake_post(url, json, headers, timeout):
        captured["json"] = json
        return _FakeResponse({"results": []})

    monkeypatch.setattr(search_module.httpx, "post", _fake_post)

    provider = TavilySearchProvider(api_key="fake-key")
    provider.search("x", include_domains=["efsa.europa.eu"], days=7)

    assert captured["json"]["include_domains"] == ["efsa.europa.eu"]
    assert captured["json"]["days"] == 7


def test_tavily_provider_omits_include_domains_and_days_when_not_given(monkeypatch):
    captured = {}

    def _fake_post(url, json, headers, timeout):
        captured["json"] = json
        return _FakeResponse({"results": []})

    monkeypatch.setattr(search_module.httpx, "post", _fake_post)

    TavilySearchProvider(api_key="fake-key").search("x")

    assert "include_domains" not in captured["json"]
    assert "days" not in captured["json"]


def test_tavily_provider_parses_results_into_search_results(monkeypatch):
    def _fake_post(url, json, headers, timeout):
        return _FakeResponse(
            {
                "results": [
                    {
                        "title": "EFSA reviews titanium dioxide",
                        "url": "https://efsa.europa.eu/example",
                        "content": "The panel concluded...",
                        "published_date": "2026-06-15",
                        "score": 0.87,
                    }
                ]
            }
        )

    monkeypatch.setattr(search_module.httpx, "post", _fake_post)

    results = TavilySearchProvider(api_key="fake-key").search("titanium dioxide")

    assert len(results) == 1
    assert results[0] == SearchResult(
        title="EFSA reviews titanium dioxide",
        url="https://efsa.europa.eu/example",
        content="The panel concluded...",
        published_date="2026-06-15",
        score=0.87,
    )


def test_tavily_provider_tolerates_missing_optional_fields(monkeypatch):
    def _fake_post(url, json, headers, timeout):
        return _FakeResponse({"results": [{"url": "https://example.com/x"}]})

    monkeypatch.setattr(search_module.httpx, "post", _fake_post)

    results = TavilySearchProvider(api_key="fake-key").search("x")

    assert results[0].title == ""
    assert results[0].published_date is None
    assert results[0].score is None


def test_tavily_provider_raises_on_http_error(monkeypatch):
    def _fake_post(url, json, headers, timeout):
        return _FakeResponse({}, status_code=500)

    monkeypatch.setattr(search_module.httpx, "post", _fake_post)

    with pytest.raises(httpx.HTTPStatusError):
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
