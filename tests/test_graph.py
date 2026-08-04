"""Tests for src/graph/ -- the LangGraph orchestration layer.

Every function a node calls that would otherwise need a real API key,
network access, or reference data (run_extraction, resolve_items,
embedding_scores, classify, evaluate, find_substitutes,
find_horizon_signals, narrate) is MOCKED via monkeypatch on src.graph.nodes'
own imported names -- no API calls anywhere in this file.
build_queries itself is NOT mocked: it is pure, already thoroughly tested
in tests/test_category.py, and it is what actually produces the fan-out --
mocking it would mean never really testing the Send dispatch at all.
"""

from pathlib import Path
from unittest.mock import Mock

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

import src.graph.nodes as nodes_module
from src.category.experiment import CONFIGS
from src.category.schemas import CategoryCandidate, CategoryResult
from src.graph.nodes import (
    make_classify_dispatch,
    make_classify_node,
    make_confirm_node,
    make_extract_node,
    make_horizon_node,
    make_narrate_node,
    make_news_node,
    make_resolve_node,
    make_substitutes_node,
    make_verdict_node,
)
from src.graph.state import PipelineState
from src.horizon.schemas import HorizonResult
from src.report.narrator import Narration
from src.resolve.schemas import ResolutionResult, ResolvedItem
from src.rules.schemas import ItemVerdict, ProductVerdict
from src.substitutes.schemas import SubstituteResult

# --------------------------------------------------------------------------- #
# fixtures: a 3-component product (Component A/B/C, one additive each) --
# all-compound top level, so build_queries produces exactly 3 component
# queries and no product-scope query.
# --------------------------------------------------------------------------- #
_COMPONENTS = [("Component A", "300"), ("Component B", "330"), ("Component C", "450")]


def _extraction_items() -> list[dict]:
    items = []
    item_id = 0
    for label, eu_id in _COMPONENTS:
        items.append(
            {
                "item_id": item_id,
                "position": item_id,
                "nesting_depth": 0,
                "parent_item_id": None,
                "verbatim": label,
                "name_as_declared": label,
                "declared_role": None,
                "role_source": "none",
                "declared_code": None,
                "code_system": "none",
                "percentage": None,
                "emphasis": False,
                "footnote_marker": None,
                "confidence": 1.0,
            }
        )
        component_id = item_id
        item_id += 1
        items.append(
            {
                "item_id": item_id,
                "position": item_id,
                "nesting_depth": 1,
                "parent_item_id": component_id,
                "verbatim": f"Additive {eu_id}",
                "name_as_declared": f"Additive {eu_id}",
                "declared_role": None,
                "role_source": "none",
                "declared_code": eu_id,
                "code_system": "E",
                "percentage": None,
                "emphasis": False,
                "footnote_marker": None,
                "confidence": 1.0,
            }
        )
        item_id += 1
    return items


def _resolved_items() -> list[ResolvedItem]:
    resolved = []
    item_id = 0
    for _label, eu_id in _COMPONENTS:
        resolved.append(
            ResolvedItem(
                item_id=item_id,
                canonical_ins=None,
                eu_canonical_id=None,
                classification="compound",
                normalised_role=None,
                codex_functional_classes=[],
                resolution_method="test",
                resolution_confidence=1.0,
                flags=[],
                candidates=[],
                matched_on=None,
            )
        )
        item_id += 1
        resolved.append(
            ResolvedItem(
                item_id=item_id,
                canonical_ins=eu_id,
                eu_canonical_id=eu_id,
                classification="additive",
                normalised_role=None,
                codex_functional_classes=[],
                resolution_method="test",
                resolution_confidence=1.0,
                flags=[],
                candidates=[],
                matched_on="code_exact",
            )
        )
        item_id += 1
    return resolved


def _fake_extraction_payload() -> dict:
    return {
        "gate": {
            "is_food_label": True,
            "has_ingredients_declaration": True,
            "confidence": 1.0,
            "evidence_found": ["test"],
            "reject_reason": None,
            "product_name": "Test Product",
            "product_descriptor": None,
            "languages_detected": ["en"],
            "language_selected": "en",
            "ingredients_panel_bbox": None,
            "warnings": [],
        },
        "extraction": {
            "declaration_verbatim": "test",
            "language": "en",
            "items": _extraction_items(),
            "unparsed_fragments": [],
            "warnings": [],
        },
        "stop_reason": None,
    }


def _fake_classify(query, scores, corpus, eu_fip, config=None, query_variants=None):
    """Stand-in for src.category.classifier.classify -- one distinguishable
    top-3 per component, keyed off which component the query is for, so
    tests can tell which query produced which result."""
    label = query.component_label
    code = {"Component A": "1.1", "Component B": "2.1", "Component C": "3.1"}[label]
    return CategoryResult(
        component_label=label,
        query=query,
        top3=[
            CategoryCandidate(code=code, name=f"Category for {label}", similarity=0.9, permitted=True),
            CategoryCandidate(code="9.9", name="Other", similarity=0.5, permitted=False),
        ],
        filtered_out=[],
        empty_intersection=False,
        excluded_additives=[],
        additive_breadth={},
    )


def _fake_verdict() -> ProductVerdict:
    item = ItemVerdict(
        item_id=1,
        eu_canonical_id="300",
        additive_name="Test additive",
        component_label="Component A",
        by_category=[],
        headline="permitted_qs",
        category_sensitive=False,
        verdict_certainty="certain",
        flags=[],
    )
    return ProductVerdict(
        items=[item],
        blocking=[],
        category_conflict=[],
        review_required=[],
        category_sensitive_items=[],
        summary="1 item(s) evaluated.",
        category_used={"Component A": "1.1"},
        category_source={"Component A": "user"},
        warnings=[],
        data_version="test",
    )


def _fake_substitutes() -> SubstituteResult:
    return SubstituteResult(suggestions=[], warnings=["substitutes ran"])


def _fake_horizon() -> HorizonResult:
    return HorizonResult(signals=[], checked_ids=["300"], warnings=["horizon ran"], data_version="test", data_retrieved=None)


def _fake_narration() -> Narration:
    return Narration(summary="No blocking issues found.", detail={}, model_id="fake-model", unfaithful_claims=[])


@pytest.fixture(autouse=True)
def _mock_pure_functions(monkeypatch):
    """Every node's underlying pure function, mocked -- see module docstring."""
    monkeypatch.setattr(nodes_module, "run_extraction", lambda label_path, extractor: _fake_extraction_payload())
    monkeypatch.setattr(
        nodes_module,
        "resolve_items",
        lambda items, refs: ResolutionResult(items=_resolved_items(), warnings=[], index_version="test"),
    )
    monkeypatch.setattr(nodes_module, "embedding_scores", lambda query_vector, embeddings: {})
    monkeypatch.setattr(nodes_module, "classify", _fake_classify)
    monkeypatch.setattr(nodes_module, "evaluate", lambda *a, **k: _fake_verdict())
    monkeypatch.setattr(nodes_module, "find_substitutes", lambda *a, **k: _fake_substitutes())
    monkeypatch.setattr(nodes_module, "find_horizon_signals", lambda *a, **k: _fake_horizon())
    # Not actually reachable with news_provider=None (make_news_node's
    # degrade-cleanly path returns before ever calling this) -- mocked
    # anyway so a real call is impossible even if that default changes.
    monkeypatch.setattr(nodes_module, "find_news_signals", lambda *a, **k: ([], {}))
    monkeypatch.setattr(nodes_module, "narrate", lambda *a, **k: _fake_narration())


def _build_test_graph(auto_confirm: bool, news_provider=None, news_cache_path=None):
    # news_provider defaults to None (the "no key configured" degrade
    # path -- see make_news_node's docstring): no network, no cache file
    # touched, {"news_signals": []} every time -- news_cache_path is never
    # even opened on that path, so a bogus default is safe there. A test
    # that passes a non-None news_provider MUST also pass a real
    # news_cache_path (tmp_path-based) -- that path DOES get opened for
    # real (news_node calls news_cache.load_cache/save_cache directly,
    # not through the mocked find_news_signals), and a relative path
    # would otherwise write a stray file into the repo root.
    if news_provider is not None and news_cache_path is None:
        raise ValueError("news_cache_path is required whenever news_provider is not None")
    config = CONFIGS["baseline"]
    graph = StateGraph(PipelineState)
    graph.add_node("extract", make_extract_node(Mock()))
    graph.add_node("resolve", make_resolve_node(Mock()))
    graph.add_node("classify", make_classify_node({}, {}, Mock(), [], config))
    graph.add_node("confirm", make_confirm_node(auto_confirm))
    graph.add_node("verdict", make_verdict_node([], {"1.1": "Category for Component A"}))
    graph.add_node("substitutes", make_substitutes_node([], []))
    graph.add_node("horizon", make_horizon_node([], {}))
    graph.add_node("news", make_news_node(news_provider, news_cache_path or Path("unreachable.json"), "fake-model"))
    graph.add_node("narrate", make_narrate_node("fake-model"))

    graph.add_edge(START, "extract")
    graph.add_edge("extract", "resolve")
    graph.add_conditional_edges("resolve", make_classify_dispatch(config), ["classify", "confirm"])
    graph.add_edge("classify", "confirm")
    graph.add_edge("confirm", "verdict")
    graph.add_edge("verdict", "substitutes")
    graph.add_edge("verdict", "horizon")
    graph.add_edge("verdict", "news")
    graph.add_edge("substitutes", "narrate")
    graph.add_edge("horizon", "narrate")
    graph.add_edge("news", "narrate")
    graph.add_edge("narrate", END)
    return graph.compile(checkpointer=InMemorySaver())


def _initial_state(**overrides) -> dict:
    state = {
        "label_path": "fake/path.jpeg",
        "text_input": None,
        "name": "Test Product",
        "description": None,
        "extraction": None,
        "resolution": None,
        "category": {},
        "confirmed_categories": {},
        "verdict": None,
        "substitutes": None,
        "horizon": None,
        "news_signals": None,
        "narration": None,
        "errors": [],
    }
    state.update(overrides)
    return state


def test_graph_runs_end_to_end_with_auto_confirm():
    graph = _build_test_graph(auto_confirm=True)
    config = {"configurable": {"thread_id": "t-end-to-end"}}

    result = graph.invoke(_initial_state(), config)

    assert "__interrupt__" not in result
    assert result["errors"] == []
    assert result["narration"]["summary"] == "No blocking issues found."
    assert result["confirmed_categories"] == {"Component A": "1.1", "Component B": "2.1", "Component C": "3.1"}


def test_three_component_product_dispatches_three_classify_sends():
    graph = _build_test_graph(auto_confirm=True)
    config = {"configurable": {"thread_id": "t-fanout"}}

    result = graph.invoke(_initial_state(), config)

    assert set(result["category"]) == {"Component A", "Component B", "Component C"}
    assert result["category"]["Component A"]["top3"][0]["code"] == "1.1"
    assert result["category"]["Component B"]["top3"][0]["code"] == "2.1"
    assert result["category"]["Component C"]["top3"][0]["code"] == "3.1"


def test_interrupt_pauses_and_command_resume_populates_confirmed_categories():
    graph = _build_test_graph(auto_confirm=False)
    config = {"configurable": {"thread_id": "t-interrupt"}}

    paused = graph.invoke(_initial_state(), config)

    assert "__interrupt__" in paused
    state = graph.get_state(config)
    assert state.next == ("confirm",)
    interrupt_payload = paused["__interrupt__"][0].value
    assert set(interrupt_payload) == {"Component A", "Component B", "Component C"}
    assert interrupt_payload["Component A"][0]["code"] == "1.1"

    resumed = graph.invoke(
        Command(resume={"Component A": "1.1", "Component B": "8.8", "Component C": "3.1"}), config
    )

    assert "__interrupt__" not in resumed
    assert resumed["confirmed_categories"] == {"Component A": "1.1", "Component B": "8.8", "Component C": "3.1"}


def test_confirmed_category_reaches_evaluate_as_an_override(monkeypatch):
    captured = {}

    def capturing_evaluate(resolved_items, category_results, eu_fip, confirmed_item_ids=frozenset()):
        captured["category_results"] = category_results
        captured["confirmed_item_ids"] = confirmed_item_ids
        return _fake_verdict()

    monkeypatch.setattr(nodes_module, "evaluate", capturing_evaluate)

    graph = _build_test_graph(auto_confirm=False)
    config = {"configurable": {"thread_id": "t-override"}}
    graph.invoke(_initial_state(), config)
    graph.invoke(Command(resume={"Component B": "8.8"}), config)  # only Component B confirmed, a NON-rank-1 code

    # item_id 3 is Component B's additive (see _resolved_items/_extraction_items).
    assert 3 in captured["confirmed_item_ids"]
    assert captured["category_results"][3].top3[0].code == "8.8"  # the confirmed code, not the retrieved rank-1 ("2.1")
    # Component A was never confirmed -- its item still sees the RETRIEVED top3, unchanged.
    assert captured["category_results"][1].top3[0].code == "1.1"


def test_substitutes_and_horizon_both_run_and_both_reach_narrate(monkeypatch):
    captured = {}

    def capturing_narrate(verdict, substitutes, horizon, model_id):
        captured["substitutes"] = substitutes
        captured["horizon"] = horizon
        return _fake_narration()

    monkeypatch.setattr(nodes_module, "narrate", capturing_narrate)

    graph = _build_test_graph(auto_confirm=True)
    config = {"configurable": {"thread_id": "t-parallel"}}
    result = graph.invoke(_initial_state(), config)

    assert result["substitutes"]["warnings"] == ["substitutes ran"]
    assert result["horizon"]["warnings"] == ["horizon ran"]
    assert captured["substitutes"].warnings == ["substitutes ran"]
    assert captured["horizon"].warnings == ["horizon ran"]


def test_news_node_degrades_cleanly_when_no_provider_configured():
    # news_provider=None (the default -- see _build_test_graph) is
    # make_news_node's "no key configured" path: state["news_signals"]
    # must still end up [] (never missing, never an error), and the EFSA
    # lane (state["horizon"]) must be completely unaffected by it.
    graph = _build_test_graph(auto_confirm=True)
    config = {"configurable": {"thread_id": "t-news-none"}}

    result = graph.invoke(_initial_state(), config)

    assert result["errors"] == []
    assert result["news_signals"] == []
    assert result["horizon"]["warnings"] == ["horizon ran"]  # EFSA lane unaffected


def test_news_node_runs_alongside_substitutes_and_horizon_when_provider_configured(monkeypatch, tmp_path):
    # A non-None provider (any object -- find_news_signals itself is
    # mocked, so nothing here ever touches the network) takes news_node
    # past its early-return and into a real find_news_signals call.
    # news_node still calls the REAL news_cache.load_cache/save_cache
    # directly (not through the mocked find_news_signals), so this needs
    # a real tmp_path, not a relative path that would write into the repo.
    captured = {}

    def capturing_find_news_signals(additive_ids, provider, cache, model_id, today, additive_names=None):
        captured["additive_ids"] = additive_ids
        captured["provider"] = provider
        return [], {"171": "cached"}

    monkeypatch.setattr(nodes_module, "find_news_signals", capturing_find_news_signals)

    fake_provider = object()
    graph = _build_test_graph(
        auto_confirm=True, news_provider=fake_provider, news_cache_path=tmp_path / "news_cache.json"
    )
    config = {"configurable": {"thread_id": "t-news-configured"}}
    result = graph.invoke(_initial_state(), config)

    assert result["errors"] == []
    assert result["news_signals"] == []  # find_news_signals returned no signals, but the node still ran
    assert captured["provider"] is fake_provider
    assert captured["additive_ids"] == ["300"]  # _fake_verdict's one item's eu_canonical_id
    # substitutes and horizon still ran too -- news joining the parallel
    # branches did not crowd either of them out.
    assert result["substitutes"]["warnings"] == ["substitutes ran"]
    assert result["horizon"]["warnings"] == ["horizon ran"]


def test_stage_error_lands_in_errors_without_crashing_the_graph(monkeypatch):
    def failing_resolve_items(items, refs):
        raise RuntimeError("boom")

    monkeypatch.setattr(nodes_module, "resolve_items", failing_resolve_items)

    graph = _build_test_graph(auto_confirm=True)
    config = {"configurable": {"thread_id": "t-error"}}

    result = graph.invoke(_initial_state(), config)  # must not raise

    assert any("resolve_node" in err and "boom" in err for err in result["errors"])
