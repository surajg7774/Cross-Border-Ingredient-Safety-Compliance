# DESIGN NOTE: this module DOES its own file I/O and network calls, like
# src/category/embedder.py -- caching generated paraphrases to disk for the
# same practical reason (avoiding repeat API cost on every re-run), not part
# of the pure retrieval core (corpus.py / filter.py / classifier.py stay
# pure). The model call itself is delegated to src/text_generation.py's
# TextGenerator (see _call_model below) -- this module no longer imports
# google-genai (or langchain-google-genai) directly, same as
# src/report/narrator.py.
"""Multi-query retrieval: paraphrase a query text several ways via one LLM
call, so retrieval is scored against several phrasings and fused, instead
of living or dying on one. MOTIVATION, measured: adding a hand-written
product description pushed the correct category OUT of one label's top-3
while pushing another label's correct category INTO rank 1 -- the same
intervention, opposite effects on different labels, i.e. high variance from
a single phrasing. Fusion (src.category.classifier.reciprocal_rank_fusion)
happens in the caller (scripts/classify_category.py); this module only
generates and caches the paraphrases.
"""

import hashlib
import json

from config import settings
from src.model_call import strip_markdown_fences
from src.text_generation import get_text_generator

# No longer read by _call_model (that retry policy now lives in
# src/text_generation.py's GeminiTextGenerator/LangChainTextGenerator, with
# the identical values). Kept in place, per instruction, as this call
# site's own record of what tuning it expects -- if text_generation.py's
# constants ever diverge from these, that is a signal worth noticing, not
# a reason to delete the comparison point.
MAX_RATE_LIMIT_RETRIES = 5
INITIAL_BACKOFF_SECONDS = 2.0
# Same retryable set as src/extractors/gemini.py: 429 = rate limited,
# 500/503 = transient server-side overload.
RETRYABLE_STATUS_CODES = {429, 500, 503}

_CACHE_PATH = settings.REFERENCE_DIR / "query_paraphrases.json"

_PROMPT = """Rewrite the following short food-category search query as {n} \
alternative phrasings that preserve its meaning exactly -- different word \
order or synonyms, never adding or removing anything it describes.

Query: {text}

Respond with RAW JSON ONLY, no markdown code fences: a JSON array of \
exactly {n} strings, nothing else."""


# No longer defines its own EmptyResponseError. _call_model used to raise
# one itself, with a message that (unlike src/text_generation.py's own,
# equivalent, class) omitted prompt_feedback -- one of four inconsistent
# empty-response shapes an audit flagged across the codebase. Now that
# _call_model fully delegates (below), this module never constructs that
# exception itself; whatever src.text_generation.TextGenerator.complete()
# raises propagates unchanged, so there is nothing left for a same-named
# local class to mean -- it would just be a second name for the same
# failure, one more place to keep in sync. src/report/narrator.py, migrated
# the same way, has none either. No caller in this codebase catches
# `multiquery.EmptyResponseError` by name (checked); a caller that needs to
# catch this specifically should catch src.text_generation.EmptyResponseError.


def _cache_key(query_text: str, model_id: str, n: int) -> str:
    # NOT keyed on the prompt text (unlike src/extractors/gemini.py's cache):
    # the prompt only asks for a context-free paraphrase of query_text, not
    # a project-specific behaviour that would need re-generating after a
    # wording tweak. If that assumption stops holding, fold the prompt into
    # this key the same way gemini.py does.
    #
    # Also NOT keyed on settings.MODEL_BACKEND, deliberately -- considered
    # and rejected when _call_model was migrated to delegate to
    # src/text_generation.py. MODEL_BACKEND selects a TRANSPORT (which
    # client library reaches model_id), not a different model or a different
    # request; src/text_generation.py's whole design goal, proven by
    # test_narrator.py's cross-backend identical-output test, is that both
    # backends behave identically for a successful call. Partitioning the
    # cache by backend would treat an infrastructure choice as if it changed
    # the answer, forcing a wasted re-generation on every backend switch for
    # no measured benefit. UNVERIFIED for real (non-mocked) traffic, though:
    # this holds only if the two backends truly produce the same output
    # distribution for identical prompts, which has only been checked
    # against a mocked identical response, not measured live. If that is
    # ever measured to not hold, this key needs MODEL_BACKEND folded in --
    # do not guess it back in preemptively.
    digest = hashlib.sha256()
    digest.update(query_text.encode("utf-8"))
    digest.update(model_id.encode("utf-8"))
    digest.update(str(n).encode("utf-8"))
    return digest.hexdigest()


def _load_cache() -> dict[str, list[str]]:
    if not _CACHE_PATH.exists():
        return {}
    return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))


def _save_cache(cache: dict[str, list[str]]) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _call_model(prompt: str, model_id: str) -> str:
    """Delegates to whichever src.text_generation.TextGenerator
    settings.MODEL_BACKEND selects -- "native" (default, the exact
    google-genai call path this function used inline before) or
    "langchain" (opt-in). See src/text_generation.py's module docstring
    for the retry policy, and for what the langchain backend cannot fully
    reproduce."""
    return get_text_generator().complete(prompt, model_id)


def generate_paraphrases(query_text: str, n: int, model_id: str) -> list[str]:
    """(n - 1) paraphrases of query_text -- the original itself is not
    included, the caller already has it. Cached on
    sha256(query_text + model_id + n), so a repeat run of the same config
    costs nothing. n must be >= 2 (n=1 means multi_query is effectively
    off; callers should not reach here for that case -- it would ask the
    model for "0 alternative phrasings").
    """
    if n < 2:
        return []
    cache = _load_cache()
    key = _cache_key(query_text, model_id, n)
    if key in cache:
        return cache[key]

    prompt = _PROMPT.format(n=n - 1, text=query_text)
    raw = _call_model(prompt, model_id)
    parsed = json.loads(strip_markdown_fences(raw))
    if not isinstance(parsed, list):
        raise TypeError(f"expected a JSON array of paraphrases, got {type(parsed).__name__}: {raw!r}")
    paraphrases = [str(p) for p in parsed][: n - 1]

    cache[key] = paraphrases
    _save_cache(cache)
    return paraphrases
