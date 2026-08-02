# DESIGN RULE: items are referenced by item_id, never by copying or
# subclassing ResolvedItem/CategoryCandidate. This stage takes plain data in
# and returns plain data out -- see engine.py's evaluate().
"""Pydantic models for the rules stage: the EU Annex II compliance verdict
for a resolved additive, judged against its candidate food categories."""

from typing import Literal

from pydantic import BaseModel


class CategoryVerdict(BaseModel):
    """The verdict under ONE candidate category."""

    fcs_code: str
    category_name: str | None
    rank: int
    verdict: Literal[
        "permitted_qs",
        "permitted_with_limit",
        "permitted_with_conditions",
        "not_permitted_in_category",
        "not_authorised_eu",
    ]
    max_level_mg_kg: float | None
    max_level_basis: str | None
    conditions: str | None
    note_codes: list[str]
    source_url: str | None
    retrieved_date: str | None


class ItemVerdict(BaseModel):
    item_id: int
    eu_canonical_id: str | None
    additive_name: str | None
    component_label: str | None
    by_category: list[CategoryVerdict]  # empty when out of scope/unresolved/no category
    headline: Literal[
        "permitted_qs",
        "permitted_with_limit",
        "permitted_with_conditions",
        "not_permitted_in_category",
        "not_authorised_eu",
        "out_of_scope",
        "unresolved",
        "category_unknown",
    ]
    category_sensitive: bool  # candidates disagree on verdict
    # "certain" when every candidate category agrees, or the verdict is
    # category-independent (not_authorised_eu via absence/prohibition,
    # out_of_scope, unresolved) -- "category_dependent" exactly when
    # category_sensitive is True. A category_dependent BLOCKING verdict
    # must never be reported as a compliance failure on its own; see
    # ProductVerdict.category_conflict.
    verdict_certainty: Literal["certain", "category_dependent"]
    flags: list[str]


class ProductVerdict(BaseModel):
    items: list[ItemVerdict]
    blocking: list[int]
    # Rank-1 verdict blocks, but some other candidate category would
    # permit it -- a retrieval miss away from a false compliance failure.
    # Deliberately excluded from `blocking`: category recall@1 is
    # measured at ~0.46, so treating these as failures is itself the bug.
    category_conflict: list[int]
    review_required: list[int]
    category_sensitive_items: list[int]
    summary: str  # deterministic f-string, NOT LLM-written
    category_used: dict[str, str]  # component_label (or "(product)") -> fcs_code used
    # component_label (or "(product)") -> "user" | "retrieved" -- lets a
    # reader tell which verdicts rest on a --category confirmation versus
    # an unconfirmed automatic retrieval (measured recall@1 ~0.46).
    category_source: dict[str, str]
    warnings: list[str]
    data_version: str  # hash of eu_fip
