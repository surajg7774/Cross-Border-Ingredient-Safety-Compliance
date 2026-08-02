"""Tests for src/category/multiquery.py -- mocked model calls, no network."""

import json

import pytest

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
