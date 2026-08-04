# DESIGN RULE: unlike lane.py (pure decision logic, no I/O at all) and
# load.py (the one FILE I/O boundary in src/horizon/), this module is a
# NETWORK I/O boundary -- TavilySearchProvider.search() makes a real HTTP
# call. Everything else here (SearchResult, FixtureSearchProvider,
# validate_search_result) stays pure and network-free, same discipline as
# every other boundary/logic split in this project (src/category/
# embedder.py's cache boundary vs. classifier.py's pure scoring;
# src/report/email.py's send_report taking a plain SmtpConfig argument
# rather than reading config.settings itself). This module does NOT import
# src.horizon.schemas: SearchResult is a raw retrieval-layer shape, not
# the HorizonSignal/NewsSignal domain model a later stage builds from
# validated results -- keeping the two separate means this module never
# has to know about affects_verdict/severity invariants at all; those get
# enforced when raw results are turned into a domain signal downstream,
# not here.
"""Retrieval boundary for the (not-yet-built) horizon news lane: the
SearchProvider Protocol, a fixture-backed implementation usable right now
with no key and no network, and a Tavily implementation written to the
same Protocol but not wired to anything yet.

PROVIDER DECISION, for the record: Tavily, not Brave or SerpAPI. Chosen
specifically for its free tier -- 1,000 credits/month, NO credit card
required. Brave eliminated its own free tier in February 2026 (now $5
prepaid metered, card required, no spending cap); this project has an
unresolved card/billing problem, so a card requirement is disqualifying
regardless of anything else measured about result quality. Brave scores
marginally higher on agent-search benchmarks (~1 point) -- not enough to
outweigh actually being reachable. SerpAPI has no ongoing free tier at
all and was never seriously in contention on cost. langchain-community
was deliberately NOT added for this: httpx is already a dependency,
Tavily's search endpoint is a plain POST, and this project already
decided against reaching for LangChain where a thin Protocol already
gives the same independence for no real benefit (see docs/findings.md
F-18, the same reasoning applied to src/agent/resolver_agent.py).

STAGE 1 (this module, as it stands today): the Protocol, SearchResult,
validate_search_result, and the fixture-backed provider are real and
usable now. TavilySearchProvider's implementation exists too, but is NOT
called anywhere in this codebase -- no config.py setting for an API key
exists yet, no factory function selects a provider, and no test exercises
it over the network (tests that cover it monkeypatch httpx.post; none
make a real request). It is written now so the request/response shape is
settled and reviewable ahead of time. It slots in behind a
get_search_provider()-style factory (mirroring src/text_generation.py's
get_text_generator(), which picks a backend the same way) once an API key
actually exists -- that wiring is later-stage work, not this one.

SEARCH DEPTH IS ALWAYS "basic" (1 credit), hardcoded as an explicit
constant rather than left to whatever Tavily defaults to -- "advanced"
costs 2 credits and can take 5+ seconds per call. At up to 12 searches
per label (one per additive), relying on a default that could change
underneath this code would silently double cost and latency. See
_SEARCH_DEPTH below.

STALE/DEAD LINKS: Tavily can return stale or dead links from its own
cache, and its response carries no direct "this is stale" signal to key
off. validate_search_result uses the best available proxy instead: URL
well-formedness (a real scheme and netloc) and the presence of a
published date. A result that fails either check is FLAGGED, not
dropped -- the caller still gets to see it and decide, with the problem
named rather than the result silently vanishing. This is a proxy, not a
verified staleness detector, and should be revisited once real Tavily
responses exist to study.

UNVERIFIED, written from Tavily's published API documentation, not a
live response: TavilySearchProvider's exact request/response shape
(Bearer auth in the Authorization header, the response's "results" list
and each item's field names) has not been confirmed against a real call.
Treat it as a first draft to check against a real response before it is
ever wired up for real use.
"""

from dataclasses import dataclass, field, replace
from typing import Protocol
from urllib.parse import urlparse

import httpx

_TAVILY_SEARCH_URL = "https://api.tavily.com/search"
# 1 credit. "advanced" costs 2 credits and can take 5+ seconds per call --
# explicit here, not Tavily's default, so a future change on their side
# can't silently double this project's cost or latency. See this module's
# docstring, "SEARCH DEPTH IS ALWAYS 'basic'".
_SEARCH_DEPTH = "basic"

_MALFORMED_URL_FLAG = "malformed_source_url"
_NO_PUBLISHED_DATE_FLAG = "no_published_date"


@dataclass(frozen=True)
class SearchResult:
    """One raw retrieval hit -- unvalidated. Always run through
    validate_search_result before display; nothing here decides what
    counts as malformed or stale on its own."""

    title: str
    url: str
    content: str
    published_date: str | None
    score: float | None
    # Populated by validate_search_result, never by a provider itself --
    # see this module's docstring on why the two are kept separate.
    flags: list[str] = field(default_factory=list)


class SearchProvider(Protocol):
    def search(
        self, query: str, *, include_domains: list[str] | None = None, days: int | None = None
    ) -> list[SearchResult]:
        """Search for `query`, optionally restricted to `include_domains`
        and/or the last `days` days -- both first-class parameters, not
        query-string tricks, since domain restriction does most of the
        real work filtering regulatory-relevant results from open-web
        junk (see the horizon-news design report). Returns RAW,
        unvalidated results -- callers run validate_search_result over
        each one before display; a provider never decides what counts as
        malformed or stale itself, so every provider is judged by the
        identical rule."""
        ...


class FixtureSearchProvider:
    """A SearchProvider backed by pre-recorded results -- never the
    network. For development and tests before a Tavily key exists and,
    afterward, for any test that wants deterministic results without
    hitting a real API. `results` maps a query string to the
    SearchResult list that exact query should return; a query with no
    matching fixture returns an empty list, not an error -- so code
    written against this provider behaves the same way it would against
    a real one for an additive nobody thought to add a fixture for."""

    def __init__(self, results: dict[str, list[SearchResult]] | None = None) -> None:
        self._results = results or {}

    def search(
        self, query: str, *, include_domains: list[str] | None = None, days: int | None = None
    ) -> list[SearchResult]:
        return list(self._results.get(query, []))


class TavilySearchProvider:
    """Tavily's REST /search endpoint via httpx -- a plain POST, no SDK,
    no langchain-community. See this module's docstring for the provider
    decision and for why this class is written but not yet called by
    anything in this codebase."""

    def __init__(self, api_key: str, timeout_seconds: float = 10.0) -> None:
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    def search(
        self, query: str, *, include_domains: list[str] | None = None, days: int | None = None
    ) -> list[SearchResult]:
        payload: dict[str, object] = {"query": query, "search_depth": _SEARCH_DEPTH}
        if include_domains:
            payload["include_domains"] = include_domains
        if days is not None:
            payload["days"] = days

        response = httpx.post(
            _TAVILY_SEARCH_URL,
            json=payload,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()

        return [
            SearchResult(
                title=item.get("title") or "",
                url=item.get("url") or "",
                content=item.get("content") or "",
                published_date=item.get("published_date"),
                score=item.get("score"),
            )
            for item in data.get("results", [])
        ]


def _is_well_formed_url(url: str) -> bool:
    """A real scheme (http/https) and a real netloc -- not a full
    reachability check (that would mean a live request per result, which
    this project's quota discipline rules out; see the horizon-news
    design report's caching section). This catches the cheap, structural
    failure mode: an empty string, a relative path, a scheme-less
    fragment -- not a dead-but-well-formed link."""
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def validate_search_result(result: SearchResult) -> SearchResult:
    """`result` with flags set for a malformed source_url and/or a
    missing published_date -- the best available proxy for "this looks
    stale" given Tavily's response carries no direct staleness signal
    (see this module's docstring). Never drops a result for failing
    either check; a caller decides what to do with a flagged one, same
    discipline as src/horizon/schemas.py's HorizonSignal.flags
    (matched_by_name, doi_unverified) -- named, not hidden."""
    flags = list(result.flags)
    if not _is_well_formed_url(result.url):
        flags.append(_MALFORMED_URL_FLAG)
    if not result.published_date:
        flags.append(_NO_PUBLISHED_DATE_FLAG)
    if flags == result.flags:
        return result
    return replace(result, flags=flags)
