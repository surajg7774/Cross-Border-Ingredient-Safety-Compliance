"""Tests for src/horizon/news_cache.py -- no network. `fetch` is always a
fake, injected callable; get_search_results never touches a real
SearchProvider. load_cache/save_cache are exercised against a tmp_path
file, never a real network-backed cache."""

from datetime import date

import pytest

from src.horizon.news_cache import TTL_DAYS, get_search_results, load_cache, save_cache
from src.horizon.search import SearchResult


def _result(url="https://example.com/x"):
    return SearchResult(title="t", url=url, content="c", published_date="2026-06-01", score=0.9)


def _cache_entry(fetched_at, results):
    return {"fetched_at": fetched_at, "results": [_serialised(r) for r in results]}


def _serialised(r):
    return {"title": r.title, "url": r.url, "content": r.content, "published_date": r.published_date, "score": r.score, "flags": r.flags}


# --------------------------------------------------------------------------- #
# key normalisation and cache-miss / fresh-hit behaviour
# --------------------------------------------------------------------------- #
def test_cache_miss_calls_fetch_and_stores_results():
    fetch_calls = []

    def fetch():
        fetch_calls.append(1)
        return [_result()]

    results, updated_cache, flags = get_search_results({}, "955", fetch, today=date(2026, 8, 4))

    assert len(fetch_calls) == 1
    assert results == [_result()]
    assert flags == []
    assert "955" in updated_cache
    assert updated_cache["955"]["fetched_at"] == "2026-08-04"


def test_cache_key_is_normalised_additive_identity():
    entry = _cache_entry("2026-08-01", [_result()])
    cache = {"955": entry}

    def fetch():
        raise AssertionError("fetch should not be called -- E 955 must normalise to the same key as 955")

    for raw_id in ("E 955", "E955", "955"):
        results, _cache, flags = get_search_results(cache, raw_id, fetch, today=date(2026, 8, 3))
        assert results == [_result()]
        assert flags == []


def test_fresh_cache_hit_never_calls_fetch():
    entry = _cache_entry("2026-08-01", [_result()])
    cache = {"955": entry}

    def fetch():
        raise AssertionError("fetch should not be called on a fresh cache hit")

    results, updated_cache, flags = get_search_results(cache, "955", fetch, today=date(2026, 8, 3))

    assert results == [_result()]
    assert flags == []
    assert updated_cache is cache  # untouched, not even a copy, on a pure read


# --------------------------------------------------------------------------- #
# TTL expiry
# --------------------------------------------------------------------------- #
def test_entry_within_ttl_is_not_stale():
    entry = _cache_entry("2026-08-01", [_result()])
    cache = {"955": entry}
    fetch_calls = []

    def fetch():
        fetch_calls.append(1)
        return [_result(url="https://example.com/fresh")]

    # Exactly TTL_DAYS old -- still fresh (<=), must not re-fetch.
    today = date(2026, 8, 1).fromordinal(date(2026, 8, 1).toordinal() + TTL_DAYS)
    results, _cache, flags = get_search_results(cache, "955", fetch, today=today)

    assert fetch_calls == []
    assert results == [_result()]
    assert flags == []


def test_entry_past_ttl_triggers_a_refetch():
    entry = _cache_entry("2026-08-01", [_result()])
    cache = {"955": entry}
    fetch_calls = []

    def fetch():
        fetch_calls.append(1)
        return [_result(url="https://example.com/fresh")]

    today = date(2026, 8, 1).fromordinal(date(2026, 8, 1).toordinal() + TTL_DAYS + 1)
    results, updated_cache, flags = get_search_results(cache, "955", fetch, today=today)

    assert len(fetch_calls) == 1
    assert results == [_result(url="https://example.com/fresh")]
    assert flags == []
    assert updated_cache["955"]["fetched_at"] == today.isoformat()


def test_ttl_is_configurable_per_call():
    entry = _cache_entry("2026-08-01", [_result()])
    cache = {"955": entry}

    def fetch():
        raise AssertionError("fetch should not be called -- within the custom TTL")

    today = date(2026, 8, 5)  # 4 days old
    results, _cache, flags = get_search_results(cache, "955", fetch, today=today, ttl_days=5)
    assert results == [_result()]
    assert flags == []


# --------------------------------------------------------------------------- #
# stale-serve on provider failure
# --------------------------------------------------------------------------- #
def test_stale_entry_served_with_flag_when_fetch_fails():
    entry = _cache_entry("2026-07-01", [_result()])  # well past TTL
    cache = {"955": entry}

    def fetch():
        raise RuntimeError("Tavily quota exhausted")

    results, updated_cache, flags = get_search_results(cache, "955", fetch, today=date(2026, 8, 4))

    assert results == [_result()]  # the stale data, served anyway
    assert flags == ["stale_cache_served"]
    assert updated_cache is cache  # not rewritten -- still the old fetched_at


def test_missing_entry_with_failing_fetch_propagates():
    def fetch():
        raise RuntimeError("Tavily quota exhausted")

    with pytest.raises(RuntimeError, match="quota exhausted"):
        get_search_results({}, "955", fetch, today=date(2026, 8, 4))


# --------------------------------------------------------------------------- #
# load_cache / save_cache -- real file I/O, tmp_path only
# --------------------------------------------------------------------------- #
def test_load_cache_returns_empty_dict_when_file_absent(tmp_path):
    assert load_cache(tmp_path / "does_not_exist.json") == {}


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "news_cache.json"
    cache = {"955": _cache_entry("2026-08-01", [_result()])}

    save_cache(path, cache)
    loaded = load_cache(path)

    assert loaded == cache


def test_save_cache_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "news_cache.json"
    save_cache(path, {})
    assert path.exists()
