# DESIGN RULE: every node is a THIN wrapper around a pure function the
# standalone scripts already call -- run_extraction, parse_declaration,
# resolve_items, classify, evaluate, find_substitutes, find_horizon_signals,
# narrate. No compliance logic lives here; a node only (a) reads what it
# needs out of PipelineState, (b) calls the pure function, (c) writes the
# result back. Reference data (extractor, resolver References, the category
# corpus/embeddings, eu_fip, codex_ins, horizon data) is passed into each
# factory function ONCE, at graph-construction time (src/graph/pipeline.py),
# and closed over -- never re-read per node, per the task's explicit
# instruction.
#
# The join logic in _join_category_results (item_id -> the CategoryResult
# that covers it, keyed on component_label rather than eu_canonical_id) is a
# THIRD copy of the same small function scripts/verdict.py's
# _build_item_category_map and app.py's _apply_category_confirmations
# already duplicate independently -- both of those functions' own
# docstrings say exactly why: "CLI-script glue, not something src/ exports".
# src/graph/ is a third independent I/O-boundary entry point, so it gets its
# own copy for the same reason, not a shared import.
"""Node functions for the LangGraph pipeline (src/graph/pipeline.py)."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from langgraph.types import Send, interrupt

from src.category.chroma_store import ChromaIndex, chroma_scores
from src.category.classifier import build_queries, classify, embedding_scores, item_component_labels
from src.category.corpus import FoodCategory
from src.category.embedder import GeminiEmbedder
from src.category.experiment import ExperimentConfig
from src.category.schemas import CategoryCandidate, CategoryQuery, CategoryResult
from src.extract.text_parser import parse_declaration
from src.extractors.base import LabelExtractor
from src.horizon import news_cache
from src.horizon.lane import find_horizon_signals
from src.horizon.news import find_news_signals
from src.horizon.schemas import HorizonResult
from src.horizon.search import SearchProvider
from src.pipeline import run_extraction
from src.report.narrator import narrate
from src.resolve.resolver import References, resolve_items
from src.resolve.schemas import ResolvedItem
from src.rules.engine import evaluate
from src.rules.schemas import ProductVerdict
from src.schemas import GateResult
from src.substitutes.advisor import find_substitutes
from src.substitutes.schemas import SubstituteResult

# The whole-label ("product"-scope) query's key in state["category"]/
# state["confirmed_categories"] -- never None: a None dict key is not as
# predictable through the checkpointer's own serialization as a plain
# string, and this matches the SAME sentinel scripts/verdict.py's CLI table
# already displays for a null component_label ("(product)").
PRODUCT_SCOPE_KEY = "(product)"


def _resolved_items_from_state(state: dict) -> list[ResolvedItem]:
    return [ResolvedItem.model_validate(item) for item in state["resolution"]["items"]]


def _extraction_items_from_state(state: dict) -> list[dict]:
    extraction = state.get("extraction") or {}
    return (extraction.get("extraction") or {}).get("items") or []


# =========================================================================== #
# extract / resolve
# =========================================================================== #
def make_extract_node(extractor: LabelExtractor) -> Callable[[dict], dict]:
    def extract_node(state: dict) -> dict:
        try:
            if state.get("label_path"):
                result = run_extraction(state["label_path"], extractor)
            else:
                # Text input bypasses the label-validity gate entirely, same
                # as scripts/extract_text.py -- no image was ever checked,
                # so the record must say that plainly.
                gate = GateResult(
                    is_food_label=True,
                    has_ingredients_declaration=True,
                    confidence=1.0,
                    evidence_found=["text_input"],
                    reject_reason=None,
                    product_name=state["name"],
                    product_descriptor=state.get("description"),
                    languages_detected=["en"],
                    language_selected="en",
                    ingredients_panel_bbox=None,
                    warnings=[
                        "input was pasted text, not a photographed label -- no gate validation was performed"
                    ],
                )
                extraction_result = parse_declaration(state["text_input"])
                result = {
                    "gate": gate.model_dump(mode="json"),
                    "extraction": extraction_result.model_dump(mode="json"),
                    "stop_reason": None,
                }
            return {"extraction": result}
        except Exception as exc:  # noqa: BLE001 -- a stage error belongs in state["errors"], not a crashed graph
            return {"errors": [f"extract_node: {exc}"]}

    return extract_node


def make_resolve_node(resolver_refs: References) -> Callable[[dict], dict]:
    def resolve_node(state: dict) -> dict:
        try:
            items = _extraction_items_from_state(state)
            result = resolve_items(items, resolver_refs)
            return {"resolution": result.model_dump(mode="json")}
        except Exception as exc:  # noqa: BLE001
            return {"errors": [f"resolve_node: {exc}"]}

    return resolve_node


# =========================================================================== #
# classify -- one Send per component query, dispatched from the routing
# function below (used as the `path` argument to add_conditional_edges)
# =========================================================================== #
def make_classify_dispatch(config: ExperimentConfig) -> Callable[[dict], list]:
    """The `path` function for add_conditional_edges("resolve", ...,
    ["classify", "confirm"]) -- reads the now-resolved state and returns
    one Send("classify", {...}) per non-empty query (real fan-out: a
    3-component product classifies via 3 concurrent node invocations, not
    a loop), or routes straight to "confirm" if there is nothing to
    classify at all (e.g. no additives resolved) -- classify is never
    invoked with zero queries.
    """

    def dispatch(state: dict) -> list:
        try:
            items = _extraction_items_from_state(state)
            resolved_items = _resolved_items_from_state(state)
            gate = (state.get("extraction") or {}).get("gate") or {}
            queries = build_queries(
                items,
                resolved_items,
                gate.get("product_name"),
                gate.get("product_descriptor"),
                config,
                state.get("description"),
            )
        except Exception:  # noqa: BLE001 -- fall through to "confirm", which will find nothing to confirm either; the underlying error already lives in state["errors"] from resolve_node/extract_node
            return ["confirm"]

        sends = [Send("classify", {"query": q.model_dump(mode="json")}) for q in queries if q.text.strip()]
        return sends or ["confirm"]

    return dispatch


def make_classify_node(
    categories: dict[str, FoodCategory],
    embeddings: dict[str, list[float]],
    embedder: GeminiEmbedder,
    eu_fip: list[dict],
    config: ExperimentConfig,
    chroma_index: ChromaIndex | None = None,
) -> Callable[[dict], dict]:
    """ONE component (or the whole-label product query) per invocation --
    Send's arg REPLACES this node's input entirely, so `state` here is just
    {"query": <CategoryQuery as dict>}, never the full PipelineState.

    chroma_index is None (default, settings.RETRIEVAL_STORE == "numpy") for
    the unchanged embedding_scores/numpy path -- given (settings.
    RETRIEVAL_STORE == "chroma", built once in src/graph/pipeline.py's
    build_pipeline) to search that persisted index via chroma_scores
    instead. Either way the query is still embedded via `embedder` first;
    only what SEARCHES the corpus differs -- classify() itself never knows
    which one produced its `scores` dict.
    """

    def classify_node(state: dict) -> dict:
        try:
            query = CategoryQuery.model_validate(state["query"])
            query_embedding = embedder.embed_query(query.text)
            if chroma_index is not None:
                scores = chroma_scores(chroma_index, query_embedding)
            else:
                scores = embedding_scores(query_embedding, embeddings)
            result = classify(query, scores, categories, eu_fip, config)
            key = result.component_label or PRODUCT_SCOPE_KEY
            return {"category": {key: result.model_dump(mode="json")}}
        except Exception as exc:  # noqa: BLE001
            return {"errors": [f"classify_node: {exc}"]}

    return classify_node


# =========================================================================== #
# confirm -- interrupt() for human-in-the-loop, or auto-confirm rank-1
# =========================================================================== #
def make_confirm_node(auto_confirm: bool = False) -> Callable[[dict], dict]:
    """interrupt() with a payload of {component_label: [{code, name,
    score}, ...]} per query -- the SAME top-3 a human would see confirming
    via --category or app.py's category_confirm screen. The caller resumes
    with Command(resume={component_label: chosen_code}), which becomes this
    function's return value verbatim (a single value resuming the one
    interrupt() call below covers every component at once, not one
    interrupt per component).

    auto_confirm=True (a graph-construction parameter, see
    src/graph/pipeline.py) skips the pause entirely and accepts rank-1 for
    every query -- needed for batch runs and tests, where nothing is
    listening for an interrupt.
    """

    def confirm_node(state: dict) -> dict:
        category = state.get("category") or {}
        if auto_confirm:
            confirmed = {
                label: result["top3"][0]["code"] for label, result in category.items() if result.get("top3")
            }
            return {"confirmed_categories": confirmed}

        payload = {
            label: [
                {"code": c["code"], "name": c["name"], "score": c["similarity"]} for c in result.get("top3", [])
            ]
            for label, result in category.items()
        }
        resume_value = interrupt(payload)
        return {"confirmed_categories": resume_value}

    return confirm_node


# =========================================================================== #
# verdict
# =========================================================================== #
def _join_category_results(
    category_results: list[CategoryResult],
    choices: dict[str, CategoryCandidate],
    resolved_items: list[ResolvedItem],
    item_labels: dict[int, str | None],
) -> tuple[dict[int, CategoryResult], frozenset[int]]:
    """item_id -> the CategoryResult that covers it, matched by the
    component label resolved for THAT item (item_labels, from
    src.category.classifier.item_component_labels) -- never by
    eu_canonical_id, which collapses two items sharing a substance id in
    different components onto whichever component's query was processed
    last. Identical in shape and intent to scripts/verdict.py's
    _build_item_category_map / app.py's _apply_category_confirmations (see
    this module's own DESIGN RULE comment for why it is a third copy, not
    a shared import). `choices` keys use PRODUCT_SCOPE_KEY, not None,
    matching state["category"]'s own keys.
    """
    by_component_label: dict[str, CategoryResult] = {}
    product_result: CategoryResult | None = None
    for result in category_results:
        key = result.component_label if result.query.scope == "component" else PRODUCT_SCOPE_KEY
        if key in choices:
            result = result.model_copy(update={"top3": [choices[key]]})
        if result.query.scope == "component":
            by_component_label[key] = result
        else:
            product_result = result

    mapping: dict[int, CategoryResult] = {}
    confirmed_item_ids: set[int] = set()
    for item in resolved_items:
        label = item_labels.get(item.item_id)
        if label is not None and label in by_component_label:
            chosen = by_component_label[label]
        elif product_result is not None:
            chosen = product_result
        else:
            continue
        mapping[item.item_id] = chosen
        key = chosen.component_label or PRODUCT_SCOPE_KEY
        if key in choices:
            confirmed_item_ids.add(item.item_id)
    return mapping, frozenset(confirmed_item_ids)


def make_verdict_node(eu_fip: list[dict], category_names: dict[str, str]) -> Callable[[dict], dict]:
    def verdict_node(state: dict) -> dict:
        try:
            resolved_items = _resolved_items_from_state(state)
            extraction_items = _extraction_items_from_state(state)
            resolved_by_id = {r.item_id: r for r in resolved_items}
            item_labels = item_component_labels(extraction_items, resolved_by_id)

            category_results = [
                CategoryResult.model_validate(v) for v in (state.get("category") or {}).values()
            ]
            # A confirmed code is looked up against food_categories.json for
            # its name, exactly as scripts/verdict.py's --category override
            # does (_parse_category_overrides) -- similarity=1.0,
            # permitted=True, since a human-confirmed code is not a
            # retrieval score.
            choices = {
                label: CategoryCandidate(code=code, name=category_names.get(code, code), similarity=1.0, permitted=True)
                for label, code in (state.get("confirmed_categories") or {}).items()
            }

            item_category_map, confirmed_item_ids = _join_category_results(
                category_results, choices, resolved_items, item_labels
            )
            verdict = evaluate(resolved_items, item_category_map, eu_fip, confirmed_item_ids)
            return {"verdict": verdict.model_dump(mode="json")}
        except Exception as exc:  # noqa: BLE001
            return {"errors": [f"verdict_node: {exc}"]}

    return verdict_node


# =========================================================================== #
# substitutes / horizon -- parallel branches, both read state["verdict"]
# only, neither depends on the other's output
# =========================================================================== #
def make_substitutes_node(eu_fip: list[dict], codex_ins: list[dict]) -> Callable[[dict], dict]:
    def substitutes_node(state: dict) -> dict:
        try:
            verdict = ProductVerdict.model_validate(state["verdict"])
            resolved_items = _resolved_items_from_state(state)
            canonical_ins_by_item = {r.item_id: r.canonical_ins for r in resolved_items if r.canonical_ins}
            # horizon_flagged=frozenset(): substitutes_node runs in the SAME
            # superstep as horizon_node (true parallel branches, per the
            # task), so it cannot see horizon's output -- the identical
            # graceful degradation scripts/substitutes.py already uses when
            # scripts/horizon.py has not been run for a label yet.
            result = find_substitutes(verdict, eu_fip, codex_ins, canonical_ins_by_item, frozenset())
            return {"substitutes": result.model_dump(mode="json")}
        except Exception as exc:  # noqa: BLE001
            return {"errors": [f"substitutes_node: {exc}"]}

    return substitutes_node


def make_horizon_node(horizon_signals: list[dict], horizon_meta: dict) -> Callable[[dict], dict]:
    def horizon_node(state: dict) -> dict:
        try:
            verdict = ProductVerdict.model_validate(state["verdict"])
            additive_ids = sorted({i.eu_canonical_id for i in verdict.items if i.eu_canonical_id})
            additive_names = {
                i.eu_canonical_id: i.additive_name for i in verdict.items if i.eu_canonical_id and i.additive_name
            }
            result: HorizonResult = find_horizon_signals(
                additive_ids,
                horizon_signals,
                additive_names=additive_names,
                data_retrieved=horizon_meta.get("retrieved"),
            )
            return {"horizon": result.model_dump(mode="json")}
        except Exception as exc:  # noqa: BLE001
            return {"errors": [f"horizon_node: {exc}"]}

    return horizon_node


def make_news_node(provider: SearchProvider | None, cache_path: Path, model_id: str) -> Callable[[dict], dict]:
    """A THIRD parallel branch alongside substitutes_node and horizon_node
    -- reads state["verdict"] only. docs/findings.md has no numbered entry
    for the parallel-branch constraint (checked; only docs/build_log.md's
    "real parallel branches" section describes it), so noting it here
    directly: substitutes_node and horizon_node run in the SAME superstep
    and cannot see each other's output (substitutes_node's own comment,
    "horizon_flagged=frozenset(): ... cannot see horizon's output"); this
    node has the identical limitation with respect to BOTH of them --
    news_node cannot see substitutes' or horizon's output either, and
    vice versa.

    Writes state["news_signals"] -- a key of its OWN, separate from
    state["horizon"], not a second writer sharing horizon_node's key
    (avoids needing a merge reducer for a case where only one of two
    simultaneous writers would touch most fields, see src/graph/state.py).
    Combining this with state["horizon"] into one HorizonResult (which now
    carries a news_signals field alongside its existing EFSA signals --
    src/horizon/schemas.py) happens where the graph's output is actually
    consumed for display (app.py), not here.

    Degrades cleanly when `provider` is None (src/horizon/search.py's
    get_search_provider() found no configured key): returns
    {"news_signals": []} immediately, no cache touched, no error -- the
    EFSA lane (horizon_node) is entirely unaffected either way, since the
    two branches share no state.
    """

    def news_node(state: dict) -> dict:
        if provider is None:
            return {"news_signals": []}
        try:
            verdict = ProductVerdict.model_validate(state["verdict"])
            additive_ids = sorted({i.eu_canonical_id for i in verdict.items if i.eu_canonical_id})
            additive_names = {
                i.eu_canonical_id: i.additive_name for i in verdict.items if i.eu_canonical_id and i.additive_name
            }
            cache = news_cache.load_cache(cache_path)
            signals, updated_cache = find_news_signals(
                additive_ids,
                provider,
                cache,
                model_id,
                datetime.now(UTC).date(),
                additive_names=additive_names,
            )
            news_cache.save_cache(cache_path, updated_cache)
            return {"news_signals": [s.model_dump(mode="json") for s in signals]}
        except Exception as exc:  # noqa: BLE001
            return {"errors": [f"news_node: {exc}"]}

    return news_node


# =========================================================================== #
# narrate -- joins after substitutes AND horizon both complete
# =========================================================================== #
def make_narrate_node(model_id: str) -> Callable[[dict], dict]:
    def narrate_node(state: dict) -> dict:
        try:
            verdict = ProductVerdict.model_validate(state["verdict"])
            substitutes = SubstituteResult.model_validate(state["substitutes"])
            horizon = HorizonResult.model_validate(state["horizon"])
            result = narrate(verdict, substitutes, horizon, model_id)
            return {"narration": result.model_dump(mode="json")}
        except Exception as exc:  # noqa: BLE001
            return {"errors": [f"narrate_node: {exc}"]}

    return narrate_node
