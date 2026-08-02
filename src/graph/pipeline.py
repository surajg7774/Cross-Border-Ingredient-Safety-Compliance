# DESIGN RULE: this module does its own file I/O (reference datasets) and
# builds the graph -- the one place in src/graph/ allowed to read a file,
# mirroring app.py's own _load_references/_load_category_scorer (this
# module's reference-loading shape is deliberately the same). Every node's
# actual LOGIC still lives in nodes.py, calling the same pure functions the
# standalone scripts call -- this module only loads reference data ONCE and
# wires nodes into a graph.
"""Builds the LangGraph pipeline: extract -> resolve -> [fan out: classify
per component] -> confirm -> verdict -> [substitutes | horizon in parallel]
-> narrate -> END.

    from src.graph.pipeline import build_pipeline
    graph, refs = build_pipeline()
    result = graph.invoke(
        {"label_path": "data/labels/Chipsmain.jpeg", "text_input": None, "name": "Chipsmain",
         "description": None, "extraction": None, "resolution": None, "category": {},
         "confirmed_categories": {}, "verdict": None, "substitutes": None, "horizon": None,
         "narration": None, "errors": []},
        {"configurable": {"thread_id": "Chipsmain"}},
    )
    # result["__interrupt__"] is present if a human confirmation is needed --
    # resume with graph.invoke(Command(resume={component_label: code}), same config).

An IN-MEMORY checkpointer only (InMemorySaver) -- no database, per the task.
State does not survive past the process, same durability app.py's
st.session_state pause already had (a rerun starts over), just without the
hand-rolled stage-marker/full-script-rerun machinery: interrupt()/
Command(resume=...) IS the pause/resume primitive now.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from config import settings
from src.category.corpus import build_corpus, parse_food_categories
from src.category.embedder import GeminiEmbedder
from src.category.experiment import CONFIGS, ExperimentConfig
from src.extractors.base import LabelExtractor
from src.extractors.gemini import GeminiExtractor
from src.graph.nodes import (
    make_classify_dispatch,
    make_classify_node,
    make_confirm_node,
    make_extract_node,
    make_horizon_node,
    make_narrate_node,
    make_resolve_node,
    make_substitutes_node,
    make_verdict_node,
)
from src.graph.state import PipelineState
from src.horizon.load import load_horizon_meta, load_horizon_signals
from src.resolve.resolver import References

# Same operating config app.py's own category classifier uses (see app.py's
# _CATEGORY_CONFIG comment) -- product queries get the user description,
# component queries get their own name; the only config that honours a
# user-supplied description at all.
DEFAULT_CATEGORY_CONFIG = CONFIGS["best"]


def _load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class PipelineReferences:
    """Every reference dataset the graph's nodes were built from -- returned
    alongside the compiled graph so a caller (scripts/run_pipeline.py) can
    write output files under the SAME data it classified against, without
    re-reading anything."""

    resolver_refs: References
    categories: dict
    eu_fip: list[dict]
    codex_ins: list[dict]
    category_names: dict[str, str]
    horizon_signals: list[dict]
    horizon_meta: dict


def _load_pipeline_references() -> PipelineReferences:
    eu_fip = _load_json(settings.REFERENCE_DIR / "eu_fip.json", [])
    codex_ins = _load_json(settings.REFERENCE_DIR / "codex_ins.json", [])
    functional_classes = _load_json(settings.REFERENCE_DIR / "functional_classes.json", [])
    label_aliases = _load_json(settings.REFERENCE_DIR / "label_aliases.json", {})
    raw_categories = _load_json(settings.REFERENCE_DIR / "food_categories.json", [])
    category_names = {c.code: c.name for c in parse_food_categories(raw_categories)}
    horizon_path = settings.REFERENCE_DIR / "horizon_signals.json"
    horizon_signals = load_horizon_signals(horizon_path) if horizon_path.exists() else []
    horizon_meta = load_horizon_meta(horizon_path) if horizon_path.exists() else {}

    return PipelineReferences(
        resolver_refs=References(
            codex_ins=codex_ins,
            functional_classes=functional_classes,
            label_aliases=label_aliases,
            eu_fip=eu_fip,
            index_version="graph",
        ),
        categories={},  # filled in by build_pipeline once the corpus is built
        eu_fip=eu_fip,
        codex_ins=codex_ins,
        category_names=category_names,
        horizon_signals=horizon_signals,
        horizon_meta=horizon_meta,
    )


def build_pipeline(
    extractor: LabelExtractor | None = None,
    category_config: ExperimentConfig = DEFAULT_CATEGORY_CONFIG,
    model_id: str | None = None,
    auto_confirm: bool = False,
):
    """Loads every reference dataset ONCE, embeds the category corpus ONCE,
    builds one closure per node over that data, wires the graph, and
    compiles it with an in-memory checkpointer.

    Returns (compiled_graph, PipelineReferences) -- the references are
    handed back so a caller can reuse the SAME corpus/eu_fip/category_names
    the graph classified against (e.g. scripts/run_pipeline.py writing
    output files), without loading anything twice.
    """
    refs = _load_pipeline_references()
    extractor = extractor or GeminiExtractor(settings.PRIMARY_MODEL)
    model_id = model_id or settings.PRIMARY_MODEL

    raw_categories = _load_json(settings.REFERENCE_DIR / "food_categories.json", [])
    categories, documents, _flags = build_corpus(raw_categories, category_config, refs.eu_fip, refs.codex_ins)
    embedder = GeminiEmbedder()
    embeddings = embedder.embed_documents(documents)
    refs.categories = categories

    graph = StateGraph(PipelineState)
    graph.add_node("extract", make_extract_node(extractor))
    graph.add_node("resolve", make_resolve_node(refs.resolver_refs))
    graph.add_node("classify", make_classify_node(categories, embeddings, embedder, refs.eu_fip, category_config))
    graph.add_node("confirm", make_confirm_node(auto_confirm))
    graph.add_node("verdict", make_verdict_node(refs.eu_fip, refs.category_names))
    graph.add_node("substitutes", make_substitutes_node(refs.eu_fip, refs.codex_ins))
    graph.add_node("horizon", make_horizon_node(refs.horizon_signals, refs.horizon_meta))
    graph.add_node("narrate", make_narrate_node(model_id))

    graph.add_edge(START, "extract")
    graph.add_edge("extract", "resolve")
    # FAN-OUT: one classify Send per component query (real concurrent
    # dispatch, not a loop) -- or straight to "confirm" if there is
    # nothing to classify (see make_classify_dispatch's docstring).
    graph.add_conditional_edges("resolve", make_classify_dispatch(category_config), ["classify", "confirm"])
    graph.add_edge("classify", "confirm")
    graph.add_edge("confirm", "verdict")
    # PARALLEL: substitutes and horizon both read state["verdict"] only,
    # neither depends on the other -- two edges from the same source node
    # is LangGraph's own fixed (non-Send) parallel-branch idiom. Both join
    # back into "narrate" before it runs.
    graph.add_edge("verdict", "substitutes")
    graph.add_edge("verdict", "horizon")
    graph.add_edge("substitutes", "narrate")
    graph.add_edge("horizon", "narrate")
    graph.add_edge("narrate", END)

    compiled = graph.compile(checkpointer=InMemorySaver())
    return compiled, refs
