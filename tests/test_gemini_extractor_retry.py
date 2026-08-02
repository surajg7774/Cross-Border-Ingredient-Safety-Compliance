"""Tests for src/extractors/gemini.py's rate-limit retry behaviour.

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
    _retry_delay_seconds,
)


def _fake_api_error(code: int, retry_delay: str | None = None) -> errors.APIError:
    details = []
    if retry_delay is not None:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay})
    return errors.APIError(code, {"error": {"code": code, "status": "RESOURCE_EXHAUSTED", "details": details}})


def test_retry_delay_seconds_parses_the_real_error_shape():
    # The exact shape captured from a live 429 during development.
    exc = _fake_api_error(429, "19.376064879s")
    assert _retry_delay_seconds(exc) == 19.376064879


def test_retry_delay_seconds_is_none_without_retry_info():
    exc = _fake_api_error(500)
    assert _retry_delay_seconds(exc) is None


def _extractor_with_mocked_client(monkeypatch) -> GeminiExtractor:
    extractor = GeminiExtractor("fake-model")
    extractor._client = Mock()
    monkeypatch.setattr(gemini_module, "_mime_type", lambda image_bytes: "image/jpeg")
    return extractor


def test_429_with_retry_delay_sleeps_that_duration(monkeypatch):
    extractor = _extractor_with_mocked_client(monkeypatch)
    fake_response = Mock(text="ok", candidates=[])
    extractor._client.models.generate_content.side_effect = [
        _fake_api_error(429, "19.376064879s"),
        fake_response,
    ]
    sleep_calls: list[float] = []
    monkeypatch.setattr(gemini_module.time, "sleep", lambda s: sleep_calls.append(s))

    result = extractor._call_model(b"fake-bytes", "prompt")

    assert result == "ok"
    assert sleep_calls == [19.376064879 + RETRY_DELAY_MARGIN_SECONDS]


def test_429_without_retry_delay_falls_back_to_exponential_backoff(monkeypatch):
    extractor = _extractor_with_mocked_client(monkeypatch)
    fake_response = Mock(text="ok", candidates=[])
    extractor._client.models.generate_content.side_effect = [
        _fake_api_error(500),  # no RetryInfo detail
        fake_response,
    ]
    sleep_calls: list[float] = []
    monkeypatch.setattr(gemini_module.time, "sleep", lambda s: sleep_calls.append(s))

    result = extractor._call_model(b"fake-bytes", "prompt")

    assert result == "ok"
    assert sleep_calls == [INITIAL_BACKOFF_SECONDS]
