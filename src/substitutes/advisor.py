# DESIGN RULE: pure decision logic -- data in, data out. No file reads, no
# printing, no settings access. Reference data (eu_fip, codex_ins) is passed
# in as arguments. Imports only src.substitutes.schemas, src.rules.schemas,
# and src.rules.row_selection -- never src.extract/, src.category/,
# src.resolve.resolver, or src.rules.engine. Because src.rules.engine is off
# limits, a handful of its small pure helpers (parent-code stripping,
# category-code extraction, the row-to-verdict mapping) are duplicated below
# rather than imported -- same logic, independent module. Row SELECTION
# (what happens when several eu_fip rows apply to the same additive+category)
# is NOT duplicated, after the duplicate copy here fell out of sync with
# engine.py's: engine.py was fixed to merge co-applicable rows instead of
# silently discarding all but one, and this module's independent copy was
# never updated to match. It is imported from src.rules.row_selection
# instead -- a third module, alongside src.rules.schemas, that belongs to no
# pipeline stage (see that module's own DESIGN RULE comment); importing it
# is not the same as importing src.rules.engine.
"""Substitute-additive advisor: for every item that BLOCKS export, propose
replacement candidates that would clear. Deterministic -- a filtered query
over eu_fip and codex_ins, no LLM, no retrieval, no network.

THE HONEST CAVEAT: technological function is NOT interchangeability. Sharing
a Codex functional class (e.g. "Colour") means a candidate does the same JOB
as the additive it replaces, not that it behaves the same way in the same
formulation. E171 (titanium dioxide)'s opacity is famously hard to replace --
which is why the industry struggled to reformulate after the 2022 EU ban --
despite several other whiteners sharing its "Colour" class. Every candidate
returned here is a CANDIDATE requiring real formulation validation, never a
recommendation.
"""

import re

from src.rules.row_selection import select_row as _select_row
from src.rules.schemas import ItemVerdict, ProductVerdict
from src.substitutes.schemas import SubstituteCandidate, SubstituteResult, SubstituteSuggestion

# The six azo colours Annex V requires the "may have an adverse effect on
# activity and attention in children" warning for. A candidate that carries
# this obligation when the blocked additive did not is flagged, not excluded
# -- it still clears the food category, it just adds a labelling cost the
# blocked additive didn't have.
_AZO_COLOURS_REQUIRING_WARNING = {"102", "104", "110", "122", "124", "129"}
_LABELLING_FLAG = "adds_labelling_obligation"
_SUBTYPE_FLAG = "classes_from_subtypes"
# src/horizon/lane.py's negative filter: a candidate itself under active
# EFSA review is not excluded -- an EFSA opinion is not law, so it may still
# be the correct answer today -- but it is flagged and ranked last, since
# recommending a replacement that is itself under review would be actively
# harmful advice. This is the only place src/horizon/ output influences this
# module; it never touches the eu_fip/codex_ins filtering itself.
_HORIZON_FLAG = "under_efsa_review"
_MAX_CANDIDATES = 5

_PAREN_SUFFIX_RE = re.compile(r"\([^)]*\)$")
_LETTER_SUFFIX_RE = re.compile(r"[a-z]$", re.IGNORECASE)


def _parent_codes(code: str) -> list[str]:
    """Successive parent widenings of an INS/EU code, nearest first --
    identical in behaviour to src/rules/engine.py's _parent_codes
    ("160b(i)" -> ["160b", "160"]), duplicated here because that module
    cannot be imported (see the DESIGN RULE comment above)."""
    parents = []
    current = code
    paren_match = _PAREN_SUFFIX_RE.search(current)
    if paren_match:
        current = current[: paren_match.start()].strip()
        parents.append(current)
    letter_match = _LETTER_SUFFIX_RE.search(current)
    if letter_match:
        current = current[: letter_match.start()]
        parents.append(current)
    return parents


def _category_code(food_category_raw: str | None) -> str:
    """"12.2.2 Seasonings and condiments" -> "12.2.2"."""
    if not food_category_raw:
        return ""
    return food_category_raw.split(" ", 1)[0].rstrip(".")


def _verdict_from_row(row: dict) -> str:
    """Same mapping as engine.py's _verdict_from_row. Never returns
    "not_authorised_eu" here: candidates with a prohibited row anywhere are
    excluded before this is called (rule 5)."""
    if row.get("conditions"):
        return "permitted_with_conditions"
    if row.get("max_level_mg_kg") is not None:
        return "permitted_with_limit"
    return "permitted_qs"


def _rows_by_id(eu_fip: list[dict]) -> dict[str, list[dict]]:
    by_id: dict[str, list[dict]] = {}
    for row in eu_fip:
        by_id.setdefault(row["canonical_id"], []).append(row)
    return by_id


def _rows_by_category(eu_fip: list[dict]) -> dict[str, dict[str, list[dict]]]:
    """fcs_code -> {canonical_id: [rows]}. Prohibitions and blanket
    "permitted anywhere" rows carry food_category_raw=None and are never
    indexed here -- a jurisdiction-wide ban is detected separately, via
    _is_prohibited_anywhere against `by_id`."""
    grouped: dict[str, dict[str, list[dict]]] = {}
    for row in eu_fip:
        code = _category_code(row.get("food_category_raw"))
        if not code:
            continue
        grouped.setdefault(code, {}).setdefault(row["canonical_id"], []).append(row)
    return grouped


def _is_prohibited_anywhere(canonical_id: str, by_id: dict[str, list[dict]]) -> bool:
    for candidate_id in (canonical_id, *_parent_codes(canonical_id)):
        if any(row["status"] == "prohibited" for row in by_id.get(candidate_id, [])):
            return True
    return False


def _codex_children_by_parent(codex_ins: list[dict]) -> dict[str, list[dict]]:
    """parent_ins -> its sub-type records -- e.g. "170" -> [170(i), 170(ii)].
    Built once per find_substitutes() call, used by _codex_functional_classes
    to fill in a parent row's missing functional_classes."""
    children: dict[str, list[dict]] = {}
    for row in codex_ins:
        parent = row.get("parent_ins")
        if parent:
            children.setdefault(parent, []).append(row)
    return children


def _codex_functional_classes(
    canonical_id: str, codex_by_ins: dict[str, dict], codex_children_by_parent: dict[str, list[dict]]
) -> tuple[list[str], bool] | None:
    """(functional_classes, inherited_from_subtypes) for canonical_id,
    matched on the INS number -- exact, then parent-widened the same way
    engine.py widens eu_fip lookups ("160b(i)" -> try "160b", then "160").
    Returns None when the id is not in codex_ins AT ALL.

    An EMPTY functional_classes list on a record that EXISTS is different
    from a record that is MISSING -- conflating them lost this project's own
    worked example: CXG 36 records functional classes on the SUB-TYPES, not
    the parent row, so 170 Calcium carbonates (the actual industry
    replacement for titanium dioxide after the EU's 2022 ban) has
    functional_classes=[] while its sub-types 170(i)/170(ii) carry
    ["Colour"]. 35 of 668 codex_ins records are such empty parent rows
    (verified: is_parent_row is true for every one). When a found record's
    own list is empty, the union of its sub-types' classes is returned
    instead, with inherited_from_subtypes=True so a candidate resolved this
    way can be flagged classes_from_subtypes -- the classes were inherited,
    not stated directly on this record.

    Used for BOTH sides of a suggestion: candidate additives come from
    eu_fip and are keyed by EU id, not Codex INS number, but the two use the
    identical notation (measured against real data: eu_fip's "160b(i)" is
    codex_ins's "160b(i)", not a different scheme) -- so a candidate's EU id
    is looked up here directly, with no separate EU-id-to-INS crosswalk
    table needed. See _blocked_functional_classes for why the BLOCKED
    additive's lookup is not this simple.
    """
    for candidate_id in (canonical_id, *_parent_codes(canonical_id)):
        row = codex_by_ins.get(candidate_id)
        if row is None:
            continue
        classes = row.get("functional_classes") or []
        if classes:
            return classes, False
        inherited = sorted(
            {
                cls
                for child in codex_children_by_parent.get(candidate_id, [])
                for cls in child.get("functional_classes") or []
            }
        )
        return inherited, True
    return None


def _blocked_functional_classes(
    canonical_ins: str | None,
    eu_canonical_id: str | None,
    codex_by_ins: dict[str, dict],
    codex_children_by_parent: dict[str, list[dict]],
) -> list[str] | None:
    """Functional classes for the BLOCKED additive -- tried via canonical_ins
    (the Codex INS number the resolver identified) FIRST, falling back to
    eu_canonical_id only if that fails.

    MEASURED BUG this fixes: eu_canonical_id is null for exactly the
    additives that block VIA ABSENCE from eu_fip (e.g. INS 143 Fast Green
    FCF) -- that null is WHY they blocked, so looking up functional classes
    by eu_canonical_id alone can never find them, even though codex_ins
    has the entry (the resolver matched it exactly). ItemVerdict does not
    carry canonical_ins (see src/rules/schemas.py -- it stays decoupled from
    resolver detail it does not need), so the caller threads it in
    separately, read from the resolution file. Both ids go through the same
    sub-notation parent fallback as _codex_functional_classes.

    The blocked side never needs the classes_from_subtypes distinction --
    SubstituteSuggestion carries no flags field for it -- so the tuple's
    second element is simply discarded here.
    """
    for candidate_id in (canonical_ins, eu_canonical_id):
        if candidate_id is None:
            continue
        result = _codex_functional_classes(candidate_id, codex_by_ins, codex_children_by_parent)
        if result is not None:
            classes, _inherited = result
            return classes
    return None


def _rank_key(candidate: SubstituteCandidate) -> tuple:
    """No added labelling obligation first, then not under active EFSA
    review, then more shared functional classes first, then a higher max
    level first (quantum satis -- a None level -- ranks above every numeric
    level), then canonical id for a stable tie-break."""
    has_obligation = _LABELLING_FLAG in candidate.flags
    under_review = _HORIZON_FLAG in candidate.flags
    level = candidate.max_level_mg_kg
    level_key = -(float("inf") if level is None else level)
    return (
        has_obligation,
        under_review,
        -len(candidate.shared_functional_classes),
        level_key,
        candidate.eu_canonical_id,
    )


def _resolve_fcs_code(item: ItemVerdict, verdict: ProductVerdict) -> str | None:
    """The category the blocked verdict was judged under. Absence and
    prohibition are category-independent (by_category is empty for both),
    so fall back to the category retrieval already used for this item's
    component/product query."""
    if item.by_category:
        return item.by_category[0].fcs_code
    return verdict.category_used.get(item.component_label or "(product)")


def _find_candidates(
    blocked_id: str | None,
    blocked_classes: list[str],
    blocked_max_level: float | None,
    fcs_code: str,
    by_id: dict[str, list[dict]],
    by_category: dict[str, dict[str, list[dict]]],
    codex_by_ins: dict[str, dict],
    codex_children_by_parent: dict[str, list[dict]],
    horizon_flagged: frozenset[str],
    warnings: list[str],
) -> list[SubstituteCandidate]:
    candidates = []
    for candidate_id, rows in by_category.get(fcs_code, {}).items():
        if candidate_id == blocked_id:  # rule 5: never suggest the blocked additive itself
            continue
        if _is_prohibited_anywhere(candidate_id, by_id):  # rule 5: exclude anything prohibited
            continue

        result = _codex_functional_classes(candidate_id, codex_by_ins, codex_children_by_parent)
        if result is None:
            # Cannot determine whether this additive shares a functional
            # class at all -- excluding it is correct (rule 1 requires a
            # confirmed match), but doing so SILENTLY would hide it from a
            # reader who might otherwise widen codex_ins themselves. Flagged
            # in `warnings` instead of a bare `continue`.
            name = rows[0].get("additive_name") or candidate_id
            warnings.append(
                f"{name} ({candidate_id}) permitted in {fcs_code} but excluded from substitute "
                "consideration -- functional class unknown (not in codex_ins)"
            )
            continue
        candidate_classes, inherited = result
        shared = sorted(set(blocked_classes) & set(candidate_classes))
        if not shared:  # rule 1: must share at least one functional class
            continue

        # Same algorithm engine.py uses for a category verdict: when 2+
        # eu_fip rows apply to this (candidate, category), MERGE their
        # distinct conditions texts (numbered) and take the lowest level,
        # rather than silently picking one and discarding the rest -- see
        # src.rules.row_selection.select_row. The merged row carries no
        # "additive_name" key (not needed there; every input row already
        # shares one), so that field is read from `rows`, not `chosen_row`.
        chosen_row, select_flags = _select_row(rows)
        candidate_level = chosen_row.get("max_level_mg_kg")
        # rule 3: candidate level must be >= the blocked additive's, where
        # both are known. A None candidate level is quantum satis --
        # unbounded, always passes. A None blocked level means the blocked
        # additive's cap is unknown, so no level comparison is possible.
        if blocked_max_level is not None and candidate_level is not None and candidate_level < blocked_max_level:
            continue

        flags = list(select_flags)
        if candidate_id in _AZO_COLOURS_REQUIRING_WARNING and blocked_id not in _AZO_COLOURS_REQUIRING_WARNING:
            flags.append(_LABELLING_FLAG)  # rule 4: flagged and ranked last, never excluded
        if inherited:
            flags.append(_SUBTYPE_FLAG)  # this record's own classes were empty; inherited from sub-types
        if candidate_id in horizon_flagged:
            flags.append(_HORIZON_FLAG)  # flagged and ranked last, never excluded -- see the module comment

        candidates.append(
            SubstituteCandidate(
                eu_canonical_id=candidate_id,
                additive_name=rows[0].get("additive_name"),
                shared_functional_classes=shared,
                fcs_code=fcs_code,
                verdict=_verdict_from_row(chosen_row),
                max_level_mg_kg=candidate_level,
                conditions=chosen_row.get("conditions"),
                source_url=chosen_row.get("source_url"),
                flags=flags,
            )
        )

    candidates.sort(key=_rank_key)
    return candidates[:_MAX_CANDIDATES]


def _suggest_for_item(
    item: ItemVerdict,
    verdict: ProductVerdict,
    canonical_ins: str | None,
    by_id: dict[str, list[dict]],
    by_category: dict[str, dict[str, list[dict]]],
    codex_by_ins: dict[str, dict],
    codex_children_by_parent: dict[str, list[dict]],
    horizon_flagged: frozenset[str],
    warnings: list[str],
) -> SubstituteSuggestion:
    base = {
        "item_id": item.item_id,
        "blocked_eu_canonical_id": item.eu_canonical_id,
        "blocked_name": item.additive_name,
        "blocked_reason": item.headline,
    }

    fcs_code = _resolve_fcs_code(item, verdict)
    if fcs_code is None:
        return SubstituteSuggestion(
            **base, fcs_code=None, candidates=[], no_candidates_reason="no food category available"
        )

    blocked_classes = _blocked_functional_classes(
        canonical_ins, item.eu_canonical_id, codex_by_ins, codex_children_by_parent
    )
    if not blocked_classes:
        return SubstituteSuggestion(
            **base,
            fcs_code=fcs_code,
            candidates=[],
            no_candidates_reason="blocked additive's functional class is unknown (not in codex_ins)",
        )

    blocked_max_level = item.by_category[0].max_level_mg_kg if item.by_category else None
    candidates = _find_candidates(
        item.eu_canonical_id,
        blocked_classes,
        blocked_max_level,
        fcs_code,
        by_id,
        by_category,
        codex_by_ins,
        codex_children_by_parent,
        horizon_flagged,
        warnings,
    )
    reason = None if candidates else "no permitted additive in this category shares a functional class"
    return SubstituteSuggestion(**base, fcs_code=fcs_code, candidates=candidates, no_candidates_reason=reason)


def find_substitutes(
    verdict: ProductVerdict,
    eu_fip: list[dict],
    codex_ins: list[dict],
    canonical_ins_by_item: dict[int, str] | None = None,
    horizon_flagged: frozenset[str] = frozenset(),
) -> SubstituteResult:
    """Substitute candidates for every item in verdict.blocking.

    Runs ONLY on `blocking`, never on `category_conflict`: a category-conflict
    item is not actually blocked -- it depends on which retrieved candidate
    category is correct, and the fix is to confirm the category (--category
    on scripts/verdict.py), not to propose a replacement additive.

    `canonical_ins_by_item` is item_id -> the Codex INS number the resolver
    identified, read from the resolution file by the caller (scripts/
    substitutes.py) -- the same "join one extra file in at the boundary"
    pattern scripts/verdict.py uses for display names. ItemVerdict itself
    does not carry canonical_ins (see src/rules/schemas.py); passing it here
    keeps that decision in src/rules/ without coupling this stage to it.

    `horizon_flagged` is the set of eu_canonical_ids src/horizon/lane.py
    found under active EFSA review (scripts/substitutes.py reads
    data/outputs/horizon/<name>.json, when present, to build it). A
    candidate in this set is NOT excluded -- an EFSA opinion is not law --
    but is flagged under_efsa_review and ranked last (see _rank_key), since
    recommending a replacement itself under active review would be actively
    harmful advice. An empty set (the default, and what a missing horizon
    file means) leaves ranking exactly as before this parameter existed.
    """
    canonical_ins_by_item = canonical_ins_by_item or {}
    codex_by_ins = {row["ins"]: row for row in codex_ins}
    codex_children_by_parent = _codex_children_by_parent(codex_ins)
    by_id = _rows_by_id(eu_fip)
    by_category = _rows_by_category(eu_fip)
    items_by_id = {item.item_id: item for item in verdict.items}

    suggestions = []
    warnings = []
    for item_id in verdict.blocking:
        item = items_by_id.get(item_id)
        if item is None:
            warnings.append(f"blocking item_id {item_id} not found in verdict.items")
            continue
        canonical_ins = canonical_ins_by_item.get(item_id)
        suggestions.append(
            _suggest_for_item(
                item,
                verdict,
                canonical_ins,
                by_id,
                by_category,
                codex_by_ins,
                codex_children_by_parent,
                horizon_flagged,
                warnings,
            )
        )

    return SubstituteResult(suggestions=suggestions, warnings=warnings)
