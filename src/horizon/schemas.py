# DESIGN RULE: this stage operates on additive IDENTITY (eu_canonical_id),
# not per-label items -- a horizon signal describes a SUBSTANCE under EFSA
# review, not a specific declaration on one label, so there is no item_id
# here to reference.
"""Pydantic models for the regulatory-horizon lane: advisory-only signals
about additives under active EFSA review. See src/horizon/lane.py's module
docstring for the hard constraint this stage exists under -- it can never
change a compliance verdict."""

from typing import Literal

from pydantic import BaseModel, Field


class HorizonSignal(BaseModel):
    eu_canonical_id: str
    substance_name: str
    stage: Literal["efsa_opinion", "efsa_assessment", "unknown"]
    title: str
    publication_date: str | None
    doi: str | None
    doi_verified: bool  # False until checked by hand against efsa.europa.eu
    source_url: str | None  # DOI resolved to a URL where possible
    years_old: int | None
    severity: Literal["advisory"]  # ALWAYS advisory -- see lane.py
    affects_verdict: bool  # ALWAYS False -- see lane.py
    note: str | None = None  # curator's annotation, from the dataset entry
    # "matched_by_name" when the E-number was absent from the dataset and
    # this signal was found via a (weaker) substance-name match instead --
    # see lane.py's matching logic. "doi_unverified" when doi_verified is
    # False -- an uncitable signal must say so, same discipline as the rule
    # engine's "uncitable_verdict" flag (src/rules/engine.py).
    flags: list[str] = Field(default_factory=list)


class NewsSignal(BaseModel):
    """One retrieved-and-classified news item about an additive -- SEPARATE
    from HorizonSignal, not a superset of it. The two share almost no real
    fields: HorizonSignal describes a primary regulatory document (a DOI
    that resolves to an actual EFSA opinion, hand-curated and DOI-verified
    by a person); this describes secondary reporting (a search hit,
    machine-retrieved, classified by a model, verified only in the narrow
    sense that quoted_span is checked to be a verbatim substring of what
    was actually retrieved -- see src/horizon/news.py). Forcing one shared
    model would mean doi/stage/year nullable here and search_query/
    retrieved_at/quoted_span/category nullable on HorizonSignal -- worse
    than two small, honest, purpose-fit schemas. Both still carry the
    SAME two invariants, because both sources are advisory-only -- see
    below.

    `category` deliberately excludes "unrelated" as a possible value:
    src/horizon/news.py's classify step returns that as a signal to build
    NO NewsSignal at all, not to construct one that says so -- a NewsSignal
    always describes something the model judged actually relevant, so an
    "unrelated" instance existing at all would be a bug, not a data point,
    and the type itself cannot represent it.

    `quoted_span` is the ONLY prose in this schema that came from a model,
    and it is a verbatim excerpt of the source, not a generated sentence --
    see src/horizon/news.py's module docstring for the full mechanism (the
    model classifies and points at a substring; code renders the displayed
    sentence from a fixed template, the model never writes it).
    """

    eu_canonical_id: str
    substance_name: str
    category: Literal["regulatory_review", "safety_opinion", "market_action", "consumer_alert"]
    quoted_span: str  # verbatim substring of the retrieved title+content
    source_url: str
    published_date: str | None
    search_query: str  # the query that retrieved this item -- provenance, not shown as-is to the reader
    retrieved_at: str  # when the search ran (distinct from published_date)
    # ALWAYS "advisory" / ALWAYS False -- constructed in THIS schema's own
    # code path (src/horizon/news.py), never inherited from HorizonSignal's
    # construction. See src/horizon/news.py's module docstring: this is a
    # SEPARATE guarantee on purpose, so neither source's invariant depends
    # on the other's code ever being correct.
    severity: Literal["advisory"]
    affects_verdict: bool
    # "malformed_source_url" / "no_published_date" carried forward from
    # src/horizon/search.py's validate_search_result -- a quality issue on
    # the underlying SearchResult is not silently dropped just because the
    # item survived relevance filtering and classification. "stale_cache_
    # served" from src/horizon/news_cache.py when a provider failure fell
    # back to a past-TTL cache entry (src/horizon/news.find_news_signals).
    flags: list[str] = Field(default_factory=list)


class RouteNewsSignal(BaseModel):
    """One retrieved-and-classified news item about an EU IMPORT ROUTE --
    an origin country and a product category, not any single additive.
    Sibling to NewsSignal (src/horizon/news.py's find_route_news_signals
    vs. find_news_signals), same two invariants (severity is always
    "advisory", affects_verdict is always False -- a trade headline never
    changes an Annex II verdict), but keyed by origin/category_name
    instead of eu_canonical_id/substance_name: a headline like "EU
    tightens checks on Indian spice imports" attaches to the PRODUCT and
    the COUNTRY, not to any single additive, so it cannot hang off
    eu_canonical_id the way NewsSignal does. See src/horizon/news.py's
    ROUTE-SCOPED NEWS section for the full mechanism -- same classify-
    and-quote discipline as NewsSignal (the model picks a category and
    quotes a verbatim span; it never composes the displayed sentence),
    just against a route-shaped category set instead of the additive one.
    """

    origin: str  # "India" -- a fixed constant in code (src/horizon/news.py's _ROUTE_ORIGIN), not user input
    category_name: str  # the confirmed product category's plain name, or "food" as the no-category fallback
    category: Literal["import_control", "border_rejection", "trade_agreement", "consumer_alert"]
    quoted_span: str  # verbatim substring of the retrieved title+content
    source_url: str
    published_date: str | None
    search_query: str  # the query that retrieved this item -- provenance, not shown as-is to the reader
    retrieved_at: str
    severity: Literal["advisory"]
    affects_verdict: bool
    flags: list[str] = Field(default_factory=list)


class HorizonResult(BaseModel):
    signals: list[HorizonSignal]
    checked_ids: list[str]  # which additives were looked up
    warnings: list[str]
    data_version: str
    data_retrieved: str | None  # so a stale snapshot is visible
    # Retrieved, machine-classified news -- a SEPARATE source from
    # `signals` above, not merged into it (see NewsSignal's own docstring
    # for why). Defaults to empty: a HorizonResult built anywhere that
    # never touches the news lane at all (e.g. app.py's direct-call
    # fallback paths, which call src/horizon/lane.py's find_horizon_
    # signals directly, same as before this feature existed) is still a
    # completely valid HorizonResult, degrading to "no news signals"
    # rather than failing to construct.
    news_signals: list[NewsSignal] = Field(default_factory=list)
    # ROUTE-scoped news -- a THIRD, separate source again (see
    # RouteNewsSignal's own docstring), never merged into `news_signals`:
    # a route item has no eu_canonical_id to carry, so folding it into
    # that list would mean making the field optional there, which would
    # let a route item silently pass through code written only for
    # additive-scoped signals. route_news_category is the plain category
    # name (or "food") this run's ONE route query actually used, kept
    # even when route_news_signals is empty so the UI can still say WHAT
    # was searched ("no recent EU news about spice exports from India")
    # instead of rendering nothing -- see src/ui/components.py.
    route_news_signals: list[RouteNewsSignal] = Field(default_factory=list)
    route_news_category: str | None = None
