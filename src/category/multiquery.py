# DESIGN NOTE: this module DOES its own file I/O and network calls, like
# src/category/embedder.py -- caching generated paraphrases to disk for the
# same practical reason (avoiding repeat API cost on every re-run), not part
# of the pure retrieval core (corpus.py / filter.py / classifier.py stay
# pure).
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
import time

from google import genai
from google.genai import errors

from config import settings

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


class EmptyResponseError(RuntimeError):
    """Raised when the model returns no text -- blocked or truncated, not a crash."""


def _cache_key(query_text: str, model_id: str, n: int) -> str:
    # NOT keyed on the prompt text (unlike src/extractors/gemini.py's cache):
    # the prompt only asks for a context-free paraphrase of query_text, not
    # a project-specific behaviour that would need re-generating after a
    # wording tweak. If that assumption stops holding, fold the prompt into
    # this key the same way gemini.py does.
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


def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.removesuffix("```").strip()
    return text


def _call_model(prompt: str, model_id: str) -> str:
    """Call the model once, retrying with exponential backoff on transient
    errors -- the same retry/backoff shape as src/extractors/gemini.py's
    GeminiExtractor._call_model, reproduced here (not imported) so this
    module stays independent, the same reasoning src/report/narrator.py's
    own _call_model gives for its own duplicate of this block."""
    client = genai.Client(api_key=settings.GOOGLE_API_KEY)
    delay = INITIAL_BACKOFF_SECONDS
    for attempt in range(MAX_RATE_LIMIT_RETRIES):
        try:
            response = client.models.generate_content(model=model_id, contents=[prompt])
            if response.text is None:
                finish_reason = None
                if response.candidates:
                    finish_reason = response.candidates[0].finish_reason
                raise EmptyResponseError(f"model returned no text (finish_reason={finish_reason})")
            return response.text
        except errors.APIError as exc:
            if exc.code in RETRYABLE_STATUS_CODES and attempt < MAX_RATE_LIMIT_RETRIES - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise RuntimeError("unreachable")


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
    parsed = json.loads(_strip_markdown_fences(raw))
    if not isinstance(parsed, list):
        raise TypeError(f"expected a JSON array of paraphrases, got {type(parsed).__name__}: {raw!r}")
    paraphrases = [str(p) for p in parsed][: n - 1]

    cache[key] = paraphrases
    _save_cache(cache)
    return paraphrases
