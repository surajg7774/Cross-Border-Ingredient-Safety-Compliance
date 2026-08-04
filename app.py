# DESIGN RULE: this file is the ONLY place UI concerns (Streamlit widgets,
# session_state, CSS) may live, and the ONLY place that owns I/O for the
# web entry point -- the same role scripts/*.py play for the CLI. It calls
# the SAME pure functions the CLI scripts call (run_extraction,
# parse_declaration, resolve_items, classify, evaluate, find_substitutes,
# find_horizon_signals) and adds no compliance logic of its own. Where a
# small piece of pure JOINING code is unavoidable (matching a confirmed
# category back onto the items it covers), it mirrors scripts/verdict.py's
# own _build_item_category_map exactly, for the same reason that function
# exists there: the join belongs at the I/O boundary, not inside
# src/rules/engine.py, which must not know how a category was confirmed.
#
# THE PAUSE IS THE POINT: category recall@1 is measured at ~0.46 and the
# category is outcome-determining (src/rules/engine.py), so this app never
# computes a verdict from a retrieved-but-unconfirmed category. The flow is
# a small state machine over st.session_state["stage"]:
#
#     input -> (extract, resolve, classify) -> category_confirm -> STOP
#     category_confirm -> (evaluate, find_horizon_signals, find_substitutes) -> results
#
# Stage results are cached in st.session_state and never recomputed on a
# Streamlit rerun -- extraction costs a real API call, and re-running it on
# every widget interaction would be both slow and wrong. This state machine
# is deliberately the same shape LangGraph's interrupt/resume would produce,
# so the backend can be swapped later without the screens changing.
#
# (Superseded: the ~0.46 recall@1 figure above is from an earlier-round
# measurement. The operating config -- with-component-name -- measures
# recall@1 = 0.53, recall@3 = 0.82, MRR = 0.66; see docs/findings.md
# F-06/F-07/F-13/F-14 and docs/build_log.md:466.)
"""Streamlit UI for the EU additive compliance checker.

    uv run streamlit run app.py
"""

import html
import json
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import streamlit as st
from langgraph.types import Command

from config import settings
from src.agent.resolver_agent import AgentProposal, make_gemini_llm, resolve_review_item
from src.agent.tools import AgentRefs, build_tools
from src.category.classifier import build_queries, classify, item_component_labels
from src.category.corpus import build_corpus
from src.category.embedder import GeminiEmbedder
from src.category.experiment import CONFIGS
from src.category.schemas import CategoryCandidate, CategoryResult
from src.extract.text_parser import parse_declaration
from src.extractors.gemini import GeminiExtractor
from src.graph.nodes import PRODUCT_SCOPE_KEY
from src.graph.pipeline import build_pipeline
from src.horizon.lane import find_horizon_signals
from src.horizon.load import load_horizon_meta, load_horizon_signals
from src.horizon.schemas import HorizonResult
from src.pipeline import run_extraction
from src.reference_data import load_eu_fip
from src.report.email import (
    EmailAttachment,
    send_report,
    smtp_config_from_settings,
    test_connection,
)
from src.report.export import ReportIdentity, to_csv, to_json, to_pdf
from src.report.narrator import Narration, narrate
from src.resolve.resolver import References, resolve_items
from src.resolve.schemas import ResolutionResult, ResolvedItem
from src.rules.engine import evaluate
from src.rules.schemas import ProductVerdict
from src.schemas import GateResult
from src.substitutes.advisor import find_substitutes
from src.substitutes.schemas import SubstituteResult
from src.ui import components
from src.ui.styles import CUSTOM_CSS

# MEASURED CHOICE, not a UI preference: CONFIGS["baseline"] never uses a
# user-supplied product description at all (description_scope="none") --
# see src/category/experiment.py. CONFIGS["best"] is the project's own
# already-measured combination of the two wins from that ablation (product
# queries get the description, component queries get their own name; see
# docs/findings.md F-07 and experiment.py's comment on "best"). Since this
# UI collects a description specifically to improve category matching, the
# only config that honours that field at all is "best" -- using "baseline"
# here would silently discard what the user typed.
_CATEGORY_CONFIG = CONFIGS["best"]

_PERMITTING_HEADLINES = {"permitted_qs", "permitted_with_limit", "permitted_with_conditions"}


@dataclass
class ReferenceData:
    eu_fip: list[dict]
    codex_ins: list[dict]
    resolver_refs: References
    horizon_signals: list[dict]
    horizon_meta: dict


def _load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


@st.cache_resource(show_spinner=False)
def _load_references() -> ReferenceData:
    eu_fip = load_eu_fip(settings.REFERENCE_DIR / "eu_fip.json")
    codex_ins = _load_json(settings.REFERENCE_DIR / "codex_ins.json", [])
    functional_classes = _load_json(settings.REFERENCE_DIR / "functional_classes.json", [])
    label_aliases = _load_json(settings.REFERENCE_DIR / "label_aliases.json", {})
    horizon_path = settings.REFERENCE_DIR / "horizon_signals.json"
    horizon_signals = load_horizon_signals(horizon_path) if horizon_path.exists() else []
    horizon_meta = load_horizon_meta(horizon_path) if horizon_path.exists() else {}
    return ReferenceData(
        eu_fip=eu_fip,
        codex_ins=codex_ins,
        resolver_refs=References(
            codex_ins=codex_ins,
            functional_classes=functional_classes,
            label_aliases=label_aliases,
            eu_fip=eu_fip,
            index_version="ui",
        ),
        horizon_signals=horizon_signals,
        horizon_meta=horizon_meta,
    )


@st.cache_resource(show_spinner="Loading the food category index…")
def _load_category_scorer():
    """(categories, score_query) -- corpus built and embedded once per
    server process. The embedder has its own disk cache
    (data/reference/category_embeddings.json), so this costs a real API
    call only the first time a given category has never been embedded."""
    raw_categories = _load_json(settings.REFERENCE_DIR / "food_categories.json", [])
    categories, documents, _flags = build_corpus(raw_categories, _CATEGORY_CONFIG)
    embedder = GeminiEmbedder()
    embeddings = embedder.embed_documents(documents)

    def score_query(text: str) -> dict[str, float]:
        from src.category.classifier import embedding_scores

        return embedding_scores(embedder.embed_query(text), embeddings)

    return categories, score_query


@st.cache_resource(show_spinner=False)
def _get_extractor() -> GeminiExtractor:
    return GeminiExtractor(settings.PRIMARY_MODEL)


# =========================================================================== #
# graph-backed pipeline -- PRIMARY path, src/graph/pipeline.py driving the
# SAME screens below. Falls back to the direct-call pipeline (the functions
# under "stage 1"/"stage 2" further down) on any failure -- this app is the
# demo surface and must not break; see _graph_should_attempt/_graph_fallback
# and every _dispatch_* function.
#
# THE PAUSE IS STILL THE POINT, now via LangGraph's interrupt()/
# Command(resume=...) instead of the hand-rolled stage marker: confirm_node
# (src/graph/nodes.py) pauses the graph exactly where category_confirm used
# to pause the state machine, and _finalise_graph resumes it. See
# docs/build_log.md for why (measured category recall@1 ~0.53).
# =========================================================================== #
@st.cache_resource(show_spinner="Reading the label, resolving identities, and retrieving categories…")
def _get_graph():
    """Built ONCE per server process and cached -- st.cache_resource returns
    the SAME compiled graph (and therefore the SAME InMemorySaver) on every
    Streamlit rerun. A fresh checkpointer per rerun would silently lose
    every paused thread's state; a fresh EXTRACTOR/embedder per rerun would
    also re-pay the corpus-embedding cost on every widget interaction --
    both are exactly what st.cache_resource is for, the same pattern
    _load_references/_load_category_scorer/_get_extractor above already
    rely on for their own expensive resources."""
    return build_pipeline(auto_confirm=False)


def _graph_should_attempt() -> bool:
    return not st.session_state.get("graph_broken", False)


def _graph_fallback(exc: Exception) -> None:
    """Record the failure and drop back to the direct pipeline for the rest
    of THIS request. `graph_broken` persists for the session (not reset
    until "New screening") so a systemic failure -- e.g. no GOOGLE_API_KEY
    -- does not retry, and fail slowly, on every single click."""
    st.session_state.graph_broken = True
    st.session_state.pop("graph_config", None)
    st.warning(
        "Something went wrong with the primary screening process, so this run is using the "
        f"backup process instead: {exc}"
    )


def _graph_initial_state(
    label_path: str | None, text_input: str | None, name: str, description: str | None
) -> dict:
    return {
        "label_path": label_path,
        "text_input": text_input,
        "name": name,
        "description": description,
        "extraction": None,
        "resolution": None,
        "category": {},
        "confirmed_categories": {},
        "verdict": None,
        "substitutes": None,
        "horizon": None,
        "narration": None,
        "errors": [],
    }


def _invoke_fresh_graph_attempt() -> dict:
    """(Re)run extract -> resolve -> classify -> confirm on a NEW thread,
    reaching the SAME interrupt fresh, and record its config as the current
    one. Used both for the first run of a screening and to re-arm
    confirmation after a "Back to category confirmation" (see
    _finalise_graph): LangGraph's own resume mechanism resolves a paused
    checkpoint's interrupt exactly once -- calling Command(resume=...)
    again at the SAME checkpoint does not re-pause it with a new value,
    verified directly against the installed langgraph version before relying
    on it here. A fresh thread each time sidesteps that entirely, and costs
    no extra API call: run_extraction (GeminiExtractor) and the embedder
    (GeminiEmbedder) each have their own on-disk cache keyed by exact input,
    so re-running identical input against them is a cache hit, not a new
    call.

    Raises if the graph does not pause at "confirm" as expected (e.g. the
    graph reported an internal error) -- the caller's try/except is what
    triggers the fallback to the direct pipeline.
    """
    graph, _refs = _get_graph()
    st.session_state.graph_attempt = st.session_state.get("graph_attempt", 0) + 1
    thread_id = f"{st.session_state.graph_run_id}-{st.session_state.graph_attempt}"
    config = {"configurable": {"thread_id": thread_id}}

    source = st.session_state.graph_source
    if source["kind"] == "image":
        with tempfile.NamedTemporaryFile(delete=False, suffix=source["suffix"]) as tmp:
            tmp.write(source["bytes"])
        image_path = Path(tmp.name)
        try:
            state = _graph_initial_state(
                str(image_path), None, st.session_state.graph_name, st.session_state.graph_description
            )
            result = graph.invoke(state, config)
        finally:
            image_path.unlink(missing_ok=True)
    else:
        state = _graph_initial_state(
            None, source["text"], st.session_state.graph_name, st.session_state.graph_description
        )
        result = graph.invoke(state, config)

    if result.get("errors"):
        raise RuntimeError("; ".join(result["errors"]))
    if "__interrupt__" not in result:
        raise RuntimeError("graph did not pause for category confirmation as expected")

    st.session_state.graph_config = config
    return result


def _ordered_category_results(result: dict) -> list[CategoryResult]:
    """The graph's state["category"] (a dict keyed by component_label, or
    PRODUCT_SCOPE_KEY for the product-scope query -- see
    src/graph/nodes.py's classify_node) reordered to match build_queries'
    own declaration order, the same order the legacy pipeline's
    category_results list is already in. Calls build_queries again purely
    to recover that order -- pure, cheap, already imported here for the
    direct pipeline below; never rescored."""
    items = result["extraction"]["extraction"]["items"]
    resolved_items = [ResolvedItem.model_validate(i) for i in result["resolution"]["items"]]
    gate = result["extraction"]["gate"]
    queries = build_queries(
        items,
        resolved_items,
        gate.get("product_name"),
        gate.get("product_descriptor"),
        _CATEGORY_CONFIG,
        st.session_state.graph_description,
    )
    order = [(q.scope, q.component_label) for q in queries]
    by_key = {}
    for payload in result["category"].values():
        parsed = CategoryResult.model_validate(payload)
        by_key[(parsed.query.scope, parsed.component_label)] = parsed
    return [by_key[key] for key in order if key in by_key]


def _run_pipeline_from_image_graph(uploaded, description: str) -> None:
    st.session_state.source_filename = uploaded.name
    st.session_state.graph_source = {
        "kind": "image",
        "bytes": uploaded.getvalue(),
        "suffix": Path(uploaded.name).suffix or ".jpg",
    }
    st.session_state.graph_name = uploaded.name
    st.session_state.graph_description = description or None
    st.session_state.graph_run_id = uuid.uuid4().hex
    st.session_state.graph_attempt = 0
    st.session_state.graph_resumed = False

    with st.status("Running screening…", expanded=True) as status:
        status.write("Reading the label, resolving identities, and retrieving categories…")
        result = _invoke_fresh_graph_attempt()

        extraction_payload = result["extraction"]
        if extraction_payload is None or extraction_payload["extraction"] is None:
            status.update(label="Could not extract this label", state="error")
            st.error((extraction_payload or {}).get("stop_reason") or "Could not extract this label.")
            return
        items = extraction_payload["extraction"]["items"]
        if not items:
            status.update(label="Nothing to check", state="error")
            st.error(extraction_payload.get("stop_reason") or "No items were extracted.")
            return

        category_results = _ordered_category_results(result)
        status.write(f"{len(items)} item(s) extracted")
        status.write(f"{len(category_results)} categor{'y' if len(category_results) == 1 else 'ies'} to confirm")
        status.update(label="Ready — confirm the food category next", state="complete")

    st.session_state.extraction = extraction_payload
    st.session_state.resolution = ResolutionResult.model_validate(result["resolution"])
    st.session_state.category_results = category_results
    st.session_state.product_label = extraction_payload["gate"].get("product_name") or "Screening result"
    st.session_state.stage = "category_confirm"
    st.rerun()


def _run_pipeline_from_text_graph(text: str, description: str) -> None:
    st.session_state.source_filename = None
    st.session_state.graph_source = {"kind": "text", "text": text}
    # Same falsy-fallback as legacy's gate.product_name=None (see
    # _run_pipeline_from_text): extract_node sets gate.product_name to this
    # verbatim, but every display site reads it via `or "Screening result"`
    # / `or source_filename`, so "" behaves identically to None there.
    st.session_state.graph_name = ""
    st.session_state.graph_description = description or None
    st.session_state.graph_run_id = uuid.uuid4().hex
    st.session_state.graph_attempt = 0
    st.session_state.graph_resumed = False

    with st.status("Running screening…", expanded=True) as status:
        status.write("Parsing, resolving identities, and retrieving categories…")
        result = _invoke_fresh_graph_attempt()

        extraction_payload = result["extraction"]
        items = extraction_payload["extraction"]["items"] if extraction_payload else []
        if extraction_payload and extraction_payload["extraction"]["unparsed_fragments"]:
            st.warning(
                f"{len(extraction_payload['extraction']['unparsed_fragments'])} fragment(s) could not be "
                "parsed: " + "; ".join(extraction_payload["extraction"]["unparsed_fragments"])
            )
        if not items:
            status.update(label="Nothing to check", state="error")
            st.error("No items were parsed from the pasted text.")
            return

        category_results = _ordered_category_results(result)
        status.write(f"{len(items)} item(s) parsed")
        status.write(f"{len(category_results)} categor{'y' if len(category_results) == 1 else 'ies'} to confirm")
        status.update(label="Ready — confirm the food category next", state="complete")

    st.session_state.extraction = extraction_payload
    st.session_state.resolution = ResolutionResult.model_validate(result["resolution"])
    st.session_state.category_results = category_results
    st.session_state.product_label = extraction_payload["gate"].get("product_name") or "Screening result"
    st.session_state.stage = "category_confirm"
    st.rerun()


def _finalise_graph(choices: dict[str | None, CategoryCandidate]) -> None:
    """The graph-backed twin of _finalise: resume confirm_node's interrupt
    with the chosen categories, then read verdict/substitutes/horizon/
    narration straight off the returned state -- src/graph/nodes.py already
    ran evaluate/find_substitutes/find_horizon_signals/narrate for us. Only
    the display-only enrichment/preview steps below (additive-name
    backfill, the pre-confirmation preview verdict) are NOT something a
    node does -- same as _finalise, these are app.py's own display
    concerns, not compliance logic, so they are recomputed here exactly as
    _finalise already does.

    If this is a RE-confirmation (the user went back and is confirming a
    different choice), the original thread's interrupt is already resolved
    -- see _invoke_fresh_graph_attempt's docstring -- so a fresh attempt is
    started first, reaching the SAME interrupt again before resuming it
    with the new choice.
    """
    graph, _refs = _get_graph()
    if st.session_state.get("graph_resumed"):
        _invoke_fresh_graph_attempt()

    resume = {(PRODUCT_SCOPE_KEY if key is None else key): candidate.code for key, candidate in choices.items()}
    with st.spinner("Computing the verdict…"):
        result = graph.invoke(Command(resume=resume), st.session_state.graph_config)
    if "__interrupt__" in result:
        raise RuntimeError("graph paused again unexpectedly while finalising")
    if result.get("errors"):
        raise RuntimeError("; ".join(result["errors"]))

    refs = _load_references()
    resolved_items = [ResolvedItem.model_validate(i) for i in result["resolution"]["items"]]
    resolved_by_id = {r.item_id: r for r in resolved_items}
    canonical_ins_by_item = {r.item_id: r.canonical_ins for r in resolved_items if r.canonical_ins}
    codex_names = {row["ins"]: row["name"] for row in refs.codex_ins}
    extraction_items = result["extraction"]["extraction"]["items"]
    item_labels = item_component_labels(extraction_items, resolved_by_id)

    verdict = ProductVerdict.model_validate(result["verdict"])
    extraction_names = _extraction_names(result["extraction"])
    verdict = _enrich_additive_names(verdict, extraction_names, canonical_ins_by_item, codex_names)

    # DISPLAY ONLY -- same purpose as _finalise's own preview_verdict (see
    # its comment): the pre-confirmation candidates, recomputed with the
    # SAME pure evaluate() call, never used for blocking/summary/anything
    # but the verdict-strip merge in render_results.
    preview_item_category_map, _unused = _apply_category_confirmations(
        st.session_state.category_results, {}, resolved_items, item_labels
    )
    preview_verdict = evaluate(resolved_items, preview_item_category_map, refs.eu_fip, frozenset())

    st.session_state.verdict = verdict
    st.session_state.preview_verdict = preview_verdict
    st.session_state.horizon_result = HorizonResult.model_validate(result["horizon"])
    st.session_state.substitute_result = SubstituteResult.model_validate(result["substitutes"])
    st.session_state.narration = Narration.model_validate(result["narration"])
    st.session_state.run_timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    st.session_state.graph_resumed = True
    st.session_state.stage = "results"
    st.rerun()


def _dispatch_run_from_image(uploaded, description: str) -> None:
    if _graph_should_attempt():
        try:
            _run_pipeline_from_image_graph(uploaded, description)
            return
        except Exception as exc:  # noqa: BLE001 -- ANY graph failure must fall back, not break the app
            _graph_fallback(exc)
    _run_pipeline_from_image(uploaded, description)


def _dispatch_run_from_text(text: str, description: str) -> None:
    if _graph_should_attempt():
        try:
            _run_pipeline_from_text_graph(text, description)
            return
        except Exception as exc:  # noqa: BLE001
            _graph_fallback(exc)
    _run_pipeline_from_text(text, description)


def _dispatch_confirm(choices: dict[str | None, CategoryCandidate]) -> None:
    if "graph_config" in st.session_state and _graph_should_attempt():
        try:
            _finalise_graph(choices)
            return
        except Exception as exc:  # noqa: BLE001
            _graph_fallback(exc)
    _finalise(choices)


def _save_upload(uploaded) -> Path:
    suffix = Path(uploaded.name).suffix or ".jpg"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded.getvalue())
    return Path(tmp.name)


def _extraction_names(extraction_payload: dict) -> dict[int, str]:
    """item_id -> a display name, falling back to verbatim -- for items
    whose additive_name is null (unresolved/ambiguous items have no eu_fip
    row to draw a name from). Same fallback scripts/verdict.py uses."""
    items = extraction_payload["extraction"]["items"]
    names = {}
    for item in items:
        name = item.get("name_as_declared") or item.get("verbatim")
        if name:
            names[item["item_id"]] = name
    return names


def _enrich_additive_names(
    verdict: ProductVerdict,
    extraction_names: dict[int, str],
    canonical_ins_by_item: dict[int, str],
    codex_names: dict[str, str],
) -> ProductVerdict:
    """A copy of `verdict` with every item's additive_name backfilled:

        additive_name = eu_fip name (already set, kept as-is)
                        else Codex INS name (e.g. INS 143 -> "Fast Green FCF",
                             absent from eu_fip -- that absence is WHY it
                             blocks, but Codex still knows the substance)
                        else name_as_declared / verbatim (the label's own
                             wording, via `extraction_names`)

    additive_name is a pure display field -- nothing in src/rules/ or
    src/substitutes/ branches on whether it is None (eu_fip presence is
    signalled by eu_canonical_id/flags instead) -- so enriching it here,
    ONCE, before find_substitutes()/narrate() ever see the verdict, fixes
    every downstream surface (substitutes' blocked_name, the narrator's
    JSON, every export, every screen) without changing any of their own
    code: they already treat additive_name as their first-choice name.

    An item resolved via the CODEX fallback tier gets its code folded into
    the name itself -- "Fast Green FCF (INS 143)", not just "Fast Green
    FCF" -- for the same reason: the code is how a user cross-references
    their own specification, and ItemVerdict deliberately does not carry
    canonical_ins (see src/substitutes/advisor.py's docstring), so this is
    the one place with both the Codex name AND the code in hand at once.
    An item WITH an eu_canonical_id already gets its E-number shown
    separately (the verdict strip's own code_html) -- folding it into the
    name too would duplicate it, so only the Codex-fallback tier does this.
    """
    items = []
    for item in verdict.items:
        name = item.additive_name
        if not name:
            canonical_ins = canonical_ins_by_item.get(item.item_id)
            codex_name = codex_names.get(canonical_ins) if canonical_ins else None
            if codex_name:
                name = f"{codex_name} (INS {canonical_ins})"
        if not name:
            name = extraction_names.get(item.item_id)
        if name and name != item.additive_name:
            item = item.model_copy(update={"additive_name": name})
        items.append(item)
    return verdict.model_copy(update={"items": items})


def _category_summary(verdict: ProductVerdict) -> str:
    """"Product: 14.1.4 (Flavoured_drinks); Seasoning: 12.1.2 (Seasonings and
    condiments)" -- the confirmed food category(ies), for the product
    identity block. Category names are pulled from the verdict's own
    CategoryVerdict rows (every one already carries category_name); this
    never re-derives them from the food-category corpus."""
    names: dict[str, str | None] = {}
    for item in verdict.items:
        for cv in item.by_category:
            names.setdefault(cv.fcs_code, cv.category_name)

    parts = []
    for key, code in verdict.category_used.items():
        label = "Product" if key == "(product)" else key
        name = names.get(code)
        parts.append(f"{label}: {code} ({name})" if name else f"{label}: {code}")
    return "; ".join(parts) if parts else "not confirmed"


def _code_sort_key(code: str) -> tuple:
    parts = code.split(".")
    return tuple(int(p) if p.isdigit() else p for p in parts)


def _reset_session() -> None:
    for key in (
        "extraction",
        "resolution",
        "category_results",
        "product_label",
        "source_filename",
        "run_timestamp",
        "verdict",
        "preview_verdict",
        "horizon_result",
        "substitute_result",
        "narration",
        "graph_config",
        "graph_run_id",
        "graph_attempt",
        "graph_resumed",
        "graph_source",
        "graph_name",
        "graph_description",
        "graph_broken",
        "agent_proposals",
        "agent_decisions",
    ):
        st.session_state.pop(key, None)
    st.session_state.stage = "input"


# =========================================================================== #
# stage 1: input
# =========================================================================== #
def render_input() -> None:
    st.markdown("## EU additive compliance screening")
    st.markdown(
        "<p class='eu-lede'>Check a food label's declared additives against EU Annex II food "
        "additive regulations. Upload a label photo, or paste the ingredients declaration as "
        "printed.</p>",
        unsafe_allow_html=True,
    )

    tab_upload, tab_text = st.tabs(["Upload label", "Enter composition"])
    description_help = (
        "A short description (e.g. 'chocolate-coated wafer biscuit') improves food category "
        "matching -- category retrieval alone selects the correct category first only about "
        "half the time."
    )

    with tab_upload:
        uploaded = st.file_uploader("Label photo", type=["png", "jpg", "jpeg"])
        description_u = st.text_input(
            "Product description (optional)", key="desc_upload", help=description_help
        )
        if st.button(
            "Run screening", key="run_upload", type="primary", disabled=uploaded is None
        ):
            _dispatch_run_from_image(uploaded, description_u)

    with tab_text:
        text = st.text_area(
            "Ingredients declaration",
            height=160,
            placeholder="Sugar, Wheat Flour, Palm Oil, Raising Agent (INS 500(ii)), Colour (INS 143), ...",
            key="composition_text",
        )
        description_t = st.text_input(
            "Product description (optional)", key="desc_text", help=description_help
        )
        if st.button(
            "Run screening", key="run_text", type="primary", disabled=not text.strip()
        ):
            _dispatch_run_from_text(text, description_t)


def _continue_pipeline(status, extraction_payload: dict, description: str, refs: ReferenceData) -> bool:
    """Resolve then classify -- shared by both input paths. Returns False
    (and leaves the page on "input") when there is nothing to check."""
    items = extraction_payload["extraction"]["items"]
    if not items:
        status.update(label="Nothing to check", state="error")
        st.error(extraction_payload.get("stop_reason") or "No items were extracted.")
        return False

    status.write("Resolving additive identities…")
    resolution = resolve_items(items, refs.resolver_refs)
    n_additive = sum(1 for r in resolution.items if r.classification == "additive")
    status.write(f"{n_additive} additive(s) resolved of {len(items)} item(s)")

    status.write("Retrieving candidate food categories…")
    gate = extraction_payload["gate"]
    queries = build_queries(
        items,
        resolution.items,
        gate.get("product_name"),
        gate.get("product_descriptor"),
        _CATEGORY_CONFIG,
        description or None,
    )
    categories, score_query = _load_category_scorer()

    category_results: list[CategoryResult] = []
    skipped = []
    for query in queries:
        if not query.text.strip():
            skipped.append(query.component_label or "(product)")
            continue
        scores = score_query(query.text)
        category_results.append(classify(query, scores, categories, refs.eu_fip, _CATEGORY_CONFIG))

    status.write(f"{len(category_results)} categor{'y' if len(category_results) == 1 else 'ies'} to confirm")
    if skipped:
        status.write(f"Skipped, no query text: {', '.join(skipped)}")
    status.update(label="Ready — confirm the food category next", state="complete")

    st.session_state.extraction = extraction_payload
    st.session_state.resolution = resolution
    st.session_state.category_results = category_results
    st.session_state.product_label = gate.get("product_name") or "Screening result"
    st.session_state.stage = "category_confirm"
    return True


def _run_pipeline_from_image(uploaded, description: str) -> None:
    refs = _load_references()
    st.session_state.source_filename = uploaded.name
    with st.status("Running screening…", expanded=True) as status:
        status.write("Reading the label…")
        image_path = _save_upload(uploaded)
        try:
            extraction_payload = run_extraction(image_path, _get_extractor(), crop=False)
        finally:
            image_path.unlink(missing_ok=True)

        if extraction_payload["extraction"] is None:
            status.update(label="Could not extract this label", state="error")
            st.error(extraction_payload["stop_reason"])
            return
        status.write(f"{len(extraction_payload['extraction']['items'])} item(s) extracted")

        if not _continue_pipeline(status, extraction_payload, description, refs):
            return
    st.rerun()


def _run_pipeline_from_text(text: str, description: str) -> None:
    refs = _load_references()
    st.session_state.source_filename = None
    with st.status("Running screening…", expanded=True) as status:
        status.write("Parsing the ingredients declaration…")
        extraction_result = parse_declaration(text)
        # Text input bypasses the label-validity gate entirely -- say so
        # plainly, the same wording scripts/extract_text.py uses. The
        # user-typed description is threaded through _continue_pipeline as
        # user_description, NOT written into product_descriptor here:
        # product_descriptor is included in EVERY query regardless of scope
        # (src/category/classifier.py's _build_query), while user_description
        # is scoped to product-only under CONFIGS["best"] -- putting it here
        # instead would leak it into every component query, the exact
        # measured regression F-07 in docs/findings.md documents.
        gate = GateResult(
            is_food_label=True,
            has_ingredients_declaration=True,
            confidence=1.0,
            evidence_found=["text_input"],
            reject_reason=None,
            product_name=None,
            product_descriptor=None,
            languages_detected=["en"],
            language_selected="en",
            ingredients_panel_bbox=None,
            warnings=["input was pasted text, not a photographed label — no gate validation was performed"],
        )
        extraction_payload = {
            "gate": gate.model_dump(),
            "extraction": extraction_result.model_dump(),
            "stop_reason": None,
        }
        status.write(f"{len(extraction_result.items)} item(s) parsed")
        if extraction_result.unparsed_fragments:
            st.warning(
                f"{len(extraction_result.unparsed_fragments)} fragment(s) could not be parsed: "
                + "; ".join(extraction_result.unparsed_fragments)
            )

        if not _continue_pipeline(status, extraction_payload, description, refs):
            return
    st.rerun()


# =========================================================================== #
# stage 2: category confirmation -- the pause
# =========================================================================== #
def _apply_category_confirmations(
    category_results: list[CategoryResult],
    choices: dict[str | None, CategoryCandidate],
    resolved_items: list[ResolvedItem],
    item_labels: dict[int, str | None],
) -> tuple[dict[int, CategoryResult], frozenset[int]]:
    """item_id -> the confirmed CategoryResult covering it, plus which item
    ids were confirmed -- identical in shape and intent to
    scripts/verdict.py's _build_item_category_map, duplicated here because
    that function is CLI-script glue, not something src/ exports. `choices`
    covers every query this app displayed for confirmation (unlike the
    CLI's --category, which is a sparse partial override), so every result
    is replaced.

    `item_labels` (item_id -> component label, from
    src.category.classifier.item_component_labels) is the join key -- NEVER
    eu_canonical_id, which is not unique per declaration and collapses two
    items sharing a substance id in different components onto whichever
    component's result happened to be processed last."""
    confirmed_labels: set[str | None] = set()
    resolved_results = []
    for result in category_results:
        key = result.component_label if result.query.scope == "component" else None
        if key in choices:
            result = result.model_copy(update={"top3": [choices[key]]})
            confirmed_labels.add(key)
        resolved_results.append(result)

    product_result = next((r for r in resolved_results if r.query.scope == "product"), None)
    by_component_label: dict[str, CategoryResult] = {
        result.component_label: result for result in resolved_results if result.query.scope == "component"
    }

    mapping: dict[int, CategoryResult] = {}
    confirmed_item_ids: set[int] = set()
    for item in resolved_items:
        component_label = item_labels.get(item.item_id)
        if component_label is not None and component_label in by_component_label:
            chosen = by_component_label[component_label]
        elif product_result is not None:
            chosen = product_result
        else:
            continue
        mapping[item.item_id] = chosen
        key = chosen.component_label if chosen.query.scope == "component" else None
        if key in confirmed_labels:
            confirmed_item_ids.add(item.item_id)
    return mapping, frozenset(confirmed_item_ids)


def _finalise(choices: dict[str | None, CategoryCandidate]) -> None:
    refs = _load_references()
    resolved_items: list[ResolvedItem] = st.session_state.resolution.items
    canonical_ins_by_item = {r.item_id: r.canonical_ins for r in resolved_items if r.canonical_ins}
    codex_names = {row["ins"]: row["name"] for row in refs.codex_ins}
    resolved_by_id = {r.item_id: r for r in resolved_items}
    item_labels = item_component_labels(st.session_state.extraction["extraction"]["items"], resolved_by_id)

    with st.spinner("Computing the verdict…"):
        item_category_map, confirmed_item_ids = _apply_category_confirmations(
            st.session_state.category_results, choices, resolved_items, item_labels
        )
        verdict = evaluate(resolved_items, item_category_map, refs.eu_fip, confirmed_item_ids)

        # NAMES, section A: additive_name is null for anything absent from
        # eu_fip (e.g. INS 143, blocked but never crosswalked), which reads
        # downstream as "item 8" everywhere -- the blocking section, the
        # substitutes' blocked_name, the narrator's JSON, every export.
        # Backfilled ONCE here, before ANYTHING else consumes `verdict`, so
        # every one of those surfaces is correct for free (they all already
        # treat additive_name as their first-choice display name). See
        # _enrich_additive_names.
        extraction_names = _extraction_names(st.session_state.extraction)
        verdict = _enrich_additive_names(verdict, extraction_names, canonical_ins_by_item, codex_names)

        # DISPLAY ONLY -- the same pure evaluate() call, but against the
        # ORIGINAL pre-confirmation candidates (an empty choices dict means
        # nothing gets replaced). MEASURED PROBLEM this exists to fix: once
        # a category is confirmed, `verdict` only ever carries a single
        # candidate per item, so the verdict strip could never show why the
        # confirmation choice mattered (e.g. Chipsmain's E551: permitted
        # under the confirmed 12.2.2, not permitted under 15.1). This
        # second verdict is NEVER used for blocking/summary/anything but
        # merging its by_category lists back onto the display copy of each
        # item in render_results() -- see _merge_preview_candidates. Its
        # items are never separately name-enriched: _merge_preview_candidates
        # bases every display dict on the (already-enriched) authoritative
        # item, only borrowing by_category from this one.
        preview_item_category_map, _unused = _apply_category_confirmations(
            st.session_state.category_results, {}, resolved_items, item_labels
        )
        preview_verdict = evaluate(resolved_items, preview_item_category_map, refs.eu_fip, frozenset())

        additive_ids = sorted({i.eu_canonical_id for i in verdict.items if i.eu_canonical_id})
        additive_names = {
            i.eu_canonical_id: i.additive_name for i in verdict.items if i.eu_canonical_id and i.additive_name
        }
        horizon_result = find_horizon_signals(
            additive_ids,
            refs.horizon_signals,
            additive_names=additive_names,
            data_retrieved=refs.horizon_meta.get("retrieved"),
        )

        horizon_flagged = frozenset(s.eu_canonical_id for s in horizon_result.signals)
        substitute_result = find_substitutes(
            verdict, refs.eu_fip, refs.codex_ins, canonical_ins_by_item, horizon_flagged
        )

    with st.spinner("Writing the narrative summary…"):
        narration = narrate(verdict, substitute_result, horizon_result, settings.PRIMARY_MODEL)

    st.session_state.verdict = verdict
    st.session_state.preview_verdict = preview_verdict
    st.session_state.horizon_result = horizon_result
    st.session_state.substitute_result = substitute_result
    st.session_state.narration = narration
    st.session_state.run_timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    st.session_state.stage = "results"
    st.rerun()


def _merge_preview_candidates(authoritative: ProductVerdict, preview: ProductVerdict) -> dict[int, dict]:
    """item_id -> a display dict for the verdict sections: the authoritative
    (confirmed) ItemVerdict's own fields, but with by_category REPLACED by
    the union of the confirmed candidate and every PRE-confirmation
    candidate for that item, plus a "confirmed_fcs_code" key naming which
    one was actually chosen. src/ui/components.py's verdict_strip_html
    renders every candidate here, full colour and outlined for the
    confirmed one, dimmed for the rest. Purely a display merge over two
    verdicts evaluate() already computed -- never changes which section an
    item belongs to or any compliance decision."""
    preview_by_id = {item.item_id: item for item in preview.items}
    display: dict[int, dict] = {}
    for item in authoritative.items:
        data = item.model_dump()
        confirmed_code = None
        if item.by_category and "category_confirmed_by_user" in item.flags:
            confirmed_code = item.by_category[0].fcs_code

        preview_item = preview_by_id.get(item.item_id)
        combined = list(preview_item.by_category) if preview_item else []
        seen_codes = {cv.fcs_code for cv in combined}
        for cv in item.by_category:
            if cv.fcs_code not in seen_codes:
                combined.append(cv)
                seen_codes.add(cv.fcs_code)

        data["by_category"] = [cv.model_dump() for cv in combined]
        data["confirmed_fcs_code"] = confirmed_code
        display[item.item_id] = data
    return display


def render_category_confirmation() -> None:
    # Same spacer div the results screen's own nav row uses (below the top
    # of the page, not flush against it) -- see render_results.
    st.markdown("<div class='eu-nav-row'></div>", unsafe_allow_html=True)
    if st.button("← Back", key="back_to_input"):
        # Returns to the input screen WITHOUT clearing extraction/resolution/
        # category_results -- nothing here re-runs extraction; this is a
        # pure stage change, not a reset. "New screening" (_reset_session)
        # is the button that discards them.
        st.session_state.stage = "input"
        st.rerun()

    st.markdown("### Confirm the food category")
    st.markdown(
        "<p class='eu-lede'>Automatic category matching selects the correct category first "
        "about half the time, and the food category determines "
        "the verdict. Confirm each one below before the compliance verdict is computed.</p>",
        unsafe_allow_html=True,
    )

    category_results: list[CategoryResult] = st.session_state.category_results
    categories, _score_query = _load_category_scorer()
    all_options = ["(keep the selection above)"] + [
        f"{code} — {categories[code].name}" for code in sorted(categories, key=_code_sort_key)
    ]

    choices: dict[str | None, CategoryCandidate] = {}
    for i, result in enumerate(category_results):
        label = result.component_label or "Whole product"
        st.markdown(f"#### {label}")
        st.caption(f"Query: {result.query.text}")

        radio_options = [
            f"{c.code} — {c.name}  (score {c.similarity:.2f})" for c in result.top3
        ]
        chosen_label = st.radio(
            "Top match", radio_options, index=0, key=f"cat_radio_{i}", label_visibility="collapsed"
        )
        chosen = result.top3[radio_options.index(chosen_label)]

        with st.expander("Choose another category"):
            override_label = st.selectbox(
                "Search all food categories", all_options, key=f"cat_override_{i}"
            )
            if override_label != all_options[0]:
                code = override_label.split(" — ", 1)[0]
                chosen = CategoryCandidate(code=code, name=categories[code].name, similarity=1.0, permitted=True)

        key = result.component_label if result.query.scope == "component" else None
        choices[key] = chosen
        st.markdown("<div class='eu-hairline'></div>", unsafe_allow_html=True)

    if st.button("Confirm and continue", type="primary"):
        _dispatch_confirm(choices)


# =========================================================================== #
# stage 3: results -- verdict, substitutes, horizon, review queue
# =========================================================================== #
def _build_identity(verdict: ProductVerdict) -> ReportIdentity:
    """The product identity block -- constructed ONCE, then shown on the
    results screen AND handed to to_csv/to_pdf unchanged, so the screen,
    the CSV and the PDF header all say the SAME thing (section B)."""
    gate = st.session_state.extraction["gate"]
    source_filename = st.session_state.get("source_filename")
    product_name = (
        gate.get("product_name") or source_filename or st.session_state.get("product_label") or "Screening result"
    )
    return ReportIdentity(
        product_name=product_name,
        source=source_filename or "pasted text",
        category=_category_summary(verdict),
        timestamp=st.session_state.get("run_timestamp") or "",
    )


# =========================================================================== #
# agent-assisted review queue -- ON DEMAND ONLY, never in the main pipeline.
# The agent (src/agent/) PROPOSES; nothing here applies a proposal without
# an explicit Accept click. Reject just records the rejection -- the item
# stays in the review queue exactly as before. Same discipline as category
# confirmation: a probabilistic proposal is acceptable only behind human
# confirmation (see docs/build_log.md).
# =========================================================================== #
def _build_agent_review_item(item_id: int) -> dict | None:
    """The plain dict src.agent.resolver_agent.resolve_review_item expects,
    joined from the SAME session_state extraction/resolution this screen
    already holds -- no file re-read, matching scripts/agent_review.py's
    own _build_review_items but sourced from session_state instead of
    files."""
    resolved_by_id = {r.item_id: r for r in st.session_state.resolution.items}
    resolved = resolved_by_id.get(item_id)
    if resolved is None:
        return None
    extraction_items = st.session_state.extraction["extraction"]["items"]
    extracted = next((e for e in extraction_items if e["item_id"] == item_id), {})
    return {
        "item_id": item_id,
        "name_as_declared": extracted.get("name_as_declared"),
        "verbatim": extracted.get("verbatim"),
        "declared_code": extracted.get("declared_code"),
        "classification": resolved.classification,
        "candidates": resolved.candidates,
        "flags": resolved.flags,
        "component_label": None,
    }


def _build_agent_refs(review_item: dict) -> AgentRefs:
    refs = _load_references()
    gate = st.session_state.extraction["gate"]
    extraction_items = st.session_state.extraction["extraction"]["items"]
    other_names = [
        name
        for e in extraction_items
        if e["item_id"] != review_item["item_id"] and (name := (e.get("name_as_declared") or e.get("verbatim")))
    ]
    verdict: ProductVerdict = st.session_state.verdict
    component = review_item.get("component_label") or "(product)"
    confirmed_category = verdict.category_used.get(component) if verdict else None
    return AgentRefs(
        codex_ins=_load_json(settings.REFERENCE_DIR / "codex_ins.json", []),
        eu_fip=refs.eu_fip,
        label_aliases=_load_json(settings.REFERENCE_DIR / "label_aliases.json", {}),
        product_name=gate.get("product_name"),
        product_descriptor=gate.get("product_descriptor"),
        confirmed_category=confirmed_category,
        other_ingredient_names=other_names,
        model_id=settings.SECONDARY_MODEL,
    )


def _ask_agent_about_item(item_id: int) -> None:
    st.session_state.setdefault("agent_proposals", {})
    review_item = _build_agent_review_item(item_id)
    if review_item is None:
        return
    try:
        refs = _build_agent_refs(review_item)
        tools = build_tools(refs)
        llm = make_gemini_llm(settings.SECONDARY_MODEL, tools)
        proposal = resolve_review_item(review_item, refs, llm, tools)
    except Exception as exc:  # noqa: BLE001 -- the assistant is optional; a failure must not break the results screen
        st.session_state.agent_proposals[item_id] = None
        st.warning(f"The assistant could not investigate this item: {exc}")
        return
    st.session_state.agent_proposals[item_id] = proposal


def _recompute_verdict_after_resolution_change() -> None:
    """The SAME computation _finalise runs, replayed after one item's
    resolution changed -- evaluate() again with the SAME confirmed
    categories the current verdict already carries (read back off
    verdict.items' own by_category/flags, not re-asked of the user), plus
    substitutes/horizon. Narration is deliberately NOT re-generated here --
    unlike evaluate()/find_substitutes()/find_horizon_signals(), it costs a
    real model call, and every OTHER deterministic display (the count
    strip, the page header's category summary, the item sections) is
    already refreshed from the new verdict; see docs/build_log.md."""
    refs = _load_references()
    resolved_items = st.session_state.resolution.items
    resolved_by_id = {r.item_id: r for r in resolved_items}
    item_labels = item_component_labels(st.session_state.extraction["extraction"]["items"], resolved_by_id)

    old_verdict: ProductVerdict = st.session_state.verdict
    choices: dict[str | None, CategoryCandidate] = {}
    for item in old_verdict.items:
        if item.by_category and "category_confirmed_by_user" in item.flags:
            cv = item.by_category[0]
            choices[item.component_label] = CategoryCandidate(
                code=cv.fcs_code, name=cv.category_name or cv.fcs_code, similarity=1.0, permitted=True
            )

    item_category_map, confirmed_item_ids = _apply_category_confirmations(
        st.session_state.category_results, choices, resolved_items, item_labels
    )
    verdict = evaluate(resolved_items, item_category_map, refs.eu_fip, confirmed_item_ids)

    canonical_ins_by_item = {r.item_id: r.canonical_ins for r in resolved_items if r.canonical_ins}
    codex_names = {row["ins"]: row["name"] for row in refs.codex_ins}
    extraction_names = _extraction_names(st.session_state.extraction)
    verdict = _enrich_additive_names(verdict, extraction_names, canonical_ins_by_item, codex_names)

    preview_item_category_map, _unused = _apply_category_confirmations(
        st.session_state.category_results, {}, resolved_items, item_labels
    )
    preview_verdict = evaluate(resolved_items, preview_item_category_map, refs.eu_fip, frozenset())

    additive_ids = sorted({i.eu_canonical_id for i in verdict.items if i.eu_canonical_id})
    additive_names = {
        i.eu_canonical_id: i.additive_name for i in verdict.items if i.eu_canonical_id and i.additive_name
    }
    horizon_result = find_horizon_signals(
        additive_ids, refs.horizon_signals, additive_names=additive_names, data_retrieved=refs.horizon_meta.get("retrieved")
    )
    horizon_flagged = frozenset(s.eu_canonical_id for s in horizon_result.signals)
    substitute_result = find_substitutes(verdict, refs.eu_fip, refs.codex_ins, canonical_ins_by_item, horizon_flagged)

    st.session_state.verdict = verdict
    st.session_state.preview_verdict = preview_verdict
    st.session_state.horizon_result = horizon_result
    st.session_state.substitute_result = substitute_result
    st.session_state.run_timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _accept_agent_proposal(item_id: int, proposal: AgentProposal) -> None:
    """Sets the resolution for THIS ITEM ONLY, then recomputes the verdict
    -- never touches any other item, never writes a verdict directly (the
    recompute goes through the same evaluate() call every other path in
    this app uses)."""
    refs = _load_references()
    resolution: ResolutionResult = st.session_state.resolution
    original = next(r for r in resolution.items if r.item_id == item_id)

    if proposal.proposed_canonical_ins:
        # Re-run the SAME resolve cascade (resolve_items) with declared_code
        # overridden to the agent's proposed code, so eu_canonical_id/
        # functional_classes/etc. are derived by the real cascade -- never
        # hand-computed here. This is a code_exact match by construction
        # (the proposed code is a real INS number), so it resolves exactly
        # as if the label had printed that code itself.
        extraction_items = st.session_state.extraction["extraction"]["items"]
        target = next(e for e in extraction_items if e["item_id"] == item_id)
        overridden = {**target, "declared_code": proposal.proposed_canonical_ins}
        re_resolved = resolve_items([overridden], refs.resolver_refs).items[0]
        updated = re_resolved.model_copy(update={"flags": [*re_resolved.flags, "agent_accepted"]})
    else:
        confidence_to_score = {"high": 0.9, "medium": 0.7, "low": 0.5}
        updated = original.model_copy(
            update={
                "canonical_ins": None,
                "eu_canonical_id": None,
                "classification": proposal.proposed_classification,
                "resolution_method": "agent_accepted",
                "resolution_confidence": confidence_to_score.get(proposal.confidence, 0.5),
                "flags": [*original.flags, "agent_accepted"],
                "candidates": [],
            }
        )

    new_items = [updated if r.item_id == item_id else r for r in resolution.items]
    st.session_state.resolution = resolution.model_copy(update={"items": new_items})

    _recompute_verdict_after_resolution_change()
    st.session_state.setdefault("agent_decisions", {})
    st.session_state.agent_decisions[item_id] = "accepted"


def _reject_agent_proposal(item_id: int) -> None:
    """Records the rejection only -- the item's resolution is untouched and
    it stays in the review queue exactly as before."""
    st.session_state.setdefault("agent_decisions", {})
    st.session_state.agent_decisions[item_id] = "rejected"


def _proposal_signature(proposal: AgentProposal) -> tuple:
    """Two proposals are "the same" for dedup purposes when every field a
    reader would actually see is identical -- used to collapse near-
    identical review-queue entries (see _group_review_items), never to
    compare full AgentProposal objects (tool_calls/item_id legitimately
    differ item to item even when the conclusion is the same)."""
    return (
        proposal.declined,
        proposal.decline_reason,
        proposal.proposed_canonical_ins,
        proposal.proposed_classification,
        proposal.confidence,
        proposal.reasoning,
    )


def _render_agent_proposal(item_ids: list[int], proposal: AgentProposal) -> None:
    """Renders ONE proposal shared by every item in `item_ids` (usually
    one, but see _group_review_items -- several near-identical review-queue
    items, e.g. two "MODIFIED CORNSTARCH" entries, share a single render
    and a single Accept/Reject that applies to all of them at once).

    The short reason is shown directly (decline_reason, or the proposed
    identity line); the LONGER reasoning paragraph -- which restates the
    same thing at more length -- and the evidence it is based on live
    behind one "Why" expander instead of printing both unconditionally."""
    if proposal.declined:
        st.info(f"Assistant declined: {proposal.decline_reason}")
    else:
        code = f" (INS {proposal.proposed_canonical_ins})" if proposal.proposed_canonical_ins else ""
        st.markdown(f"**Assistant proposes:** {proposal.proposed_classification}{code} — confidence: {proposal.confidence}")

    evidence = [line for line in proposal.evidence if line and line.strip()]
    if proposal.reasoning or evidence:
        with st.expander("Why"):
            if proposal.reasoning:
                st.write(proposal.reasoning)
            if evidence:
                st.caption(f"Evidence ({len(evidence)})")
                for line in evidence:
                    st.markdown(f"- {line}")

    if proposal.tool_calls:
        with st.expander(f"Tool calls ({len(proposal.tool_calls)})"):
            for call in proposal.tool_calls:
                st.caption(f"**{call.get('tool')}**({json.dumps(call.get('args', {}))}) → {call.get('result_summary')}")

    key_id = item_ids[0]
    if not proposal.declined:
        col_accept, col_reject = st.columns(2)
        with col_accept:
            if st.button("Accept", key=f"agent_accept_{key_id}", type="primary"):
                for item_id in item_ids:
                    _accept_agent_proposal(item_id, proposal)
                st.rerun()
        with col_reject:
            if st.button("Reject", key=f"agent_reject_{key_id}"):
                for item_id in item_ids:
                    _reject_agent_proposal(item_id)
                st.rerun()
    elif st.button("Dismiss", key=f"agent_dismiss_{key_id}"):
        for item_id in item_ids:
            _reject_agent_proposal(item_id)
        st.rerun()


def _group_review_items(items: list[dict], names: dict[int, str] | None) -> list[list[dict]]:
    """Items with the SAME declared name that also got the SAME assistant
    proposal (asked, not yet decided) are grouped -- MEASURED on a real
    label: two "MODIFIED CORNSTARCH" entries produced near-identical
    declines verbatim. Items not yet asked, or whose proposals diverge,
    are never grouped: collapsing before proposals exist (or differ) would
    hide a real distinction, not a duplicate."""
    groups: list[list[dict]] = []
    index_by_key: dict[tuple, int] = {}
    for item in items:
        item_id = item["item_id"]
        display_name = item.get("additive_name") or (names or {}).get(item_id) or f"item {item_id}"
        proposal = st.session_state.agent_proposals.get(item_id)
        decision = st.session_state.agent_decisions.get(item_id)
        key = (display_name, _proposal_signature(proposal)) if proposal is not None and decision is None else None
        if key is not None and key in index_by_key:
            groups[index_by_key[key]].append(item)
            continue
        if key is not None:
            index_by_key[key] = len(groups)
        groups.append([item])
    return groups


def _render_review_group(group: list[dict], names: dict[int, str] | None) -> None:
    item_ids = [item["item_id"] for item in group]
    primary = group[0]
    primary_id = primary["item_id"]
    display_name = primary.get("additive_name") or (names or {}).get(primary_id) or f"item {primary_id}"
    candidates = [f.split(":", 1)[1].strip() for f in (primary.get("flags") or []) if f.startswith("candidate:")]

    st.markdown(f"**{html.escape(display_name)}**", unsafe_allow_html=True)
    if len(group) > 1:
        st.caption(f"This name appears {len(group)} times on this label.")
    if candidates:
        st.caption("Possible matches: " + ", ".join(candidates))
    else:
        st.caption("No candidate match found -- needs manual identification.")

    decision = st.session_state.agent_decisions.get(primary_id)
    proposal = st.session_state.agent_proposals.get(primary_id)

    if decision == "accepted":
        st.success("Accepted -- the resolution and verdict were updated.")
    elif decision == "rejected":
        st.caption("Rejected -- left in the review queue.")
        if st.button("Ask the assistant", key=f"agent_ask_{primary_id}"):
            with st.spinner("Investigating…"):
                for item_id in item_ids:
                    _ask_agent_about_item(item_id)
            st.rerun()
    elif proposal is not None:
        _render_agent_proposal(item_ids, proposal)
    else:
        if st.button("Ask the assistant", key=f"agent_ask_{primary_id}"):
            with st.spinner("Investigating…"):
                for item_id in item_ids:
                    _ask_agent_about_item(item_id)
            st.rerun()

    st.markdown("<div class='eu-hairline'></div>", unsafe_allow_html=True)


def render_agent_review_queue(items: list[dict], names: dict[int, str] | None = None) -> None:
    """The "unresolved" slice of the review queue (identity resolution) --
    "category_unknown" items still go through components.render_review_queue
    unchanged, since those need a food-category confirmation, not an
    identity proposal. Same section title/note styling as the plain queue,
    with an "Ask the assistant" control (per item, and for the whole queue)
    added below each entry. Items that turn out to share a name AND a
    proposal render once, via _group_review_items."""
    if not items:
        return
    st.session_state.setdefault("agent_proposals", {})
    st.session_state.setdefault("agent_decisions", {})

    st.markdown(f"<div class='eu-section-title'>Review queue ({len(items)})</div>", unsafe_allow_html=True)
    st.markdown(
        "<p class='eu-section-note'>These items need a human decision -- the substance could not be "
        "identified. An assistant can investigate using the same reference data this app already "
        "uses and PROPOSE an identity; nothing is applied until you accept it.</p>",
        unsafe_allow_html=True,
    )

    if st.button("Ask the assistant about the whole queue", key="agent_ask_all"):
        with st.spinner(f"Investigating {len(items)} item(s)…"):
            for item in items:
                if st.session_state.agent_decisions.get(item["item_id"]) is None:
                    _ask_agent_about_item(item["item_id"])
        st.rerun()

    for group in _group_review_items(items, names):
        _render_review_group(group, names)


def render_results() -> None:
    verdict: ProductVerdict = st.session_state.verdict
    preview_verdict: ProductVerdict = st.session_state.preview_verdict
    substitute_result: SubstituteResult = st.session_state.substitute_result
    horizon_result: HorizonResult = st.session_state.horizon_result
    narration: Narration = st.session_state.narration
    names = _extraction_names(st.session_state.extraction)
    identity = _build_identity(verdict)

    st.markdown(f"## {html.escape(identity.product_name)}")
    st.markdown(
        "<div class='eu-identity'>"
        f"<span class='eu-identity-item'><strong>Source:</strong> {html.escape(identity.source)}</span>"
        f"<span class='eu-identity-item'><strong>Category:</strong> {html.escape(identity.category)}</span>"
        f"<span class='eu-identity-item'><strong>Run:</strong> {html.escape(identity.timestamp)}</span>"
        "</div>",
        unsafe_allow_html=True,
    )

    # Navigation, below the title (not flush against it) -- Back returns to
    # category confirmation so a DIFFERENT category can be chosen and the
    # verdict recomputed; that recompute is cheap (no API call, unlike
    # narration, which does cost one when re-confirmed). New screening
    # discards everything and starts over.
    st.markdown("<div class='eu-nav-row'></div>", unsafe_allow_html=True)
    col_back, col_new = st.columns([1, 1])
    with col_back:
        if st.button("← Back to category confirmation"):
            st.session_state.stage = "category_confirm"
            st.rerun()
    with col_new:
        if st.button("New screening"):
            _reset_session()
            st.rerun()

    # The narration -- prominent, above the item sections. Rendered via
    # plain st.markdown (Streamlit's own markdown parser, safe by default,
    # no unsafe_allow_html). There is deliberately no separate rendering of
    # verdict.summary (src/rules/engine.py's own deterministic words) below
    # this: the item counts it states are the count strip just below
    # (render_count_strip), which category was used is the page header
    # above (identity.category), its two caveats (dosage, category
    # confirmation) are re-derived in CODE, not prose, just below the count
    # strip (components.render_verdict_caveats -- never dependent on
    # narration succeeding), and narrate() ITSELF falls back to
    # verdict.summary verbatim, unmodified, as narration.summary on ANY
    # model failure (see src/report/narrator.py's own docstring) -- so
    # nothing verdict.summary said is ever lost, only no longer duplicated
    # ON TOP of itself when narration succeeds. The caption below is what
    # actually tells a reader which case (real narration vs. fallback)
    # they are looking at.
    st.markdown(narration.summary)
    # narration.detail is topic -> list of short sentences (a JSON object,
    # not a markdown string -- see src/report/narrator.py's Narration
    # schema for the bug this fixes: a flat string field silently rendered
    # a stringified Python dict when the model returned one anyway). Each
    # topic gets its own bold sub-heading and its points as bullets.
    for topic, points in narration.detail.items():
        st.markdown(f"**{topic}**")
        for point in points:
            st.markdown(f"- {point}")
    # LOAD-BEARING, small as it looks: model_id reads "unavailable" on the
    # narrate() fallback above, "gemini-3.5-flash" (or whichever model ran)
    # otherwise -- this caption is the ONLY visible signal distinguishing
    # real narration from the deterministic fallback now that there is no
    # separate verdict.summary box. A real regression was caught by this
    # caption once already; do not remove it.
    st.caption(f"Written by {narration.model_id} from the assessment above. It adds no facts.")
    if narration.unfaithful_claims:
        st.warning(
            "The narration above contains claims NOT found in the underlying assessment: "
            + "; ".join(narration.unfaithful_claims)
        )

    item_dicts = [item.model_dump() for item in verdict.items]
    out_of_scope_items = [d for d in item_dicts if d["headline"] == "out_of_scope"]
    # Split, not one combined list: "unresolved" needs an IDENTITY (the
    # agent below can help); "category_unknown" needs a food category --
    # the existing category_confirm flow, unrelated to identity resolution.
    identity_review_items = [d for d in item_dicts if d["headline"] == "unresolved"]
    category_review_items = [d for d in item_dicts if d["headline"] == "category_unknown"]
    display_items = _merge_preview_candidates(verdict, preview_verdict)
    blocking_items = [display_items[i] for i in verdict.blocking]
    conflict_items = [display_items[i] for i in verdict.category_conflict]
    permitted_items = [
        display_items[item.item_id]
        for item in verdict.items
        if item.headline in _PERMITTING_HEADLINES
        and item.item_id not in verdict.blocking
        and item.item_id not in verdict.category_conflict
    ]

    # Orientation before detail -- the counts are the point; whether a
    # reader clicks through the sections below is their choice. Four
    # MUTUALLY EXCLUSIVE counts (see components.count_buckets), unlike the
    # section lists above where a permitted_with_conditions item can
    # legitimately appear in more than one place (e.g. verdict.blocking vs.
    # verdict.category_conflict) -- the strip must never double-count.
    components.render_count_strip(components.count_buckets(item_dicts))
    # Deterministic, code-computed caveats (dosage, category confirmation) --
    # see components._verdict_caveats for why these are not a narration
    # prompt rule: unlike the narration above, this never reads narration
    # at all, so it renders identically whether narration succeeded or
    # narrate() fell back to verdict.summary.
    components.render_verdict_caveats(item_dicts)

    # Any Group clause (Group I, Group II, ...) shared by more than one
    # item's primary candidate renders ONCE here, before any section, and
    # every item that carries it -- Blocking, Category-dependent, or
    # Permitted alike -- links back to it instead of repeating it (see
    # components.render_group_conditions_block for the measurement and the
    # merged-clause case it deliberately leaves untouched).
    group_registry = components.render_group_conditions_block(
        blocking_items + conflict_items + permitted_items
    )

    components.render_verdict_section(
        "Blocking", blocking_items, names, is_blocking=True, group_registry=group_registry
    )
    components.render_verdict_section(
        "Category-dependent", conflict_items, names, group_registry=group_registry
    )
    components.render_permitted_section("Permitted", permitted_items, names, group_registry=group_registry)

    components.render_substitutes(substitute_result.model_dump())
    components.render_horizon(horizon_result.model_dump())

    render_agent_review_queue(identity_review_items, names)
    components.render_review_queue(category_review_items, names)
    components.render_out_of_scope(out_of_scope_items, names)

    render_export_section(verdict, substitute_result, horizon_result, narration, identity)


def render_export_section(
    verdict: ProductVerdict,
    substitutes: SubstituteResult,
    horizon: HorizonResult,
    narration: Narration,
    identity: ReportIdentity,
) -> None:
    """Three download buttons (JSON/CSV/PDF) and an opt-in email section.
    Every export carries the same caveats the screen above does -- see
    src/report/export.py's module docstring."""
    st.markdown("<div class='eu-section-title'>Export and share</div>", unsafe_allow_html=True)

    stem = "".join(c if c.isalnum() else "_" for c in identity.product_name).strip("_") or "report"

    json_text = to_json(verdict, substitutes, horizon, narration)
    csv_text = to_csv(verdict, identity)
    pdf_bytes = to_pdf(verdict, substitutes, horizon, narration, identity)

    col_json, col_csv, col_pdf = st.columns(3)
    with col_json:
        st.download_button(
            "Download JSON", json_text.encode("utf-8"), file_name=f"{stem}.json", mime="application/json"
        )
    with col_csv:
        st.download_button("Download CSV", csv_text.encode("utf-8"), file_name=f"{stem}.csv", mime="text/csv")
    with col_pdf:
        st.download_button("Download PDF", pdf_bytes, file_name=f"{stem}.pdf", mime="application/pdf")

    # Server-side SMTP only (config.py's SMTP_* settings, read via
    # smtp_config_from_settings()) -- there is no credential form here.
    # Nobody sending a compliance report should be typing SMTP credentials
    # into it, and this app has exactly one deployment with one operator
    # who can set .env. When any of the five settings is absent, the whole
    # section is skipped -- not shown disabled, not shown with an
    # explanatory message -- so there is nothing here for the settings to
    # go stale against. The JSON/CSV/PDF downloads above are unaffected.
    smtp_config = smtp_config_from_settings()
    if smtp_config is not None:
        with st.expander("Email this report", expanded=False):
            # A deployment check, not a user action: whoever set the five
            # SMTP_* env vars needs a way to verify them without the first
            # sign of a typo being a real send failing for an end user.
            # There is no credential input left to "test" here -- this
            # button exercises whatever .env already has.
            if st.button("Verify SMTP configuration", key="smtp_test"):
                try:
                    test_connection(smtp_config)
                    st.success("Connection succeeded.")
                except Exception as exc:  # noqa: BLE001 -- any connection failure must surface to the user
                    st.error(f"Connection failed: {exc}")

            recipient = st.text_input("Recipient email address", key="email_recipient")
            st.caption("Sending transmits the PDF report above to the address entered here. Nobody else.")
            if st.button("Send", key="send_email", disabled=not recipient.strip()):
                try:
                    send_report(
                        smtp_config,
                        recipient.strip(),
                        subject=f"EU additive compliance report -- {identity.product_name}",
                        body=f"{narration.summary}\n\nThe full report is attached as a PDF.",
                        attachment=EmailAttachment(
                            filename=f"{stem}.pdf", content=pdf_bytes, mime_type="application/pdf"
                        ),
                    )
                    st.success(f"Sent to {recipient.strip()}.")
                except Exception as exc:  # noqa: BLE001 -- any SMTP failure must surface to the user, not crash
                    st.error(f"Could not send the report: {exc}")


# =========================================================================== #
# entry point
# =========================================================================== #
def main() -> None:
    st.set_page_config(page_title="EU Additive Compliance", layout="centered")
    st.markdown(f"<style>{CUSTOM_CSS}</style>", unsafe_allow_html=True)
    st.session_state.setdefault("stage", "input")

    stage = st.session_state.stage
    if stage == "category_confirm":
        render_category_confirmation()
    elif stage == "results":
        render_results()
    else:
        render_input()


if __name__ == "__main__":
    main()
