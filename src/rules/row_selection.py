# DESIGN RULE: this module holds the eu_fip row-selection ALGORITHM, not
# stage logic -- like src/reference_data.py, it belongs to no pipeline
# stage, decides no verdict, and both src/rules/engine.py and
# src/substitutes/advisor.py may import it without crossing the
# stage-boundary rule each of those modules' own DESIGN RULE comments
# describe. This is NOT src.rules.engine: importing this module is not
# the same as advisor.py importing engine.py.
"""Given several eu_fip rows for the same (additive, category), decide
what a compliance report shows. Extracted from src/rules/engine.py,
where this logic originated, after src/substitutes/advisor.py's
independent (necessarily duplicated, per its DESIGN RULE) copy fell out
of sync with it: engine.py was fixed to MERGE co-applicable rows
(distinct conditions concatenated and numbered, most restrictive level
kept, note codes unioned) instead of silently picking one and discarding
the rest, but advisor.py's copy was never updated to match -- measured,
7 of 19 real substitute candidates in data/outputs/substitutes/ have 2+
eu_fip rows for their (id, category), and advisor.py silently dropped
the second clause on every one of them. A pure, stage-agnostic module
keeps this one algorithm in one place, the same pattern already used by
src/reference_data.py for the &nbsp;-placeholder bug.

A row `select_row` returns from its MERGE branch is a constructed dict,
not one of the input eu_fip rows -- it carries only the fields a caller
needs to build a verdict/candidate (status, max_level_mg_kg,
max_level_basis, conditions, note_codes, source_url, effective_date) and
has NO `additive_name` (or `canonical_id`) key, unlike every unmerged
eu_fip row. src/substitutes/advisor.py had to be changed to read
`additive_name` from its own `rows[0]` instead of the row `select_row`
returned, for exactly this reason -- any future caller of `select_row`
needs the same care.
"""

import re

_BLANK_MARKER_RE = re.compile(r"&nbsp;|\xa0", re.IGNORECASE)


def has_condition_text(text: str) -> bool:
    return bool(_BLANK_MARKER_RE.sub("", text).strip())


def normalize_row(row: dict) -> dict:
    """A row whose `conditions` is truthy but blank once &nbsp;/NBSP
    markers are stripped (a scraped placeholder for an empty cell) reads
    as None instead -- otherwise it survives every `if row["conditions"]`
    check below and renders as an empty numbered clause once merged
    alongside a real one."""
    conditions = row.get("conditions")
    if conditions and not has_condition_text(conditions):
        return {**row, "conditions": None}
    return row


def restrictiveness_key(row: dict) -> tuple:
    """Sort key for the MOST restrictive of several conflicting rows for
    the same (additive, category): prohibited beats permitted; a numeric
    cap beats none, and a lower cap beats a higher one; conditions text
    beats a blanket gmp permission."""
    status_rank = 0 if row["status"] == "prohibited" else 1
    if row.get("max_level_mg_kg") is not None:
        level_rank, level_value = 0, row["max_level_mg_kg"]
    else:
        level_rank, level_value = 1, float("inf")
    conditions_rank = 0 if row.get("conditions") else 1
    return (status_rank, level_rank, level_value, conditions_rank)


def dedupe_rows(rows: list[dict]) -> list[dict]:
    """Collapse EXACT duplicate rows -- same additive, category, status,
    level, and conditions recorded more than once."""
    seen: dict[tuple, dict] = {}
    for row in rows:
        key = (
            row["canonical_id"],
            row["food_category_raw"],
            row["status"],
            row.get("max_level_mg_kg"),
            row.get("conditions"),
        )
        seen.setdefault(key, row)
    return list(seen.values())


def merge_levels(rows: list[dict]) -> tuple[float | None, str | None]:
    """(max_level_mg_kg, max_level_basis) across several rows: the MINIMUM
    (most restrictive) non-null level, with the basis that came with it --
    None/first-row's-basis if every row is unbounded (quantum satis /
    conditions-only)."""
    leveled = [row for row in rows if row.get("max_level_mg_kg") is not None]
    if not leveled:
        return None, rows[0].get("max_level_basis")
    winner = min(leveled, key=lambda row: row["max_level_mg_kg"])
    return winner["max_level_mg_kg"], winner.get("max_level_basis")


def merge_conditions(rows: list[dict]) -> str | None:
    """Every DISTINCT conditions text across several rows, concatenated and
    numbered when there is more than one -- never a choice between them.
    Unlike a numeric level, one clause of prose is not "more restrictive"
    than another; both can be true and both must be shown, or a
    manufacturer reads a compliance report as clearing a condition it
    never checked."""
    distinct = list(dict.fromkeys(row["conditions"] for row in rows if row.get("conditions")))
    if not distinct:
        return None
    if len(distinct) == 1:
        return distinct[0]
    return "\n\n".join(f"{i}) {text}" for i, text in enumerate(distinct, start=1))


def merge_note_codes(rows: list[dict]) -> list[str]:
    codes: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for code in row.get("note_codes") or []:
            if code not in seen:
                seen.add(code)
                codes.append(code)
    return codes


def select_row(rows: list[dict]) -> tuple[dict, list[str]]:
    """Returns (row, flags) for one (additive, category) lookup.

    Most eu_fip (additive, category) pairs have exactly one row after
    dedup. When more than one survives, MEASURED (see docs/build_log.md
    Section J, "conflicting_rows on Khusmain"): of the 134 additives
    permitted via "Group I, Additives" in category 14.1.4, 131 (98%) carry
    EXACTLY the same two-clause pair, verbatim -- this is overwhelmingly a
    genuine structural pattern, not noisy data. EU FIP frequently splits
    ONE legal permission into several rows, one per distinct restriction
    clause that applies simultaneously. Picking one row as "the" answer
    silently discarded whichever clause lost the tie-break -- a compliance
    report that drops an applicable condition is wrong, not merely
    incomplete.

    So: when every surviving row agrees on STATUS (the common case, both
    permitted, just differently conditioned), they are MERGED -- the most
    restrictive numeric level, but every distinct conditions text, note
    code, kept. Flagged "multiple_provisions_apply: N" so a reader knows
    more than one clause was combined. This MERGE behaviour is what
    Section J's own measurement above eventually motivated, overturning
    that section's original conclusion (a single representative row,
    picked by tie-break, left unchanged) -- see the SUPERSEDED note
    appended to the end of Section J for the full account of what changed
    and why. Section J is cited here only for the underlying measurement
    (131 of 134 rows sharing one two-clause pattern); its ORIGINAL verdict
    on what to do about that measurement no longer holds.

    A genuine STATUS disagreement (one row permitted, another prohibited)
    is a different kind of conflict -- not co-applicable provisions but an
    unresolved contradiction in the source data -- and keeps the old
    single-row, most-restrictive-wins behaviour (prohibited always wins),
    flagged "conflicting_rows" exactly as before: there is no sensible way
    to "merge" a permission with a ban.

    The row returned on a genuine STATUS disagreement, or when only one
    row survives dedup, is one of the ORIGINAL input rows -- every eu_fip
    field, including additive_name and canonical_id, is present. Only the
    MERGE branch's return value is a constructed dict missing those two
    fields (see this module's own docstring for why that matters to
    callers).
    """
    deduped = dedupe_rows(rows)
    if len(deduped) == 1:
        return deduped[0], []

    if len({row["status"] for row in deduped}) > 1:
        return min(deduped, key=restrictiveness_key), ["conflicting_rows"]

    level, basis = merge_levels(deduped)
    merged = {
        "status": deduped[0]["status"],
        "max_level_mg_kg": level,
        "max_level_basis": basis,
        "conditions": merge_conditions(deduped),
        "note_codes": merge_note_codes(deduped),
        "source_url": deduped[0].get("source_url"),
        "effective_date": deduped[0].get("effective_date"),
    }
    return merged, [f"multiple_provisions_apply: {len(deduped)}"]
