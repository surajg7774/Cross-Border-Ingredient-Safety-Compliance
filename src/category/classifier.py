# DESIGN RULE: pure retrieval + ranking. classify() never calls the
# embedder itself -- the query embedding is computed by the caller
# (scripts/classify_category.py) and passed in, so this module never touches
# the network. No LLM re-ranker: published measurements show general LLMs
# used as rerankers DEGRADE retrieval (NDCG@10 falling from 81.58% to
# ~79-80%); that claim gets tested against this project's own data in the
# eval stage rather than assumed here.
"""Retrieve the EU food categories a query (a label, or one composite
component) plausibly belongs to, then rank by the deterministic additive
permission filter.
"""

from itertools import combinations

import numpy as np

from src.category.corpus import FoodCategory
from src.category.experiment import CONFIGS, ExperimentConfig
from src.category.filter import FilterResult, permitted_in
from src.category.schemas import CategoryCandidate, CategoryQuery, CategoryResult, FilteredCandidate
from src.resolve.schemas import ResolvedItem

TOP_K_RETRIEVED = 10
TOP_N_RESULT = 3

_INGREDIENT_CLASSIFICATIONS = {"food_ingredient", "compound"}


def cosine_similarity(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a, dtype=float), np.array(b, dtype=float)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)


def embedding_scores(query_embedding: list[float], embeddings: dict[str, list[float]]) -> dict[str, float]:
    """code -> cosine similarity to query_embedding. The embedding half of
    the scores dict classify() ranks -- the TF-IDF half is
    src.category.tfidf.tfidf_scores. Kept separate from classify() itself
    so classify() never needs to know which retrieval method produced its
    scores.
    """
    return {code: cosine_similarity(query_embedding, vector) for code, vector in embeddings.items()}


def mean_pairwise_similarity(vectors: dict[str, list[float]]) -> float:
    """Mean cosine similarity across every distinct pair of vectors --
    diagnostic for how well the embedding space discriminates a closed
    corpus (see scripts/diagnose_embeddings.py and
    src.category.tfidf.tfidf_mean_pairwise_similarity, the TF-IDF
    equivalent of this same measure).
    """
    codes = sorted(vectors)
    pairs = list(combinations(codes, 2))
    if not pairs:
        return 0.0
    return sum(cosine_similarity(vectors[a], vectors[b]) for a, b in pairs) / len(pairs)


def _ingredient_names(items: list[dict], resolved_by_id: dict[int, ResolvedItem]) -> list[str]:
    names = []
    for item in items:
        resolved = resolved_by_id.get(item["item_id"])
        if not resolved or resolved.classification not in _INGREDIENT_CLASSIFICATIONS:
            continue
        if item.get("name_as_declared"):
            names.append(item["name_as_declared"])
    return names


def _additive_ids(items: list[dict], resolved_by_id: dict[int, ResolvedItem]) -> list[str]:
    ids = []
    for item in items:
        resolved = resolved_by_id.get(item["item_id"])
        if resolved and resolved.classification == "additive" and resolved.eu_canonical_id:
            ids.append(resolved.eu_canonical_id)
    return ids


def _build_query(
    scope: str,
    component_label: str | None,
    product_name: str | None,
    product_descriptor: str | None,
    user_description: str | None,
    scope_items: list[dict],
    resolved_by_id: dict[int, ResolvedItem],
    config: ExperimentConfig,
) -> CategoryQuery:
    parts = []
    fields_used = []

    # PREPENDED: "Seasoning" is nearly the category name itself, the most
    # discriminative token available for that query -- unlike "Centre",
    # which says nothing on its own, but the ablation measures both, not
    # just the cases where it obviously helps.
    if config.include_component_name and scope == "component" and component_label:
        parts.append(component_label)
        fields_used.append("component_name")

    if config.include_descriptor and product_name:
        parts.append(product_name)
        fields_used.append("product_name")
    if config.include_descriptor and product_descriptor:
        parts.append(product_descriptor)
        fields_used.append("product_descriptor")
    # Hand-supplied, not extracted -- an ingredient list alone cannot tell a
    # biscuit from a cracker from a cake, but the manufacturer knows.
    # MEASURED: adding it to every query ("both") fixed product-level
    # misses but broke component queries -- "Baked potato chips, savoury
    # snack" in a SEASONING query told the retriever the seasoning itself
    # was a potato chip. The description describes the PRODUCT; a
    # component is not the product.
    description_applies = (scope == "product" and config.description_scope in ("product", "both")) or (
        scope == "component" and config.description_scope == "both"
    )
    if description_applies and user_description:
        parts.append(user_description)
        fields_used.append("user_description")

    names = _ingredient_names(scope_items, resolved_by_id)
    if names:
        parts.extend(names)
        fields_used.append("ingredient_names")

    return CategoryQuery(
        scope=scope,
        component_label=component_label,
        text=" ".join(parts),
        fields_used=fields_used,
        additive_ids=_additive_ids(scope_items, resolved_by_id),
    )


def _depth0_ancestor_id(item: dict, items_by_id: dict[int, dict]) -> int:
    """Walk parent_item_id up to this item's nesting_depth==0 ancestor (or
    itself, if it's already at depth 0)."""
    current = item
    while current["parent_item_id"] is not None:
        current = items_by_id[current["parent_item_id"]]
    return current["item_id"]


def item_component_labels(items: list[dict], resolved_by_id: dict[int, ResolvedItem]) -> dict[int, str | None]:
    """item_id -> the label of the component it is physically declared
    inside -- its nearest depth-0 COMPOUND ancestor, found by climbing
    parent_item_id (see _depth0_ancestor_id) -- or None (product scope)
    if the item itself sits at depth 0, or its depth-0 ancestor is not a
    compound. Same walk, and the SAME label text
    (name_as_declared or f"component_{item_id}"), as build_queries() uses
    to group descendants into a component query -- so a label returned
    here always matches a CategoryResult.component_label exactly, if one
    exists for that component.

    This is what lets the script/UI boundary join item -> CategoryResult
    on item_id instead of on eu_canonical_id: a substance id is not
    unique per declaration, so joining on it collapses two items sharing
    an eu_canonical_id in DIFFERENT components onto whichever component's
    query happened to be processed last (e.g. maltitol declared in both
    a product's Inner Layer and its Outer Layer/Chocolate Paste). Every
    item gets its OWN ancestor walk here -- never a result borrowed from
    another item.
    """
    items_by_id = {item["item_id"]: item for item in items}
    labels: dict[int, str | None] = {}
    for item in items:
        if item["nesting_depth"] == 0:
            labels[item["item_id"]] = None
            continue
        ancestor_id = _depth0_ancestor_id(item, items_by_id)
        resolved_ancestor = resolved_by_id.get(ancestor_id)
        if not resolved_ancestor or resolved_ancestor.classification != "compound":
            labels[item["item_id"]] = None
            continue
        ancestor = items_by_id[ancestor_id]
        labels[item["item_id"]] = ancestor.get("name_as_declared") or f"component_{ancestor_id}"
    return labels


def _descendants_by_component(items: list[dict]) -> dict[int, list[dict]]:
    """Every item below nesting_depth 0, grouped by its depth-0 ancestor's
    item_id. Flattens all nesting beneath a depth-0 component into one list:
    deeper structure (a "Biscuit" compound nested inside "Centre") is not
    scoped as its own component -- see build_queries' docstring."""
    items_by_id = {item["item_id"]: item for item in items}
    groups: dict[int, list[dict]] = {}
    for item in items:
        if item["nesting_depth"] == 0:
            continue
        component_id = _depth0_ancestor_id(item, items_by_id)
        groups.setdefault(component_id, []).append(item)
    return groups


def build_queries(
    items: list[dict],
    resolved_items: list[ResolvedItem],
    product_name: str | None,
    product_descriptor: str | None,
    config: ExperimentConfig = CONFIGS["baseline"],
    user_description: str | None = None,
) -> list[CategoryQuery]:
    """One PRODUCT query for the whole label, plus one COMPONENT query per
    depth-0 compound that carries at least one additive among its
    descendants.

    Each additive is judged against the food category of the part it is
    physically IN, not the product as a whole: E551 in a chips label is
    declared inside the "Seasoning" block and is permitted in 12.2.2
    (seasonings), not 15.1 (savoury snacks) -- judging it against the
    product category alone made a correct verdict look non-compliant. So an
    additive's eu_canonical_id goes to exactly ONE query: the query for its
    nearest depth-0 compound ancestor, found by walking parent_item_id up to
    nesting_depth 0 (see _depth0_ancestor_id) -- or the PRODUCT query, if it
    sits at depth 0 itself with no such ancestor.

    The PRODUCT query is built from depth-0 item names (food_ingredient or
    compound) and carries the depth-0 additives directly, UNLESS every
    depth-0 item is a compound -- then a product-level query would be
    meaningless text ("Inner Layer Outer Layer") and the component queries
    already cover the whole label between them, so it is skipped.

    A COMPONENT query is built from its compound's OWN DESCENDANTS (any
    depth below it, not just direct children) -- "Seasoning" or "Centre"
    alone says nothing about the food. A compound with no additive among
    its descendants gets no query at all (Chips' "Edible Vegetable Oil
    (Rice Bran Oil)" is a compound but carries no additive; a category for
    it would be noise nobody needs).

    KNOWN LIMITATION: only depth-0 components are scoped. A compound nested
    inside another compound (Chocolateraw's "Biscuit" inside "Centre") is
    folded into its depth-0 ancestor's query rather than getting its own
    category, and the EU carry-over principle (Reg 1333/2008 Art 18 -- an
    additive may be permitted in a compound food if permitted in one of its
    own ingredients) is not modelled at all.

    user_description is a hand-supplied, per-label product description --
    not extracted from the label. config.description_scope decides which
    queries it's added to (see ExperimentConfig).
    """
    resolved_by_id = {r.item_id: r for r in resolved_items}
    top_level = [item for item in items if item["nesting_depth"] == 0]
    descendants = _descendants_by_component(items)

    compounds = [
        item
        for item in top_level
        if (resolved := resolved_by_id.get(item["item_id"])) and resolved.classification == "compound"
    ]
    all_compound = bool(top_level) and len(compounds) == len(top_level)

    queries = []
    if not all_compound:
        queries.append(
            _build_query(
                "product", None, product_name, product_descriptor, user_description, top_level, resolved_by_id, config
            )
        )

    for component in compounds:
        component_items = descendants.get(component["item_id"], [])
        if not _additive_ids(component_items, resolved_by_id):
            continue  # no additives under this compound -- a category here would be noise
        label = component.get("name_as_declared") or f"component_{component['item_id']}"
        queries.append(
            _build_query(
                "component",
                label,
                product_name,
                product_descriptor,
                user_description,
                component_items,
                resolved_by_id,
                config,
            )
        )
    return queries


def classify(
    query: CategoryQuery,
    scores: dict[str, float],
    corpus: dict[str, FoodCategory],
    eu_fip: list[dict],
    config: ExperimentConfig = CONFIGS["baseline"],
) -> CategoryResult:
    """Retrieve the 10 nearest categories by `scores` (code -> similarity,
    already computed by the caller -- embedding_scores or
    src.category.tfidf.tfidf_scores; classify() does not care which),
    apply the deterministic additive-permission filter (unless
    config.intersection_filter is False -- the ablation that measures its
    effect), and rank permitted matches first.
    """
    retrieved = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)[:TOP_K_RETRIEVED]

    if config.intersection_filter:
        filter_result = permitted_in(query.additive_ids, eu_fip)
    else:
        filter_result = FilterResult(categories=set(), excluded_additives=[], breadth={}, empty_intersection=False)
    permitted_codes = filter_result.categories

    ranked = sorted(retrieved, key=lambda pair: (pair[0] not in permitted_codes, -pair[1]))

    top3 = [
        CategoryCandidate(
            code=code,
            name=corpus[code].name,
            similarity=score,
            permitted=code in permitted_codes,
        )
        for code, score in ranked[:TOP_N_RESULT]
    ]

    def _reason(code: str) -> str:
        if not config.intersection_filter:
            return "filter_disabled"
        return "not_permitted" if code not in permitted_codes else "ranked_below_top3"

    filtered_out = [
        FilteredCandidate(code=code, name=corpus[code].name, similarity=score, reason=_reason(code))
        for code, score in ranked[TOP_N_RESULT:]
    ]

    return CategoryResult(
        component_label=query.component_label,
        query=query,
        top3=top3,
        filtered_out=filtered_out,
        empty_intersection=filter_result.empty_intersection,
        excluded_additives=filter_result.excluded_additives,
        additive_breadth=filter_result.breadth,
    )
