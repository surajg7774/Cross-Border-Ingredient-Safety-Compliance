"""Tests for src/text_generation.py -- the TextGenerator Protocol and its
two implementations. No real network calls: GeminiTextGenerator's client
is swapped for a Mock post-construction (same pattern as
tests/test_gemini_extractor_retry.py); LangChainTextGenerator's underlying
ChatGoogleGenerativeAI.generate is monkeypatched at the class level, since
a fresh ChatGoogleGenerativeAI is constructed inside every complete() call
(cheap, no I/O at construction time) rather than cached on the instance.
"""

from unittest.mock import Mock

import pytest
from google.genai import errors
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError

from config import settings
from src.text_generation import (
    EmptyResponseError,
    GeminiTextGenerator,
    LangChainTextGenerator,
    get_text_generator,
)


def _fake_api_error(code: int, retry_delay: str | None = None) -> errors.APIError:
    details = []
    if retry_delay is not None:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay})
    return errors.APIError(code, {"error": {"code": code, "status": "RESOURCE_EXHAUSTED", "details": details}})


def _llm_result(text: str, finish_reason: str | None = "STOP", llm_output: dict | None = None) -> LLMResult:
    metadata = {"finish_reason": finish_reason} if finish_reason else {}
    message = AIMessage(content=text, response_metadata=metadata)
    return LLMResult(generations=[[ChatGeneration(message=message)]], llm_output=llm_output or {})


# --------------------------------------------------------------------------- #
# get_text_generator -- the MODEL_BACKEND switch
# --------------------------------------------------------------------------- #
def test_get_text_generator_defaults_to_gemini(monkeypatch):
    monkeypatch.setattr(settings, "MODEL_BACKEND", "native")
    assert isinstance(get_text_generator(), GeminiTextGenerator)


def test_get_text_generator_returns_langchain_when_selected(monkeypatch):
    monkeypatch.setattr(settings, "MODEL_BACKEND", "langchain")
    assert isinstance(get_text_generator(), LangChainTextGenerator)


# --------------------------------------------------------------------------- #
# GeminiTextGenerator
# --------------------------------------------------------------------------- #
def _gemini_generator_with_mocked_client() -> GeminiTextGenerator:
    generator = GeminiTextGenerator()
    generator._client = Mock()
    return generator


def test_gemini_generator_returns_text():
    generator = _gemini_generator_with_mocked_client()
    generator._client.models.generate_content.return_value = Mock(text="hello", candidates=[])

    assert generator.complete("prompt", "fake-model") == "hello"


def test_gemini_generator_raises_on_empty_response():
    generator = _gemini_generator_with_mocked_client()
    generator._client.models.generate_content.return_value = Mock(
        text=None, candidates=[], prompt_feedback="BLOCKED"
    )

    with pytest.raises(EmptyResponseError):
        generator.complete("prompt", "fake-model")


def test_gemini_generator_retries_on_429_then_succeeds(monkeypatch):
    generator = _gemini_generator_with_mocked_client()
    generator._client.models.generate_content.side_effect = [
        _fake_api_error(429),
        Mock(text="hello", candidates=[]),
    ]
    monkeypatch.setattr("src.model_call.time.sleep", lambda s: None)

    assert generator.complete("prompt", "fake-model") == "hello"


# --------------------------------------------------------------------------- #
# LangChainTextGenerator
# --------------------------------------------------------------------------- #
def test_langchain_generator_returns_text(monkeypatch):
    monkeypatch.setattr(ChatGoogleGenerativeAI, "generate", lambda self, messages: _llm_result("hello"))

    generator = LangChainTextGenerator()
    assert generator.complete("prompt", "fake-model") == "hello"


def test_langchain_generator_constructs_with_max_retries_one(monkeypatch):
    # Disables ChatGoogleGenerativeAI's OWN retry loop (default 6) so
    # with_retry is the only retry policy in effect -- see
    # src/text_generation.py's module docstring.
    captured: dict = {}
    real_init = ChatGoogleGenerativeAI.__init__

    def _capture_init(self, **kwargs):
        captured.update(kwargs)
        return real_init(self, **kwargs)

    monkeypatch.setattr(ChatGoogleGenerativeAI, "__init__", _capture_init)
    monkeypatch.setattr(ChatGoogleGenerativeAI, "generate", lambda self, messages: _llm_result("hello"))

    LangChainTextGenerator().complete("prompt", "fake-model")

    assert captured["max_retries"] == 1


def test_langchain_generator_raises_on_empty_response_with_metadata(monkeypatch):
    # A candidate exists but carries no text (e.g. truncated) -- finish_reason
    # survives via response_metadata, per the module docstring.
    result = _llm_result("", finish_reason="MAX_TOKENS")
    monkeypatch.setattr(ChatGoogleGenerativeAI, "generate", lambda self, messages: result)

    generator = LangChainTextGenerator()
    with pytest.raises(EmptyResponseError, match="MAX_TOKENS"):
        generator.complete("prompt", "fake-model")


def test_langchain_generator_raises_on_zero_candidates_using_llm_output_prompt_feedback(monkeypatch):
    # The zero-candidates case: langchain_google_genai's own non-streaming
    # branch attaches NO metadata to the message itself -- llm_output is
    # what recovers prompt_feedback, per the module docstring.
    result = _llm_result("", finish_reason=None, llm_output={"prompt_feedback": {"block_reason": "SAFETY"}})
    monkeypatch.setattr(ChatGoogleGenerativeAI, "generate", lambda self, messages: result)

    generator = LangChainTextGenerator()
    with pytest.raises(EmptyResponseError, match="SAFETY"):
        generator.complete("prompt", "fake-model")


def test_langchain_generator_unwraps_wrapped_429_and_retries(monkeypatch):
    # ChatGoogleGenerativeAI re-raises a 429 ClientError as its own
    # ChatGoogleGenerativeAIError (see _handle_client_error) -- this must be
    # unwrapped back to the original APIError so with_retry's `except
    # errors.APIError` actually catches it and retries, exactly as the
    # native path would.
    api_error = _fake_api_error(429)
    calls = {"n": 0}

    def _generate(self, messages):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ChatGoogleGenerativeAIError("Error calling model") from api_error
        return _llm_result("hello")

    monkeypatch.setattr(ChatGoogleGenerativeAI, "generate", _generate)
    monkeypatch.setattr("src.model_call.time.sleep", lambda s: None)

    generator = LangChainTextGenerator()
    assert generator.complete("prompt", "fake-model") == "hello"
    assert calls["n"] == 2


def test_langchain_generator_does_not_unwrap_unrelated_errors(monkeypatch):
    # A ChatGoogleGenerativeAIError NOT caused by an APIError (e.g. a
    # message-formatting bug) must propagate as-is, not be swallowed or
    # misidentified as retryable.
    def _generate(self, messages):
        raise ChatGoogleGenerativeAIError("unrelated failure")

    monkeypatch.setattr(ChatGoogleGenerativeAI, "generate", _generate)

    generator = LangChainTextGenerator()
    with pytest.raises(ChatGoogleGenerativeAIError, match="unrelated failure"):
        generator.complete("prompt", "fake-model")
