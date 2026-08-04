# DESIGN RULE: this module does its own file I/O (reference datasets) and
# builds the graph -- the one place in src/graph/ allowed to read a file,
# mirroring app.py's own _load_references/_load_category_scorer (this
# module's reference-loading shape is deliberately the same). Every node's
# actual LOGIC still lives in nodes.py, calling the same pure functions the
# standalone scripts call -- this module only loads reference data ONCE and
# wires nodes into a graph.
"""Builds the LangGraph pipeline: extract -> resolve -> [fan out: classify
per component] -> confirm -> verdict -> [substitutes | horizon | news in
parallel] -> narrate -> END.

    from src.graph.pipeline import build_pipeline
    graph, refs = build_pipeline()
    result = graph.invoke(
        {"label_path": "data/labels/Chipsmain.jpeg", "text_input": None, "name": "Chipsmain",
         "description": None, "extraction": None, "resolution": None, "category": {},
         "confirmed_categories": {}, "verdict": None, "substitutes": None, "horizon": None,
         "news_signals": None, "narration": None, "errors": []},
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
from src.category.chroma_store import build_chroma_index
from src.category.corpus import build_corpus, parse_food_categories
from src.category.embedder import DIMENSIONALITY, GeminiEmbedder
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
    make_news_node,
    make_resolve_node,
    make_substitutes_node,
    make_verdict_node,
)
from src.graph.state import PipelineState
from src.horizon.load import load_horizon_meta, load_horizon_signals
from src.horizon.search import get_search_provider
from src.reference_data import load_eu_fip
from src.resolve.resolver import References

# Where make_news_node's disk cache lives -- under the SAME general cache
# directory CHROMA_PERSIST_DIR sits in (settings.CACHE_DIR), not a
# dedicated setting: this is one file, not a directory tree, and the news
# lane has no other configuration surface (see src/horizon/news_cache.py).
NEWS_CACHE_PATH = settings.CACHE_DIR / "horizon_news_cache.json"

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
    eu_fip = load_eu_fip(settings.REFERENCE_DIR / "eu_fip.json")
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

    # settings.RETRIEVAL_STORE is read ONCE, here, at graph-construction time
    # -- not per classify-node call -- matching nodes.py's own DESIGN RULE
    # that reference data is passed in once and closed over. "numpy"
    # (default) leaves chroma_index None and make_classify_node falls back
    # to its existing embedding_scores path, unchanged. Building the index
    # (persist_path=settings.CHROMA_PERSIST_DIR) costs no API call either
    # way -- it indexes the SAME `embeddings` dict just computed above, from
    # cache -- and is a no-op rebuild whenever the persisted corpus_hash
    # already matches (see chroma_store.py's STALENESS section).
    chroma_index = None
    if settings.RETRIEVAL_STORE == "chroma":
        chroma_index = build_chroma_index(
            documents,
            embeddings,
            persist_path=settings.CHROMA_PERSIST_DIR,
            model_id=embedder.model_id,
            dimensionality=DIMENSIONALITY,
        )

    # get_search_provider() (src/horizon/search.py) is read ONCE, here, at
    # graph-construction time -- not per news-node call -- matching this
    # module's own established pattern for every other reference/setting
    # read (RETRIEVAL_STORE above, eu_fip, the category corpus). None when
    # TAVILY_API_KEY is unset; make_news_node degrades cleanly on None,
    # see its own docstring.
    search_provider = get_search_provider()

    graph = StateGraph(PipelineState)
    graph.add_node("extract", make_extract_node(extractor))
    graph.add_node("resolve", make_resolve_node(refs.resolver_refs))
    graph.add_node(
        "classify",
        make_classify_node(categories, embeddings, embedder, refs.eu_fip, category_config, chroma_index=chroma_index),
    )
    graph.add_node("confirm", make_confirm_node(auto_confirm))
    graph.add_node("verdict", make_verdict_node(refs.eu_fip, refs.category_names, refs.codex_ins))
    graph.add_node("substitutes", make_substitutes_node(refs.eu_fip, refs.codex_ins))
    graph.add_node("horizon", make_horizon_node(refs.horizon_signals, refs.horizon_meta))
    graph.add_node("news", make_news_node(search_provider, NEWS_CACHE_PATH, model_id))
    graph.add_node("narrate", make_narrate_node(model_id))

    graph.add_edge(START, "extract")
    graph.add_edge("extract", "resolve")
    # FAN-OUT: one classify Send per component query (real concurrent
    # dispatch, not a loop) -- or straight to "confirm" if there is
    # nothing to classify (see make_classify_dispatch's docstring).
    graph.add_conditional_edges("resolve", make_classify_dispatch(category_config), ["classify", "confirm"])
    graph.add_edge("classify", "confirm")
    graph.add_edge("confirm", "verdict")
    # PARALLEL: substitutes, horizon, and news all read state["verdict"]
    # only, none depends on either of the others -- three edges from the
    # same source node is LangGraph's own fixed (non-Send) parallel-branch
    # idiom, the same one substitutes/horizon already used before news
    # joined them (see make_news_node's own docstring for what "cannot
    # see each other's output" means here). All three join back into
    # "narrate" before it runs.
    graph.add_edge("verdict", "substitutes")
    graph.add_edge("verdict", "horizon")
    graph.add_edge("verdict", "news")
    graph.add_edge("substitutes", "narrate")
    graph.add_edge("horizon", "narrate")
    graph.add_edge("news", "narrate")
    graph.add_edge("narrate", END)

    compiled = graph.compile(checkpointer=InMemorySaver())
    return compiled, refs
