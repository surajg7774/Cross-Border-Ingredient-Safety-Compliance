# DESIGN RULE: pure set intersection over eu_fip rows passed in as an
# argument -- no file reads, no embeddings, no network.
"""Two SEPARATE deterministic filters over eu_fip -- do not let them
double-filter silently, see docs/findings.md's functional-class-filter
entry:

    permitted_in()                -- EXACT additive-id intersection: which
                                      categories permit ALL of a resolved
                                      set of additive ids TOGETHER. Applied
                                      as a POST-retrieval re-rank inside
                                      classify() (config.intersection_filter)
                                      -- it never removes a retrieved
                                      candidate, only promotes permitted
                                      ones ahead of not-permitted ones
                                      within the already-retrieved top-K.
                                      MEASURED: narrows 118 candidate
                                      categories to 15 for Pepsi and 12 for
                                      Chips, with no embeddings involved.

    functional_class_candidates() -- COARSER, functional-CLASS-level
                                      intersection (e.g. "some Preservative
                                      is permitted here"), usable as a
                                      candidate-narrowing PRE-filter BEFORE
                                      similarity search runs at all (see
                                      scripts/classify_category.py's
                                      functional_class_filter ablation) --
                                      it can remove a candidate from
                                      consideration entirely, something
                                      permitted_in() never does on its own.
"""

import re
from dataclasses import dataclass


def _category_code(food_category_raw: str) -> str:
    """"14.1.1 Water, including..." -> "14.1.1". A few rows have a stray
    trailing "." on the code ("20.")."""
    return food_category_raw.split(" ", 1)[0].rstrip(".")


def _permitted_categories(additive_id: str, eu_fip: list[dict]) -> set[str]:
    """Category codes where this one additive has a status == "permitted" row."""
    return {
        _category_code(row["food_category_raw"])
        for row in eu_fip
        if row["canonical_id"] == additive_id
        and row["status"] == "permitted"
        and row["food_category_raw"]
    }


@dataclass
class FilterResult:
    categories: set[str]  # the intersection -- possibly empty, see empty_intersection
    excluded_additives: list[str]  # additive ids with zero permitted rows anywhere
    breadth: dict[str, int]  # additive id -> how many categories permit it alone
    empty_intersection: bool


def permitted_in(additive_ids: list[str], eu_fip: list[dict]) -> FilterResult:
    """Food categories where ALL of additive_ids are permitted together.

    Three cases handled explicitly, matching the measured data:
      - an additive with no permitted rows anywhere is EXCLUDED from the
        intersection (recorded in excluded_additives) rather than collapsing
        the whole result to empty -- this is what happened to Chocolate's
        additives before the cause (an unresolved parent code) was found.
      - an empty intersection across the remaining additives is a real
        result -- no EU category permits this exact combination -- returned
        with empty_intersection=True, not raised as an error.
      - breadth records, per additive, how many categories it is permitted
        in on its own -- E330 alone is permitted in 92 of 118, so it barely
        narrows anything; a report can say "category-independent for N of M".

    With no additive_ids at all (nothing to check, e.g. a pure-oil component
    with no resolved additives), categories stays empty and every retrieved
    candidate is later ranked by similarity alone -- not a signal of trouble,
    just nothing for this filter to narrow.
    """
    if not additive_ids:
        return FilterResult(categories=set(), excluded_additives=[], breadth={}, empty_intersection=True)

    per_additive = {aid: _permitted_categories(aid, eu_fip) for aid in additive_ids}
    breadth = {aid: len(cats) for aid, cats in per_additive.items()}
    excluded = [aid for aid, cats in per_additive.items() if not cats]
    considered = [cats for aid, cats in per_additive.items() if aid not in excluded]

    if not considered:
        return FilterResult(categories=set(), excluded_additives=excluded, breadth=breadth, empty_intersection=True)

    categories = set.intersection(*considered)
    return FilterResult(
        categories=categories,
        excluded_additives=excluded,
        breadth=breadth,
        empty_intersection=not categories,
    )


# --------------------------------------------------------------------------- #
# functional-class pre-filter -- a SEPARATE mechanism from permitted_in()
# above, see this module's docstring.
# --------------------------------------------------------------------------- #
_PAREN_SUFFIX_RE = re.compile(r"\([^)]*\)$")
_LETTER_SUFFIX_RE = re.compile(r"[a-z]$", re.IGNORECASE)


def _parent_codes(code: str) -> list[str]:
    """Successive parent widenings of an INS/EU code, nearest first --
    identical in behaviour to src/rules/engine.py's and
    src/substitutes/advisor.py's own _parent_codes ("160b(i)" -> ["160b",
    "160"]), duplicated here rather than imported for the same reason those
    two modules' own DESIGN RULE comments already give: this module stays a
    pure, independent function of its arguments, not coupled to another
    stage's private helpers."""
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


def _codex_children_by_parent(codex_ins: list[dict]) -> dict[str, list[dict]]:
    children: dict[str, list[dict]] = {}
    for row in codex_ins:
        parent = row.get("parent_ins")
        if parent:
            children.setdefault(parent, []).append(row)
    return children


def _codex_functional_classes(
    additive_id: str, codex_by_ins: dict[str, dict], codex_children_by_parent: dict[str, list[dict]]
) -> list[str]:
    """Functional classes for one additive id, exact then parent-widened --
    same lookup src/substitutes/advisor.py's _codex_functional_classes
    already does (ported, not imported, same reasoning as _parent_codes
    above), INCLUDING the sub-type-inheritance case: 35 of 668 codex_ins
    records carry their functional classes on SUB-TYPES, not the parent row
    (e.g. 170 Calcium carbonates has functional_classes=[] while 170(i)/
    170(ii) carry ["Colour"]) -- a naive direct lookup would undercount
    exactly those additives, which matters here since undercounting shrinks
    what this filter treats as "related", making it MORE aggressive, not
    less, for precisely the additives already measured to need the
    sub-type fallback."""
    for candidate_id in (additive_id, *_parent_codes(additive_id)):
        row = codex_by_ins.get(candidate_id)
        if row is None:
            continue
        classes = row.get("functional_classes") or []
        if classes:
            return classes
        return sorted(
            {
                cls
                for child in codex_children_by_parent.get(candidate_id, [])
                for cls in child.get("functional_classes") or []
            }
        )
    return []


def functional_classes_by_category(eu_fip: list[dict], codex_ins: list[dict]) -> dict[str, set[str]]:
    """fcs_code -> the union of functional classes of every additive with
    at least one PERMITTED row in that category. Corpus-level: computed
    ONCE per (eu_fip, codex_ins) pair, independent of which additives any
    particular query declares -- see query_functional_classes for the
    query side of the same filter."""
    codex_by_ins = {row["ins"]: row for row in codex_ins}
    codex_children_by_parent = _codex_children_by_parent(codex_ins)
    result: dict[str, set[str]] = {}
    for row in eu_fip:
        if row["status"] != "permitted" or not row.get("food_category_raw"):
            continue
        code = _category_code(row["food_category_raw"])
        classes = _codex_functional_classes(row["canonical_id"], codex_by_ins, codex_children_by_parent)
        if classes:
            result.setdefault(code, set()).update(classes)
    return result


def query_functional_classes(additive_ids: list[str], codex_ins: list[dict]) -> set[str]:
    """The union of functional classes for one query's OWN additive_ids --
    small (a handful of ids per query), so computed fresh per call rather
    than precomputed like functional_classes_by_category above."""
    codex_by_ins = {row["ins"]: row for row in codex_ins}
    codex_children_by_parent = _codex_children_by_parent(codex_ins)
    classes: set[str] = set()
    for additive_id in additive_ids:
        classes.update(_codex_functional_classes(additive_id, codex_by_ins, codex_children_by_parent))
    return classes


def functional_class_candidates(query_classes: set[str], category_classes: dict[str, set[str]]) -> set[str] | None:
    """Category codes sharing at least one functional class with
    query_classes -- the actual PRE-filter restriction (the caller drops
    every OTHER code from its scores dict before ranking, see
    scripts/classify_category.py's functional_class_filter ablation).

    Returns None (no restriction) when query_classes is empty -- matching
    permitted_in()'s own precedent for "nothing to check, e.g. a pure-oil
    component with no resolved additives": not a signal of trouble, just
    nothing for this filter to narrow, so every retrieved candidate is
    later ranked by similarity alone.
    """
    if not query_classes:
        return None
    return {code for code, classes in category_classes.items() if classes & query_classes}
