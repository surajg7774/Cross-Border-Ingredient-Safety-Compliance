# DESIGN RULE: items are referenced by item_id, never by copying or
# subclassing ExtractedItem from the extraction stage. This stage only reads
# the dict fields it needs (declared_code, name_as_declared, verbatim,
# declared_role, item_id) -- it never imports src.schemas.
"""Pydantic models for resolver output: canonical identities for extraction items."""

from typing import Literal

from pydantic import BaseModel, Field


class ResolvedItem(BaseModel):
    """The canonical identity (or lack of one) found for one extraction item."""

    item_id: int
    canonical_ins: str | None
    eu_canonical_id: str | None
    classification: Literal[
        "additive", "food_ingredient", "flavouring", "enzyme", "compound", "unknown", "ambiguous"
    ]
    normalised_role: str | None
    codex_functional_classes: list[str]
    resolution_method: str
    resolution_confidence: float
    flags: list[str]
    # Candidate INS numbers for an "ambiguous" item -- kept separate from
    # flags, which is for real signals (eu_widened_to_parent, family_guard,
    # crosswalk_by_name), not a 17-entry candidate list to filter around.
    candidates: list[str] = Field(default_factory=list)
    matched_on: str | None


class ResolutionResult(BaseModel):
    """Full resolution of one extraction file's items."""

    items: list[ResolvedItem]
    warnings: list[str]
    index_version: str
