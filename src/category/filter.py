# DESIGN RULE: pure set intersection over eu_fip rows passed in as an
# argument -- no file reads, no embeddings, no network.
"""Deterministic intersection filter: which EU food categories permit ALL of
a set of additives together. MEASURED: narrows 118 candidate categories to
15 for Pepsi and 12 for Chips, with no embeddings involved.
"""

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
