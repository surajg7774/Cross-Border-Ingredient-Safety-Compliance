"""Gemini-backed extractor: sends label images to the Google GenAI API and parses the response."""

import hashlib
import json
import time
from io import BytesIO

from google import genai
from google.genai import errors, types
from PIL import Image
from pydantic import BaseModel, ValidationError

from config import settings
from src.prompts import EXTRACT_PROMPT, GATE_PROMPT
from src.schemas import ExtractionResult, GateResult

MAX_VALIDATION_RETRIES = 2
MAX_RATE_LIMIT_RETRIES = 5
INITIAL_BACKOFF_SECONDS = 2.0
# 429 = rate limited, 500/503 = transient server-side overload — all worth
# backing off and retrying rather than failing the whole run immediately.
RETRYABLE_STATUS_CODES = {429, 500, 503}


class EmptyResponseError(RuntimeError):
    """Raised when the model returns no text — blocked or truncated, not a crash."""


def _strip_markdown_fences(text: str | None) -> str:
    """Remove ```json / ``` fences a model added despite being told not to."""
    if not text:
        return ""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.removesuffix("```").strip()
    return text


def _mime_type(image_bytes: bytes) -> str:
    fmt = Image.open(BytesIO(image_bytes)).format or "JPEG"
    return f"image/{fmt.lower()}"


def _cache_key(image_bytes: bytes, model_id: str, pass_name: str, prompt: str) -> str:
    # The prompt text is part of the key so that editing a prompt invalidates
    # the cache — otherwise prompt iteration would silently keep returning
    # results generated under the old wording.
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
        self._client = genai.Client(api_key=settings.GOOGLE_API_KEY)

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
                result = schema.model_validate(json.loads(_strip_markdown_fences(raw)))
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
        """Call the model once, retrying with exponential backoff on transient errors."""
        part = types.Part.from_bytes(data=image_bytes, mime_type=_mime_type(image_bytes))
        delay = INITIAL_BACKOFF_SECONDS
        for attempt in range(MAX_RATE_LIMIT_RETRIES):
            try:
                response = self._client.models.generate_content(
                    model=self.model_id, contents=[prompt, part]
                )
                if response.text is None:
                    # The SDK returns None text when a response is blocked
                    # (safety) or truncated (e.g. hit max output tokens) —
                    # treat that as a failed attempt, not a crash.
                    finish_reason = None
                    if response.candidates:
                        finish_reason = response.candidates[0].finish_reason
                    raise EmptyResponseError(
                        f"model returned no text (finish_reason={finish_reason}, "
                        f"prompt_feedback={response.prompt_feedback})"
                    )
                return response.text
            except errors.APIError as exc:
                if exc.code in RETRYABLE_STATUS_CODES and attempt < MAX_RATE_LIMIT_RETRIES - 1:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise
        raise RuntimeError("unreachable")
