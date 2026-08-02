# DESIGN RULE: items are referenced by item_id, never by copying or
# subclassing ItemVerdict/ProductVerdict from the rules stage. This stage
# takes plain data in and returns plain data out -- see advisor.py's
# find_substitutes().
"""Pydantic models for the substitute-advisor stage: replacement candidates
for additives that BLOCK export, given the compliance verdict already
computed by src/rules/engine.py."""

from pydantic import BaseModel


class SubstituteCandidate(BaseModel):
    """One additive that could replace a blocked one -- a CANDIDATE, not a
    recommendation. See advisor.py's module docstring for why."""

    eu_canonical_id: str
    additive_name: str | None
    shared_functional_classes: list[str]
    fcs_code: str  # the category it was checked in
    verdict: str  # permitted_qs / permitted_with_limit / permitted_with_conditions
    max_level_mg_kg: float | None
    conditions: str | None
    source_url: str | None
    flags: list[str]  # e.g. "adds_labelling_obligation"


class SubstituteSuggestion(BaseModel):
    """Candidates for ONE blocked item."""

    item_id: int  # the BLOCKED item this replaces
    blocked_eu_canonical_id: str | None
    blocked_name: str | None
    blocked_reason: str  # the headline verdict that blocked it
    fcs_code: str | None
    candidates: list[SubstituteCandidate]
    no_candidates_reason: str | None  # why the list is empty, if it is


class SubstituteResult(BaseModel):
    suggestions: list[SubstituteSuggestion]
    warnings: list[str]
