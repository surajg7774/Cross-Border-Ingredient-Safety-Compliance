"""Tests for the category stage (src/category/). Plain pytest, small
hand-built dicts standing in for the reference datasets -- no fixture files,
no API calls (the embedder's network client is never instantiated here).
"""

from src.category.chroma_store import build_chroma_index, chroma_scores
from src.category.classifier import (
    build_queries,
    classify,
    cosine_similarity,
    embedding_scores,
    item_component_labels,
    mean_pairwise_similarity,
)
from src.category.corpus import build_corpus, parse_food_categories, validate_corpus
from src.category.embedder import _cache_key
from src.category.experiment import CONFIGS
from src.category.filter import permitted_in
from src.category.schemas import CategoryQuery
from src.category.tfidf import build_tfidf_index, tfidf_scores
from src.resolve.schemas import ResolvedItem


def _raw_category(code, name, desc=None):
    return {
        "childrenValues": [
            {"valueIdentifier": "refDataFoodCategoryLevel", "value": code},
            {"valueIdentifier": "refDataFoodCategoryEN", "value": name},
            {"valueIdentifier": "refDataFoodCategoryDesc", "value": desc},
        ]
    }


def _resolved(item_id, classification, eu_canonical_id=None):
    return ResolvedItem(
        item_id=item_id,
        canonical_ins=None,
        eu_canonical_id=eu_canonical_id,
        classification=classification,
        normalised_role=None,
        codex_functional_classes=[],
        resolution_method="test",
        resolution_confidence=1.0,
        flags=[],
        candidates=[],
        matched_on=None,
    )


def _item(item_id, nesting_depth, parent_item_id, name_as_declared):
    return {
        "item_id": item_id,
        "nesting_depth": nesting_depth,
        "parent_item_id": parent_item_id,
        "name_as_declared": name_as_declared,
    }


# --------------------------------------------------------------------------- #
# corpus.py
# --------------------------------------------------------------------------- #
def test_parent_chain_inheritance_fills_in_a_thin_child_description():
    # "7.2 Fine bakery wares" has no description of its own -- too thin to
    # match an ingredient list against -- so it must inherit its parent's
    # (7's) name and description text.
    raw = [
        _raw_category("7", "Bakery wares", "products prepared mainly with cereal flour"),
        _raw_category("7.2", "Fine bakery wares", None),
    ]
    categories, documents, _flags = build_corpus(raw)
    assert categories["7.2"].description is None
    assert "Bakery wares" in documents["7.2"]
    assert "cereal flour" in documents["7.2"]


def test_validate_corpus_flags_category_15_wrong_description():
    # Real data-quality bug: category 15's description is entirely about
    # category 14 (beverages) -- it must be flagged and excluded, while a
    # normal self-referencing description (7 -> "(7.2)") must not be.
    raw = [
        _raw_category("7", "Bakery wares", "sweet or salty fine bakery wares (7.2)."),
        _raw_category("7.2", "Fine bakery wares", None),
        _raw_category("14", "Beverages", None),
        _raw_category("14.1", "Water", None),
        _raw_category(
            "15",
            "Ready-to-eat savouries and snacks",
            "This category includes waters and carbonated waters (14.1).",
        ),
    ]
    categories = parse_food_categories(raw)
    flags = validate_corpus(categories)
    flagged_codes = {f.code for f in flags}
    assert "15" in flagged_codes
    assert "7" not in flagged_codes

    _, documents, _ = build_corpus(raw)
    assert "carbonated waters" not in documents["15"]
    assert "Ready-to-eat savouries and snacks" in documents["15"]  # name is kept


# --------------------------------------------------------------------------- #
# filter.py
# --------------------------------------------------------------------------- #
def test_additive_with_no_permitted_rows_is_excluded_not_collapsing_to_empty():
    eu_fip = [
        {"canonical_id": "A", "status": "permitted", "food_category_raw": "1 cat one"},
        {"canonical_id": "A", "status": "permitted", "food_category_raw": "2 cat two"},
        {"canonical_id": "B", "status": "prohibited", "food_category_raw": "1 cat one"},
    ]
    result = permitted_in(["A", "B"], eu_fip)
    assert result.categories == {"1", "2"}
    assert result.excluded_additives == ["B"]
    assert not result.empty_intersection


def test_empty_intersection_is_a_flag_not_an_exception():
    eu_fip = [
        {"canonical_id": "A", "status": "permitted", "food_category_raw": "1 cat one"},
        {"canonical_id": "C", "status": "permitted", "food_category_raw": "2 cat two"},
    ]
    result = permitted_in(["A", "C"], eu_fip)
    assert result.categories == set()
    assert result.empty_intersection is True
    assert result.excluded_additives == []


# --------------------------------------------------------------------------- #
# classifier.py
# --------------------------------------------------------------------------- #
def test_cosine_similarity_on_hand_built_vectors():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == -1.0
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0  # zero vector, no divide-by-zero


def test_chips_shape_product_and_seasoning_with_additive_scoping():
    # Real bug: E551 is declared inside "Seasoning", not at product level,
    # and is permitted in 12.2.2 (seasonings) but NOT 15.1 (savoury snacks)
    # -- judging it against the product category alone made a correct
    # verdict look non-compliant. Must produce a product query AND a
    # separate Seasoning query, with every additive attributed to
    # Seasoning and none to the product. "Edible Vegetable Oil" is a
    # compound too but carries no additive, so it gets no query at all.
    items = [
        _item(0, 0, None, "Potato"),
        _item(1, 0, None, "Edible Vegetable Oil"),
        _item(2, 1, 1, "Rice Bran Oil"),
        _item(3, 0, None, "Seasoning"),
        _item(4, 1, 3, "Iodised Salt"),
        _item(5, 1, 3, "Anticaking Agent"),
    ]
    resolved = [
        _resolved(0, "food_ingredient"),
        _resolved(1, "compound"),
        _resolved(2, "food_ingredient"),
        _resolved(3, "compound"),
        _resolved(4, "food_ingredient"),
        _resolved(5, "additive", eu_canonical_id="551"),
    ]
    queries = build_queries(items, resolved, None, None)
    assert len(queries) == 2

    product = next(q for q in queries if q.scope == "product")
    seasoning = next(q for q in queries if q.scope == "component")
    assert seasoning.component_label == "Seasoning"
    assert product.additive_ids == []
    assert seasoning.additive_ids == ["551"]
    assert "Iodised Salt" in seasoning.text
    assert "Seasoning" not in seasoning.text  # own name excluded, only descendants
    assert "Potato" in product.text
    assert "Edible Vegetable Oil" in product.text  # the compound's own name, not recursed into
    assert "Rice Bran Oil" not in product.text


def test_compound_with_no_additive_descendants_gets_no_query():
    # A plain sibling ("Potato") keeps this from being the all-compound
    # case, so a genuinely absent component query (not just an absent
    # product query) is what's under test.
    items = [
        _item(0, 0, None, "Potato"),
        _item(1, 0, None, "Edible Vegetable Oil"),
        _item(2, 1, 1, "Rice Bran Oil"),
    ]
    resolved = [
        _resolved(0, "food_ingredient"),
        _resolved(1, "compound"),
        _resolved(2, "food_ingredient"),
    ]
    queries = build_queries(items, resolved, None, None)
    assert len(queries) == 1
    assert queries[0].scope == "product"  # the compound itself never gets its own query


def test_pepsi_shape_depth0_additives_go_to_product_query():
    # A "FLAVOUR" compound sits beside plain top-level ingredients
    # ("CARBONATED WATER", "CAFFEINE") -- one product with an incidental
    # sub-mixture, not several physical parts. Its only descendant is a
    # flavouring, not an additive, so FLAVOUR gets no query of its own.
    # A real additive declared directly at depth 0 must reach the product
    # query's additive_ids.
    items = [
        _item(0, 0, None, "CARBONATED WATER"),
        _item(1, 0, None, "CAFFEINE"),
        _item(2, 0, None, "FLAVOUR"),
        _item(3, 1, 2, "NATURAL FLAVOURING SUBSTANCES"),
        _item(4, 0, None, "PRESERVATIVE"),
    ]
    resolved = [
        _resolved(0, "food_ingredient"),
        _resolved(1, "food_ingredient"),
        _resolved(2, "compound"),
        _resolved(3, "flavouring"),
        _resolved(4, "additive", eu_canonical_id="211"),
    ]
    queries = build_queries(items, resolved, None, None)
    assert len(queries) == 1
    assert queries[0].scope == "product"
    assert queries[0].text.strip()  # must not be empty
    assert "CARBONATED WATER" in queries[0].text
    assert "CAFFEINE" in queries[0].text
    assert queries[0].additive_ids == ["211"]


def test_ice_cream_shape_all_compound_top_level_has_no_product_query():
    # Every depth-0 item is a compound -- a product query would be
    # meaningless text ("Inner Layer Outer Layer"), so it is skipped; each
    # layer gets its own query, scoped to its own additives only.
    items = [
        _item(0, 0, None, "Inner Layer"),
        _item(1, 1, 0, "Milk and Milk Solids"),
        _item(2, 1, 0, "Stabiliser"),
        _item(3, 0, None, "Outer Layer"),
        _item(4, 1, 3, "Cocoa Butter"),
        _item(5, 1, 3, "Emulsifier"),
    ]
    resolved = [
        _resolved(0, "compound"),
        _resolved(1, "food_ingredient"),
        _resolved(2, "additive", eu_canonical_id="466"),
        _resolved(3, "compound"),
        _resolved(4, "food_ingredient"),
        _resolved(5, "additive", eu_canonical_id="471"),
    ]
    queries = build_queries(items, resolved, "Ice Cream Bar", "Medium Fat")
    assert len(queries) == 2
    assert all(q.scope == "component" for q in queries)
    labels = {q.component_label for q in queries}
    assert labels == {"Inner Layer", "Outer Layer"}
    inner = next(q for q in queries if q.component_label == "Inner Layer")
    outer = next(q for q in queries if q.component_label == "Outer Layer")
    assert inner.additive_ids == ["466"]
    assert outer.additive_ids == ["471"]
    assert "Milk and Milk Solids" in inner.text
    assert "Cocoa Butter" not in inner.text  # belongs to the other component only


# --------------------------------------------------------------------------- #
# item_component_labels -- the per-item ancestor walk the script/UI
# boundary joins item -> CategoryResult on, replacing a join keyed on
# eu_canonical_id (which collapses when the same substance is declared in
# two different components).
# --------------------------------------------------------------------------- #
def test_ice_creammain_real_shape_same_eu_id_two_components_get_different_labels():
    # THE MEASURED BUG: Ice-creammain declares maltitol (eu_canonical_id
    # 965) twice -- item 2 at depth 1 directly under Inner Layer, and item
    # 15 at depth 2 under Chocolate Paste (depth 1) under Outer Layer
    # (depth 0). A join keyed on eu_canonical_id collapses both onto
    # whichever component's query ran last; item_component_labels must
    # give each item its OWN, correct label.
    items = [
        _item(0, 0, None, "Inner Layer"),
        _item(1, 1, 0, "Milk and Milk Solids"),
        _item(2, 1, 0, "Maltitol"),
        _item(13, 0, None, "Outer Layer"),
        _item(14, 1, 13, "Chocolate Paste"),
        _item(15, 2, 14, "Maltitol"),
    ]
    resolved_by_id = {
        r.item_id: r
        for r in [
            _resolved(0, "compound"),
            _resolved(1, "food_ingredient"),
            _resolved(2, "additive", eu_canonical_id="965"),
            _resolved(13, "compound"),
            _resolved(14, "compound"),
            _resolved(15, "additive", eu_canonical_id="965"),
        ]
    }

    labels = item_component_labels(items, resolved_by_id)

    assert labels[2] == "Inner Layer"
    assert labels[15] == "Outer Layer"
    assert labels[2] != labels[15]


def test_three_level_walk_returns_depth0_ancestor_not_intermediate_compound():
    # depth 2 -> depth 1 (Chocolate Paste, itself a compound) -> depth 0
    # (Outer Layer) -- the label must be the DEPTH-0 ancestor's, never the
    # intermediate compound's, matching build_queries' own KNOWN LIMITATION
    # that only depth-0 compounds get their own component scope.
    items = [
        _item(13, 0, None, "Outer Layer"),
        _item(14, 1, 13, "Chocolate Paste"),
        _item(15, 2, 14, "Maltitol"),
    ]
    resolved_by_id = {
        r.item_id: r
        for r in [
            _resolved(13, "compound"),
            _resolved(14, "compound"),
            _resolved(15, "additive", eu_canonical_id="965"),
        ]
    }

    labels = item_component_labels(items, resolved_by_id)

    assert labels[15] == "Outer Layer"
    assert labels[15] != "Chocolate Paste"


def test_chocolatemain_real_shape_442_in_two_components_gets_two_routings():
    # Chocolatemain declares E442 in both Milk Chocolate (item 5, depth 1)
    # and Centre (item 23, depth 1, under a different depth-0 compound).
    items = [
        _item(0, 0, None, "Milk Chocolate"),
        _item(4, 1, 0, "Cocos Solids"),
        _item(5, 1, 0, None),
        _item(8, 0, None, "Centre"),
        _item(22, 1, 8, "Milk Solids"),
        _item(23, 1, 8, None),
    ]
    resolved_by_id = {
        r.item_id: r
        for r in [
            _resolved(0, "compound"),
            _resolved(4, "food_ingredient"),
            _resolved(5, "additive", eu_canonical_id="442"),
            _resolved(8, "compound"),
            _resolved(22, "food_ingredient"),
            _resolved(23, "additive", eu_canonical_id="442"),
        ]
    }

    labels = item_component_labels(items, resolved_by_id)

    assert labels[5] == "Milk Chocolate"
    assert labels[23] == "Centre"
    assert labels[5] != labels[23]


def test_non_composite_label_item_component_labels_unaffected():
    # No nesting at all -- every item at depth 0, no compounds -- every
    # item is product-scope (None), same as before this fix.
    items = [
        _item(0, 0, None, "Sugar"),
        _item(1, 0, None, None),  # bare E-number, e.g. E330
    ]
    resolved_by_id = {
        r.item_id: r
        for r in [
            _resolved(0, "food_ingredient"),
            _resolved(1, "additive", eu_canonical_id="330"),
        ]
    }

    labels = item_component_labels(items, resolved_by_id)

    assert labels == {0: None, 1: None}


def test_descendant_of_a_non_compound_depth0_item_is_product_scope():
    # A depth-0 ancestor that is NOT classified "compound" (e.g. data
    # oddity, or a food_ingredient with a stray child) must not produce a
    # component label -- matches build_queries' own `compounds` filter.
    items = [
        _item(0, 0, None, "Flavour"),
        _item(1, 1, 0, "Vanillin"),
    ]
    resolved_by_id = {
        r.item_id: r
        for r in [
            _resolved(0, "flavouring"),
            _resolved(1, "additive", eu_canonical_id="330"),
        ]
    }

    labels = item_component_labels(items, resolved_by_id)

    assert labels[1] is None


def test_classify_ranks_permitted_candidates_above_unpermitted_ones():
    # Hand-built, unit-length vectors and a fake corpus/eu_fip -- no
    # embedder involved, so this exercises the ranking logic alone.
    categories, _documents, _ = build_corpus(
        [_raw_category("1", "Category One"), _raw_category("2", "Category Two")]
    )
    embeddings = {"1": [1.0, 0.0], "2": [1.0, 0.0]}  # identical similarity to the query
    eu_fip = [{"canonical_id": "330", "status": "permitted", "food_category_raw": "2 cat two"}]
    query = CategoryQuery(scope="product", component_label=None, text="x", fields_used=[], additive_ids=["330"])

    result = classify(query, embedding_scores([1.0, 0.0], embeddings), categories, eu_fip)

    assert result.top3[0].code == "2"  # permitted, ranked first despite tied similarity
    assert result.top3[0].permitted is True
    assert result.top3[1].code == "1"
    assert result.top3[1].permitted is False


def test_classify_ranks_identically_regardless_of_score_provenance():
    # classify() only ever sees a code -> float mapping -- it must rank the
    # same whether that mapping came from a plain dict, embedding_scores(),
    # or tfidf_scores(). Three different provenances, same relative order.
    categories, _documents, _ = build_corpus(
        [_raw_category("1", "Category One"), _raw_category("2", "Category Two")]
    )
    eu_fip = []
    query = CategoryQuery(scope="product", component_label=None, text="x", fields_used=[])

    hand_built = {"1": 0.9, "2": 0.1}
    from_embedding = embedding_scores([1.0, 0.0], {"1": [0.9, 0.43589], "2": [0.1, 0.99499]})
    tfidf_index = build_tfidf_index({"1": "category one alpha", "2": "category two beta"})
    from_tfidf = tfidf_scores(tfidf_index, "alpha")  # only doc "1" shares vocabulary with the query

    for scores in (hand_built, from_embedding, from_tfidf):
        result = classify(query, scores, categories, eu_fip)
        assert [c.code for c in result.top3] == ["1", "2"]


def test_mean_pairwise_similarity_of_identical_vectors_is_one():
    vectors = {"a": [1.0, 0.0], "b": [1.0, 0.0], "c": [1.0, 0.0]}
    assert mean_pairwise_similarity(vectors) == 1.0


# --------------------------------------------------------------------------- #
# tfidf.py
# --------------------------------------------------------------------------- #
def test_tfidf_scores_covers_every_document_code():
    documents = {"1": "category one alpha", "2": "category two beta", "3": "category three gamma"}
    index = build_tfidf_index(documents)
    scores = tfidf_scores(index, "alpha")
    assert set(scores) == {"1", "2", "3"}


def test_tfidf_literal_token_match_ranks_first():
    # "Seasoning" -> "Seasonings and condiments" is exactly this case: a
    # literal shared token TF-IDF should rank above documents with none.
    documents = {
        "12.2.2": "Seasonings and condiments",
        "15.1": "Potato cereal flour or starch based snacks",
        "5.1": "Cocoa and chocolate products",
    }
    index = build_tfidf_index(documents)
    scores = tfidf_scores(index, "Seasoning spices salt")
    best = max(scores, key=scores.get)
    assert best == "12.2.2"


# --------------------------------------------------------------------------- #
# chroma_store.py -- hand-written vectors only, no API calls
# --------------------------------------------------------------------------- #
def test_chroma_scores_covers_every_document_code():
    documents = {"1": "category one", "2": "category two", "3": "category three"}
    embeddings = {"1": [1.0, 0.0], "2": [0.0, 1.0], "3": [0.7071, 0.7071]}
    index = build_chroma_index(documents, embeddings)
    scores = chroma_scores(index, [1.0, 0.0])
    assert set(scores) == {"1", "2", "3"}


def test_chroma_and_embedding_scores_agree_on_top3_ordering():
    # Same vectors, same cosine metric, same 5 documents -- chromadb is a
    # vector-STORE ablation, not an embedding-model one, so the two paths
    # must rank identically (see chroma_store.py's EXPECTED comment).
    documents = {
        "1": "a",
        "2": "b",
        "3": "c",
        "4": "d",
        "5": "e",
    }
    embeddings = {
        "1": [1.0, 0.0, 0.0],
        "2": [0.9, 0.1, 0.0],
        "3": [0.0, 1.0, 0.0],
        "4": [0.0, 0.9, 0.1],
        "5": [0.0, 0.0, 1.0],
    }
    query = [1.0, 0.1, 0.0]

    from_embedding = embedding_scores(query, embeddings)

    index = build_chroma_index(documents, embeddings)
    from_chroma = chroma_scores(index, query)

    top3_embedding = sorted(from_embedding, key=from_embedding.get, reverse=True)[:3]
    top3_chroma = sorted(from_chroma, key=from_chroma.get, reverse=True)[:3]
    assert top3_embedding == top3_chroma


def test_chroma_exact_match_scores_near_one_not_near_zero():
    # Locks in the distance -> similarity conversion (similarity = 1 -
    # distance): a query identical to a document must score near 1.0. Get
    # the conversion backwards and this would score near 0.0 instead.
    documents = {"1": "category one", "2": "category two"}
    embeddings = {"1": [1.0, 0.0], "2": [0.0, 1.0]}
    index = build_chroma_index(documents, embeddings)
    scores = chroma_scores(index, [1.0, 0.0])
    assert scores["1"] > 0.99


# --------------------------------------------------------------------------- #
# experiment.py -- ablation configs
# --------------------------------------------------------------------------- #
def _chips_shape_items_and_resolved():
    """Potato (plain) + Seasoning (compound, carries E551) -- one product
    query, one component query, matching the real Chipsmain shape."""
    items = [
        _item(0, 0, None, "Potato"),
        _item(1, 0, None, "Seasoning"),
        _item(2, 1, 1, "Iodised Salt"),
        _item(3, 1, 1, "Anticaking Agent"),
    ]
    resolved = [
        _resolved(0, "food_ingredient"),
        _resolved(1, "compound"),
        _resolved(2, "food_ingredient"),
        _resolved(3, "additive", eu_canonical_id="551"),
    ]
    return items, resolved


def test_include_component_name_prepends_to_component_query_only():
    items, resolved = _chips_shape_items_and_resolved()
    baseline = build_queries(items, resolved, None, None, CONFIGS["baseline"])
    with_name = build_queries(items, resolved, None, None, CONFIGS["with-component-name"])

    baseline_product = next(q for q in baseline if q.scope == "product")
    baseline_component = next(q for q in baseline if q.scope == "component")
    named_product = next(q for q in with_name if q.scope == "product")
    named_component = next(q for q in with_name if q.scope == "component")

    assert named_product.text == baseline_product.text  # product query untouched
    assert named_component.text == f"Seasoning {baseline_component.text}"
    assert "component_name" in named_component.fields_used
    assert "component_name" not in named_product.fields_used


def test_description_scope_product_adds_to_product_only():
    # MEASURED: "both" fixed product-level misses but broke component
    # queries -- the description describes the PRODUCT, not a component.
    items, resolved = _chips_shape_items_and_resolved()
    queries = build_queries(
        items, resolved, None, None, CONFIGS["description-product-only"], "Baked potato chips, savoury snack"
    )
    product = next(q for q in queries if q.scope == "product")
    component = next(q for q in queries if q.scope == "component")

    assert "Baked potato chips, savoury snack" in product.text
    assert "Baked potato chips, savoury snack" not in component.text
    assert "user_description" in product.fields_used
    assert "user_description" not in component.fields_used


def test_description_scope_both_adds_to_both_scopes():
    items, resolved = _chips_shape_items_and_resolved()
    queries = build_queries(
        items, resolved, None, None, CONFIGS["with-description"], "Baked potato chips, savoury snack"
    )
    product = next(q for q in queries if q.scope == "product")
    component = next(q for q in queries if q.scope == "component")

    assert "Baked potato chips, savoury snack" in product.text
    assert "Baked potato chips, savoury snack" in component.text
    assert "user_description" in product.fields_used
    assert "user_description" in component.fields_used


def test_baseline_query_text_unchanged_by_new_flags():
    # Both new flags default OFF -- baseline text must be byte-identical to
    # before this turn's additions, even when a user_description is passed
    # in: it must simply be ignored unless the config asks for it.
    items, resolved = _chips_shape_items_and_resolved()
    queries = build_queries(
        items, resolved, None, None, CONFIGS["baseline"], "Baked potato chips, savoury snack"
    )
    product = next(q for q in queries if q.scope == "product")
    component = next(q for q in queries if q.scope == "component")

    assert product.text == "Potato Seasoning"
    assert component.text == "Iodised Salt"
    assert "user_description" not in product.fields_used
    assert "component_name" not in component.fields_used


def test_no_parent_inheritance_config_drops_ancestor_text():
    raw = [
        _raw_category("7", "Bakery wares", "products prepared mainly with cereal flour"),
        _raw_category("7.2", "Fine bakery wares", None),
    ]
    _, documents, _ = build_corpus(raw, CONFIGS["no-parent-inheritance"])
    assert "Bakery wares" not in documents["7.2"]
    assert "cereal flour" not in documents["7.2"]
    assert documents["7.2"] == "7.2 -- Fine bakery wares"


def test_no_descriptor_config_drops_product_name_and_descriptor():
    items = [_item(0, 0, None, "Potato"), _item(1, 0, None, "Salt")]
    resolved = [_resolved(0, "food_ingredient"), _resolved(1, "food_ingredient")]
    queries = build_queries(items, resolved, "Chips", "Salted", CONFIGS["no-descriptor"])
    assert len(queries) == 1
    assert "Chips" not in queries[0].text
    assert "Salted" not in queries[0].text
    assert "product_name" not in queries[0].fields_used
    assert "product_descriptor" not in queries[0].fields_used
    assert "Potato" in queries[0].text


def test_filter_off_config_ranks_by_similarity_alone():
    categories, _documents, _ = build_corpus(
        [_raw_category("1", "Category One"), _raw_category("2", "Category Two")]
    )
    # "2" would win under the filter (it's permitted); with the filter off,
    # the higher-similarity "1" must win instead.
    embeddings = {"1": [1.0, 0.0], "2": [0.9, 0.1]}
    eu_fip = [{"canonical_id": "330", "status": "permitted", "food_category_raw": "2 cat two"}]
    query = CategoryQuery(scope="product", component_label=None, text="x", fields_used=[], additive_ids=["330"])

    result = classify(query, embedding_scores([1.0, 0.0], embeddings), categories, eu_fip, CONFIGS["filter-off"])

    assert result.top3[0].code == "1"
    assert result.top3[0].permitted is False
    assert result.empty_intersection is False


# --------------------------------------------------------------------------- #
# embedder.py
# --------------------------------------------------------------------------- #
def test_cache_key_changes_with_model_id():
    # gemini-embedding-001 and gemini-embedding-2 have incompatible embedding
    # spaces -- the model id MUST be part of the cache key, or a later model
    # swap would silently reuse stale vectors from a different space.
    key_a = _cache_key("some category text", "gemini-embedding-001", 768)
    key_b = _cache_key("some category text", "gemini-embedding-2", 768)
    assert key_a != key_b
