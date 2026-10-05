# DESIGN RULE: The extractor reports OBSERVATIONS ONLY. There is deliberately
# no "is_additive" field and no canonical ID. The model records what is
# PRINTED on the label; a later deterministic resolver decides what it means.
# declared_code must be copied verbatim — the model must NEVER infer or
# invent an E-number for an item that doesn't show one.
"""Pydantic models for extractor output. Observations only — no compliance judgments."""

from typing import Literal

from pydantic import BaseModel, Field


class GateResult(BaseModel):
    """Verdict on whether an image is a food label worth extracting, plus what was seen."""

    is_food_label: bool
    has_ingredients_declaration: bool
    # Derived deterministically by pipeline.py from evidence_found, not asked
    # of the model — self-reported LLM confidence is uncalibrated. Defaults
    # to 0.0 here only because the raw model response has no such field.
    confidence: float = Field(default=0.0, ge=0, le=1)
    evidence_found: list[str]
    reject_reason: str | None
    product_name: str | None
    product_descriptor: str | None
    languages_detected: list[str]
    language_selected: str | None
    ingredients_panel_bbox: list[float] | None = Field(
        default=None, min_length=4, max_length=4
    )
    # Populated by pipeline.py (e.g. "bbox not localised"), not the model —
    # defaults to empty since the raw model response has no such field.
    warnings: list[str] = Field(default_factory=list)


class ExtractedItem(BaseModel):
    """One entry from the ingredients declaration, exactly as printed."""

    item_id: int
    position: int
    nesting_depth: int
    parent_item_id: int | None
    verbatim: str
    name_as_declared: str | None
    declared_role: str | None
    role_source: Literal["explicit_prefix", "group_heading", "none"]
    declared_code: str | None
    # Which numbering system the label printed the code in — an
    # observation, not a judgment. A bare number on an FSSAI label is INS
    # by convention, but deciding that is the resolver's job, not the
    # extractor's.
    code_system: Literal["E", "INS", "bare", "none"]
    percentage: float | None
    emphasis: bool
    # The marker character(s) (e.g. "+", "#", "*") attached to this item in
    # the declaration, if any — this lets a later stage link an item to its
    # footnote; the extractor only records that a marker is present, not
    # what the footnote says. Null when there is no marker.
    footnote_marker: str | None
    confidence: float = Field(ge=0, le=1)


class ExtractionResult(BaseModel):
    """Full parse of one ingredients declaration."""

    declaration_verbatim: str
    language: str
    items: list[ExtractedItem]
    unparsed_fragments: list[str]
    warnings: list[str]
    # Allergen advisory statements ("Contains...", "May contain...") are a
    # separate EU labelling requirement (Reg 1169/2011 Annex II) from
    # additive compliance. Capturing them here costs nothing and keeps them
    # out of unparsed_fragments, which should mean parse FAILURE only.
    allergen_statements: list[str]
    # Marker -> explanation text, linking a footnote_marker on an item to
    # what the footnote actually says. Kept separate from unparsed_fragments,
    # which means parse FAILURE only. A footnote can change how an item is
    # classified — "+Used as natural flavouring agent" indicates a
    # flavouring, regulated separately from additives.
    footnotes: dict[str, str]
    # Regulatory statements printed with the declaration that are neither
    # ingredients nor allergen advisories — e.g. "Contains permitted natural
    # colour(s)", "Polyols may have laxative effect", "Contains added
    # flavour". Kept because an unnamed additive class is itself a
    # compliance signal.
    declaration_statements: list[str]
