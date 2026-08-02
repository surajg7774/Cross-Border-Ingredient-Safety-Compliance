# DESIGN RULE: items are referenced by item_id, never by copying or
# subclassing ExtractedItem/ResolvedItem. This stage only reads the dict/
# ResolvedItem fields it needs -- it never imports src.schemas or
# src.resolve.resolver.
"""Pydantic models for the category stage: which EU food categories a
label's (or one composite component's) declared composition retrieves."""

from typing import Literal

from pydantic import BaseModel, Field


class CategoryQuery(BaseModel):
    """The text sent to the embedder for one label, or one depth-0 component."""

    # "product": the whole label's own top-level ingredients. "component": a
    # single depth-0 compound's descendants (e.g. Chips' "Seasoning") --
    # lets the eval and the report tell a product-level category verdict
    # from a component-level one instead of treating both as "the label".
    scope: Literal["product", "component"]
    component_label: str | None  # e.g. "Milk Chocolate"; None for a "product" scope query
    text: str
    # Which pieces of the query were actually available -- product_name is
    # null on several labels (the crop shows only the ingredients panel), so
    # the eval needs to know what a given query was actually built from.
    fields_used: list[str]
    # eu_canonical_id values in scope for this component, fed to the
    # intersection filter -- the whole label's additives for a non-composite
    # query, or just this component's for a composite one.
    additive_ids: list[str] = Field(default_factory=list)


class CategoryCandidate(BaseModel):
    code: str
    name: str
    similarity: float
    permitted: bool  # survived the intersection filter


class FilteredCandidate(BaseModel):
    code: str
    name: str
    similarity: float
    reason: str  # "not_permitted" | "ranked_below_top3"


class CategoryResult(BaseModel):
    """The classifier's verdict for one query (one label, or one component)."""

    component_label: str | None
    query: CategoryQuery
    top3: list[CategoryCandidate]
    filtered_out: list[FilteredCandidate]
    # Diagnostics from the intersection filter (src/category/filter.py),
    # surfaced here rather than buried in a nested object -- a report reads
    # them directly off the result it's already iterating.
    empty_intersection: bool
    excluded_additives: list[str]  # additive ids with no permitted rows anywhere
    additive_breadth: dict[str, int]  # additive id -> how many categories permit it alone
