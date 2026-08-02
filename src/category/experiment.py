"""Experiment configuration for the category-stage ablation harness.

Each named config flips ONE pipeline behaviour off from baseline, so a
single run's score isolates that behaviour's effect. Passed explicitly into
the pure functions that need it (corpus.py, classifier.py, tfidf.py) --
never read from settings inside src/.
"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ExperimentConfig:
    name: str = "baseline"
    parent_inheritance: bool = True  # corpus.py: embed ancestor name/description text
    intersection_filter: bool = True  # classifier.py: apply the additive-permission filter
    include_descriptor: bool = True  # classifier.py: include product_name/product_descriptor in queries
    # classifier.py: prepend a component query's own label to its text --
    # OFF by default because "Centre" alone says nothing about the food, but
    # measured as an ablation because "Seasoning" is nearly the category
    # name itself and is the most discriminative token available for it.
    include_component_name: bool = False
    # classifier.py: where the hand-supplied per-label user_description
    # (data/labels/descriptions.json) is added.
    #   "none":    never (default -- it is hand-supplied, not extracted).
    #   "product": product-scope queries only. MEASURED reason: the
    #              description describes the PRODUCT, and a component is
    #              not the product -- "Baked potato chips, savoury snack" in
    #              a SEASONING query told the retriever the seasoning
    #              itself was a potato chip, and broke a component query
    #              (12.2.2) that ranked correctly without it.
    #   "both":    every query -- the original behaviour, kept only so
    #              CONFIGS["with-description"] stays comparable to its
    #              already-logged experiments.csv rows.
    description_scope: Literal["none", "product", "both"] = "none"
    # classify()/scripts/classify_category.py: which retrieval scores
    # classify() ranks. "tfidf" never calls the embedder at all -- see
    # scripts/classify_category.py and src/category/tfidf.py. "chromadb"
    # still calls the embedder (same vectors as "embedding") but indexes
    # and searches them through Chroma instead of exhaustive numpy cosine
    # similarity -- a vector-STORE ablation, not an embedding-model one;
    # see src/category/chroma_store.py.
    retrieval_method: Literal["embedding", "tfidf", "chromadb"] = "embedding"
    # corpus.py::build_documents: how much of each category's TEXT goes into
    # the embedded document -- orthogonal to every ablation above, all of
    # which vary the QUERY side. Every corpus experiment before this one
    # added or removed text around a baseline that already included the
    # legal-prose description; the category NAME alone (e.g. "3 edible
    # ices", "12.2.2 Seasonings and condiments") was never tried on its own.
    # MEASURED: mean pairwise cosine similarity across all 155 documents is
    # 0.7847, rank-1 to rank-10 gap on a real query is 0.0335 -- descriptions
    # share heavy boilerplate ("category" in 100 of 132, "this" in 98,
    # "covers" in 71), so it is plausible the description text has been
    # DILUTING the signal, not sharpening it.
    #   "full":               current behaviour -- code + name + description
    #                         + (parent_inheritance) ancestor name/description.
    #                         UNCHANGED DEFAULT.
    #   "name-only":          code + name ONLY. No description, no parent
    #                         chain, regardless of parent_inheritance. The
    #                         null hypothesis.
    #   "name-plus-examples": code + name + the sentence carrying an example
    #                         clause ("e.g.", "for example", "Examples
    #                         include", "such as"), when the description has
    #                         one -- else name only, same as "name-only".
    #   "strip-framing":      same shape as "full" (respects
    #                         parent_inheritance), but every description used
    #                         has its leading, sentence-initial framing
    #                         phrases removed first ("This category
    #                         covers/includes/comprises", "Includes all
    #                         other") -- stock filler that names nothing.
    #                         "Includes" that introduces real content (e.g.
    #                         "Includes chocolate-coated wafers...") is left
    #                         alone; only the exact filler phrases above are
    #                         stripped, sentence-initial only.
    #   "enriched":           code + name + the distinct Codex functional
    #                         classes of every additive permitted in that
    #                         category (eu_fip joined to codex_ins, same
    #                         parent-widen + subtype-union fallback as
    #                         src.substitutes.advisor, ranked by how many
    #                         permitted additives share each class, capped at
    #                         10). Uses data the legal text does not carry.
    #                         Requires eu_fip/codex_ins to be passed into
    #                         build_documents/build_corpus.
    corpus_mode: Literal["full", "name-only", "name-plus-examples", "strip-framing", "enriched"] = "full"
    # Retrieval-STRATEGY ablations -- all three are refinements of the SAME
    # embedding vector space (never a separate retrieval_method), applied at
    # query time in scripts/classify_category.py::_build_scorer, mutually
    # exclusive in every CONFIGS entry below (their combined effect is
    # untested). classify() itself never knows which of these produced the
    # scores dict it was handed -- see its own docstring.
    #
    # mmr_lambda: maximal marginal relevance re-ranking (src.category.
    # classifier.mmr_scores). None = off. MEASURED motivation: Khusmain's
    # top-3 was 14.1.4 / 14.1 / 14 -- three levels of ONE branch. The user
    # must CONFIRM the category, so a diverse top-3 is worth as much as a
    # better rank-1. lambda trades relevance (1.0) for diversity (0.0);
    # standard MMR, iterating over the top-20 by raw similarity until 10
    # are selected.
    mmr_lambda: float | None = None
    # multi_query: total number of query variants to retrieve with -- the
    # original query.text plus (multi_query - 1) LLM-generated paraphrases
    # (src.category.multiquery.generate_paraphrases), each retrieved
    # separately and fused with reciprocal rank fusion. 0 = off. MEASURED
    # motivation: adding a hand-written product description pushed the
    # correct category OUT of one label's top-3 while pushing another
    # label's correct category INTO rank 1 -- same intervention, opposite
    # effects, i.e. high variance from a single phrasing.
    multi_query: int = 0
    # hybrid: fuse the embedding path's top-10 with the TF-IDF path's
    # top-10 (src.category.tfidf) over the SAME documents, via reciprocal
    # rank fusion. MEASURED motivation: TF-IDF alone scores far below
    # embeddings (0.24 vs 0.59), but include_component_name gave +0.17 on
    # BOTH methods -- literal token overlap carries signal the dense
    # retriever partly misses. Fusing two rankings that fail on different
    # cases often beats either alone.
    hybrid: bool = False


CONFIGS: dict[str, ExperimentConfig] = {
    "baseline": ExperimentConfig(name="baseline"),
    "no-parent-inheritance": ExperimentConfig(name="no-parent-inheritance", parent_inheritance=False),
    "filter-off": ExperimentConfig(name="filter-off", intersection_filter=False),
    "no-descriptor": ExperimentConfig(name="no-descriptor", include_descriptor=False),
    "with-component-name": ExperimentConfig(name="with-component-name", include_component_name=True),
    # Kept as description_scope="both" (not "product") so this config's
    # already-logged experiments.csv rows stay comparable to new runs under
    # the same name -- it is the deliberately-worse "both scopes" variant.
    "with-description": ExperimentConfig(name="with-description", description_scope="both"),
    "description-product-only": ExperimentConfig(name="description-product-only", description_scope="product"),
    "with-description-and-component-name": ExperimentConfig(
        name="with-description-and-component-name",
        description_scope="both",
        include_component_name=True,
    ),
    # The two measured wins combined: product queries get the description,
    # component queries get their own name, neither poisons the other.
    "best": ExperimentConfig(name="best", description_scope="product", include_component_name=True),
    "tfidf": ExperimentConfig(name="tfidf", retrieval_method="tfidf"),
    "tfidf-with-component-name": ExperimentConfig(
        name="tfidf-with-component-name", retrieval_method="tfidf", include_component_name=True
    ),
    "chromadb": ExperimentConfig(name="chromadb", retrieval_method="chromadb"),
    "chromadb-with-component-name": ExperimentConfig(
        name="chromadb-with-component-name", retrieval_method="chromadb", include_component_name=True
    ),
    # Corpus-construction experiments -- all default OFF (config.corpus_mode
    # defaults to "full", so none of these change baseline behaviour).
    "name-only": ExperimentConfig(name="name-only", corpus_mode="name-only"),
    "name-plus-examples": ExperimentConfig(name="name-plus-examples", corpus_mode="name-plus-examples"),
    "strip-framing": ExperimentConfig(name="strip-framing", corpus_mode="strip-framing"),
    "enriched": ExperimentConfig(name="enriched", corpus_mode="enriched"),
    # include_component_name is the best-measured flag so far (+0.17 on both
    # embedding and tfidf retrieval) -- paired here with the two most
    # promising corpus modes, name-only (the null hypothesis) and enriched
    # (uses data the legal text does not carry).
    "name-only-with-component-name": ExperimentConfig(
        name="name-only-with-component-name", corpus_mode="name-only", include_component_name=True
    ),
    "enriched-with-component-name": ExperimentConfig(
        name="enriched-with-component-name", corpus_mode="enriched", include_component_name=True
    ),
    # Retrieval-strategy experiments -- all default OFF (mmr_lambda=None,
    # multi_query=0, hybrid=False), paired with include_component_name
    # since that is the operating config (F-06) -- testing a new strategy
    # against a weaker base would confuse the comparison.
    "mmr-0.7": ExperimentConfig(name="mmr-0.7", mmr_lambda=0.7, include_component_name=True),
    "mmr-0.5": ExperimentConfig(name="mmr-0.5", mmr_lambda=0.5, include_component_name=True),
    "mqr-3": ExperimentConfig(name="mqr-3", multi_query=3, include_component_name=True),
    "mqr-5": ExperimentConfig(name="mqr-5", multi_query=5, include_component_name=True),
    "hybrid": ExperimentConfig(name="hybrid", hybrid=True, include_component_name=True),
}
