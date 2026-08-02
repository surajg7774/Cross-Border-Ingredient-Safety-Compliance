# DESIGN RULE: this stage operates on additive IDENTITY (eu_canonical_id),
# not per-label items -- a horizon signal describes a SUBSTANCE under EFSA
# review, not a specific declaration on one label, so there is no item_id
# here to reference.
"""Pydantic models for the regulatory-horizon lane: advisory-only signals
about additives under active EFSA review. See src/horizon/lane.py's module
docstring for the hard constraint this stage exists under -- it can never
change a compliance verdict."""

from typing import Literal

from pydantic import BaseModel, Field


class HorizonSignal(BaseModel):
    eu_canonical_id: str
    substance_name: str
    stage: Literal["efsa_opinion", "efsa_assessment", "unknown"]
    title: str
    publication_date: str | None
    doi: str | None
    doi_verified: bool  # False until checked by hand against efsa.europa.eu
    source_url: str | None  # DOI resolved to a URL where possible
    years_old: int | None
    severity: Literal["advisory"]  # ALWAYS advisory -- see lane.py
    affects_verdict: bool  # ALWAYS False -- see lane.py
    note: str | None = None  # curator's annotation, from the dataset entry
    # "matched_by_name" when the E-number was absent from the dataset and
    # this signal was found via a (weaker) substance-name match instead --
    # see lane.py's matching logic. "doi_unverified" when doi_verified is
    # False -- an uncitable signal must say so, same discipline as the rule
    # engine's "uncitable_verdict" flag (src/rules/engine.py).
    flags: list[str] = Field(default_factory=list)


class HorizonResult(BaseModel):
    signals: list[HorizonSignal]
    checked_ids: list[str]  # which additives were looked up
    warnings: list[str]
    data_version: str
    data_retrieved: str | None  # so a stale snapshot is visible
