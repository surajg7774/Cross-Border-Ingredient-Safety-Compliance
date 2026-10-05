"""Gemini-backed extractor: sends label images to the Google GenAI API and parses the response."""

import hashlib
import json
from io import BytesIO

from PIL import Image
from pydantic import BaseModel, ValidationError

from config import settings
from src.model_call import strip_markdown_fences
from src.prompts import EXTRACT_PROMPT, GATE_PROMPT
from src.schemas import ExtractionResult, GateResult
from src.text_generation import EmptyResponseError, get_vision_generator

MAX_VALIDATION_RETRIES = 2
# No longer read by _call_model (that retry policy -- including
# retry_delay_fn=retry_delay_seconds and RETRY_DELAY_MARGIN_SECONDS -- now
# lives in src/text_generation.py's GeminiVisionGenerator/
# LangChainVisionGenerator, with these identical values). Kept in place,
# per instruction, as this call site's own record of what tuning it
# expects, and because tests/test_gemini_extractor_retry.py still imports
# INITIAL_BACKOFF_SECONDS and RETRY_DELAY_MARGIN_SECONDS from here directly.
MAX_RATE_LIMIT_RETRIES = 5
INITIAL_BACKOFF_SECONDS = 2.0
# Added to the server's own RetryInfo.retryDelay before sleeping -- a small
# safety margin, not a guess at the delay itself (see
# src/model_call.py's retry_delay_seconds).
RETRY_DELAY_MARGIN_SECONDS = 1.0
# 429 = rate limited, 500/503 = transient server-side overload — all worth
# backing off and retrying rather than failing the whole run immediately.
RETRYABLE_STATUS_CODES = {429, 500, 503}

# No longer defines its own EmptyResponseError. _call_model used to raise
# one itself; now that it delegates to src.text_generation's VisionGenerator
# (see below), that module's EmptyResponseError is what actually gets
# raised. This module's own class would be dead code AND, worse, a trap:
# _run()'s except tuple below catches EmptyResponseError BY NAME as one of
# its three retry-triggering failure types (see that docstring). If a local
# class were re-added here without also re-pointing the except tuple at it,
# the tuple would silently stop matching what src.text_generation actually
# raises -- an empty response would then propagate immediately instead of
# triggering the MAX_VALIDATION_RETRIES retry-with-feedback loop, which is
# exactly the dangerous failure mode this module exists to avoid (a label
# that fails to read must retry-then-FAIL, never silently produce nothing).
# tests/test_gemini_extractor.py's
# test_empty_response_triggers_validation_retry_not_immediate_raise guards
# against this regression.


def _mime_type(image_bytes: bytes) -> str:
    fmt = Image.open(BytesIO(image_bytes)).format or "JPEG"
    return f"image/{fmt.lower()}"


def _cache_key(image_bytes: bytes, model_id: str, pass_name: str, prompt: str) -> str:
    # The prompt text is part of the key so that editing a prompt invalidates
    # the cache — otherwise prompt iteration would silently keep returning
    # results generated under the old wording.
    #
    # Also NOT keyed on settings.MODEL_BACKEND. This rests on
    # REQUEST-CONSTRUCTION equivalence, verified by reading the installed
    # langchain-google-genai source (see src/text_generation.py's module
    # docstring, "WHAT WAS CHECKED FOR MULTIMODAL INPUT SPECIFICALLY"):
    # langchain's "media" content block and google-genai's own
    # types.Part.from_bytes both build the identical Part(inline_data=
    # Blob(...)) object before either reaches the same underlying
    # generate_content call. That is NOT the same as a live comparison of
    # actual model output across both backends on a real image -- none has
    # been made. A live cross-backend extraction comparison on real labels
    # is required before MODEL_BACKEND=langchain is trusted for real
    # extraction runs; until then, treat a cached result as backend-specific
    # in spirit even though the key does not partition on it. If that
    # comparison ever finds real divergence, MODEL_BACKEND must be folded
    # into this key -- do not guess it back in preemptively before that.
    digest = hashlib.sha256()
    digest.update(image_bytes)
    digest.update(model_id.encode("utf-8"))
    digest.update(pass_name.encode("utf-8"))
    digest.update(prompt.encode("utf-8"))
    return digest.hexdigest()


class GeminiExtractor:
    """LabelExtractor backed by the Google GenAI SDK."""

    def __init__(self, model_id: str):
        self.model_id = model_id

    def gate(self, image_bytes: bytes) -> GateResult:
        return self._run(image_bytes, GATE_PROMPT, "gate", GateResult)

    def extract(self, image_bytes: bytes, language: str) -> ExtractionResult:
        prompt = EXTRACT_PROMPT.replace("{language}", language)
        return self._run(image_bytes, prompt, "extract", ExtractionResult)

    def _run(
        self, image_bytes: bytes, prompt: str, pass_name: str, schema: type[BaseModel]
    ) -> BaseModel:
        """Check cache, else call the model with up to MAX_VALIDATION_RETRIES retries."""
        settings.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        key = _cache_key(image_bytes, self.model_id, pass_name, prompt)
        raw_path = settings.CACHE_DIR / f"{key}.raw.txt"
        parsed_path = settings.CACHE_DIR / f"{key}.parsed.json"
        if raw_path.exists() and parsed_path.exists():
            return schema.model_validate_json(parsed_path.read_text(encoding="utf-8"))

        last_error: Exception | None = None
        for attempt in range(MAX_VALIDATION_RETRIES + 1):
            call_prompt = prompt
            if attempt > 0:
                call_prompt = (
                    f"{prompt}\n\nYour previous response failed validation with this "
                    f"error — fix it and respond again with RAW JSON ONLY:\n{last_error}"
                )
            raw = None
            try:
                raw = self._call_model(image_bytes, call_prompt)
                result = schema.model_validate(json.loads(strip_markdown_fences(raw)))
            except (json.JSONDecodeError, ValidationError, EmptyResponseError) as exc:
                last_error = exc
                # Save whatever we have — the raw text if the model returned
                # one, otherwise the failure details — so a failing run is
                # still debuggable instead of leaving no evidence behind.
                raw_path.write_text(raw if raw is not None else str(exc), encoding="utf-8")
                continue
            raw_path.write_text(raw, encoding="utf-8")
            parsed_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
            return result

        raise ValueError(f"{pass_name} extraction failed after retries: {last_error}")

    def _call_model(self, image_bytes: bytes, prompt: str) -> str:
        """Delegates to whichever src.text_generation.VisionGenerator
        settings.MODEL_BACKEND selects -- "native" (default, the exact
        google-genai call path this function used inline before) or
        "langchain" (opt-in). See src/text_generation.py's module docstring
        for the retry policy, and for what the langchain backend does and
        does not reproduce for multimodal input specifically -- including
        what is not yet live-verified before trusting it for a real
        extraction run."""
        return get_vision_generator().complete(prompt, image_bytes, _mime_type(image_bytes), self.model_id)
