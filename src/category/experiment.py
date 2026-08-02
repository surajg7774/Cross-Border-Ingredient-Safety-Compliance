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
}
