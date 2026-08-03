"""Tests for src/model_call.py -- the fence-stripping and retry/backoff
plumbing shared by every direct google-genai call site (previously
duplicated in src/extractors/gemini.py, src/agent/resolver_agent.py,
src/report/narrator.py, src/category/multiquery.py). No real network
calls -- errors.APIError instances are hand-built, time.sleep is mocked.
"""

from google.genai import errors

from src.model_call import retry_delay_seconds, strip_markdown_fences, with_retry


def _fake_api_error(code: int, retry_delay: str | None = None) -> errors.APIError:
    details = []
    if retry_delay is not None:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay})
    return errors.APIError(code, {"error": {"code": code, "status": "RESOURCE_EXHAUSTED", "details": details}})


# --------------------------------------------------------------------------- #
# retry_delay_seconds
# --------------------------------------------------------------------------- #
def test_retry_delay_seconds_parses_the_real_error_shape():
    # The exact shape captured from a live 429 during development.
    exc = _fake_api_error(429, "19.376064879s")
    assert retry_delay_seconds(exc) == 19.376064879


def test_retry_delay_seconds_is_none_without_retry_info():
    exc = _fake_api_error(500)
    assert retry_delay_seconds(exc) is None


# --------------------------------------------------------------------------- #
# strip_markdown_fences
# --------------------------------------------------------------------------- #
def test_strip_markdown_fences_removes_json_fence():
    assert strip_markdown_fences('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_markdown_fences_removes_bare_fence():
    assert strip_markdown_fences("```\nhello\n```") == "hello"


def test_strip_markdown_fences_leaves_unfenced_text_untouched():
    assert strip_markdown_fences('{"a": 1}') == '{"a": 1}'


def test_strip_markdown_fences_empty_and_none_return_empty_string():
    assert strip_markdown_fences("") == ""
    assert strip_markdown_fences(None) == ""


# --------------------------------------------------------------------------- #
# with_retry
# --------------------------------------------------------------------------- #
def test_with_retry_returns_immediately_on_success():
    calls = []

    def _call():
        calls.append(1)
        return "ok"

    result = with_retry(_call, max_retries=3, initial_backoff_seconds=1.0, retryable_status_codes={429})

    assert result == "ok"
    assert len(calls) == 1


def test_with_retry_retries_on_retryable_code_then_succeeds(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("src.model_call.time.sleep", lambda s: sleeps.append(s))
    attempts = [_fake_api_error(429), "ok"]

    def _call():
        result = attempts.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    result = with_retry(_call, max_retries=3, initial_backoff_seconds=2.0, retryable_status_codes={429})

    assert result == "ok"
    assert sleeps == [2.0]


def test_with_retry_raises_immediately_on_non_retryable_code():
    exc = _fake_api_error(400)

    def _call():
        raise exc

    try:
        with_retry(_call, max_retries=3, initial_backoff_seconds=1.0, retryable_status_codes={429})
        raise AssertionError("expected APIError to propagate")
    except errors.APIError as raised:
        assert raised is exc


def test_with_retry_raises_after_exhausting_max_retries(monkeypatch):
    monkeypatch.setattr("src.model_call.time.sleep", lambda s: None)
    exc = _fake_api_error(429)

    def _call():
        raise exc

    try:
        with_retry(_call, max_retries=2, initial_backoff_seconds=1.0, retryable_status_codes={429})
        raise AssertionError("expected APIError to propagate after exhausting retries")
    except errors.APIError as raised:
        assert raised is exc


def test_with_retry_exponential_backoff_doubles_and_respects_cap(monkeypatch):
    # Proves the parameterization the task calls out explicitly: the
    # embedder's tuning (8 retries, 65s cap) and the extractor's tuning (5
    # retries, uncapped) are both expressible through the SAME shared loop
    # -- the policy is shared, the tuning is not.
    sleeps: list[float] = []
    monkeypatch.setattr("src.model_call.time.sleep", lambda s: sleeps.append(s))
    exc = _fake_api_error(429)
    calls = {"n": 0}

    def _call():
        calls["n"] += 1
        if calls["n"] <= 4:
            raise exc
        return "ok"

    result = with_retry(
        _call,
        max_retries=8,
        initial_backoff_seconds=2.0,
        retryable_status_codes={429},
        max_backoff_seconds=6.0,
    )

    assert result == "ok"
    # 2.0, 4.0, then capped at 6.0 (would be 8.0 uncapped), then 6.0 again.
    assert sleeps == [2.0, 4.0, 6.0, 6.0]


def test_with_retry_uncapped_backoff_keeps_doubling(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("src.model_call.time.sleep", lambda s: sleeps.append(s))
    exc = _fake_api_error(429)
    calls = {"n": 0}

    def _call():
        calls["n"] += 1
        if calls["n"] <= 3:
            raise exc
        return "ok"

    result = with_retry(_call, max_retries=5, initial_backoff_seconds=2.0, retryable_status_codes={429})

    assert result == "ok"
    assert sleeps == [2.0, 4.0, 8.0]


def test_with_retry_honours_retry_delay_fn_over_exponential_backoff(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("src.model_call.time.sleep", lambda s: sleeps.append(s))
    attempts = [_fake_api_error(429, "19.376064879s"), "ok"]

    def _call():
        result = attempts.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    result = with_retry(
        _call,
        max_retries=3,
        initial_backoff_seconds=2.0,
        retryable_status_codes={429},
        retry_delay_fn=retry_delay_seconds,
        retry_delay_margin_seconds=1.0,
    )

    assert result == "ok"
    assert sleeps == [19.376064879 + 1.0]


def test_with_retry_falls_back_to_exponential_when_retry_delay_fn_returns_none(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("src.model_call.time.sleep", lambda s: sleeps.append(s))
    attempts = [_fake_api_error(500), "ok"]  # no RetryInfo detail

    def _call():
        result = attempts.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    result = with_retry(
        _call,
        max_retries=3,
        initial_backoff_seconds=2.0,
        retryable_status_codes={429, 500},
        retry_delay_fn=retry_delay_seconds,
        retry_delay_margin_seconds=1.0,
    )

    assert result == "ok"
    assert sleeps == [2.0]
