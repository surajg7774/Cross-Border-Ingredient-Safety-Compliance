# DESIGN RULE: this module builds NewsSignal instances from SearchResults
# (src/horizon/search.py) -- it is the decision/classification logic for
# the news source, the role lane.py plays for the curated EFSA source.
# Unlike lane.py, this module is NOT pure end to end: is_relevant,
# render_news_sentence, check_modality_backstop, and find_news_signals
# itself are pure (data -- including the provider and cache -- in, data
# out, no settings access); classify_and_quote calls a model (via
# src.text_generation's TextGenerator seam, same pattern as src/report/
# narrator.py's _call_model) and is therefore not. find_news_signals ALSO
# does not import src.rules.schemas -- same reasoning lane.py's own
# DESIGN RULE gives for find_horizon_signals: it takes plain additive_ids/
# additive_names, never a ProductVerdict, so this module never needs to
# know how a verdict is shaped. The caller (src/graph/nodes.py's
# news_node) extracts those from state["verdict"], exactly as horizon_node
# already does for find_horizon_signals.
# STAGE 2: wired into the graph (src/graph/nodes.py's news_node) and the
# UI (src/ui/components.py). No test in this project calls a real model or
# a real SearchProvider through this module -- every test monkeypatches
# _call_model and uses FixtureSearchProvider, exactly as
# tests/test_narrator.py does for src/report/narrator.py.
"""Turns a retrieved SearchResult into a NewsSignal, or refuses to.

THE CONSTRAINT THIS MODULE EXISTS TO ENFORCE: the model may do at most one
neutral sentence per retrieved item, and it may NEVER write that sentence
itself. Free-form summarisation of web text about additive legality is out
of scope -- this project has measured three times that a prompt is a
request, not a guarantee (fence-stripping, the four empty-response shapes,
the caveats box), and "don't editorialise" is exactly the kind of
instruction a model drifts on under a strong headline ("E110 may soon be
banned in the EU" from a headline that says something weaker).

So the model's role is narrowed to something a prompt CAN reliably
guarantee: classify one retrieved item into a fixed category, and point at
a short verbatim excerpt of its own title/content as evidence. It does not
compose. Three structural checks then run on that output, and reject
rather than repair when a rule assessment implies actual harm:

1. quoted_span must be an exact substring of the retrieved title+content
   (whitespace-normalised only -- not case-folded, not fuzzy-matched).
   Reject the whole item if it is not -- classify_and_quote returns None,
   no NewsSignal gets built. A model that could not produce a real
   quotation gets no output at all, not a best-effort one.

2. check_category_consistency then checks the category the model picked
   against that SAME quoted_span (not the whole title/content the model
   also saw -- see the function's own comment for the MEASURED case this
   closes: "regulatory_review" badged over a quote that says the review
   completed). The verbatim check above proves the quote is real; this
   proves the badge is not contradicted by it. Category is chosen from
   the full retrieved text, which quoted_span alone does not constrain --
   without this, a badge can be "supported" by text the reader never
   sees, the same failure quoted_span's own verbatim check exists to stop
   for the sentence, just displaced onto the category field instead.

3. render_news_sentence deterministically builds the DISPLAYED sentence
   from a fixed per-category template plus the verbatim quote -- never
   from anything the model wrote freely. check_modality_backstop then
   scans that rendered sentence: a severity word ("banned", "illegal",
   "prohibited", "withdrawn", ...) may only appear if the SAME word
   appears in the source text. Sibling to src/report/narrator.py's
   _faithfulness_check -- identical verbatim-containment shape, a new
   word class. This is a BACKSTOP, not the primary defence: with
   quoted_span verified and the sentence template-built, there should be
   no free text left for an escalating word to hide in. It exists to
   catch a future regression in the template itself (e.g. someone adds
   free wording to a category label), not to be relied on as the main
   safeguard -- see build_news_signal, which treats a violation here
   exactly like a failed verbatim check: refuse to build the signal.
"""

import json
import logging
import re
from datetime import date

from src.horizon.news_cache import get_search_results
from src.horizon.schemas import NewsSignal
from src.horizon.search import SearchProvider, SearchResult, validate_search_result
from src.model_call import strip_markdown_fences
from src.text_generation import get_text_generator

log = logging.getLogger("horizon.news")

# Any word at least this long counts as a "recognisable stem" of the
# additive's name for relevance filtering -- short common words ("acid",
# "gum") are excluded so they don't false-positive-match unrelated
# articles; a name with no word this long falls back to requiring the
# exact whole name (see is_relevant).
_MIN_STEM_LENGTH = 4

# QUOTA CAPS -- named, not implicit. A 12-additive label must not silently
# become 12 searches plus 36+ model calls.
#
# MAX_ADDITIVES_PER_RUN: one search per DISTINCT additive (never per item
# -- see find_news_signals), capped at 12 -- this project has already
# measured/cited "12+ searches for one screening" as the realistic ceiling
# for a composite label (the horizon-news design report), so 12 covers the
# whole label without needing an unbounded loop. Additives beyond the cap
# are simply not searched this run -- see find_news_signals' docstring for
# what that means for coverage (the SAME "partial, not comprehensive"
# discipline lane.py already applies to the curated EFSA dataset).
MAX_ADDITIVES_PER_RUN = 12
# MAX_RESULTS_CLASSIFIED_PER_ADDITIVE: classify_and_quote is one model
# call per SearchResult -- classifying every result Tavily returns for an
# additive (its "basic" search_depth can return several) would make each
# additive's OWN cost unbounded regardless of the additive cap above. 3
# keeps a full 12-additive run to at most 12 searches + 36 model calls,
# not "12 searches + however many results each one happened to return".
MAX_RESULTS_CLASSIFIED_PER_ADDITIVE = 3

# Domain allowlist from the horizon-news design report's query-
# construction section -- primary/regulatory sources plus the trade press
# that actually covers EU additive regulation, not the open web. Passed
# to SearchProvider.search()'s include_domains, not folded into the query
# text -- domain restriction does more of the real filtering work than
# query phrasing (see src/horizon/search.py's SearchProvider docstring).
_INCLUDE_DOMAINS = (
    "efsa.europa.eu",
    "food.ec.europa.eu",
    "ec.europa.eu",
    "webgate.ec.europa.eu",  # the RASFF portal lives here
    "foodnavigator.com",
    "foodingredientsfirst.com",
    "foodsafetynews.com",
    "just-food.com",
    "reuters.com",
    "apnews.com",
)
# Regulatory news is not real-time (see src/horizon/news_cache.py's own
# TTL reasoning) -- restricting the SEARCH itself to the last year keeps
# results current without the query missing genuinely recent coverage the
# way a much narrower window (e.g. 30 days) would on a feature that only
# runs when a label happens to be screened, not continuously.
_DEFAULT_DAYS = 365

_CATEGORIES = frozenset(
    {"regulatory_review", "safety_opinion", "market_action", "consumer_alert", "unrelated"}
)
# "unrelated" is a valid CLASSIFIER output but deliberately not a valid
# NewsSignal.category -- see schemas.py's NewsSignal docstring. An item
# classified "unrelated" builds no signal at all.
_SIGNAL_CATEGORIES = _CATEGORIES - {"unrelated"}

_CATEGORY_LABELS = {
    "regulatory_review": "Under regulatory review",
    "safety_opinion": "Referenced in a safety opinion",
    "market_action": "Market or import action reported",
    "consumer_alert": "Referenced in a consumer alert",
}

# Severity/modality words that imply a stronger claim than "this additive
# was mentioned somewhere" -- may only appear in a rendered sentence if
# the identical word already appears in the source text. Same closed-list,
# verbatim-containment shape as src/report/narrator.py's own "Never write
# 'banned'" prompt rule -- but enforced here as a hard, code-level check,
# not a request to the model.
_MODALITY_WORDS = ("banned", "ban", "illegal", "prohibited", "withdrawn", "recalled", "recall", "outlawed")
_MODALITY_WORD_RE = re.compile(r"\b(" + "|".join(_MODALITY_WORDS) + r")\b", re.IGNORECASE)

# MEASURED against a live retrieval (E 968, .cache/horizon_news_cache.json):
# the model classified an item "regulatory_review" while its OWN quoted
# evidence read "Re-evaluation completed in 2023" -- EFSA's own pages name
# a finished re-evaluation report "Re-evaluation of X as a food additive",
# identically to how they'd describe the still-open process, so nothing in
# the category definitions given to the model disambiguates "review" the
# process from "review" the published output. Same "reject rather than
# repair" shape as _MODALITY_WORD_RE/check_modality_backstop, but checked
# against quoted_span directly (the text the reader actually sees), not
# the rendered sentence -- there is no rendered-sentence intermediary that
# could introduce the word, category and quoted_span are already both in
# hand inside classify_and_quote. "published" and "opened"/"launched"/
# "initiated" are deliberately excluded from both lists: each describes an
# action that can equally be a step within an ongoing review OR mark its
# conclusion (e.g. "opinion published for consultation" is still ongoing),
# so including them would reject genuinely correct pairs, not just
# contradictory ones.
_COMPLETION_WORDS = ("completed", "concludes", "concluded", "adopted", "finalised", "finalized")
_ONGOING_WORDS = ("ongoing", "underway", "in progress")
_COMPLETION_WORD_RE = re.compile(r"\b(" + "|".join(_COMPLETION_WORDS) + r")\b", re.IGNORECASE)
_ONGOING_WORD_RE = re.compile(r"\b(" + "|".join(_ONGOING_WORDS) + r")\b", re.IGNORECASE)


def check_category_consistency(category: str, quoted_span: str) -> bool:
    """False if `quoted_span` itself contradicts `category` -- a
    "regulatory_review" (an in-progress process) whose own quoted evidence
    says the review COMPLETED, or a "safety_opinion" (a published result)
    whose own quoted evidence says it is still ONGOING. True for every
    other category/quoted_span pair, including market_action and
    consumer_alert, which this function does not judge at all -- their
    definitions do not share this specific process-vs-output ambiguity.
    """
    if category == "regulatory_review":
        return not _COMPLETION_WORD_RE.search(quoted_span)
    if category == "safety_opinion":
        return not _ONGOING_WORD_RE.search(quoted_span)
    return True

_CLASSIFY_PROMPT_RULES = """You are classifying ONE retrieved news/press item about a food additive, \
for a horizon-scanning feature attached to an EU food-additive compliance tool. You do NOT write a \
summary, a headline, or any sentence of your own. You do two things only:

1. Pick exactly one category from this fixed set, describing what kind of item this is:
   - "regulatory_review": an EFSA/European Commission review, opinion process, or consultation that \
is STILL IN PROGRESS -- the text describes it as ongoing, underway, open, or not yet concluded
   - "safety_opinion": a safety assessment or scientific opinion that has ALREADY BEEN PUBLISHED or \
COMPLETED -- the text says the review concluded, the opinion was adopted, or states its finding \
(e.g. "found safe", "safe at a new ADI"). A page describing a finished re-evaluation is \
"safety_opinion" even if its own title still uses the word "review" or "re-evaluation" -- that word \
names the process that PRODUCED the opinion, not its current status; judge status from the \
surrounding text, not the presence of that word alone
   - "market_action": an import restriction, market withdrawal, or trade action
   - "consumer_alert": a RASFF alert or consumer-facing safety notice
   - "unrelated": the item does not genuinely concern this additive's EU regulatory status at all

2. Copy a short excerpt (one sentence or less) VERBATIM, character-for-character, from the title or \
content given below -- as evidence for the category you picked. Do not paraphrase, summarise, \
combine, or add a single word that is not already there. If you picked "unrelated", quoted_span must \
be an empty string.

Respond with RAW JSON ONLY, no markdown code fences, matching exactly this shape:
{{"category": "<one of regulatory_review|safety_opinion|market_action|consumer_alert|unrelated>", \
"quoted_span": "<verbatim excerpt, or empty string if unrelated>"}}

Additive: {additive_name}

Title: {title}

Content: {content}
"""


def _normalise_text(text: str) -> str:
    return " ".join(text.split())


def _content_words(name: str) -> list[str]:
    return [w for w in _normalise_text(name).lower().split() if len(w) >= _MIN_STEM_LENGTH]


# MEASURED against a live Tavily response (query: "Titanium dioxide"): a
# result scoring 0.52 was an EFSA sweeteners working-group minutes PDF
# that mentions titanium dioxide only as the reason another agenda item
# was deprioritised -- not actually about it. It sits on an allowed
# domain (efsa.europa.eu) and contains the additive's name verbatim, so
# it survives BOTH domain restriction and is_relevant() unchanged: this
# is the exact gap neither of those can close, since both check WHERE a
# result is from and WHETHER it mentions the additive, never HOW CENTRAL
# the additive is to it. Score is the only signal left. 0.6 is a starting
# threshold (comfortably above the measured 0.52 false-positive, not yet
# tuned against a larger sample) -- revisit once more real queries exist
# to check it against.
_MIN_RELEVANCE_SCORE = 0.6


def meets_score_threshold(result: SearchResult) -> bool:
    """True if `result`.score is at least _MIN_RELEVANCE_SCORE. A result
    with no score at all (score is None) fails the threshold -- absence
    of a signal is not evidence of relevance, so it is never treated as a
    free pass. Applied BEFORE is_relevant (see passes_prefilter) --
    score and keyword relevance catch different failure modes and
    neither substitutes for the other."""
    return result.score is not None and result.score >= _MIN_RELEVANCE_SCORE


def is_relevant(result: SearchResult, additive_name: str) -> bool:
    """True if `result`'s title+content contains the additive's name, or a
    recognisable stem of it, as a WHOLE WORD -- not a bare substring, so a
    short name like "gum" does not false-positive-match inside "legume".
    Run BEFORE anything reaches the model -- domain restriction
    (src/horizon/search.py's include_domains) narrows the SOURCE a result
    comes from, not its TOPIC; a regulatory-domain page about an unrelated
    substance is still a real failure mode this catches cheaply, for
    free, before spending a model call on it. Does NOT catch "on-topic
    but not central" (see meets_score_threshold for that gap)."""
    haystack = _normalise_text(f"{result.title} {result.content}").lower()
    candidates = _content_words(additive_name) or [_normalise_text(additive_name).lower()]
    return any(re.search(rf"\b{re.escape(word)}\b", haystack) for word in candidates)


def passes_prefilter(result: SearchResult, additive_name: str) -> bool:
    """meets_score_threshold AND is_relevant, in that order -- the
    ordering named in this function's own existence rather than left to
    whichever a future call site happens to check first. Both run before
    anything reaches the model; neither alone catches what the other
    does (see each function's own docstring)."""
    return meets_score_threshold(result) and is_relevant(result, additive_name)


def _build_classify_prompt(additive_name: str, result: SearchResult) -> str:
    return _CLASSIFY_PROMPT_RULES.format(additive_name=additive_name, title=result.title, content=result.content)


# The news lane is ADVISORY ONLY -- NewsSignal.affects_verdict is always
# False (see build_news_signal) -- so a struggling model here should
# degrade the news section, not cost the whole screening minutes of
# waiting. src.text_generation's shared default budget (5 attempts,
# 2s/4s/8s/16s/32s backoff, uncapped -- tuned for extraction/resolution/
# narration, which SHOULD wait as long as it takes) costs up to ~30s per
# failing call, and this lane makes up to MAX_RESULTS_CLASSIFIED_PER_
# ADDITIVE calls per additive -- a minute or more of waiting was OBSERVED
# from repeated 503 UNAVAILABLE "high demand" errors. Fewer attempts (2,
# vs. 5) and a lower backoff cap (4s, vs. uncapped) instead: one retry,
# a few seconds, then give up and let this ONE result be skipped (see
# classify_and_quote's caller, which already treats a failed classify as
# "no signal" rather than an error).
NEWS_MAX_RETRIES = 2
NEWS_INITIAL_BACKOFF_SECONDS = 2.0
NEWS_MAX_BACKOFF_SECONDS = 4.0


def _call_model(prompt: str, model_id: str) -> str:
    """Delegates to whichever TextGenerator settings.MODEL_BACKEND selects
    -- same seam src/report/narrator.py and src/category/multiquery.py
    already use, but with the NEWS_* retry budget above instead of that
    seam's own (larger) default -- see the comment there. Kept as its own
    function (rather than inlined into classify_and_quote) so tests can
    monkeypatch this ONE call site, the same pattern tests/test_narrator.py
    already establishes."""
    return get_text_generator().complete(
        prompt,
        model_id,
        max_retries=NEWS_MAX_RETRIES,
        initial_backoff_seconds=NEWS_INITIAL_BACKOFF_SECONDS,
        max_backoff_seconds=NEWS_MAX_BACKOFF_SECONDS,
    )


def classify_and_quote(result: SearchResult, additive_name: str, model_id: str) -> tuple[str, str] | None:
    """(category, quoted_span) for `result`, or None if classification
    should produce NO signal at all -- category "unrelated", a malformed/
    unparseable model response, an unrecognised category, a quoted_span
    that is not a verbatim (whitespace-normalised) substring of the
    retrieved title+content, or a quoted_span that contradicts the
    category chosen for it (check_category_consistency). Every rejection
    path returns None, never a best-effort guess -- see this module's
    docstring."""
    prompt = _build_classify_prompt(additive_name, result)
    try:
        raw = _call_model(prompt, model_id)
        data = json.loads(strip_markdown_fences(raw))
        category = str(data["category"])
        quoted_span = str(data["quoted_span"])
    except Exception as exc:  # noqa: BLE001 -- any failure (network, malformed JSON, missing key) means
        # no signal, never a crash -- classify_and_quote's contract is "a value or None", same discipline
        # as narrate()'s fallback-on-any-failure.
        log.warning("classify_and_quote: model call/parse failed: %s: %s", type(exc).__name__, exc)
        return None

    if category not in _SIGNAL_CATEGORIES:
        return None

    if not quoted_span:
        return None

    source_text = _normalise_text(f"{result.title} {result.content}")
    if _normalise_text(quoted_span) not in source_text:
        log.warning("classify_and_quote: quoted_span not verbatim in source, rejecting: %r", quoted_span)
        return None

    if not check_category_consistency(category, quoted_span):
        log.warning(
            "classify_and_quote: quoted_span contradicts category %r, rejecting: %r", category, quoted_span
        )
        return None

    return category, quoted_span


def render_news_sentence(additive_name: str, category: str, quoted_span: str, source: SearchResult) -> str:
    """The ONLY sentence ever displayed for a news item -- built entirely
    from a fixed per-category template plus the model's verbatim quote,
    never from model-composed prose. Raises KeyError if `category` is not
    one of _CATEGORY_LABELS' keys -- callers are expected to have already
    gone through classify_and_quote, which never returns an invalid one."""
    label = _CATEGORY_LABELS[category]
    date_part = f", {source.published_date}" if source.published_date else ""
    return f'{additive_name}: {label} -- "{quoted_span}" ({source.url}{date_part}).'


def check_modality_backstop(rendered_sentence: str, source_text: str) -> list[str]:
    """Severity/modality words present in `rendered_sentence` but ABSENT
    from `source_text` -- sibling to src/report/narrator.py's
    _faithfulness_check, identical verbatim-containment shape, a new word
    class (severity vocabulary instead of E-numbers/mg-kg/category
    codes). Empty list means no violation. Backstop only -- see this
    module's docstring for why the template above should make this
    unreachable in the first place."""
    normalised_source = _normalise_text(source_text).lower()
    violations = []
    for match in _MODALITY_WORD_RE.finditer(rendered_sentence):
        word = match.group(1).lower()
        if word not in normalised_source and word not in violations:
            violations.append(word)
    return violations


def build_news_signal(
    result: SearchResult,
    eu_canonical_id: str,
    additive_name: str,
    search_query: str,
    retrieved_at: str,
    model_id: str,
) -> NewsSignal | None:
    """Ties classify_and_quote, render_news_sentence, and
    check_modality_backstop together into one NewsSignal, or None when
    any step refuses. `result` should already have passed is_relevant and
    src/horizon/search.py's validate_search_result -- this function does
    not re-run either; result.flags is carried straight onto the returned
    signal so a quality issue on the underlying SearchResult (e.g.
    "malformed_source_url") is never silently dropped just because the
    item survived classification.

    severity="advisory" and affects_verdict=False are hardcoded here, in
    THIS function's own construction of NewsSignal -- not inherited from
    src/horizon/lane.py's HorizonSignal construction, not read from
    anywhere else. See schemas.py's NewsSignal docstring for why that
    independence is deliberate.
    """
    classified = classify_and_quote(result, additive_name, model_id)
    if classified is None:
        return None
    category, quoted_span = classified

    sentence = render_news_sentence(additive_name, category, quoted_span, result)
    source_text = f"{result.title} {result.content}"
    violations = check_modality_backstop(sentence, source_text)
    if violations:
        log.warning(
            "build_news_signal: modality backstop rejected a signal for %s: %s", additive_name, violations
        )
        return None

    return NewsSignal(
        eu_canonical_id=eu_canonical_id,
        substance_name=additive_name,
        category=category,
        quoted_span=quoted_span,
        source_url=result.url,
        # NewsSignal.published_date is str | None (schemas.py, unchanged
        # by this correction) -- SearchResult.published_date is now a
        # parsed date; isoformat() here, not str(), so the stored value
        # is unambiguous regardless of how a date happens to str()-format.
        published_date=result.published_date.isoformat() if result.published_date else None,
        search_query=search_query,
        retrieved_at=retrieved_at,
        severity="advisory",
        affects_verdict=False,
        flags=list(result.flags),
    )


def _build_query(additive_name: str) -> str:
    """The additive's own display name, not its E-number -- news and trade
    press overwhelmingly use chemical/common names (see the horizon-news
    design report's query-construction section). Identity-derived only,
    never anything product- or label-specific, so the SAME additive
    produces the SAME query (and therefore the SAME cache key) regardless
    of which label it showed up on -- see src/horizon/news_cache.py's own
    docstring on why the cache is keyed on additive identity, not label."""
    return f"{additive_name} food additive EU regulation"


def find_news_signals(
    additive_ids: list[str],
    provider: SearchProvider | None,
    cache: dict,
    model_id: str,
    today: date,
    additive_names: dict[str, str] | None = None,
) -> tuple[list[NewsSignal], dict]:
    """News-retrieved signals for `additive_ids`, mirroring find_horizon_
    signals' shape: additive_ids/additive_names are the SAME plain,
    verdict-decoupled inputs that function takes (see this module's
    DESIGN RULE), and the return is a plain value, not a partial mutation
    -- (signals, updated_cache), so the caller (src/graph/nodes.py's
    news_node) decides whether/how to persist the cache, exactly as
    src/horizon/news_cache.py's own get_search_results already returns an
    updated cache rather than writing one.

    `provider` is None when src/horizon/search.py's get_search_provider()
    found no configured key -- find_news_signals degrades to (\\[\\],
    cache) unchanged, no error, exactly like src/horizon/search.py's own
    "not a silent no-op, but also not a crash" contract for that case.

    For each of at most MAX_ADDITIVES_PER_RUN distinct additive_ids (never
    per item -- the SAME additive across several items or components in
    one label is searched once): build the query (_build_query), hit the
    cache (src/horizon/news_cache.get_search_results, 7-day TTL, stale-
    serve on a provider failure), validate every raw result
    (src/horizon/search.validate_search_result), keep the ones that pass
    passes_prefilter (score AND keyword relevance), classify-and-quote at
    most MAX_RESULTS_CLASSIFIED_PER_ADDITIVE of the survivors, and build a
    NewsSignal for each one classify_and_quote/build_news_signal accepts.
    A cache miss that also fails (get_search_results raises -- provider
    down, nothing cached yet for this additive) is logged and skipped,
    not fatal to the rest of the run: one additive's search failing must
    not lose every other additive's signals.

    `additive_ids` beyond the cap are not searched at all this run --
    the SAME "partial coverage, stated plainly" discipline lane.py's own
    coverage warning already applies to the curated EFSA dataset, just
    without a matching warnings list here (news_signals carries no
    separate warnings field -- see schemas.py's NewsSignal); logged via
    this module's own logger instead.
    """
    if provider is None:
        return [], cache

    checked_ids = sorted(set(additive_ids))
    capped_ids = checked_ids[:MAX_ADDITIVES_PER_RUN]
    if len(checked_ids) > MAX_ADDITIVES_PER_RUN:
        log.info(
            "find_news_signals: %d distinct additives, capped to %d this run: %s",
            len(checked_ids),
            MAX_ADDITIVES_PER_RUN,
            checked_ids[MAX_ADDITIVES_PER_RUN:],
        )

    additive_names = additive_names or {}
    signals: list[NewsSignal] = []
    updated_cache = cache

    for additive_id in capped_ids:
        additive_name = additive_names.get(additive_id, additive_id)
        query = _build_query(additive_name)

        def _fetch(query=query):
            return provider.search(query, include_domains=list(_INCLUDE_DOMAINS), days=_DEFAULT_DAYS)

        try:
            raw_results, updated_cache, cache_flags = get_search_results(updated_cache, additive_id, _fetch, today)
        except Exception as exc:  # noqa: BLE001 -- one additive's search failing must not lose the rest
            log.warning(
                "find_news_signals: search failed for %s (%s), skipping: %s: %s",
                additive_name,
                additive_id,
                type(exc).__name__,
                exc,
            )
            continue

        validated = [validate_search_result(r) for r in raw_results]
        candidates = [r for r in validated if passes_prefilter(r, additive_name)]
        candidates = candidates[:MAX_RESULTS_CLASSIFIED_PER_ADDITIVE]

        for result in candidates:
            signal = build_news_signal(result, additive_id, additive_name, query, today.isoformat(), model_id)
            if signal is None:
                continue
            if cache_flags:
                # "stale_cache_served" (src/horizon/news_cache.py) applies
                # to the whole batch this additive's search returned, not
                # to one result specifically -- carried onto every signal
                # built from it so a reader can tell this signal may be
                # older than the usual 7-day freshness window.
                signal = signal.model_copy(update={"flags": signal.flags + list(cache_flags)})
            signals.append(signal)

    return signals, updated_cache
