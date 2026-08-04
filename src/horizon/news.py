# DESIGN RULE: this module builds NewsSignal instances from SearchResults
# (src/horizon/search.py) -- it is the decision/classification logic for
# the news source, the role lane.py plays for the curated EFSA source.
# Unlike lane.py, this module is NOT pure end to end: is_relevant,
# render_news_sentence, and check_modality_backstop are pure; classify_and_
# quote calls a model (via src.text_generation's TextGenerator seam, same
# pattern as src/report/narrator.py's _call_model) and is therefore not.
# STAGE 1: nothing here is called by the graph or the UI yet, and no test
# in this project calls a real model through this module -- every test
# monkeypatches _call_model, exactly as tests/test_narrator.py does for
# src/report/narrator.py.
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
compose. Two structural checks then run on that output, and reject rather
than repair when a rule assessment implies actual harm:

1. quoted_span must be an exact substring of the retrieved title+content
   (whitespace-normalised only -- not case-folded, not fuzzy-matched).
   Reject the whole item if it is not -- classify_and_quote returns None,
   no NewsSignal gets built. A model that could not produce a real
   quotation gets no output at all, not a best-effort one.

2. render_news_sentence deterministically builds the DISPLAYED sentence
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

from src.horizon.schemas import NewsSignal
from src.horizon.search import SearchResult
from src.model_call import strip_markdown_fences
from src.text_generation import get_text_generator

log = logging.getLogger("horizon.news")

# Any word at least this long counts as a "recognisable stem" of the
# additive's name for relevance filtering -- short common words ("acid",
# "gum") are excluded so they don't false-positive-match unrelated
# articles; a name with no word this long falls back to requiring the
# exact whole name (see is_relevant).
_MIN_STEM_LENGTH = 4

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

_CLASSIFY_PROMPT_RULES = """You are classifying ONE retrieved news/press item about a food additive, \
for a horizon-scanning feature attached to an EU food-additive compliance tool. You do NOT write a \
summary, a headline, or any sentence of your own. You do two things only:

1. Pick exactly one category from this fixed set, describing what kind of item this is:
   - "regulatory_review": an EFSA/European Commission review, opinion process, or consultation
   - "safety_opinion": a published safety assessment or scientific opinion
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


def is_relevant(result: SearchResult, additive_name: str) -> bool:
    """True if `result`'s title+content contains the additive's name, or a
    recognisable stem of it, as a WHOLE WORD -- not a bare substring, so a
    short name like "gum" does not false-positive-match inside "legume".
    Run BEFORE anything reaches the model -- domain restriction
    (src/horizon/search.py's include_domains) narrows the SOURCE a result
    comes from, not its TOPIC; a regulatory-domain page about an unrelated
    substance is still a real failure mode this catches cheaply, for
    free, before spending a model call on it."""
    haystack = _normalise_text(f"{result.title} {result.content}").lower()
    candidates = _content_words(additive_name) or [_normalise_text(additive_name).lower()]
    return any(re.search(rf"\b{re.escape(word)}\b", haystack) for word in candidates)


def _build_classify_prompt(additive_name: str, result: SearchResult) -> str:
    return _CLASSIFY_PROMPT_RULES.format(additive_name=additive_name, title=result.title, content=result.content)


def _call_model(prompt: str, model_id: str) -> str:
    """Delegates to whichever TextGenerator settings.MODEL_BACKEND selects
    -- same seam src/report/narrator.py and src/category/multiquery.py
    already use. Kept as its own function (rather than inlined into
    classify_and_quote) so tests can monkeypatch this ONE call site, the
    same pattern tests/test_narrator.py already establishes."""
    return get_text_generator().complete(prompt, model_id)


def classify_and_quote(result: SearchResult, additive_name: str, model_id: str) -> tuple[str, str] | None:
    """(category, quoted_span) for `result`, or None if classification
    should produce NO signal at all -- category "unrelated", a malformed/
    unparseable model response, an unrecognised category, or a
    quoted_span that is not a verbatim (whitespace-normalised) substring
    of the retrieved title+content. Every rejection path returns None,
    never a best-effort guess -- see this module's docstring."""
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
        published_date=result.published_date,
        search_query=search_query,
        retrieved_at=retrieved_at,
        severity="advisory",
        affects_verdict=False,
        flags=list(result.flags),
    )
