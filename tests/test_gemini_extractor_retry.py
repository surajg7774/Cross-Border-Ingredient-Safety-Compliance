"""Tests for src/extractors/gemini.py's GeminiExtractor wiring its own
tuning constants into src/model_call.py's shared retry policy correctly,
through src/text_generation.py's GeminiVisionGenerator (_call_model
delegates there now, same as src/report/narrator.py and
src/category/multiquery.py delegate to the TextGenerator side). The retry
LOOP itself (exponential backoff, RetryInfo handling) is tested directly in
tests/test_model_call.py -- these tests only prove GeminiExtractor's own
tuning constants (INITIAL_BACKOFF_SECONDS, RETRY_DELAY_MARGIN_SECONDS) are
what actually gets used, end to end through a mocked google.genai.Client.

MEASURED: a 429 (GenerateRequestsPerMinutePerProjectPerModel-FreeTier)
carries an explicit RetryInfo.retryDelay the server wants respected --
these tests mock time.sleep and the API client, no real network calls.
"""

from unittest.mock import Mock

from google.genai import errors

import src.extractors.gemini as gemini_module
from src.extractors.gemini import (
    INITIAL_BACKOFF_SECONDS,
    RETRY_DELAY_MARGIN_SECONDS,
    GeminiExtractor,
)


def _fake_api_error(code: int, retry_delay: str | None = None) -> errors.APIError:
    details = []
    if retry_delay is not None:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay})
    return errors.APIError(code, {"error": {"code": code, "status": "RESOURCE_EXHAUSTED", "details": details}})


def _extractor_with_mocked_client(monkeypatch) -> tuple[GeminiExtractor, Mock]:
    # _call_model now delegates to src.text_generation.get_vision_generator(),
    # which constructs a fresh GeminiVisionGenerator -- and, inside it, a
    # fresh google.genai.Client -- on every call; there is no persistent
    # ._client instance on GeminiExtractor any more to swap from outside.
    # Patched globally instead, same reason and same pattern as
    # tests/test_narrator.py and tests/test_multiquery.py use for their own
    # cross-backend tests.
    extractor = GeminiExtractor("fake-model")
    monkeypatch.setattr(gemini_module, "_mime_type", lambda image_bytes: "image/jpeg")
    fake_client = Mock()
    monkeypatch.setattr("google.genai.Client", lambda **kwargs: fake_client)
    return extractor, fake_client


def test_429_with_retry_delay_sleeps_that_duration(monkeypatch):
    extractor, fake_client = _extractor_with_mocked_client(monkeypatch)
    fake_response = Mock(text="ok", candidates=[])
    fake_client.models.generate_content.side_effect = [
        _fake_api_error(429, "19.376064879s"),
        fake_response,
    ]
    sleep_calls: list[float] = []
    monkeypatch.setattr("src.model_call.time.sleep", lambda s: sleep_calls.append(s))

    result = extractor._call_model(b"fake-bytes", "prompt")

    assert result == "ok"
    assert sleep_calls == [19.376064879 + RETRY_DELAY_MARGIN_SECONDS]


def test_429_without_retry_delay_falls_back_to_exponential_backoff(monkeypatch):
    extractor, fake_client = _extractor_with_mocked_client(monkeypatch)
    fake_response = Mock(text="ok", candidates=[])
    fake_client.models.generate_content.side_effect = [
        _fake_api_error(500),  # no RetryInfo detail
        fake_response,
    ]
    sleep_calls: list[float] = []
    monkeypatch.setattr("src.model_call.time.sleep", lambda s: sleep_calls.append(s))

    result = extractor._call_model(b"fake-bytes", "prompt")

    assert result == "ok"
    assert sleep_calls == [INITIAL_BACKOFF_SECONDS]
