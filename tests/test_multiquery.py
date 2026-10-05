"""Tests for src/category/multiquery.py -- mocked model calls, no network."""

import json
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_google_genai import ChatGoogleGenerativeAI

from config import settings
from src.category import multiquery
from src.category.multiquery import generate_paraphrases


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Every test gets its own empty cache file -- otherwise a previous
    test's cached paraphrases would let a later test's mocked model never
    get called, hiding whether caching is actually being exercised."""
    monkeypatch.setattr(multiquery, "_CACHE_PATH", tmp_path / "query_paraphrases.json")


def test_generate_paraphrases_returns_n_minus_one_strings(monkeypatch):
    calls = []

    def fake_call_model(prompt, model_id):
        calls.append(prompt)
        return json.dumps(["Seasoning spice blend", "Spice and seasoning mix"])

    monkeypatch.setattr(multiquery, "_call_model", fake_call_model)

    result = generate_paraphrases("Seasoning", 3, "fake-model")

    assert result == ["Seasoning spice blend", "Spice and seasoning mix"]
    assert len(calls) == 1
    assert "2" in calls[0]  # n-1 = 2 requested in the prompt


def test_generate_paraphrases_caches_on_text_model_and_n(monkeypatch):
    calls = []

    def fake_call_model(prompt, model_id):
        calls.append(prompt)
        return json.dumps(["variant a", "variant b"])

    monkeypatch.setattr(multiquery, "_call_model", fake_call_model)

    first = generate_paraphrases("Seasoning", 3, "fake-model")
    second = generate_paraphrases("Seasoning", 3, "fake-model")

    assert first == second
    assert len(calls) == 1  # the second call hit the cache, no second model call


def test_generate_paraphrases_different_n_is_a_different_cache_entry(monkeypatch):
    calls = []

    def fake_call_model(prompt, model_id):
        calls.append(prompt)
        return json.dumps(["v1", "v2", "v3", "v4"])

    monkeypatch.setattr(multiquery, "_call_model", fake_call_model)

    generate_paraphrases("Seasoning", 3, "fake-model")
    generate_paraphrases("Seasoning", 5, "fake-model")

    assert len(calls) == 2  # different n -- not a cache hit off the n=3 entry


def test_generate_paraphrases_strips_markdown_fences(monkeypatch):
    monkeypatch.setattr(
        multiquery, "_call_model", lambda prompt, model_id: '```json\n["a", "b"]\n```'
    )
    assert generate_paraphrases("Seasoning", 3, "fake-model") == ["a", "b"]


def test_generate_paraphrases_truncates_extra_entries(monkeypatch):
    # A model returning more than n-1 entries (over-generation) does not
    # silently expand what gets embedded downstream.
    monkeypatch.setattr(
        multiquery, "_call_model", lambda prompt, model_id: json.dumps(["a", "b", "c", "d"])
    )
    assert generate_paraphrases("Seasoning", 3, "fake-model") == ["a", "b"]


def test_generate_paraphrases_rejects_a_non_list_response(monkeypatch):
    monkeypatch.setattr(
        multiquery, "_call_model", lambda prompt, model_id: json.dumps({"paraphrases": ["a", "b"]})
    )
    with pytest.raises(TypeError):
        generate_paraphrases("Seasoning", 3, "fake-model")


def test_generate_paraphrases_n_below_2_returns_empty_without_calling_model(monkeypatch):
    def fail_if_called(prompt, model_id):
        raise AssertionError("should not be called when n < 2")

    monkeypatch.setattr(multiquery, "_call_model", fail_if_called)
    assert generate_paraphrases("Seasoning", 1, "fake-model") == []
    assert generate_paraphrases("Seasoning", 0, "fake-model") == []


# --------------------------------------------------------------------------- #
# MODEL_BACKEND switch -- both backends through the REAL get_text_generator()
# selection (not a _call_model monkeypatch, which would bypass the switch
# entirely), each with a mocked provider, proving identical output for
# identical input. Same pattern as tests/test_narrator.py.
# --------------------------------------------------------------------------- #
_FAKE_JSON_RESPONSE = json.dumps(["Seasoning spice blend", "Spice and seasoning mix"])


def test_paraphrases_identical_output_through_both_backends(monkeypatch):
    query_text, n, model_id = "Seasoning", 3, "fake-model"

    # -- native: google-genai's own Client, constructed inside
    # GeminiTextGenerator.__init__ -- patched globally (google.genai.Client
    # itself), since _call_model -> get_text_generator() constructs a fresh
    # GeminiTextGenerator on every call; there is no instance to swap
    # ._client on from outside.
    fake_gemini_client = Mock()
    fake_gemini_client.models.generate_content.return_value = Mock(text=_FAKE_JSON_RESPONSE, candidates=[])
    monkeypatch.setattr("google.genai.Client", lambda **kwargs: fake_gemini_client)
    monkeypatch.setattr(settings, "MODEL_BACKEND", "native")

    native_result = generate_paraphrases(query_text, n, model_id)

    # The cache key does NOT include MODEL_BACKEND (see multiquery._cache_key's
    # own comment on why not) -- a second call with the SAME (query_text,
    # model_id, n) would otherwise hit the cache the native call just wrote
    # and never touch the langchain path at all. Reset it so this is a real
    # second call, not a cache hit.
    multiquery._save_cache({})

    # -- langchain: ChatGoogleGenerativeAI.generate patched at the class
    # level for the same reason (a fresh instance per complete() call).
    fake_langchain_result = LLMResult(
        generations=[[ChatGeneration(message=AIMessage(content=_FAKE_JSON_RESPONSE))]],
        llm_output={},
    )
    monkeypatch.setattr(ChatGoogleGenerativeAI, "generate", lambda self, messages: fake_langchain_result)
    monkeypatch.setattr(settings, "MODEL_BACKEND", "langchain")

    langchain_result = generate_paraphrases(query_text, n, model_id)

    assert native_result == langchain_result
    assert native_result == ["Seasoning spice blend", "Spice and seasoning mix"]


def test_paraphrases_native_backend_is_the_default(monkeypatch):
    # No MODEL_BACKEND override at all -- current behaviour is what runs.
    assert settings.MODEL_BACKEND == "native"

    fake_gemini_client = Mock()
    fake_gemini_client.models.generate_content.return_value = Mock(text=_FAKE_JSON_RESPONSE, candidates=[])
    monkeypatch.setattr("google.genai.Client", lambda **kwargs: fake_gemini_client)

    result = generate_paraphrases("Seasoning", 3, "fake-model")

    assert result == ["Seasoning spice blend", "Spice and seasoning mix"]
    fake_gemini_client.models.generate_content.assert_called_once()
