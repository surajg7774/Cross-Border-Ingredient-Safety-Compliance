# DESIGN RULE: this module holds provider-facing PLUMBING shared by every
# direct google-genai call site (src/extractors/gemini.py, src/agent/
# resolver_agent.py, src/report/narrator.py, src/category/multiquery.py) --
# it is not a pipeline stage, so importing it from any of them is not the
# stage-boundary violation importing one STAGE's module from another would
# be (see e.g. src/substitutes/advisor.py's own DESIGN RULE comment on why
# it duplicates rather than imports src.rules.engine -- that rule is about
# stage independence, not about never sharing code at all). Caching and
# empty-response handling are deliberately NOT here: those differ across
# call sites for real reasons (different data shapes, different fallback
# behaviour) and were left alone in this consolidation on purpose.
"""Two pieces of plumbing every direct model call site duplicated
byte-for-byte (fence-stripping) or near-identically (the retry loop shape):

    strip_markdown_fences(text) -> str
    retry_delay_seconds(exc) -> float | None
    with_retry(call, *, max_retries, initial_backoff_seconds,
               retryable_status_codes, retry_delay_fn=None,
               retry_delay_margin_seconds=0.0, max_backoff_seconds=None) -> T

Each call site keeps its OWN tuning constants exactly where they already
were (MAX_RATE_LIMIT_RETRIES, INITIAL_BACKOFF_SECONDS, etc. -- e.g.
src/category/embedder.py's 8-retry/65s-cap tuning for a per-minute embed
quota vs. src/extractors/gemini.py's 5-retry, uncapped tuning are both
legitimate and stay put) -- only the LOOP SHAPE moved here, taking those
constants as parameters. `retry_delay_fn` is optional: gemini.py and
resolver_agent.py pass `retry_delay_seconds` (below) to honour a 429's
server-supplied RetryInfo.retryDelay before falling back to exponential
backoff; narrator.py and multiquery.py pass none, preserving their
existing plain-exponential-only behaviour exactly.
"""

import time
from collections.abc import Callable
from typing import TypeVar

from google.genai import errors

T = TypeVar("T")


def strip_markdown_fences(text: str | None) -> str:
    """Remove ```json / ``` fences a model added despite being told not to.
    Identical behaviour at all 4 former call sites -- consolidated here."""
    if not text:
        return ""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.removesuffix("```").strip()
    return text


def retry_delay_seconds(exc: errors.APIError) -> float | None:
    """The server's own explicit wait time from a 429's RetryInfo detail
    (e.g. "Please retry in 49.445998484s." -> 49.445998484), if present.

    MEASURED (originally on src/extractors/gemini.py): scripts/agent_review.py
    --all hit GenerateRequestsPerMinutePerProjectPerModel-FreeTier
    (quotaValue 15) on gemini-3.5-flash-lite -- a per-minute limit a fixed
    exponential backoff starting at a couple of seconds can undershoot by a
    wide margin; the server was asking for 19-49s. exc.details is the raw
    error body, `{"error": {..., "details": [..., {"@type":
    ".../google.rpc.RetryInfo", "retryDelay": "49s"}]}}` -- a list of typed
    detail objects, not always present (only quota errors carry RetryInfo;
    a transient 500/503 usually doesn't), which is why with_retry falls back
    to exponential backoff when this returns None.
    """
    details = getattr(exc, "details", None)
    if not isinstance(details, dict):
        return None
    error = details.get("error", details)
    for detail in error.get("details") or []:
        if not isinstance(detail, dict):
            continue
        if str(detail.get("@type", "")).endswith("RetryInfo"):
            raw = detail.get("retryDelay")
            if isinstance(raw, str) and raw.endswith("s"):
                try:
                    return float(raw[:-1])
                except ValueError:
                    return None
    return None


def with_retry(
    call: Callable[[], T],
    *,
    max_retries: int,
    initial_backoff_seconds: float,
    retryable_status_codes: set[int],
    retry_delay_fn: Callable[[errors.APIError], float | None] | None = None,
    retry_delay_margin_seconds: float = 0.0,
    max_backoff_seconds: float | None = None,
) -> T:
    """Call `call()`, retrying up to `max_retries` attempts total whenever it
    raises a google.genai.errors.APIError whose `.code` is in
    `retryable_status_codes`. Backoff is exponential -- doubling from
    `initial_backoff_seconds`, capped at `max_backoff_seconds` if given --
    UNLESS `retry_delay_fn` is given and returns a delay for the caught
    exception, in which case that delay (plus `retry_delay_margin_seconds`)
    is used instead of the exponential schedule for that attempt. Re-raises
    immediately on a non-retryable status code, or on the final attempt.
    """
    delay = initial_backoff_seconds
    for attempt in range(max_retries):
        try:
            return call()
        except errors.APIError as exc:
            if exc.code in retryable_status_codes and attempt < max_retries - 1:
                delay_override = retry_delay_fn(exc) if retry_delay_fn else None
                if delay_override is not None:
                    time.sleep(delay_override + retry_delay_margin_seconds)
                else:
                    time.sleep(delay)
                    delay = min(delay * 2, max_backoff_seconds) if max_backoff_seconds else delay * 2
                continue
            raise
    raise RuntimeError("unreachable")
