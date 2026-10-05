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

STAGE 2: wired. get_search_provider() (mirroring src/text_generation.py's
get_text_generator()) returns TavilySearchProvider(settings.TAVILY_API_KEY)
when a key is configured, or None when it is not -- NOT a fixture
provider, NOT a silent no-op. None is a real, meaningful value every
caller must handle explicitly: it means "search is not configured",
distinct from "search ran and returned nothing" (an empty list). This is
the ONLY function in this module that touches config.settings --
TavilySearchProvider.__init__ still takes api_key as a plain constructor
argument (same reasoning as src/report/email.py's send_report taking a
plain SmtpConfig rather than reading settings itself: stays testable
without an environment). src/graph/nodes.py's news_node calls
get_search_provider() -- it never references TavilySearchProvider by
name directly, and neither does anything else outside src/horizon/; see
tests/test_horizon_search.py's structural test, tightened for this stage
to check the whole src/ tree outside src/horizon/, not just this one
file. FixtureSearchProvider remains for tests only; this factory never
returns one.

SEARCH DEPTH IS ALWAYS "basic" (1 credit), hardcoded as an explicit
constant rather than left to whatever Tavily defaults to -- "advanced"
costs 2 credits and can take 5+ seconds per call. At up to 12 searches
per label (one per additive), relying on a default that could change
underneath this code would silently double cost and latency. See
_SEARCH_DEPTH below.

SEARCH TOPIC IS ALWAYS "news", explicit and required, same reasoning as
search depth -- VERIFIED against a live call, not assumed: without
topic="news", Tavily's response has no published_date field AT ALL; with
it, published_date is present but as RFC 2822 text ("Tue, 10 Mar 2026
00:00:00 GMT"), not ISO. Every date-parsing decision below assumes this
topic is always set -- see _parse_tavily_date and _SEARCH_TOPIC.

INCLUDE_ANSWER IS ALWAYS EXPLICITLY False, and the response is asserted
to honour it. Tavily's top-level response carries query,
follow_up_questions, answer, images, results, response_time, request_id
(confirmed live) -- `answer` is Tavily's OWN generated summary of the
results, and this lane must never receive it: constraint 3 (see the
horizon-news design report) is that the model gets at most one neutral,
verbatim-quoted sentence per retrieved item, produced by THIS project's
own classify-and-quote step, never a third party's summary sitting
upstream of it where it could leak into a prompt unexamined. Not
requesting it is not enough on its own -- TavilySearchProvider.search()
raises if `answer` ever comes back non-null anyway, so a future default
change on Tavily's side fails loudly instead of quietly handing an
unvetted summary to whatever reads SearchResult next.

STALE/DEAD LINKS: Tavily can return stale or dead links from its own
cache, and its response carries no direct "this is stale" signal to key
off. validate_search_result uses the best available proxy instead: URL
well-formedness (a real scheme and netloc), the presence of a published
date, and now whether a present date actually PARSED (see
_UNPARSEABLE_DATE_FLAG) -- a result failing any of these is FLAGGED, not
dropped, so the caller still gets to see it and decide, with the problem
named rather than the result silently vanishing. This is a proxy, not a
verified staleness detector, and should be revisited as more real Tavily
responses are studied.

VERIFIED against a live response (query: "Titanium dioxide"): result
field names are title, url, content, published_date, score -- exactly
what TavilySearchProvider.search() already parsed. Also verified: a
result can be topically ON-DOMAIN and NAME-MATCHING and still not be
about the additive at all -- the same live query returned an EFSA
sweeteners working-group minutes PDF at score 0.52 that mentions
titanium dioxide only as the reason another agenda item was
deprioritised. Domain restriction and is_relevant() (src/horizon/
news.py) cannot close this gap; only the score can. See
news.py's _MIN_RELEVANCE_SCORE.
"""

from dataclasses import dataclass, field, replace
from datetime import date
from email.utils import parsedate_to_datetime
from typing import Protocol
from urllib.parse import urlparse

import httpx

from config import settings

_TAVILY_SEARCH_URL = "https://api.tavily.com/search"
# 1 credit. "advanced" costs 2 credits and can take 5+ seconds per call --
# explicit here, not Tavily's default, so a future change on their side
# can't silently double this project's cost or latency. See this module's
# docstring, "SEARCH DEPTH IS ALWAYS 'basic'".
_SEARCH_DEPTH = "basic"
# Required for published_date to be present at all -- see this module's
# docstring, "SEARCH TOPIC IS ALWAYS 'news'".
_SEARCH_TOPIC = "news"
# Explicit, not relied on as Tavily's default -- see this module's
# docstring, "INCLUDE_ANSWER IS ALWAYS EXPLICITLY False". Response is
# still asserted to honour this; see TavilySearchProvider.search().
_INCLUDE_ANSWER = False

_MALFORMED_URL_FLAG = "malformed_source_url"
_NO_PUBLISHED_DATE_FLAG = "no_published_date"
_UNPARSEABLE_DATE_FLAG = "unparseable_published_date"


def _parse_tavily_date(raw: str | None) -> date | None:
    """`raw` (Tavily's RFC 2822 published_date, e.g. "Tue, 10 Mar 2026
    00:00:00 GMT" -- MEASURED against a live response, not assumed) to a
    plain date, or None if `raw` is absent or does not parse. The
    ORIGINAL string is kept regardless (SearchResult.published_date_raw)
    so a parse failure is a flaggable, visible fact
    (_UNPARSEABLE_DATE_FLAG in validate_search_result), not silently
    indistinguishable from a date that was never present at all."""
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).date()
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class SearchResult:
    """One raw retrieval hit -- unvalidated. Always run through
    validate_search_result before display; nothing here decides what
    counts as malformed or stale on its own."""

    title: str
    url: str
    content: str
    published_date: date | None  # parsed from Tavily's RFC 2822 text; None if absent or unparseable
    published_date_raw: str | None  # exactly what the provider returned, always kept
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
        payload: dict[str, object] = {
            "query": query,
            "search_depth": _SEARCH_DEPTH,
            "topic": _SEARCH_TOPIC,
            "include_answer": _INCLUDE_ANSWER,
        }
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

        # Requesting include_answer=False is not enough on its own -- see
        # this module's docstring, "INCLUDE_ANSWER IS ALWAYS EXPLICITLY
        # False". A non-null answer here means Tavily's own summary would
        # otherwise flow straight to whatever reads SearchResult next,
        # which constraint 3 (the horizon-news design report) rules out
        # entirely -- fail loudly rather than let that happen quietly.
        if data.get("answer") is not None:
            raise RuntimeError(
                "Tavily response included a non-null 'answer' field -- this lane must never receive "
                "Tavily's own generated summary. Check that include_answer is not being requested."
            )

        results = []
        for item in data.get("results", []):
            raw_date = item.get("published_date")
            results.append(
                SearchResult(
                    title=item.get("title") or "",
                    url=item.get("url") or "",
                    content=item.get("content") or "",
                    published_date=_parse_tavily_date(raw_date),
                    published_date_raw=raw_date,
                    score=item.get("score"),
                )
            )
        return results


def get_search_provider() -> SearchProvider | None:
    """TavilySearchProvider(settings.TAVILY_API_KEY) when a key is
    configured, else None. See this module's docstring -- None is a real,
    meaningful return value, never a fixture provider and never a silent
    stand-in for "search ran and found nothing"; the caller (src/graph/
    nodes.py's news_node, via src/horizon/news.find_news_signals) must
    handle it explicitly."""
    if not settings.TAVILY_API_KEY:
        return None
    return TavilySearchProvider(settings.TAVILY_API_KEY)


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
    missing or unparseable published_date -- the best available proxy for
    "this looks stale" given Tavily's response carries no direct
    staleness signal (see this module's docstring). A date that is
    PRESENT (published_date_raw is set) but failed to parse
    (published_date is None) is flagged _UNPARSEABLE_DATE_FLAG, distinct
    from a date that was never returned at all (_NO_PUBLISHED_DATE_FLAG)
    -- the two are different failures worth telling apart: one is Tavily
    returning nothing, the other is this project's own parser not
    understanding what Tavily returned. Never drops a result for failing
    any check; a caller decides what to do with a flagged one, same
    discipline as src/horizon/schemas.py's HorizonSignal.flags
    (matched_by_name, doi_unverified) -- named, not hidden."""
    flags = list(result.flags)
    if not _is_well_formed_url(result.url):
        flags.append(_MALFORMED_URL_FLAG)
    if result.published_date is None:
        flags.append(_UNPARSEABLE_DATE_FLAG if result.published_date_raw else _NO_PUBLISHED_DATE_FLAG)
    if flags == result.flags:
        return result
    return replace(result, flags=flags)
