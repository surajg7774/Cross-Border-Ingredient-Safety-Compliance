# DESIGN RULE: pure decision logic -- data in, data out. No file reads, no
# printing, no settings access. Reference data (eu_fip) and the category
# results are passed in as arguments, never loaded here. Imports only
# src.resolve.schemas and src.category.schemas from other stages -- never
# src.extract/, never src.resolve.resolver or src.category.classifier/filter.
"""The compliance verdict: given a resolved additive and a food category,
what does EU law say? Deterministic -- every decision below is a table
lookup or a hard-coded branch, never an LLM call or a heuristic guess.
"""

import hashlib
import json
import re

from src.category.schemas import CategoryResult
from src.resolve.schemas import ResolvedItem
from src.rules.row_selection import normalize_row as _normalize_row
from src.rules.row_selection import select_row as _select_row
from src.rules.schemas import CategoryVerdict, ItemVerdict, ProductVerdict

# Classifications that are structurally not Annex II additives at all --
# regulated (if at all) under a different regulation, so no eu_fip lookup
# is meaningful for them.
_OUT_OF_SCOPE_REGULATION = {
    "flavouring": "Reg 1334/2008",
    "enzyme": "Reg 1332/2008",
    "food_ingredient": "not an additive",
    "compound": "not an additive",
}
_UNRESOLVED_CLASSIFICATIONS = {"unknown", "ambiguous"}
_BLOCKING_VERDICTS = {"not_authorised_eu", "not_permitted_in_category"}
_PERMITTING_VERDICTS = {"permitted_qs", "permitted_with_limit", "permitted_with_conditions"}
# Measured: category retrieval recall@1 is ~0.46. Used in the summary's
# retrieval caveat whenever any item is verdict_certainty="category_dependent".
_MEASURED_CATEGORY_RECALL_AT_1 = 0.46

_PAREN_SUFFIX_RE = re.compile(r"\([^)]*\)$")
_LETTER_SUFFIX_RE = re.compile(r"[a-z]$", re.IGNORECASE)

# eu_fip source rows sometimes carry a literal HTML "&nbsp;" entity (or a
# real non-breaking-space character) as a scraped placeholder for an empty
# conditions cell -- truthy as a Python string, but MEASURED to render as
# an empty markdown ordered-list item ("2)" with nothing visible after it)
# once a row like this is merged and numbered alongside a real clause (see
# eu_fip.json's ('150c', '14.2.1') pair: "1) &nbsp;\n\n2) only ..."). Every
# row is normalized once here, at ingestion, so every later truthiness
# check on row["conditions"] (src.rules.row_selection's `_merge_conditions`,
# `_restrictiveness_key`, and this module's own `_verdict_from_row`) is
# correct without special-casing. `_normalize_row`/`_select_row` themselves
# live in src.rules.row_selection now (imported above) -- shared with
# src/substitutes/advisor.py's independent row selection, since a fix to
# this algorithm previously had to be applied twice by hand and drifted
# (see that module's DESIGN RULE comment).


def _category_code(food_category_raw: str | None) -> str:
    """"12.2.2 Seasonings and condiments" -> "12.2.2". A few rows have a
    stray trailing "." on the code ("20.")."""
    if not food_category_raw:
        return ""
    return food_category_raw.split(" ", 1)[0].rstrip(".")


def _parent_codes(code: str) -> list[str]:
    """Successive parent widenings of an INS/EU code, nearest first:
    "160b(i)" -> ["160b", "160"]; "500(ii)" -> ["500"]; "472e" -> ["472"].

    The EU records permissions at the FAMILY level, not the sub-type level
    -- "500" (Sodium carbonates) carries 104 rows including 7.2, while
    "500(ii)" (Sodium hydrogen carbonate) carries 2 rows, none in 7.2. This
    never fires in the resolver stage because 500(ii) DOES exist in
    eu_fip; it just carries almost no permission rows of its own.
    """
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


def _build_indices(eu_fip: list[dict]) -> tuple[dict[tuple[str, str], list[dict]], dict[str, list[dict]]]:
    """(canonical_id, category_code) -> matching rows, and canonical_id ->
    every row for that additive (any category, any status) -- the second
    index is what "appears elsewhere in eu_fip" (rule 4) and "appears
    anywhere" (parent fallback) are checked against.
    """
    by_id_category: dict[tuple[str, str], list[dict]] = {}
    by_id: dict[str, list[dict]] = {}
    for row in eu_fip:
        row = _normalize_row(row)
        canonical_id = row["canonical_id"]
        by_id.setdefault(canonical_id, []).append(row)
        code = _category_code(row.get("food_category_raw"))
        if code:
            by_id_category.setdefault((canonical_id, code), []).append(row)
    return by_id_category, by_id


def _verdict_from_row(row: dict) -> str:
    """MEASURED: 18,284 of 18,987 eu_fip rows (96%) carry conditions text,
    so permitted_with_conditions dominates. The conditions prose itself
    ("only tuna", "Note 18: E 300, E 301 and E 302 are authorised
    individually or in combination") is NOT parsed -- surfaced verbatim on
    the CategoryVerdict for a human to read. Parsing it is out of scope.
    """
    if row["status"] == "prohibited":
        return "not_authorised_eu"
    if row.get("conditions"):
        return "permitted_with_conditions"
    if row.get("max_level_mg_kg") is not None:
        return "permitted_with_limit"
    return "permitted_qs"


def _appears_anywhere(canonical_id: str, by_id: dict[str, list[dict]]) -> bool:
    if by_id.get(canonical_id):
        return True
    return any(by_id.get(parent) for parent in _parent_codes(canonical_id))


def _prohibited_id(canonical_id: str, by_id: dict[str, list[dict]]) -> str | None:
    """The canonical_id (exact code, or the nearest parent) that carries a
    jurisdiction-wide prohibition for this additive, or None if neither the
    exact code nor any parent is prohibited anywhere in eu_fip.

    eu_fip records a prohibition as a row with food_category_raw = None --
    "171 | None | prohibited" for titanium dioxide (delisted EU-wide by
    Commission Regulation 2022/63) -- because the prohibition applies to
    EVERY category, not one. Such a row can never appear in
    by_id_category's (canonical_id, fcs_code) index at all (there is no
    category to key it on), so a per-candidate lookup can never find it and
    would wrongly fall through to "the additive appears elsewhere in
    eu_fip" -> not_permitted_in_category -- telling a manufacturer to move
    the additive to a different category, when the correct answer is that
    it cannot be used in the EU at all. Matching a jurisdiction-wide rule
    against one category is structurally impossible, so it is checked here,
    separately, before any category-specific lookup.
    """
    for candidate_id in (canonical_id, *_parent_codes(canonical_id)):
        if any(row["status"] == "prohibited" for row in by_id.get(candidate_id, [])):
            return candidate_id
    return None


def _lookup_candidate(
    canonical_id: str,
    fcs_code: str,
    category_name: str | None,
    rank: int,
    by_id_category: dict[tuple[str, str], list[dict]],
    by_id: dict[str, list[dict]],
    flags: list[str],
) -> CategoryVerdict:
    """One candidate category's verdict. `flags` is mutated in place with
    cross-cutting signals (prohibited, widened_to_parent_for_lookup,
    conflicting_rows, multiple_provisions_apply, uncitable_verdict) --
    CategoryVerdict itself carries no flags field, since these are
    properties of the LOOKUP, reported once per item rather than
    duplicated across candidates.
    """
    rows = by_id_category.get((canonical_id, fcs_code), [])
    widened = False
    if not rows:
        # PARENT FALLBACK: an exact sub-notation match with no row in this
        # category would wrongly read as not_permitted_in_category -- try
        # the family-level code(s) before concluding that.
        for parent in _parent_codes(canonical_id):
            rows = by_id_category.get((parent, fcs_code), [])
            if rows:
                widened = True
                break

    if rows:
        chosen, select_flags = _select_row(rows)
        flags.extend(select_flags)
        if widened:
            flags.append("widened_to_parent_for_lookup")
        verdict = _verdict_from_row(chosen)
        if verdict == "not_authorised_eu":
            flags.append("prohibited")
        if not chosen.get("source_url"):
            flags.append("uncitable_verdict")
        return CategoryVerdict(
            fcs_code=fcs_code,
            category_name=category_name,
            rank=rank,
            verdict=verdict,
            max_level_mg_kg=chosen.get("max_level_mg_kg"),
            max_level_basis=chosen.get("max_level_basis"),
            conditions=chosen.get("conditions"),
            note_codes=chosen.get("note_codes") or [],
            source_url=chosen.get("source_url"),
            retrieved_date=chosen.get("effective_date"),
        )

    # No row for this category, exact code or any parent. Absence of a row
    # is a FIXABLE formulation problem if the additive is authorised
    # somewhere else in the EU; it is a dead end if it is not authorised
    # anywhere at all. Never collapse the two.
    verdict = "not_permitted_in_category" if _appears_anywhere(canonical_id, by_id) else "not_authorised_eu"
    return CategoryVerdict(
        fcs_code=fcs_code,
        category_name=category_name,
        rank=rank,
        verdict=verdict,
        max_level_mg_kg=None,
        max_level_basis=None,
        conditions=None,
        note_codes=[],
        source_url=None,
        retrieved_date=None,
    )


def _additive_name(canonical_id: str | None, by_id: dict[str, list[dict]]) -> str | None:
    if canonical_id is None:
        return None
    rows = by_id.get(canonical_id)
    return rows[0].get("additive_name") if rows else None


def _evaluate_item(
    resolved: ResolvedItem,
    category_result: CategoryResult | None,
    by_id_category: dict[tuple[str, str], list[dict]],
    by_id: dict[str, list[dict]],
    confirmed: bool,
) -> ItemVerdict:
    additive_name = _additive_name(resolved.eu_canonical_id, by_id)
    component_label = category_result.component_label if category_result else None

    # 1. Structurally not an Annex II additive -- no lookup at all.
    if resolved.classification in _OUT_OF_SCOPE_REGULATION:
        governing = _OUT_OF_SCOPE_REGULATION[resolved.classification]
        return ItemVerdict(
            item_id=resolved.item_id,
            eu_canonical_id=resolved.eu_canonical_id,
            additive_name=additive_name,
            component_label=component_label,
            by_category=[],
            headline="out_of_scope",
            category_sensitive=False,
            verdict_certainty="certain",
            flags=[f"governing_regulation: {governing}"],
        )

    # 2. Not identified -- NOT a ban. A typo, a food ingredient, or a gap in
    # our own index all look like this. But absence from the Union list is
    # a FINDING, not an unknown: when the resolver has already IDENTIFIED
    # the substance (canonical_ins set, classification "additive") and it
    # simply has no eu_canonical_id, that means Codex knows this substance
    # and the EU crosswalk found no entry for it -- e.g. INS 143 (Fast
    # Green FCF), absent from eu_fip and prohibited as a food dye in the
    # EU. Reading that as "unresolved" produced a FALSE CLEAR on a real
    # blocking finding ("No item was found not permitted"), the most
    # dangerous output this system can produce. Only a genuinely
    # UNIDENTIFIED substance (classification unknown/ambiguous, or no
    # canonical_ins at all) is unresolved. ResolvedItem.canonical_ins is
    # read directly here, not inferred from flags.
    if resolved.classification in _UNRESOLVED_CLASSIFICATIONS:
        flags = [f"candidate: {c}" for c in resolved.candidates]
        return ItemVerdict(
            item_id=resolved.item_id,
            eu_canonical_id=resolved.eu_canonical_id,
            additive_name=additive_name,
            component_label=component_label,
            by_category=[],
            headline="unresolved",
            category_sensitive=False,
            verdict_certainty="certain",
            flags=flags,
        )
    if (
        resolved.eu_canonical_id is None
        and resolved.canonical_ins is not None
        and resolved.classification == "additive"
    ):
        return ItemVerdict(
            item_id=resolved.item_id,
            eu_canonical_id=None,
            additive_name=additive_name,
            component_label=component_label,
            by_category=[],
            headline="not_authorised_eu",
            category_sensitive=False,
            verdict_certainty="certain",
            flags=["not_in_eu_list"],
        )
    if resolved.eu_canonical_id is None:
        flags = [f"candidate: {c}" for c in resolved.candidates]
        return ItemVerdict(
            item_id=resolved.item_id,
            eu_canonical_id=None,
            additive_name=additive_name,
            component_label=component_label,
            by_category=[],
            headline="unresolved",
            category_sensitive=False,
            verdict_certainty="certain",
            flags=flags,
        )

    canonical_id = resolved.eu_canonical_id

    # 3. Prohibition is JURISDICTION-WIDE, exactly like absence -- checked
    # BEFORE any category-specific lookup, and evaluated ONCE per item
    # rather than once per candidate, since the verdict does not depend on
    # which category is correct. Must fire even with no category result at
    # all (a text-input item with a prohibited additive and no category is
    # not_authorised_eu, not category_unknown) -- see _prohibited_id.
    prohibited_id = _prohibited_id(canonical_id, by_id)
    if prohibited_id is not None:
        flags = ["prohibited"]
        if prohibited_id != canonical_id:
            flags.append("widened_to_parent_for_lookup")
        return ItemVerdict(
            item_id=resolved.item_id,
            eu_canonical_id=canonical_id,
            additive_name=additive_name,
            component_label=component_label,
            by_category=[],
            headline="not_authorised_eu",
            category_sensitive=False,
            verdict_certainty="certain",
            flags=flags,
        )

    candidates = category_result.top3 if category_result else []

    # 4. No category result (or an empty one) -- no verdict is computable
    # without a category, UNLESS the additive is absent from eu_fip
    # entirely: absence is absolute, not category-dependent, so that
    # verdict does not need a category to be computed (text input, which
    # skips the category stage entirely, hits this).
    if not candidates:
        if not _appears_anywhere(canonical_id, by_id):
            return ItemVerdict(
                item_id=resolved.item_id,
                eu_canonical_id=canonical_id,
                additive_name=additive_name,
                component_label=component_label,
                by_category=[],
                headline="not_authorised_eu",
                category_sensitive=False,
                verdict_certainty="certain",
                flags=[],
            )
        return ItemVerdict(
            item_id=resolved.item_id,
            eu_canonical_id=canonical_id,
            additive_name=additive_name,
            component_label=component_label,
            by_category=[],
            headline="category_unknown",
            category_sensitive=False,
            verdict_certainty="certain",
            flags=[],
        )

    # 5. Evaluate against every candidate category -- category retrieval is
    # measured at recall@3 = 0.76 and no human has confirmed it, so the
    # rank-1 verdict is a headline, not a certainty (category_unconfirmed),
    # and category_sensitive records when the candidates would actually
    # change the answer (E551: permitted in 12.2.2, not in 15.1). When the
    # caller has confirmed this query's category (--category), `candidates`
    # is a single-entry list built by the caller from that confirmation,
    # not retrieval -- category_sensitive/verdict_certainty fall out
    # correctly on their own (one candidate can never disagree with
    # itself), only the flag differs.
    flags = ["category_confirmed_by_user" if confirmed else "category_unconfirmed"]
    by_category = [
        _lookup_candidate(canonical_id, candidate.code, candidate.name, rank, by_id_category, by_id, flags)
        for rank, candidate in enumerate(candidates, start=1)
    ]
    flags = list(dict.fromkeys(flags))  # de-dupe, preserve first-seen order

    category_sensitive = len({cv.verdict for cv in by_category}) > 1
    return ItemVerdict(
        item_id=resolved.item_id,
        eu_canonical_id=canonical_id,
        additive_name=additive_name,
        component_label=component_label,
        by_category=by_category,
        headline=by_category[0].verdict,
        category_sensitive=category_sensitive,
        verdict_certainty="category_dependent" if category_sensitive else "certain",
        flags=flags,
    )


def _is_category_conflict(item: ItemVerdict) -> bool:
    """Rank-1 verdict blocks, but some OTHER candidate would permit it --
    the exact shape of the Parle-G bug: 503, 500(ii), and 472e are all
    permitted via Group I in 7.2 (Fine bakery wares, rank 2), but rank-1
    retrieved 11.1 (Sugars and syrups), where they are not permitted. A
    retrieval miss must not become a confident compliance failure.
    """
    if not item.by_category or item.by_category[0].verdict not in _BLOCKING_VERDICTS:
        return False
    return any(cv.verdict in _PERMITTING_VERDICTS for cv in item.by_category[1:])


def _describe_conflict(item: ItemVerdict) -> str:
    """"<additive> not permitted under <code> (<name>), permitted under
    <code> (<name>)" -- the competing codes and names, so the reader can
    resolve the disagreement without opening the JSON."""
    blocked = item.by_category[0]
    permitting = next(cv for cv in item.by_category[1:] if cv.verdict in _PERMITTING_VERDICTS)
    name = item.additive_name or f"item {item.item_id}"
    return (
        f"{name} not permitted under {blocked.fcs_code} ({blocked.category_name}), "
        f"permitted under {permitting.fcs_code} ({permitting.category_name})"
    )


def _data_version(eu_fip: list[dict]) -> str:
    """A short hash of the eu_fip contents actually used, computed from the
    in-memory data passed in -- no file read, so this stays pure."""
    digest = hashlib.sha256(json.dumps(eu_fip, sort_keys=True, default=str).encode("utf-8"))
    return digest.hexdigest()[:12]


def _build_summary(
    items: list[ItemVerdict],
    blocking: list[int],
    category_conflict: list[int],
    review_required: list[int],
) -> str:
    """Deterministic f-string -- never LLM-written, never claims a label
    'clears for export': a label proves PRESENCE of an additive, not the
    DOSAGE actually used, so a permitted_with_limit verdict must say that
    dosage cannot be verified from a label alone. Equally, a
    category_dependent blocking verdict must never be reported as "NOT
    PERMITTED" -- that IS the false positive this function exists to avoid.

    MEASURED BUG this avoids repeating: review_required (see evaluate())
    deliberately unions in permitted_with_conditions items -- so a human
    remembers to check their conditions text -- but src/ui/components.py's
    count_buckets deliberately EXCLUDES those same items from its "Needs
    review" bucket (they are "Permitted, conditions to check" instead;
    see count_buckets' own docstring and test_count_buckets_are_mutually_
    exclusive_and_sum_to_item_total for why), and src/report/narrator.py's
    prompt rule states the identical split for LLM narration ("a
    permitted_with_conditions item belongs ONLY under 'What is permitted'
    -- do NOT also list it under 'What needs review'"). A single "N
    item(s) require manual review" sentence that quietly counted
    permitted_with_conditions items too (Pepsimain, MEASURED: 7 items
    called "require manual review" here, "Needs review" 0 in the count
    strip -- narration normally hides this, since narrate() overwrites
    this whole string, but the two are wrong to disagree even when
    hidden) contradicted the strip it renders alongside on every run
    where narration fell back to this function. Split below so both
    describe the SAME item, with the SAME word, every time.
    """
    parts = [f"{len(items)} item(s) evaluated."]
    if blocking:
        parts.append(f"{len(blocking)} item(s) NOT PERMITTED (item_id {blocking}) -- see flags.")
    else:
        parts.append("No item was found not permitted.")

    if category_conflict:
        conflict_items = [item for item in items if item.item_id in category_conflict]
        # Group identical (blocked category, permitting category) patterns
        # so items sharing one query (e.g. Parle-G's 503/500(ii)/472e, all
        # judged against the same rank-1/rank-2 pair) are not repeated
        # three times verbatim -- still names every item driving each
        # pattern, so nothing is lost.
        patterns: dict[tuple[str, str | None, str, str | None], list[str]] = {}
        for item in conflict_items:
            blocked = item.by_category[0]
            permitting = next(cv for cv in item.by_category[1:] if cv.verdict in _PERMITTING_VERDICTS)
            key = (blocked.fcs_code, blocked.category_name, permitting.fcs_code, permitting.category_name)
            patterns.setdefault(key, []).append(item.additive_name or f"item {item.item_id}")
        descriptions = [
            f"{', '.join(names)} not permitted under {b_code} ({b_name}), permitted under {p_code} ({p_name})"
            for (b_code, b_name, p_code, p_name), names in patterns.items()
        ]
        parts.append(
            f"{len(category_conflict)} item(s) depend on the food category: "
            + "; ".join(descriptions)
            + ". Confirm the category before treating this as a compliance failure."
        )

    if review_required:
        # Split by headline, not by slicing review_required itself: a
        # permitted_with_conditions item can never also be unresolved,
        # category_unknown, or (by construction -- _is_category_conflict
        # requires a _BLOCKING_VERDICTS headline) in category_conflict, so
        # this partition is exact, not an approximation.
        needs_review = [
            item.item_id
            for item in items
            if item.item_id in review_required
            and (item.headline in ("unresolved", "category_unknown") or item.item_id in category_conflict)
        ]
        conditions_to_check = [
            item.item_id
            for item in items
            if item.item_id in review_required and item.headline == "permitted_with_conditions"
        ]
        if needs_review:
            parts.append(f"{len(needs_review)} item(s) require manual review (item_id {needs_review}).")
        if conditions_to_check:
            parts.append(
                f"{len(conditions_to_check)} item(s) are permitted subject to conditions that must be "
                f"checked against the product, not a missing verdict (item_id {conditions_to_check})."
            )

    # Checked on the underlying max_level_mg_kg field, not the
    # "permitted_with_limit" verdict label: 96% of eu_fip rows carry
    # conditions text, so the conditions branch usually wins first (rule 4
    # checks conditions before max_level) even when a numeric cap is ALSO
    # present -- e.g. INS 551 "Permitted via Silicon dioxide..." still
    # carries a real 20000 mg/kg cap. The caveat must fire whenever a real
    # numeric limit exists, not only on the rarer pure permitted_with_limit
    # verdict, or this exact case would silently skip it.
    has_limit = any(cv.max_level_mg_kg is not None for item in items for cv in item.by_category)
    if has_limit:
        parts.append(
            "Some additives are permitted only up to a maximum level. A label declares "
            "PRESENCE, not the quantity actually used -- this does not clear the product "
            "for export; the dosage used cannot be verified from a label alone."
        )

    # A query the caller confirmed (--category) is described so the reader
    # knows which verdicts rest on a human decision rather than retrieval.
    confirmed_descriptions: dict[str, str] = {}
    for item in items:
        if "category_confirmed_by_user" in item.flags and item.by_category:
            key = item.component_label or "product"
            cv = item.by_category[0]
            confirmed_descriptions[key] = f"{key} = {cv.fcs_code} ({cv.category_name})"
    if confirmed_descriptions:
        parts.append("Food category confirmed by user: " + ", ".join(confirmed_descriptions.values()) + ".")

    # The retrieval caveat is no longer true of a confirmed category, so it
    # is gated on "category_unconfirmed" actually being present on some
    # item -- not on verdict_certainty, since three retrieved candidates
    # can happen to agree (verdict_certainty="certain") without a human
    # ever having looked at any of them.
    if any("category_unconfirmed" in item.flags for item in items):
        parts.append(
            f"The food category was retrieved automatically (measured recall@1 is about "
            f"{_MEASURED_CATEGORY_RECALL_AT_1:.0%}) and has not been confirmed by a human -- "
            "verdicts that depend on which candidate category is correct may change once it is."
        )
    return " ".join(parts)


def evaluate(
    resolved_items: list[ResolvedItem],
    category_results: dict[int, CategoryResult],
    eu_fip: list[dict],
    confirmed_item_ids: frozenset[int] = frozenset(),
) -> ProductVerdict:
    """The compliance verdict for a whole product.

    `category_results` is item_id -> the CategoryResult that covers it,
    ALREADY matched by the caller (nearest depth-0 compound ancestor, else
    the product query -- the same scoping the category stage itself used).
    This module never re-derives that matching from extraction.json; it
    has no idea what extraction items even look like.

    `confirmed_item_ids` are items whose CategoryResult was REPLACED by the
    caller with a single human-confirmed candidate (--category), rather
    than left as the retrieved top-3. This module does not build that
    replacement itself -- it only needs to know which items to flag
    "category_confirmed_by_user" instead of "category_unconfirmed"; the
    single-candidate list already makes category_sensitive/verdict_certainty
    correct on its own (see _evaluate_item, step 5).
    """
    by_id_category, by_id = _build_indices(eu_fip)

    items = [
        _evaluate_item(
            resolved,
            category_results.get(resolved.item_id),
            by_id_category,
            by_id,
            resolved.item_id in confirmed_item_ids,
        )
        for resolved in resolved_items
    ]

    category_conflict = [item.item_id for item in items if _is_category_conflict(item)]
    # A blocking verdict only counts once it is CERTAIN -- category
    # recall@1 is measured at ~0.46, so a rank-1-only block is exactly the
    # false positive this split exists to prevent. Every category_conflict
    # item is, by construction, blocking-but-not-certain, so it is excluded
    # here and reported separately instead.
    blocking = [
        item.item_id
        for item in items
        if item.headline in _BLOCKING_VERDICTS and item.verdict_certainty == "certain"
    ]
    review_required = [
        item.item_id
        for item in items
        if item.headline in ("unresolved", "category_unknown", "permitted_with_conditions")
        or item.item_id in category_conflict
    ]
    category_sensitive_items = [item.item_id for item in items if item.category_sensitive]

    category_used: dict[str, str] = {}
    category_source: dict[str, str] = {}
    for item in items:
        if item.by_category:
            key = item.component_label or "(product)"
            category_used[key] = item.by_category[0].fcs_code
            category_source[key] = "user" if "category_confirmed_by_user" in item.flags else "retrieved"

    warnings = []
    n_category_unknown = sum(1 for item in items if item.headline == "category_unknown")
    if n_category_unknown:
        warnings.append(
            f"{n_category_unknown} item(s) have no category classification available -- "
            "run scripts/classify_category.py before trusting this verdict as complete."
        )

    return ProductVerdict(
        items=items,
        blocking=blocking,
        category_conflict=category_conflict,
        review_required=review_required,
        category_sensitive_items=category_sensitive_items,
        summary=_build_summary(items, blocking, category_conflict, review_required),
        category_used=category_used,
        category_source=category_source,
        warnings=warnings,
        data_version=_data_version(eu_fip),
    )
