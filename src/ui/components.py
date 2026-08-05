# DESIGN RULE: pure render functions -- plain dicts in (never ItemVerdict/
# ProductVerdict/etc. pydantic instances, so this module never imports
# src.rules.schemas or any other stage's schema module), Streamlit draw
# calls out. No compliance logic lives here: a function in this module may
# choose a colour bucket or a human-readable label for a verdict STRING
# that src/rules/engine.py already computed, but it never decides whether
# an additive is permitted -- that decision is entirely upstream. Display
# names are the one apparent exception worth flagging: by the time a dict
# reaches this module, app.py has already backfilled additive_name (eu_fip
# name, else Codex name, else the label's own wording) -- see app.py's
# _enrich_additive_names -- so _display_name below only ever falls back for
# items no name source could resolve at all.
"""Render functions for the compliance-checker UI: the additives table (the
signature element -- see app.py's module docstring), the count strip, the
out-of-scope and review-queue tables, and the substitutes/horizon panels."""

import html
import re

import streamlit as st

_PERMITTING_VERDICTS = frozenset({"permitted_qs", "permitted_with_limit", "permitted_with_conditions"})


def _level_phrase(max_level_mg_kg: float | None) -> str:
    if max_level_mg_kg is None:
        return "no fixed limit"
    level = int(max_level_mg_kg) if max_level_mg_kg == int(max_level_mg_kg) else max_level_mg_kg
    return f"up to {level} mg/kg"


# UI REDESIGN (ui-simplify branch): the FOUR status strings a reader with
# no regulatory training needs -- never a raw verdict string
# (not_authorised_eu), never "quantum satis"/"ML" as bare terms. Whether a
# permitting verdict reads "Allowed" or "Allowed with limits" is decided
# by whether a REAL numeric cap exists (max_level_mg_kg), not by which of
# the three permitting verdict strings produced it: permitted_with_
# conditions can carry a real mg/kg limit too (96% of eu_fip rows carry
# conditions text; src/rules/engine.py's own dosage caveat already fires
# off max_level_mg_kg directly for exactly this reason, never off the
# permitted_with_limit label alone) -- treating permitted_with_conditions
# as always-just-"Allowed" would silently drop a real numeric limit from
# the one column a reader scans for it.
def _status_label(verdict: str, max_level_mg_kg: float | None) -> tuple[str, str]:
    """(plain-English status, colour bucket for data-bucket=)."""
    if verdict == "not_authorised_eu":
        return "Not allowed in the EU", "blocked"
    if verdict == "not_permitted_in_category":
        return "Not allowed in this food", "blocked"
    return ("Allowed with limits" if max_level_mg_kg is not None else "Allowed"), "permitted"


def _max_amount_cell(verdict: str, max_level_mg_kg: float | None) -> str:
    """The Maximum amount column: the real mg/kg figure, or "No fixed
    limit" -- NEVER "quantum satis" as a bare term (see render_substitutes
    for the one place that word used to leak through) -- or "—" for a
    verdict that blocks outright, where a limit is not applicable at all."""
    if verdict not in _PERMITTING_VERDICTS:
        return "—"
    return _level_phrase(max_level_mg_kg).capitalize()


# WHY a blocking verdict blocks -- a bare name with no reason forces a
# reader to already know EU additive law to understand the finding.
# "prohibited" (a jurisdiction-wide ban, e.g. E171) and absence from the EU
# list (e.g. an unlisted colour) are different findings with different
# implications for a manufacturer, so they get different wording.
_PROHIBITED_REASON = "prohibited — removed from the EU permitted list"
_NOT_AUTHORISED_REASON = (
    "not authorised — this additive does not appear in the EU list of permitted food "
    "additives, in any food category"
)

# governing_regulation value (src/rules/engine.py's _OUT_OF_SCOPE_REGULATION)
# -> the plain-language reason shown next to an out-of-scope item. Regulation
# NUMBERS are deliberately dropped from this default view -- a reader with
# no regulatory training gets nothing from "Reg 1334/2008"; the real
# citation stays on the item's own governing_regulation flag, untouched, in
# the JSON export.
_OUT_OF_SCOPE_REASONS: dict[str, str] = {
    "Reg 1334/2008": "a flavouring, covered by different rules",
    "Reg 1332/2008": "an enzyme, covered by different rules",
    "not an additive": "an ordinary food",
}

# Internal flag name -> the plain phrase shown in the substitutes table.
# The raw flag is untouched in the JSON export -- only display changes.
_SUBSTITUTE_FLAG_LABELS: dict[str, str] = {
    "classes_from_subtypes": "function inferred from sub-types",
    "adds_labelling_obligation": "requires a warning label",
    "under_efsa_review": "under EFSA review",
}

# eu_fip records permissions at the LEAF food category, so a retrieved
# parent code (e.g. "14.1" or "14" alongside a confirmed "14.1.4") has no
# row of its own and would always render "not permitted in this category"
# -- technically true, entirely meaningless, and it makes the strip claim a
# divergence between an additive and a category that CONTAINS it. Matched
# by dotted-code prefix, not by tree traversal: eu_fip codes are already
# flat "N.N.N..." strings.
_GROUP_CONDITIONS_RE = re.compile(r"^(?:\d+\)\s*)?Permitted via (Group [IVX]+(?:,\s*[A-Za-z]+)?)")
_PERIOD_OF_APPLICATION_RE = re.compile(r"period of application", re.IGNORECASE)

# src/rules/row_selection.py's merge_conditions numbers EVERY clause
# ("1) ...\n\n2) ...") only when 2+ DISTINCT co-applicable rows are merged;
# a single clause, merged or not, is never numbered. A second numbered
# clause after a paragraph break therefore means this conditions string
# carries more than the Group permission alone (MEASURED, real examples in
# data/outputs/verdict/: GraphTestChips.json's E627/E631 are "1) Permitted
# via Group I...\n\n2) Permitted via Ribonucleotides" -- collapsing that to
# a bare Group I reference would silently drop the Ribonucleotides clause).
_MERGED_CLAUSE_MARKER_RE = re.compile(r"\n\s*\n\s*\d+\)\s")

# Splits a merge_conditions() string BEFORE each numbered marker (the
# marker itself, "2) ", "3) ", ... stays with the clause it introduces, so
# each split piece is still individually parseable by _LEADING_CLAUSE_
# NUMBER_RE below). This is the ONLY boundary _split_merged_clauses treats
# as confident -- it is produced by code (row_selection.merge_conditions),
# not by wherever eu_fip's own free-text newlines happen to fall.
_MERGED_CLAUSE_SPLIT_RE = re.compile(r"\n\s*\n\s*(?=\d+\)\s)")
_LEADING_CLAUSE_NUMBER_RE = re.compile(r"^\d+\)\s*")


def _split_merged_clauses(conditions: str) -> list[str] | None:
    """The individual clauses of a merged multi-clause conditions string,
    each with its own leading "N) " marker stripped -- or None if
    `conditions` does not have that shape at all. merge_conditions()
    numbers a clause ONLY when 2+ distinct co-applicable rows were merged
    (src/rules/row_selection.py); a single clause, however it wraps, is
    NEVER numbered -- so None here also correctly means "this is one
    clause", not "detection failed".

    MEASURED (see _render_conditions_body): eu_fip conditions text is
    riddled with mid-clause newlines that are NOT clause boundaries --
    of 1194 distinct conditions strings in data/reference/eu_fip.json,
    only 96 (8.0%) contain a first_sentence()-style sentence boundary at
    all, and even those routinely fall mid-clause (a period inside a
    Regulation citation, not a real full stop) -- so a raw "\\n" or a
    sentence-boundary regex is never trusted here. The "N) " marker
    inserted by merge_conditions is the only boundary this project
    controls the placement of, so it is the only one treated as
    confident."""
    if not _MERGED_CLAUSE_MARKER_RE.search(conditions):
        return None
    clauses = _MERGED_CLAUSE_SPLIT_RE.split(conditions)
    return [_LEADING_CLAUSE_NUMBER_RE.sub("", clause).strip() for clause in clauses]


def _normalize_conditions(conditions: str) -> str:
    """Whitespace/punctuation-insensitive identity for a conditions string.
    MEASURED against data/reference/eu_fip.json: the dominant Group I
    clause alone (9,051 of 18,987 rows, 47.7% of Annex II) is stored in
    three literal forms -- wrapped with a mid-sentence newline, collapsed
    to one line, and with/without a trailing period. Without this, the
    "same" clause is treated as three different ones."""
    return re.sub(r"\s+", " ", conditions).strip().rstrip(".").strip()


def _pure_group_match(conditions: str) -> re.Match | None:
    """Whether `conditions` is EXCLUSIVELY a Group clause, as opposed to a
    merged multi-clause string where Group is only one co-applicable
    clause among several -- see _MERGED_CLAUSE_MARKER_RE. Only a pure
    match is safe to collapse to a shared-block reference without dropping
    a clause a per-item render would otherwise have shown."""
    if _MERGED_CLAUSE_MARKER_RE.search(conditions):
        return None
    return _GROUP_CONDITIONS_RE.match(_normalize_conditions(conditions))

# food_categories.json's refDataFoodCategoryEN names carry the legal
# citation inline ("Cocoa and chocolate products as covered by Directive
# 2000/36/EC"), not as a separate field -- 25 of 154 categories MEASURED to
# have one. Inline, seven repeats of the full sentence is unreadable; the
# citation is dropped for body text and the caller shows the untouched
# category_name once, in the header, instead. Non-greedy up to the next
# " and " (not just end-of-string) because a few names put real category
# content AFTER the citation ("Fruit juices as defined by Directive
# 2001/112/EC and vegetable juices").
_CATEGORY_CITATION_RE = re.compile(
    r"\s+as (?:defined|covered|referred to)\s+(?:by|in)\s+(?:Directive|Regulation)\b[^,]*?(?=\s+and\b|$)",
    re.IGNORECASE,
)


def _short_category_name(name: str | None) -> str | None:
    if not name:
        return None
    return _CATEGORY_CITATION_RE.sub("", name).strip()


def _category_display(fcs_code: str | None, category_name: str | None) -> str:
    """"Cocoa and chocolate products (5.1)" -- body text. The full legal
    title (category_name, untouched) is shown once, in the identity
    header, by app.py's _category_summary -- never re-derived here."""
    short = _short_category_name(category_name)
    if not short:
        return fcs_code or ""
    return f"{short} ({fcs_code})" if fcs_code else short


def _is_ancestor_code(candidate_code: str, of_code: str) -> bool:
    """True if `candidate_code` is a dotted-code ancestor of `of_code` --
    "14.1" is an ancestor of "14.1.4"; "14.1.4" is not an ancestor of "14.1"
    (directional) nor of itself."""
    return candidate_code != of_code and of_code.startswith(candidate_code + ".")


def _group_note(conditions: str) -> str | None:
    """"Permitted as part of Group I. The conditions below apply to every
    additive in that group..." -- shown above any conditions block whose
    text opens with a Group reference, so identical-looking conditions
    across unrelated additives (E330, E440: both Group I) read as the
    correct, boring explanation they are, not as a bug. The group name is
    read off the conditions text itself -- eu_fip's via_group/group_code
    live on the ROW, not on CategoryVerdict, and this module only ever
    sees the already-resolved verdict, never a row."""
    match = _GROUP_CONDITIONS_RE.match(conditions)
    if not match:
        return None
    return (
        f"Permitted as part of {match.group(1)}. The conditions below apply to every additive in "
        "that group, so they mention substances that may not be in your product."
    )


def _period_of_application_note(conditions: str) -> str | None:
    """eu_fip has no structured expiry/end-date field (checked: no row, in
    any dataset revision so far, carries one) -- a "period of application"
    date lives only inside this prose and may be historical. Never
    filtered or hidden -- a past date in a live provision is worth seeing
    -- just labelled, so it is not mistaken for a current instruction."""
    if not _PERIOD_OF_APPLICATION_RE.search(conditions):
        return None
    return (
        'This condition includes a transition date ("period of application"). eu_fip records no '
        "separate expiry field -- dates like this exist only in this text and may be historical. "
        "The source link is authoritative for current status."
    )


def _conditions_notes(conditions: str | None) -> list[str]:
    if not conditions:
        return []
    return [note for note in (_group_note(conditions), _period_of_application_note(conditions)) if note]


def _esc(text: str | None) -> str:
    return html.escape(text) if text else ""


def _display_name(item: dict, names: dict[int, str] | None) -> str:
    if item.get("additive_name"):
        return item["additive_name"]
    if names and item.get("item_id") in names:
        return names[item["item_id"]]
    if item.get("eu_canonical_id"):
        return f"E{item['eu_canonical_id']}"
    return f"item {item.get('item_id')}"


def _blocking_reason(item: dict) -> str:
    return _PROHIBITED_REASON if "prohibited" in (item.get("flags") or []) else _NOT_AUTHORISED_REASON


def _out_of_scope_reason(item: dict) -> str:
    for flag in item.get("flags") or []:
        if flag.startswith("governing_regulation:"):
            governing = flag.split(":", 1)[1].strip()
            return _OUT_OF_SCOPE_REASONS.get(governing, governing)
    return "an ordinary food"


def _substitute_flag_label(flag: str) -> str:
    return _SUBSTITUTE_FLAG_LABELS.get(flag, flag)


# Ambiguous-name candidates (src.resolve.schemas.ResolvedItem.candidates,
# carried on an item as "candidate: <code>" flags) are bare EU/Codex INS
# codes ("960a") -- meaningless to a reader with no regulatory training.
# Below this many, real names (from a code -> name lookup app.py builds off
# refs.codex_ins) are worth showing in full; at or above it, a name-by-name
# list is worse than a count + range -- MEASURED against data/reference/
# label_aliases.json: "modified starch"/"modified cornstarch" both resolve
# to 17 candidates spanning E1400-E1452, 17 long INS names is not something
# anyone scans, but "17 possible matches (E1400-E1452)" is instantly
# legible.
_MANY_CANDIDATES = 6


def candidates_phrase(codes: list[str], ins_names: dict[str, str] | None = None) -> str:
    """`codes` (bare INS codes) as a reader-facing phrase -- real names,
    comma-joined, when there are few; a count and the first-to-last code
    range when there are many. A code with no entry in `ins_names` falls
    back to "E{code}" rather than disappearing -- an unnamed candidate is
    still a real candidate."""
    if not codes:
        return ""
    if len(codes) >= _MANY_CANDIDATES:
        return f"{len(codes)} possible matches (E{codes[0]}–E{codes[-1]})"
    lookup = ins_names or {}
    return ", ".join(lookup.get(code, f"E{code}") for code in codes)


def _primary_candidate(item: dict) -> dict | None:
    """The candidate this item's citation/conditions/"in force since" text
    actually rest on: the CONFIRMED one when the item carries a
    confirmed_fcs_code (see app.py's _merge_preview_candidates), else
    whichever candidate is listed first -- the item is not category-
    dependent at all, so there is nothing to disambiguate."""
    by_category = item.get("by_category") or []
    if not by_category:
        return None
    confirmed_code = item.get("confirmed_fcs_code")
    if confirmed_code:
        for cv in by_category:
            if cv["fcs_code"] == confirmed_code:
                return cv
    return by_category[0]


def _diverging_candidates(item: dict) -> list[dict]:
    """The category candidates this item's verdict GENUINELY depends on --
    empty whenever every real (non-ancestor) candidate agrees, or there is
    only one to begin with, since there is nothing to explain in that
    case. 2+ elements, confirmed candidate first, only when they truly
    diverge (MEASURED case: E551 -- permitted under the confirmed 12.2.2,
    not permitted under 15.1).

    DESIGN FLAW this avoids re-introducing: retrieval can return an
    ANCESTOR of the real answer alongside it -- Khusmain's candidates were
    "14.1.4", "14.1" and "14", where the latter two are not alternatives,
    they CONTAIN 14.1.4. eu_fip records permissions at the leaf, so a
    parent code always evaluates "not permitted in this category" --
    true, meaningless, and it would falsely claim a divergence. Ancestors
    of the reference candidate (_primary_candidate -- confirmed, else the
    first retrieved) are filtered out before any divergence check runs,
    exactly as the original verdict strip this replaces did."""
    by_category = item.get("by_category") or []
    if not by_category:
        return []
    deduped: dict[str, dict] = {}
    for cv in by_category:
        deduped.setdefault(cv["fcs_code"], cv)
    all_candidates = list(deduped.values())

    confirmed_code = item.get("confirmed_fcs_code")
    top = _primary_candidate(item)
    reference_code = top["fcs_code"] if top else all_candidates[0]["fcs_code"]

    candidates = [cv for cv in all_candidates if not _is_ancestor_code(cv["fcs_code"], reference_code)]
    if len(candidates) <= 1 or len({cv["verdict"] for cv in candidates}) <= 1:
        return []
    return sorted(candidates, key=lambda cv: cv["fcs_code"] != confirmed_code)


# headline -> which count-strip bucket it belongs to. Partitioned purely by
# each item's `headline` (a single Literal value per item, never more than
# one), NOT by membership in ProductVerdict.blocking/category_conflict/
# review_required -- those are DERIVED lists that overlap by design (e.g.
# review_required deliberately also includes permitted_with_conditions
# items, so a human remembers to check their conditions; a count strip
# built from review_required directly double-counted every conditional
# item in both the "Permitted" and "Review" cells). A headline-based
# partition sums to len(items) exactly, always, because every item has
# exactly one headline.
_BLOCKED_HEADLINES = frozenset({"not_authorised_eu", "not_permitted_in_category"})
_NEEDS_REVIEW_HEADLINES = frozenset({"unresolved", "category_unknown"})
_PERMITTED_HEADLINES = frozenset({"permitted_qs", "permitted_with_limit", "permitted_with_conditions"})


def count_buckets(items: list[dict]) -> dict[str, int]:
    """The four MUTUALLY EXCLUSIVE counts for the count strip, always
    summing to len(items). A category-dependent blocking verdict (headline
    is blocking-type, but excluded from ProductVerdict.blocking because
    verdict_certainty is "category_dependent") is counted under "Blocked"
    here regardless: this app always confirms the category before a
    verdict is computed at all, so that case is empty in practice, but the
    headline-only partition stays correct even if it weren't."""
    counts = {"Blocked": 0, "Permitted, conditions to check": 0, "Needs review": 0, "Out of scope": 0}
    for item in items:
        headline = item.get("headline")
        if headline == "out_of_scope":
            counts["Out of scope"] += 1
        elif headline in _NEEDS_REVIEW_HEADLINES:
            counts["Needs review"] += 1
        elif headline in _BLOCKED_HEADLINES:
            counts["Blocked"] += 1
        elif headline in _PERMITTED_HEADLINES:
            counts["Permitted, conditions to check"] += 1
    return counts


def render_count_strip(counts: dict[str, int]) -> None:
    """BLOCKED n - PERMITTED n - REVIEW n - OUT OF SCOPE n, at the top of
    the results screen. Pure orientation -- clicking is not the point, the
    counts are: a reader should know the shape of the result before
    scrolling through every item."""
    cells = "".join(
        f"<span class='eu-count-cell'><strong>{value}</strong> {_esc(label)}</span>"
        for label, value in counts.items()
    )
    st.markdown(f"<div class='eu-count-strip'>{cells}</div>", unsafe_allow_html=True)


def _verdict_caveats(items: list[dict]) -> list[str]:
    """The two caveats ProductVerdict.summary (src/rules/engine.py) always
    states in prose, computed here DETERMINISTICALLY instead -- never from
    the narration model's output. A prompt is a request, not a guarantee:
    this project has already measured that three times (fence-stripping,
    the four different empty-response shapes, and the category-confirmation
    wording that only had explicit phrasing for one of its two directions).
    A prompt rule for these two would be a fourth instance of the same
    mistake, so this is plain code instead -- it cannot go missing because
    a model declined to mention it, and it is exactly as present on the
    narrate() fallback path as when narration succeeds, since it never
    reads narration at all.

    - Dosage: fires whenever ANY item's chosen category carries a real
      numeric max_level_mg_kg -- the SAME condition src/rules/engine.py's
      own _build_summary checks for the identical caveat in verdict.summary.
    - Category confirmation: read directly off each item's OWN flags
      (category_confirmed_by_user / category_unconfirmed) -- never
      re-derived from verdict.summary's prose, so a future wording change
      there cannot silently break this. If any item's category is
      unconfirmed, that caveat wins over a "you confirmed it" one even when
      other items in the same product ARE confirmed -- the uncertain case
      is the one worth surfacing, not the reassuring one.
    """
    caveats = []

    has_limit = any(
        candidate.get("max_level_mg_kg") is not None
        for item in items
        for candidate in item.get("by_category") or []
    )
    if has_limit:
        caveats.append(
            "A label declares that a permitted-with-limit additive is present, not the dosage "
            "actually used -- the maximum levels shown below cannot be verified from a label alone."
        )

    unconfirmed = any("category_unconfirmed" in (item.get("flags") or []) for item in items)
    confirmed = any("category_confirmed_by_user" in (item.get("flags") or []) for item in items)
    if unconfirmed:
        caveats.append("The food category was retrieved automatically and has not been confirmed by you.")
    elif confirmed:
        caveats.append("You confirmed the food category used for this assessment.")

    return caveats


def render_verdict_caveats(items: list[dict]) -> None:
    """Near the count strip, not inside the narration -- see
    _verdict_caveats for why these two lines are deterministic code rather
    than a prompt rule. Caption-styled, not a bordered box: the point is
    that it cannot go missing, not that it is prominent."""
    for caveat in _verdict_caveats(items):
        st.caption(caveat)


def _render_conditions_body(conditions: str, note_codes: list[str] | None) -> None:
    """The conditions text itself, once the citation/date above it are
    handled by the caller: one caption line per applicable note, then the
    conditions text -- as a numbered list, one <li> per clause, if
    merge_conditions() numbered it (_split_merged_clauses), or as a
    single block otherwise. Never split anywhere else and never hidden
    behind an expander.

    MEASURED BUG this replaces: the previous version split on the first
    literal "\\n" in `conditions` (conditions.partition("\\n")) and put
    everything after it behind a "Conditions of use" expander. eu_fip's
    own newlines are mid-clause word-wraps, not clause boundaries, so
    that consistently cut the visible text mid-thought -- e.g. Pepsimain
    E150d showed "...Period of application:" inline with "until 31 July
    2014" hidden in the expander; Pepsimain E330 showed "...E 968 may"
    inline with "not be used except..." hidden. A merged string made it
    worse: clause 1 rendered inline as plain text while clause 2+ went
    through st.write() inside the expander, which parsed the literal
    "2) " marker as a CommonMark ordered-list start -- so a reader saw a
    list beginning at "2." with no "1." anywhere. Splitting only on the
    marker merge_conditions() itself inserts, and rendering every clause
    the SAME way, fixes both: nothing is hidden, and a merged string's
    numbering always starts at 1 (native <ol> numbering, not the literal
    "N) " text, which is stripped before display).

    A single, long, un-numbered clause (most of them: only a merge
    produces the marker this function looks for) still renders as one
    block, full length, un-split -- see _split_merged_clauses' own
    docstring for why no weaker boundary (a raw newline, a sentence-end
    heuristic) is trusted on this data. Shared by
    _render_citation_and_conditions (one item's own conditions) and
    render_group_conditions_block (a Group clause's shared block) so this
    only has to be right in one place."""
    for note in _conditions_notes(conditions):
        st.markdown(f"<p class='eu-caption'>{_esc(note)}</p>", unsafe_allow_html=True)

    clauses = _split_merged_clauses(conditions)
    if clauses:
        items = "".join(f"<li>{_esc(clause)}</li>" for clause in clauses)
        st.markdown(f"<ol class='eu-caption'>{items}</ol>", unsafe_allow_html=True)
    else:
        st.markdown(f"<p class='eu-caption'>{_esc(conditions)}</p>", unsafe_allow_html=True)

    if note_codes:
        st.caption("Note codes: " + ", ".join(note_codes))


def _group_conditions_map(items: list[dict]) -> dict[str, dict]:
    """Normalized Group-clause conditions text -> one representative
    candidate dict carrying that exact text (the first one seen, in item
    order). Pure and Streamlit-free so it is unit-testable on its own.
    Only PURE Group clauses are included (see _pure_group_match) -- a
    merged conditions string where Group is one of several co-applicable
    clauses is deliberately excluded, so it keeps rendering in full,
    per item, and never loses a clause a per-item render would have
    shown."""
    reps: dict[str, dict] = {}
    for item in items:
        top = _primary_candidate(item)
        conditions = (top or {}).get("conditions")
        if not conditions or not _pure_group_match(conditions):
            continue
        reps.setdefault(_normalize_conditions(conditions), top)
    return reps


def render_group_conditions_block(items: list[dict]) -> dict[str, str]:
    """Renders every DISTINCT Group clause (Group I, Group II, ...) found
    across every item passed in -- whatever its Status -- since a
    category-dependent item's confirmed candidate can carry the identical
    text a plainly-permitted item's does, not just a permitted one --
    exactly once, before the additives table renders. Returns
    normalized-text -> anchor-id so render_additives_table's per-row
    detail (_render_citation_and_conditions) can replace every occurrence
    of that text with a short in-page link back here
    (`<a href="#anchor-id">`) instead of repeating the clause verbatim per
    additive (MEASURED: one clause alone covers 9,051 of 18,987 eu_fip
    rows, 47.7% of the EU's additive permissions list). Renders nothing,
    and returns {}, if no item's conditions is a pure Group clause --
    callers can call this unconditionally.

    Titled "Shared conditions", not "Group conditions": "Group I"/"Group
    II" are real EU terms that appear in the quoted law below (and are
    explained, every time, by _group_note, immediately above the clause
    that uses them) -- but the SECTION heading itself should not presume
    a reader already knows what a "Group" is before reaching that
    explanation."""
    reps = _group_conditions_map(items)
    if not reps:
        return {}

    registry = {normalized: f"group-conditions-{i + 1}" for i, normalized in enumerate(reps)}

    st.markdown("<div class='eu-section-title'>Shared conditions</div>", unsafe_allow_html=True)
    st.markdown(
        "<p class='eu-caption'>Some additives below share the exact same EU rule. It is shown once "
        "here -- look for a link back to it under any additive that uses it.</p>",
        unsafe_allow_html=True,
    )
    for normalized, top in reps.items():
        st.markdown(f"<div id='{registry[normalized]}'></div>", unsafe_allow_html=True)
        _render_conditions_body(top["conditions"], top.get("note_codes"))
        st.markdown("<div class='eu-hairline'></div>", unsafe_allow_html=True)

    return registry


def _render_citation_and_conditions(
    top: dict | None, flags: list[str], group_registry: dict[str, str] | None = None
) -> None:
    """The citation (a real link, or an explicit "no source URL" note --
    never silence), the in-force date, and the conditions -- via
    _render_conditions_body, never split except at a merge_conditions()
    clause boundary. Called from _render_additive_detail, one additive's
    expander at a time -- kept as its own function so a citation or a
    numbered-conditions fix only has to happen once.

    When `top`'s conditions is a PURE Group clause already rendered once
    by render_group_conditions_block (its anchor id is in group_registry,
    keyed by normalized text), the full text is replaced by a short link
    back to that shared block instead of repeating it here -- an
    unambiguous jump target for a reader who lands mid-page, not just
    prose saying "above". Anything else (no match, or a merged string
    where Group is only one of several clauses) renders exactly as
    before."""
    if top is not None:
        # Every source_url becomes a real link; a null one says so
        # explicitly instead of silently rendering nothing -- this is the
        # inconsistency fix: previously only the uncitable_verdict FLAG
        # (which can be set by a DIFFERENT candidate than the one shown)
        # triggered a note, so some missing citations were silent.
        if top.get("source_url"):
            st.markdown(
                f"<p class='eu-caption'><a href='{_esc(top['source_url'])}' target='_blank'>Source</a></p>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                "<p class='eu-caption'>No source URL on this record.</p>", unsafe_allow_html=True
            )
        # "in force since", not "retrieved" -- this date is eu_fip's
        # effective_date (when the provision entered force), never when
        # the data was fetched. This project tracks no real retrieval date
        # for eu_fip, so that second line is omitted rather than faked.
        if top.get("retrieved_date"):
            st.markdown(
                f"<p class='eu-caption'>In force since {_esc(top['retrieved_date'])}.</p>",
                unsafe_allow_html=True,
            )
    elif "uncitable_verdict" in flags:
        st.markdown(
            "<p class='eu-caption'>No citable source is recorded for this verdict.</p>",
            unsafe_allow_html=True,
        )

    if top and top.get("conditions"):
        conditions = top["conditions"]
        anchor = (group_registry or {}).get(_normalize_conditions(conditions))
        if anchor:
            group_match = _pure_group_match(conditions)
            group_name = group_match.group(1) if group_match else "Group"
            st.markdown(
                f"<p class='eu-caption'>Permitted via {_esc(group_name)} — see the "
                f"<a href='#{anchor}'>{_esc(group_name)} conditions above</a>.</p>",
                unsafe_allow_html=True,
            )
        else:
            _render_conditions_body(conditions, top.get("note_codes"))


# Worst-first: a reader should see what needs a decision before what
# doesn't. Any status not in this map (should not happen -- _status_label
# only ever returns one of these four) sorts last, not first, so a bug in
# _status_label fails safe (buried, not falsely prioritised) rather than
# crashing the table.
_STATUS_SEVERITY = {
    "Not allowed in the EU": 0,
    "Not allowed in this food": 1,
    "Allowed with limits": 2,
    "Allowed": 3,
}


def _additive_row(item: dict, names: dict[int, str] | None) -> dict:
    """One item's plain row content for render_additives_table --
    Additive/Status/Maximum amount/In force since/Where -- computed from
    the SAME primary-candidate selection (_primary_candidate) every other
    per-item render already uses: the confirmed candidate wins, else the
    first retrieved. `top` is None only for a headline-only block
    (not_authorised_eu with no by_category at all -- absence/prohibition
    is jurisdiction-wide, not category-dependent, so there is no category
    row to look anything up against)."""
    top = _primary_candidate(item)
    display_name = _display_name(item, names)
    eu_id = item.get("eu_canonical_id")
    additive = f"{display_name} (E{eu_id})" if eu_id else display_name

    if top is None:
        status, bucket = "Not allowed in the EU", "blocked"
        max_amount, in_force, where = "—", "—", "—"
    else:
        status, bucket = _status_label(top["verdict"], top.get("max_level_mg_kg"))
        max_amount = _max_amount_cell(top["verdict"], top.get("max_level_mg_kg"))
        in_force = top.get("retrieved_date") or "—"
        where = _category_display(top.get("fcs_code"), top.get("category_name"))
        component = item.get("component_label")
        if component:
            where = f"{component}: {where}"

    return {
        "item": item,
        "top": top,
        "display_name": display_name,
        "additive": additive,
        "status": status,
        "bucket": bucket,
        "max_amount": max_amount,
        "in_force": in_force,
        "where": where,
    }


def _detail_lead_line(row: dict) -> str:
    """The plain-English sentence above the conditions text in a row's
    expander -- what the status means for THIS product, in one sentence,
    never a reword of the LAW itself (that stays verbatim, immediately
    below -- see _render_citation_and_conditions). Deterministic
    templates, not a model summary: this project has measured three
    times elsewhere (src/report/narrator.py) that a prompt is a request,
    not a guarantee, and getting this ONE sentence wrong (claiming a
    condition says something it doesn't) is worse than a slightly
    generic-but-always-true one."""
    top = row["top"]
    name = row["display_name"]
    if top is None or top["verdict"] == "not_authorised_eu":
        return f"{name} is {_blocking_reason(row['item'])}."
    if top["verdict"] == "not_permitted_in_category":
        return f"{name} is not permitted for use in {_category_display(top.get('fcs_code'), top.get('category_name'))}."
    where = _category_display(top.get("fcs_code"), top.get("category_name"))
    if top.get("conditions"):
        return (
            f"{name} is allowed in {where}, subject to the condition below — read it before using "
            "this ingredient here."
        )
    return f"{name} is allowed in {where}."


def _render_additive_detail(row: dict, group_registry: dict[str, str] | None) -> None:
    """Everything that doesn't fit a table cell: the plain-English lead
    line, which part of the product this depends on (only when
    candidates genuinely diverge -- _diverging_candidates), and the
    citation/in-force-date/conditions text verbatim
    (_render_citation_and_conditions, unchanged machinery)."""
    item = row["item"]
    st.markdown(f"<p class='eu-caption'>{_esc(_detail_lead_line(row))}</p>", unsafe_allow_html=True)

    diverging = _diverging_candidates(item)
    if diverging:
        confirmed_code = item.get("confirmed_fcs_code")
        st.markdown(
            "<p class='eu-caption'>This depends on which part of the product it applies to:</p>",
            unsafe_allow_html=True,
        )
        lines = []
        for cv in diverging:
            status, _bucket = _status_label(cv["verdict"], cv.get("max_level_mg_kg"))
            where = _category_display(cv["fcs_code"], cv.get("category_name"))
            tag = " — confirmed" if cv["fcs_code"] == confirmed_code else ""
            lines.append(f"<li>{_esc(status)} in {_esc(where)}{tag}.</li>")
        st.markdown(f"<ul class='eu-caption'>{''.join(lines)}</ul>", unsafe_allow_html=True)

    _render_citation_and_conditions(row["top"], item.get("flags") or [], group_registry)


def render_additives_table(
    items: list[dict], names: dict[int, str] | None = None, group_registry: dict[str, str] | None = None
) -> None:
    """The primary results view, one row per additive: Additive / Status /
    Maximum amount / In force since / Where -- worst-first
    (_STATUS_SEVERITY), so a reader sees what needs a decision before
    what doesn't. Replaces the old Blocking/Category-dependent/Permitted
    sections and their colour-block strip: those three headline-derived
    buckets are still exactly what determines a row's Status text, just
    read off one plain column instead of three differently-labelled
    sections a reader had to already understand the difference between.

    Each row expands (one st.expander per row, directly below the table
    -- the same pattern render_substitutes already uses for per-candidate
    detail, since an HTML <table> cannot host a Streamlit widget inside a
    <tr>) to show what does not fit a cell -- see _render_additive_detail.

    No same-block grouping (contrast the old render_permitted_section):
    "one row per additive" is now literal, and the repetition cost that
    grouping existed to avoid is gone anyway now that conditions text is
    collapsed behind a closed expander by default, not printed inline
    for every additive that shares it."""
    if not items:
        return
    rows = sorted(
        (_additive_row(item, names) for item in items),
        key=lambda r: _STATUS_SEVERITY.get(r["status"], 99),
    )
    st.markdown(f"<div class='eu-section-title'>Additives ({len(rows)})</div>", unsafe_allow_html=True)
    table_rows = "".join(
        "<tr>"
        f"<td>{_esc(r['additive'])}</td>"
        f"<td><span class='eu-badge {r['bucket']}'>{_esc(r['status'])}</span></td>"
        f"<td class='eu-code'>{_esc(r['max_amount'])}</td>"
        f"<td class='eu-code'>{_esc(r['in_force'])}</td>"
        f"<td>{_esc(r['where'])}</td>"
        "</tr>"
        for r in rows
    )
    st.markdown(
        "<table class='eu-table'><thead><tr>"
        "<th>Additive</th><th>Status</th><th>Maximum amount</th><th>In force since</th><th>Where</th>"
        f"</tr></thead><tbody>{table_rows}</tbody></table>",
        unsafe_allow_html=True,
    )

    for r in rows:
        with st.expander(r["additive"]):
            _render_additive_detail(r, group_registry)


def render_out_of_scope(items: list[dict], names: dict[int, str] | None = None) -> None:
    """Flavourings, enzymes, ordinary food ingredients -- items never
    checked against the EU's additive permissions at all. Collapsed by
    default (there is nothing actionable here), but present and labelled
    with why, so a reader can tell "assessed and cleared" from "never
    assessed" -- an item missing from every other section is not the same
    as a permitted one. Two columns, Ingredient/Why, regulation numbers
    dropped from this default view (_out_of_scope_reason/_OUT_OF_SCOPE_
    REASONS) -- the real citation stays on the item's own
    governing_regulation flag in the JSON export."""
    if not items:
        return
    with st.expander(f"Out of scope ({len(items)})", expanded=False):
        st.caption("These aren't food additives, so this checklist doesn't apply to them.")
        rows = "".join(
            f"<tr><td>{_esc(_display_name(item, names))}</td><td>{_esc(_out_of_scope_reason(item))}</td></tr>"
            for item in items
        )
        st.markdown(
            "<table class='eu-table'><thead><tr><th>Ingredient</th><th>Why</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>",
            unsafe_allow_html=True,
        )


def render_review_queue(
    items: list[dict], names: dict[int, str] | None = None, ins_names: dict[str, str] | None = None
) -> None:
    """Unresolved and ambiguous items -- work for a human to complete, not
    an error state. `ins_names` (INS code -> real name, built by app.py
    off refs.codex_ins) is threaded through to candidates_phrase so a
    "candidate:" flag never shows a bare code -- see that function's own
    docstring."""
    if not items:
        return
    st.markdown(f"<div class='eu-section-title'>Review queue ({len(items)})</div>", unsafe_allow_html=True)
    st.markdown(
        "<p class='eu-section-note'>These items need a human decision to complete the audit -- "
        "either the substance could not be identified, or no food category could be determined "
        "for it.</p>",
        unsafe_allow_html=True,
    )
    for item in items:
        display_name = _display_name(item, names)
        reason = "no food category available" if item.get("headline") == "category_unknown" else "not identified"
        candidates = [f.split(":", 1)[1].strip() for f in (item.get("flags") or []) if f.startswith("candidate:")]
        st.markdown(f"**{_esc(display_name)}** <span class='eu-badge warn'>{_esc(reason)}</span>", unsafe_allow_html=True)
        if candidates:
            st.caption("Possible matches: " + candidates_phrase(candidates, ins_names))
        else:
            st.caption("No candidate match found -- needs manual identification.")
        st.markdown("<div class='eu-hairline'></div>", unsafe_allow_html=True)


def render_substitutes(result: dict) -> None:
    """Substitute candidates for every blocked item. Renders nothing when
    there are no suggestions -- the common case when nothing is blocked."""
    suggestions = result.get("suggestions") or []
    if not suggestions:
        return

    st.markdown("<div class='eu-section-title'>Substitutes</div>", unsafe_allow_html=True)
    st.markdown(
        "<p class='eu-section-note'>Candidates require real formulation validation. "
        "They are not recommendations.</p>",
        unsafe_allow_html=True,
    )
    for suggestion in suggestions:
        name = suggestion.get("blocked_name") or suggestion.get("blocked_eu_canonical_id") or f"item {suggestion['item_id']}"
        st.markdown(f"**{_esc(name)}** blocked ({_esc(suggestion.get('blocked_reason'))})")
        candidates = suggestion.get("candidates") or []
        if not candidates:
            st.caption(suggestion.get("no_candidates_reason") or "No candidates found.")
            st.markdown("<div class='eu-hairline'></div>", unsafe_allow_html=True)
            continue

        rows = []
        for c in candidates:
            # Same Maximum-amount wording as the additives table -- never
            # "quantum satis" as a bare term (see _max_amount_cell).
            level = _max_amount_cell(c["verdict"], c.get("max_level_mg_kg"))
            flags_display = ", ".join(_substitute_flag_label(f) for f in c.get("flags") or []) or "—"
            rows.append(
                "<tr>"
                f"<td class='eu-code'>E{_esc(c['eu_canonical_id'])}</td>"
                f"<td>{_esc(c.get('additive_name'))}</td>"
                f"<td>{_esc(', '.join(c.get('shared_functional_classes') or []))}</td>"
                f"<td>{_esc(_status_label(c['verdict'], c.get('max_level_mg_kg'))[0])}</td>"
                f"<td class='eu-code'>{_esc(level)}</td>"
                f"<td>{_esc(flags_display)}</td>"
                "</tr>"
            )
        table = (
            "<table class='eu-table'><thead><tr>"
            "<th>EU id</th><th>Name</th><th>Shared functional class</th>"
            "<th>Status</th><th>Maximum amount</th><th>Flags</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        )
        st.markdown(table, unsafe_allow_html=True)

        # Per-candidate conditions/source -- previously invisible entirely.
        # One expander per candidate that actually has something to show.
        for c in candidates:
            if not c.get("conditions") and not c.get("source_url"):
                continue
            label = c.get("additive_name") or f"E{c.get('eu_canonical_id')}"
            with st.expander(f"Conditions — {label}"):
                for note in _conditions_notes(c.get("conditions")):
                    st.caption(note)
                if c.get("conditions"):
                    st.write(c["conditions"])
                if c.get("source_url"):
                    st.markdown(
                        f"<a href='{_esc(c['source_url'])}' target='_blank'>Source</a>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.caption("No source URL on this record.")

        st.markdown("<div class='eu-hairline'></div>", unsafe_allow_html=True)

    warnings = result.get("warnings") or []
    if warnings:
        # Developer-diagnostic lines ("X excluded -- functional class
        # unknown") read as noise inline; collapsed, with the reason
        # spelled out once instead of repeated per line.
        label = f"{len(warnings)} additive{'s' if len(warnings) != 1 else ''} could not be assessed as substitutes"
        with st.expander(label):
            st.caption(
                "Their technological function is not recorded in the Codex INS list, so we "
                "cannot confirm they serve the same purpose as the blocked additive."
            )
            for warning in warnings:
                st.caption(warning)


_NEWS_CATEGORY_LABELS = {
    "regulatory_review": "Under regulatory review",
    "safety_opinion": "Safety opinion",
    "market_action": "Market or import action",
    "consumer_alert": "Consumer alert",
}

_ROUTE_NEWS_CATEGORY_LABELS = {
    "import_control": "EU import control",
    "border_rejection": "Rejected at the EU border",
    "trade_agreement": "Trade agreement development",
    "consumer_alert": "EU consumer alert",
}


def _order_news_signals(news_signals: list[dict]) -> list[dict]:
    """`news_signals`, grouped by additive (first-appearance order
    preserved) with each additive's own signals sorted newest
    published_date first (missing dates last). MEASURED: Pepsimain's E950
    got a "safety_opinion" signal (quoting a completed re-evaluation
    that concludes it is safe) and a "regulatory_review" signal, in
    whatever order src.horizon.news.find_news_signals happened to return
    them -- nothing told the reader which was current. This does not
    resolve a genuinely contradictory category/quote pair (see
    src.horizon.news.check_category_consistency for that, a refuse-to-
    build check upstream of this ever reaching the UI); it only orders
    what legitimately reaches here so recency is visible without the
    reader having to compare dates buried in each card."""
    groups: dict[tuple, list[dict]] = {}
    for signal in news_signals:
        key = (signal.get("eu_canonical_id"), signal.get("substance_name"))
        groups.setdefault(key, []).append(signal)
    ordered: list[dict] = []
    for group in groups.values():
        group.sort(key=lambda s: s.get("published_date") or "", reverse=True)
        ordered.extend(group)
    return ordered


def _render_news_signals(news_signals: list[dict]) -> None:
    """Retrieved-and-classified news items, below the EFSA table in the
    SAME "Regulatory horizon" section -- but visually its own block, not
    folded into the table above, because it is a genuinely different KIND
    of source (src/horizon/schemas.py's NewsSignal docstring: secondary
    reporting, machine-retrieved and machine-classified against a
    verbatim-quote check, never a hand-curated primary regulatory
    document). The provenance line names that plainly, every time, not
    just in an expander -- a reader should not have to click to learn
    this is web search, not a person. Renders nothing when there is
    nothing to show; this is additional coverage, never a required
    section the way the EFSA table's "no signals" caption is."""
    if not news_signals:
        return

    st.markdown(
        "<p class='eu-caption'><strong>From retrieved news</strong> — automatically retrieved from "
        "web search, not vetted by a person. Read the source before acting on any of these.</p>",
        unsafe_allow_html=True,
    )
    for signal in _order_news_signals(news_signals):
        category = signal.get("category")
        category_label = _NEWS_CATEGORY_LABELS.get(category, category or "")
        st.markdown(
            f"<span class='eu-badge'>{_esc(category_label)}</span> "
            f"<span class='eu-code'>E{_esc(signal.get('eu_canonical_id'))}</span> "
            f"{_esc(signal.get('substance_name'))}",
            unsafe_allow_html=True,
        )
        st.markdown(f"<p class='eu-caption'>“{_esc(signal.get('quoted_span'))}”</p>", unsafe_allow_html=True)

        source_url = signal.get("source_url")
        source_line = (
            f"<a href='{_esc(source_url)}' target='_blank'>Source</a>"
            if source_url
            else "No source URL on this record."
        )
        published_date = signal.get("published_date")
        date_part = f" · {_esc(published_date)}" if published_date else ""
        st.markdown(f"<p class='eu-caption'>{source_line}{date_part}</p>", unsafe_allow_html=True)

        # A quality issue carried from src/horizon/search.py's
        # validate_search_result or src/horizon/news_cache.py's stale-
        # serve path is named here, not silently dropped -- same
        # discipline the EFSA table's own "doi_unverified" badge follows.
        flags = signal.get("flags") or []
        if flags:
            st.markdown(
                f"<p class='eu-caption'><span class='eu-badge warn'>{_esc(', '.join(flags))}</span></p>",
                unsafe_allow_html=True,
            )
        st.markdown("<div class='eu-hairline'></div>", unsafe_allow_html=True)


def _render_route_news(route_news_signals: list[dict], route_news_category: str | None) -> None:
    """India -> EU trade/import news about this product's food CATEGORY,
    never about one specific additive -- a SEPARATE section from
    _render_news_signals (additive-scoped), with its OWN section title,
    so a route headline ("EU tightens checks on Indian spice imports")
    can never be mistaken for something about one substance: nothing here
    ever carries an eu_canonical_id (see RouteNewsSignal's own docstring
    for why it structurally cannot). Same provenance discipline as the
    additive lane -- stated plainly, not just in an expander.

    Unlike _render_news_signals (which has nothing worth saying about an
    additive with zero signals -- rendering nothing at all), this section
    is ALWAYS shown when the lane ran at all (route_news_category is not
    None): an explicit "nothing found" line, naming what was searched, is
    a more useful answer than an absent section -- a reader should not
    have to wonder whether the route lane ran and found nothing, or never
    ran at all. Renders nothing only when route_news_category is None --
    the lane never ran this screening (no search provider configured, or
    a HorizonResult built without ever touching the graph's news_node at
    all, e.g. app.py's legacy non-graph fallback path)."""
    if route_news_category is None:
        return

    st.markdown("<div class='eu-section-title'>Import route: India → EU</div>", unsafe_allow_html=True)
    st.markdown(
        "<p class='eu-caption'>About exporting this product's food category from India to the EU -- "
        "never about one specific additive. Automatically retrieved from web search, not vetted by a "
        "person. Read the source before acting on any of these.</p>",
        unsafe_allow_html=True,
    )

    if not route_news_signals:
        st.caption(f"No recent EU news found about {route_news_category} exports from India in this category.")
        return

    for signal in route_news_signals:
        category = signal.get("category")
        category_label = _ROUTE_NEWS_CATEGORY_LABELS.get(category, category or "")
        st.markdown(
            f"<span class='eu-badge'>{_esc(category_label)}</span> "
            f"{_esc(signal.get('origin'))} {_esc(signal.get('category_name'))} exports",
            unsafe_allow_html=True,
        )
        st.markdown(f"<p class='eu-caption'>“{_esc(signal.get('quoted_span'))}”</p>", unsafe_allow_html=True)

        source_url = signal.get("source_url")
        source_line = (
            f"<a href='{_esc(source_url)}' target='_blank'>Source</a>"
            if source_url
            else "No source URL on this record."
        )
        published_date = signal.get("published_date")
        date_part = f" · {_esc(published_date)}" if published_date else ""
        st.markdown(f"<p class='eu-caption'>{source_line}{date_part}</p>", unsafe_allow_html=True)

        # A quality issue carried from src/horizon/search.py's
        # validate_search_result or src/horizon/news_cache.py's stale-
        # serve path -- same discipline _render_news_signals follows.
        flags = signal.get("flags") or []
        if flags:
            st.markdown(
                f"<p class='eu-caption'><span class='eu-badge warn'>{_esc(', '.join(flags))}</span></p>",
                unsafe_allow_html=True,
            )
        st.markdown("<div class='eu-hairline'></div>", unsafe_allow_html=True)


def render_horizon(result: dict) -> None:
    """Regulatory-horizon signals. The coverage caveat (see docs/findings.md
    F-12) is never DROPPED -- silently making a partial dataset read as
    comprehensive is exactly the bug F-12 covers -- but it no longer prints
    unconditionally: three paragraphs explaining the limits of an empty
    result is worse than the empty result. Both caveats live in "About
    these signals", one line short of always-visible; the empty-signals
    case gets a single line that points there. news_signals (a DIFFERENT
    source -- retrieved, not curated; see _render_news_signals) renders
    below the EFSA table, never merged into it. route_news_signals
    (India -> EU, whole-category -- see _render_route_news) renders as
    its OWN, separately-titled section after that, never merged into
    either."""
    st.markdown("<div class='eu-section-title'>Regulatory horizon</div>", unsafe_allow_html=True)

    signals = result.get("signals") or []
    warnings = result.get("warnings") or []

    with st.expander("About these signals"):
        st.markdown(
            "<p class='eu-section-note'>Advisory only. These signals never change the compliance "
            "verdict above -- an EFSA opinion is not law. Additives can be re-assessed by EFSA years "
            "before the law changes; these are early signals, not current requirements.</p>",
            unsafe_allow_html=True,
        )
        for warning in warnings:
            st.markdown(f"<p class='eu-caption'>{_esc(warning)}</p>", unsafe_allow_html=True)

    if not signals:
        st.caption("No EFSA review signals for these additives. Coverage is partial — see the report notes.")
    else:
        rows = []
        for signal in signals:
            doi = signal.get("doi")
            if "doi_unverified" in (signal.get("flags") or []):
                doi_cell = f"{_esc(doi) or '—'} <span class='eu-badge warn'>unverified</span>"
            else:
                doi_cell = _esc(doi) or "—"
            rows.append(
                "<tr>"
                f"<td class='eu-code'>E{_esc(signal['eu_canonical_id'])}</td>"
                f"<td>{_esc(signal.get('substance_name'))}</td>"
                f"<td>{_esc(signal.get('stage'))}</td>"
                f"<td class='eu-code'>{_esc(signal.get('publication_date'))}</td>"
                f"<td>{doi_cell}</td>"
                "</tr>"
            )
        table = (
            "<table class='eu-table'><thead><tr>"
            "<th>EU id</th><th>Substance</th><th>Stage</th><th>Year</th><th>DOI</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        )
        st.markdown(table, unsafe_allow_html=True)
        n_unverified = sum(1 for s in signals if "doi_unverified" in (s.get("flags") or []))
        if n_unverified:
            st.markdown(
                f"<p class='eu-caption'>{n_unverified} signal(s) have a citation that has not "
                "been verified against efsa.europa.eu -- treat as unconfirmed.</p>",
                unsafe_allow_html=True,
            )

    _render_news_signals(result.get("news_signals") or [])
    _render_route_news(result.get("route_news_signals") or [], result.get("route_news_category"))
