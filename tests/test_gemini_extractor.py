"""Tests for src/extractors/gemini.py's GeminiExtractor -- the empty-response
guard's exception-class wiring (the highest-risk regression in the
VisionGenerator migration), and the MODEL_BACKEND switch through the REAL
get_vision_generator() selection (not a _call_model monkeypatch, which
would bypass the switch entirely), each with a mocked provider, proving
identical output for identical input. Same pattern as tests/test_narrator.py
and tests/test_multiquery.py.

tests/test_gemini_extractor_retry.py covers the RetryInfo-aware retry LOOP
specifically; this file does not repeat that.
"""

import json
from unittest.mock import Mock

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_google_genai import ChatGoogleGenerativeAI

import src.extractors.gemini as gemini_module
from config import settings
from src.extractors.gemini import GeminiExtractor
from src.schemas import GateResult
from src.text_generation import EmptyResponseError


def _gate_json() -> str:
    return json.dumps(
        {
            "is_food_label": True,
            "has_ingredients_declaration": True,
            "evidence_found": ["ingredients list present"],
            "reject_reason": None,
            "product_name": "Test Product",
            "product_descriptor": None,
            "languages_detected": ["en"],
            "language_selected": "en",
        }
    )


def _extract_json() -> str:
    return json.dumps(
        {
            "declaration_verbatim": "Sugar, Salt",
            "language": "en",
            "items": [],
            "unparsed_fragments": [],
            "warnings": [],
            "allergen_statements": [],
            "footnotes": {},
            "declaration_statements": [],
        }
    )


def test_empty_response_triggers_validation_retry_not_immediate_raise(monkeypatch, tmp_path):
    # GUARDS AGAINST A FUTURE REGRESSION. gemini.py used to define its own
    # EmptyResponseError; that class was deleted when _call_model started
    # delegating to src.text_generation's VisionGenerator, which raises
    # src.text_generation.EmptyResponseError instead -- and _run()'s except
    # tuple was updated to catch THAT class, in the same change. If a future
    # edit re-adds a local EmptyResponseError to gemini.py (or otherwise
    # changes this import) WITHOUT also updating _run()'s except tuple to
    # match, this exact test starts failing: an empty response would stop
    # being retried at all and would propagate immediately instead of
    # triggering the MAX_VALIDATION_RETRIES retry-with-feedback loop -- the
    # dangerous failure mode this module exists to avoid (a label that
    # fails to read must retry-then-FAIL, never silently produce nothing).
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    extractor = GeminiExtractor("fake-model")
    calls: list[str] = []

    def fake_call_model(image_bytes: bytes, prompt: str) -> str:
        calls.append(prompt)
        if len(calls) <= 2:
            raise EmptyResponseError("model returned no text (finish_reason=SAFETY, prompt_feedback=None)")
        return _gate_json()

    monkeypatch.setattr(extractor, "_call_model", fake_call_model)

    result = extractor.gate(b"fake-bytes")

    assert isinstance(result, GateResult)
    assert result.is_food_label is True
    # 2 failed attempts + 1 success, within MAX_VALIDATION_RETRIES=2 (3 total attempts) --
    # if the except tuple had missed EmptyResponseError, this would be 1, not 3.
    assert len(calls) == 3
    # The retry prompt carries the previous failure's message, proving the
    # loop actually ran with feedback rather than just happening to succeed.
    assert "model returned no text" in calls[1]


def test_gate_produces_identical_output_through_both_backends(monkeypatch, tmp_path):
    image_bytes = b"fake-image-bytes"
    monkeypatch.setattr(gemini_module, "_mime_type", lambda image_bytes: "image/jpeg")

    # -- native: google-genai's own Client, constructed inside
    # GeminiVisionGenerator.__init__ -- patched globally (google.genai.Client
    # itself), since _call_model -> get_vision_generator() constructs a
    # fresh GeminiVisionGenerator on every call; there is no instance to
    # swap ._client on from outside.
    fake_gemini_client = Mock()
    fake_gemini_client.models.generate_content.return_value = Mock(text=_gate_json(), candidates=[])
    monkeypatch.setattr("google.genai.Client", lambda **kwargs: fake_gemini_client)
    monkeypatch.setattr(settings, "MODEL_BACKEND", "native")
    # Own cache dir per backend -- _cache_key does not include MODEL_BACKEND
    # (see src.extractors.gemini._cache_key's own comment on why not), so a
    # shared cache dir would let the second call hit the first call's cache
    # entry and never touch the langchain path at all.
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path / "native")

    native_result = GeminiExtractor("fake-model").gate(image_bytes)

    # -- langchain: ChatGoogleGenerativeAI.generate patched at the class
    # level for the same reason (a fresh instance per complete() call).
    fake_langchain_result = LLMResult(
        generations=[[ChatGeneration(message=AIMessage(content=_gate_json()))]],
        llm_output={},
    )
    monkeypatch.setattr(ChatGoogleGenerativeAI, "generate", lambda self, messages: fake_langchain_result)
    monkeypatch.setattr(settings, "MODEL_BACKEND", "langchain")
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path / "langchain")

    langchain_result = GeminiExtractor("fake-model").gate(image_bytes)

    assert native_result == langchain_result
    assert native_result.is_food_label is True
    assert native_result.product_name == "Test Product"


def test_extract_produces_identical_output_through_both_backends(monkeypatch, tmp_path):
    image_bytes = b"fake-image-bytes"
    monkeypatch.setattr(gemini_module, "_mime_type", lambda image_bytes: "image/jpeg")

    fake_gemini_client = Mock()
    fake_gemini_client.models.generate_content.return_value = Mock(text=_extract_json(), candidates=[])
    monkeypatch.setattr("google.genai.Client", lambda **kwargs: fake_gemini_client)
    monkeypatch.setattr(settings, "MODEL_BACKEND", "native")
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path / "native")

    native_result = GeminiExtractor("fake-model").extract(image_bytes, "en")

    fake_langchain_result = LLMResult(
        generations=[[ChatGeneration(message=AIMessage(content=_extract_json()))]],
        llm_output={},
    )
    monkeypatch.setattr(ChatGoogleGenerativeAI, "generate", lambda self, messages: fake_langchain_result)
    monkeypatch.setattr(settings, "MODEL_BACKEND", "langchain")
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path / "langchain")

    langchain_result = GeminiExtractor("fake-model").extract(image_bytes, "en")

    assert native_result == langchain_result
    assert native_result.declaration_verbatim == "Sugar, Salt"
