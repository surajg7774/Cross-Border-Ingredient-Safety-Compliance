# DESIGN RULE: mixed-purity in ONE file, deliberately -- same shape as
# src/category/embedder.py's own cache (thin load/save I/O plus the
# hit/miss decision logic together), not the schemas/lane/load three-way
# split the rest of src/horizon/ uses for the curated dataset. That split
# serves a different purpose (separating a REFERENCE DATASET on disk from
# MATCHING logic); this is a cache, and a cache's I/O and its TTL/stale-
# serve decision are tightly coupled enough that embedder.py's precedent
# fits better than lane.py's. get_search_results itself takes `fetch` as
# an injected callable and `cache` as a plain dict -- no file access, no
# settings access -- so it stays testable without touching disk at all;
# only load_cache/save_cache below do real I/O.
"""Disk cache for the news lane's RETRIEVAL layer -- caches what a
SearchProvider.search() call returns, so a cache hit skips the search
call itself, before relevance filtering or classification ever run.
Caching happens here, not in src/horizon/news.py, because the same
additive's news doesn't change just because a different model classified
it differently on a later run -- only the underlying search results are
worth remembering.

KEYED ON NORMALISED ADDITIVE IDENTITY -- never on label or item_id. See
src/horizon/schemas.py's own module DESIGN RULE: this stage operates on
additive identity, not per-label items, because the same additive
(silicon dioxide, citric acid, ...) recurs across many different labels,
and news about it doesn't change per label. A per-label or per-item_id
cache key would mean re-searching the identical additive every time it
appears on a different product -- exactly the quota waste identity-keyed
caching exists to avoid.

TTL: 7 days. Regulatory news is not real-time -- an EFSA review runs
months to years, RASFF alerts for a specific additive are infrequent,
trade press doesn't publish multiple substance-specific pieces a day. A
7-day window costs essentially no signal a user would act on.

STALE-SERVE: when the provider call fails (network error, exhausted
quota) and a cache entry exists but is past its TTL, that stale entry is
served anyway, flagged "stale_cache_served" -- visible, never silent,
the same discipline src/horizon/schemas.py's HorizonResult.data_retrieved
already applies to the curated dataset. A MISSING entry with a failing
fetch has nothing to fall back to and propagates the failure.
"""

import json
from datetime import date

from src.horizon.search import SearchResult

TTL_DAYS = 7

_STALE_SERVED_FLAG = "stale_cache_served"


def _normalise_identity(raw: str) -> str:
    """'E 955' -> '955', 'INS 470(i)' -> '470(i)' -- identical to
    src/horizon/lane.py's _normalise_id (itself duplicated from
    src/resolve/resolver.py's _normalise_code), duplicated here rather
    than imported for the same independent-pure-module reason lane.py's
    own docstring gives for its copy."""
    text = raw.lower().replace(" ", "")
    if text.startswith("ins"):
        return text[3:]
    if text.startswith("e"):
        return text[1:]
    return text


def _serialise(results: list[SearchResult]) -> list[dict]:
    # published_date is a date object (src/horizon/search.py) -- not
    # JSON-serialisable as-is, so it goes to disk as isoformat() text and
    # comes back via date.fromisoformat() in _deserialise. published_date_raw
    # is already a plain string (or None) and round-trips unchanged.
    return [
        {
            "title": r.title,
            "url": r.url,
            "content": r.content,
            "published_date": r.published_date.isoformat() if r.published_date else None,
            "published_date_raw": r.published_date_raw,
            "score": r.score,
            "flags": r.flags,
        }
        for r in results
    ]


def _deserialise(raw: list[dict]) -> list[SearchResult]:
    return [
        SearchResult(
            title=r["title"],
            url=r["url"],
            content=r["content"],
            published_date=date.fromisoformat(r["published_date"]) if r["published_date"] else None,
            published_date_raw=r.get("published_date_raw"),
            score=r["score"],
            flags=r.get("flags", []),
        )
        for r in raw
    ]


def load_cache(path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_cache(path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _age_days(fetched_at: str, today: date) -> int:
    return (today - date.fromisoformat(fetched_at)).days


def get_search_results(
    cache: dict,
    additive_id: str,
    fetch,
    today: date,
    ttl_days: int = TTL_DAYS,
) -> tuple[list[SearchResult], dict, list[str]]:
    """(results, updated_cache, flags) for `additive_id`.

    A cache entry within `ttl_days` is returned as-is, no `fetch` call --
    the whole point of the cache. A MISSING or EXPIRED entry calls
    `fetch()` (a zero-argument callable, e.g. `lambda: provider.search(
    query, ...)` -- this function does not know or care how the search is
    actually performed). On success, the cache is updated and fresh
    results returned with no flags. On failure (fetch raises -- a
    provider error or exhausted quota, this function does not
    distinguish which): an EXPIRED-but-present entry is served anyway,
    flagged "stale_cache_served" so this is visible rather than silently
    indistinguishable from a fresh result; a genuinely MISSING entry has
    nothing to serve and the exception propagates -- there is no data to
    fall back to, so pretending otherwise would be worse than failing
    loudly.

    `cache` is never mutated in place -- a NEW dict is returned on a
    write, the input is left untouched, so a caller controls exactly when
    (or whether) to persist it via save_cache.
    """
    key = _normalise_identity(additive_id)
    entry = cache.get(key)
    is_fresh = entry is not None and _age_days(entry["fetched_at"], today) <= ttl_days

    if is_fresh:
        return _deserialise(entry["results"]), cache, []

    try:
        results = fetch()
    except Exception:
        if entry is not None:
            return _deserialise(entry["results"]), cache, [_STALE_SERVED_FLAG]
        raise

    updated_cache = dict(cache)
    updated_cache[key] = {"fetched_at": today.isoformat(), "results": _serialise(results)}
    return results, updated_cache, []
