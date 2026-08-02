"""Tests for src/agent/ -- the review-queue resolver agent.

Both the LLM and every tool are mocked -- no API calls anywhere in this
file. MockLLM scripts a fixed sequence of turns (a request to call tools,
then a final JSON answer); tools are src.agent.tools.ToolSpec instances
whose `fn` is a Mock(), so a test can assert exactly which tool was called,
with what arguments, without needing real reference data or search_web to
ever actually reach the network.
"""

import copy
import json
from unittest.mock import Mock

from google.genai import errors

import src.agent.resolver_agent as resolver_agent_module
from src.agent.resolver_agent import (
    INITIAL_BACKOFF_SECONDS,
    MAX_STEPS,
    RETRY_DELAY_MARGIN_SECONDS,
    GeminiLLM,
    LLMTurn,
    resolve_review_item,
)
from src.agent.tools import ToolSpec


def _fake_api_error(code: int, retry_delay: str | None = None) -> errors.APIError:
    details = []
    if retry_delay is not None:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay})
    return errors.APIError(code, {"error": {"code": code, "status": "RESOURCE_EXHAUSTED", "details": details}})


def _mock_tool(name: str, result: dict) -> ToolSpec:
    return ToolSpec(
        name=name, description=f"mock {name}", parameters={"type": "OBJECT", "properties": {}}, fn=Mock(return_value=result)
    )


def _mock_tools() -> dict[str, ToolSpec]:
    return {
        "lookup_codex": _mock_tool("lookup_codex", {"found": False, "queried": "?", "source": "codex_ins.json"}),
        "lookup_eu_fip": _mock_tool("lookup_eu_fip", {"found": False, "queried": "?", "source": "eu_fip.json"}),
        "check_food_lexicon": _mock_tool(
            "check_food_lexicon",
            {"is_known_food": True, "matched_term": "black pepper", "source": "label_aliases.json:never_additives"},
        ),
        "list_family_members": _mock_tool(
            "list_family_members",
            {"members": [{"ins": "960a", "name": "Steviol glycosides from Stevia rebaudiana Bertoni"}], "source": "codex_ins.json"},
        ),
        "get_product_context": _mock_tool(
            "get_product_context", {"product_name": "Test Product", "source": "extraction + resolution + verdict"}
        ),
        "search_web": _mock_tool("search_web", {"available": False, "source": "unavailable: mocked"}),
    }


class _FakeRefs:
    """Only what src.agent.resolver_agent._build_prompt actually reads."""

    product_name = "Test Product"


class MockLLM:
    """Scripts a fixed sequence of LLMTurn objects. Raises IndexError if
    called more times than scripted -- keeps a test honest about exactly
    how many turns it expects."""

    def __init__(self, turns: list[LLMTurn]):
        self._turns = list(turns)
        self.calls = 0

    def start(self, prompt: str) -> LLMTurn:
        self.calls += 1
        return self._turns.pop(0)

    def send_tool_results(self, results: list[dict]) -> LLMTurn:
        self.calls += 1
        return self._turns.pop(0)


def _item(**overrides) -> dict:
    base = {
        "item_id": 1,
        "name_as_declared": "Test Item",
        "verbatim": "Test Item",
        "declared_code": None,
        "classification": "unknown",
        "candidates": [],
        "flags": [],
    }
    base.update(overrides)
    return base


def _final_answer(**overrides) -> str:
    payload = {
        "proposed_canonical_ins": None,
        "proposed_classification": "unknown",
        "confidence": "low",
        "reasoning": "test reasoning",
        "evidence": ["some tool evidence"],
        "declined": True,
        "decline_reason": "test decline reason",
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_agent_calls_list_family_members_for_ambiguous_item():
    tools = _mock_tools()
    llm = MockLLM(
        [
            LLMTurn(tool_calls=[{"name": "list_family_members", "args": {"codes": ["960a", "960b", "960c", "960d"]}}]),
            LLMTurn(
                text=_final_answer(
                    declined=True,
                    decline_reason="four production methods, the label does not say which",
                    evidence=["list_family_members showed 960a-d are all steviol glycoside variants"],
                )
            ),
        ]
    )
    item = _item(classification="ambiguous", candidates=["960a", "960b", "960c", "960d"], flags=["ambiguous_candidates"])

    proposal = resolve_review_item(item, _FakeRefs(), llm, tools)

    tools["list_family_members"].fn.assert_called_once_with(codes=["960a", "960b", "960c", "960d"])
    assert any(c["tool"] == "list_family_members" for c in proposal.tool_calls)


def test_agent_calls_check_food_lexicon_for_unresolved_plain_food_item():
    tools = _mock_tools()
    llm = MockLLM(
        [
            LLMTurn(tool_calls=[{"name": "check_food_lexicon", "args": {"term": "black pepper powder"}}]),
            LLMTurn(
                text=_final_answer(
                    declined=False,
                    proposed_classification="food_ingredient",
                    confidence="high",
                    evidence=["check_food_lexicon confirmed 'black pepper' is a known plain food ingredient"],
                )
            ),
        ]
    )
    item = _item(name_as_declared="Black pepper powder", verbatim="Black pepper powder", classification="unknown")

    proposal = resolve_review_item(item, _FakeRefs(), llm, tools)

    tools["check_food_lexicon"].fn.assert_called_once_with(term="black pepper powder")
    assert proposal.proposed_classification == "food_ingredient"
    assert proposal.declined is False


def test_empty_evidence_proposal_is_rejected_and_marked_declined():
    tools = _mock_tools()
    llm = MockLLM(
        [
            LLMTurn(tool_calls=[{"name": "lookup_codex", "args": {"term_or_code": "999"}}]),
            LLMTurn(
                text=_final_answer(
                    declined=False,
                    proposed_classification="additive",
                    proposed_canonical_ins="999",
                    confidence="high",
                    evidence=[],  # empty -- must force a decline regardless of the model's own claim
                )
            ),
        ]
    )
    item = _item()

    proposal = resolve_review_item(item, _FakeRefs(), llm, tools)

    assert proposal.declined is True
    assert proposal.proposed_canonical_ins is None
    assert proposal.decline_reason is not None and "evidence" in proposal.decline_reason.lower()


def test_step_cap_halts_a_loop_that_keeps_calling_tools():
    tools = _mock_tools()
    # More turns than the step cap allows -- the graph must stop calling
    # the model at MAX_STEPS regardless, never exhaust this list.
    turns = [LLMTurn(tool_calls=[{"name": "get_product_context", "args": {}}]) for _ in range(MAX_STEPS + 5)]
    llm = MockLLM(turns)
    item = _item()

    proposal = resolve_review_item(item, _FakeRefs(), llm, tools)

    assert llm.calls == MAX_STEPS
    assert proposal.declined is True
    assert proposal.decline_reason is not None and "step cap" in proposal.decline_reason.lower()


def test_agent_never_mutates_the_input_item():
    tools = _mock_tools()
    llm = MockLLM(
        [
            LLMTurn(tool_calls=[{"name": "get_product_context", "args": {}}]),
            LLMTurn(text=_final_answer(evidence=["get_product_context returned the product name"])),
        ]
    )
    item = _item(classification="ambiguous", candidates=["960a", "960b"], flags=["ambiguous_candidates"])
    before = copy.deepcopy(item)

    resolve_review_item(item, _FakeRefs(), llm, tools)

    assert item == before


def test_declined_proposal_is_a_valid_outcome_not_an_error():
    tools = _mock_tools()
    llm = MockLLM(
        [
            LLMTurn(tool_calls=[{"name": "search_web", "args": {"query": "INS 924 food additive"}}]),
            LLMTurn(
                text=_final_answer(
                    declined=True,
                    decline_reason="absent from every reference dataset and search is unavailable",
                    evidence=["search_web returned available=false"],
                )
            ),
        ]
    )
    item = _item(name_as_declared="INS 924", verbatim="INS 924", declared_code="INS 924")

    proposal = resolve_review_item(item, _FakeRefs(), llm, tools)  # must not raise

    assert proposal.declined is True
    assert proposal.decline_reason
    assert proposal.item_id == item["item_id"]
    assert proposal.name_as_declared == "INS 924"


# --------------------------------------------------------------------------- #
# GeminiLLM rate-limit retry -- MEASURED: scripts/agent_review.py --all hit
# GenerateRequestsPerMinutePerProjectPerModel-FreeTier (429). The client and
# generate_content call are mocked; no real network calls.
# --------------------------------------------------------------------------- #
def _gemini_llm_with_mocked_client() -> GeminiLLM:
    llm = GeminiLLM("fake-model", _mock_tools())
    llm._ensure_client()  # builds the real config/declarations, no network call
    llm._client = Mock()
    return llm


def test_gemini_llm_429_with_retry_delay_sleeps_that_duration(monkeypatch):
    llm = _gemini_llm_with_mocked_client()
    fake_response = Mock(text="{}", function_calls=None, candidates=[], model_version="fake-model-v1")
    llm._client.models.generate_content.side_effect = [
        _fake_api_error(429, "19.376064879s"),
        fake_response,
    ]
    sleep_calls: list[float] = []
    monkeypatch.setattr(resolver_agent_module.time, "sleep", lambda s: sleep_calls.append(s))

    turn = llm.start("prompt")

    assert turn.text == "{}"
    assert sleep_calls == [19.376064879 + RETRY_DELAY_MARGIN_SECONDS]
    assert llm.resolved_model_id == "fake-model-v1"


def test_gemini_llm_429_without_retry_delay_falls_back_to_exponential_backoff(monkeypatch):
    llm = _gemini_llm_with_mocked_client()
    fake_response = Mock(text="{}", function_calls=None, candidates=[], model_version="fake-model-v1")
    llm._client.models.generate_content.side_effect = [
        _fake_api_error(500),  # no RetryInfo detail
        fake_response,
    ]
    sleep_calls: list[float] = []
    monkeypatch.setattr(resolver_agent_module.time, "sleep", lambda s: sleep_calls.append(s))

    turn = llm.start("prompt")

    assert turn.text == "{}"
    assert sleep_calls == [INITIAL_BACKOFF_SECONDS]
