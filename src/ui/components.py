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
"""Render functions for the compliance-checker UI: the verdict strip (the
signature element -- see app.py's module docstring), the count strip,
verdict sections, the out-of-scope and review-queue lists, and the
substitutes/horizon panels."""

import html
import re

import streamlit as st

# verdict string -> (plain-English label, colour bucket). The internal
# verdict strings themselves are UNCHANGED -- they are what the JSON export
# writes (verdict.model_dump()) and what every other module compares
# against; only this display mapping changed. Never "banned" -- the EU
# verdict is "not authorised as a food additive in the EU" (src/report/
# narrator.py:65 hardcodes this same rule for the LLM narration, and
# src/report/export.py's own _VERDICT_LABELS follows it too -- "Not
# allowed in the EU" here previously contradicted both).
#
# FOLLOW-UP, not done here: this is the THIRD independent verdict-string ->
# label mapping in the codebase (src/report/export.py has its own, and
# narrator.py hardcodes the not_authorised_eu phrase in its prompt) -- the
# three are free to drift, exactly as this one just did. Centralizing them
# into one shared mapping is out of scope for this fix.
_VERDICT_BUCKETS: dict[str, str] = {
    "permitted_qs": "permitted",
    "permitted_with_limit": "permitted",
    "permitted_with_conditions": "permitted",
    "not_permitted_in_category": "blocked",
    "not_authorised_eu": "blocked",
}
_VERDICT_LABELS: dict[str, str] = {
    "permitted_qs": "Allowed — no fixed limit",
    "permitted_with_conditions": "Allowed — conditions to check",
    "not_permitted_in_category": "Not allowed in this kind of food",
    "not_authorised_eu": "Not authorised in the EU",
}


def _level_phrase(max_level_mg_kg: float | None) -> str:
    if max_level_mg_kg is None:
        return "no fixed limit"
    level = int(max_level_mg_kg) if max_level_mg_kg == int(max_level_mg_kg) else max_level_mg_kg
    return f"up to {level} mg/kg"


def _verdict_label(verdict: str, max_level_mg_kg: float | None = None) -> tuple[str, str]:
    bucket = _VERDICT_BUCKETS.get(verdict, "neutral")
    if verdict == "permitted_with_limit":
        return f"Allowed — {_level_phrase(max_level_mg_kg)}", bucket
    return _VERDICT_LABELS.get(verdict, verdict.replace("_", " ")), bucket


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

# governing_regulation value (src/rules/engine.py's _OUT_OF_SCOPE_REGULATION) -> the
# plain-language reason shown next to an out-of-scope item.
_OUT_OF_SCOPE_REASONS: dict[str, str] = {
    "Reg 1334/2008": "flavouring — regulated under Reg 1334/2008, not the additives regulation",
    "Reg 1332/2008": "enzyme — Reg 1332/2008",
    "not an additive": "food ingredient — not an additive",
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
    return "not an additive"


def _substitute_flag_label(flag: str) -> str:
    return _SUBSTITUTE_FLAG_LABELS.get(flag, flag)


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


def verdict_strip_html(item: dict, display_name: str) -> str:
    """The signature element: one horizontal band per additive.

    MEASURED PROBLEM this fixes: once a category is confirmed, the old
    version only ever received a single candidate, so the strip could never
    show why the choice mattered (e.g. Chipsmain's E551 -- permitted under
    the confirmed 12.2.2, not permitted under 15.1). app.py now merges the
    PRE-confirmation candidates back onto each item (see
    _merge_preview_candidates) and marks which one was confirmed via
    item["confirmed_fcs_code"]; this function renders all of them, full
    colour and outlined for the confirmed one, dimmed for the rest -- and
    still collapses to one quiet block with no divergence line whenever
    every candidate's verdict actually agrees, confirmed or not.

    DESIGN FLAW this also fixes: retrieval can return an ANCESTOR of the
    real answer alongside it -- Khusmain's candidates were "14.1.4", "14.1"
    and "14", where the latter two are not alternatives, they CONTAIN
    14.1.4. eu_fip records permissions at the leaf, so a parent code always
    renders "not permitted in this category" -- true, meaningless, and it
    falsely claims a divergence. Ancestors of the confirmed (or, if
    unconfirmed, the rank-1) candidate are rendered separately, unlabelled
    with any verdict, and never count toward "verdict depends on category".
    """
    by_category = item.get("by_category") or []
    eu_id = item.get("eu_canonical_id")
    code_html = f"<span class='eu-code'>E{_esc(eu_id)}</span>" if eu_id else ""
    component = item.get("component_label")
    component_html = f"<span class='eu-strip-component'>in {_esc(component)}</span>" if component else ""
    head = (
        f"<div class='eu-strip-head'>{code_html}"
        f"<span class='eu-strip-name'>{_esc(display_name)}</span>{component_html}</div>"
    )

    if not by_category:
        return f"<div class='eu-strip'>{head}</div>"

    # Dedup by fcs_code (the confirmed candidate may also appear among the
    # pre-confirmation ones), first occurrence wins.
    deduped: dict[str, dict] = {}
    for cv in by_category:
        deduped.setdefault(cv["fcs_code"], cv)
    all_candidates = list(deduped.values())

    confirmed_code = item.get("confirmed_fcs_code")
    reference_code = confirmed_code or all_candidates[0]["fcs_code"]

    # Parents of the reference candidate are not competing verdicts --
    # split them out before any divergence logic sees them.
    ancestors = [cv for cv in all_candidates if _is_ancestor_code(cv["fcs_code"], reference_code)]
    candidates = [cv for cv in all_candidates if cv not in ancestors]

    distinct_verdicts = {cv["verdict"] for cv in candidates}

    if len(candidates) == 1 or len(distinct_verdicts) <= 1:
        # All (non-ancestor) candidates agree, or there is only one -- one
        # quiet block, no divergence line, regardless of confirmation.
        primary = next((cv for cv in candidates if cv["fcs_code"] == confirmed_code), candidates[0])
        shown = [primary]
    else:
        # Confirmed candidate first (stable sort keeps the rest in their
        # original retrieval-rank order).
        shown = sorted(candidates, key=lambda cv: cv["fcs_code"] != confirmed_code)

    blocks = []
    for cv in shown:
        label, bucket = _verdict_label(cv["verdict"], cv.get("max_level_mg_kg"))
        is_confirmed = confirmed_code is not None and cv["fcs_code"] == confirmed_code
        # Only dim when there IS a confirmed candidate to contrast against
        # -- an unconfirmed multi-candidate strip shows every block at full
        # weight, since none of them is "the" answer yet.
        dimmed = len(shown) > 1 and confirmed_code is not None and not is_confirmed
        code_part = f"<span class='eu-code'>{_esc(cv['fcs_code'])}</span>"
        if cv.get("category_name"):
            code_part += f" <span class='eu-block-category'>{_esc(_short_category_name(cv['category_name']))}</span>"
        tag = "<span class='eu-block-confirmed-tag'>confirmed</span>" if is_confirmed else ""
        attrs = f"data-bucket='{bucket}' data-confirmed='{str(is_confirmed).lower()}'"
        if dimmed:
            attrs += " data-dimmed='true'"
        blocks.append(
            f"<div class='eu-block' {attrs}>{code_part}<span class='eu-block-verdict'>{_esc(label)}</span>{tag}</div>"
        )

    for cv in ancestors:
        blocks.append(
            f"<div class='eu-block' data-bucket='neutral' data-confirmed='false' data-dimmed='true'>"
            f"<span class='eu-code'>{_esc(cv['fcs_code'])}</span>"
            f"<span class='eu-block-verdict'>parent category</span></div>"
        )

    blocks_html = f"<div class='eu-strip-blocks'>{''.join(blocks)}</div>"

    divergence = ""
    if len(shown) > 1:
        divergence = "<div class='eu-divergence'>verdict depends on category</div>"

    return f"<div class='eu-strip'>{head}{blocks_html}{divergence}</div>"


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
    handled by the caller: one caption line per applicable note, first
    line inline, the rest (if any) behind an expander. Shared by
    _render_citation_and_conditions (one item's own conditions) and
    render_group_conditions_block (a Group clause's shared block) so the
    "first line inline, rest in an expander" presentation only has to be
    right in one place."""
    for note in _conditions_notes(conditions):
        st.markdown(f"<p class='eu-caption'>{_esc(note)}</p>", unsafe_allow_html=True)

    first_line, _, rest = conditions.partition("\n")
    st.markdown(f"<p class='eu-caption'>{_esc(first_line)}</p>", unsafe_allow_html=True)
    rest = rest.strip()
    if rest or note_codes:
        with st.expander("Conditions of use"):
            if rest:
                st.write(rest)
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
    across every item passed in -- Blocking, Category-dependent, and
    Permitted alike, since a Category-dependent item's confirmed candidate
    can carry the identical text a Permitted item's does, not just a
    Permitted one -- exactly once, before any section renders. Returns
    normalized-text -> anchor-id so render_verdict_section and
    render_permitted_section can replace every occurrence of that text
    with a short in-page link back here (`<a href="#anchor-id">`) instead
    of repeating the clause verbatim per additive (MEASURED: one clause
    alone covers 9,051 of 18,987 eu_fip rows, 47.7% of Annex II). Renders
    nothing, and returns {}, if no item's conditions is a pure Group
    clause -- callers can call this unconditionally."""
    reps = _group_conditions_map(items)
    if not reps:
        return {}

    registry = {normalized: f"group-conditions-{i + 1}" for i, normalized in enumerate(reps)}

    st.markdown("<div class='eu-section-title'>Group conditions</div>", unsafe_allow_html=True)
    st.markdown(
        "<p class='eu-caption'>The blocks below are set once, in EU law, for an entire Group of "
        "additives -- shown once here; each additive that carries one links back to it instead of "
        "repeating it.</p>",
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
    never silence), the in-force date, and the conditions -- first line
    inline, the rest behind an expander labelled what it is. Shared by
    render_verdict_row (one additive) and _render_permitted_group (several
    additives that share this exact block) -- so a citation or a numbered-
    conditions fix only has to happen once.

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


def render_verdict_row(
    item: dict,
    names: dict[int, str] | None = None,
    *,
    is_blocking: bool = False,
    group_registry: dict[str, str] | None = None,
) -> None:
    """One additive's full row: the verdict strip, WHY it blocks (blocking
    section only), its citation, in-force date, and conditions."""
    display_name = _display_name(item, names)
    st.markdown(verdict_strip_html(item, display_name), unsafe_allow_html=True)

    if is_blocking:
        st.markdown(f"<p class='eu-caption'>{_esc(_blocking_reason(item))}</p>", unsafe_allow_html=True)

    _render_citation_and_conditions(_primary_candidate(item), item.get("flags") or [], group_registry)
    st.markdown("<div class='eu-hairline'></div>", unsafe_allow_html=True)


def render_verdict_section(
    title: str,
    items: list[dict],
    names: dict[int, str] | None = None,
    *,
    is_blocking: bool = False,
    group_registry: dict[str, str] | None = None,
) -> None:
    """A titled group of verdict rows -- BLOCKING or CATEGORY-DEPENDENT.
    Renders nothing when `items` is empty, so callers can invoke every
    section unconditionally. PERMITTED items go through
    render_permitted_section instead (see its docstring for why).
    `group_registry` (see render_group_conditions_block) is only ever
    passed for Category-dependent items in practice -- a Blocking item's
    primary candidate is itself the blocking verdict, never a Group
    permission -- but it is honoured here regardless, since nothing about
    this function's own logic depends on that being true."""
    if not items:
        return
    st.markdown(f"<div class='eu-section-title'>{_esc(title)} ({len(items)})</div>", unsafe_allow_html=True)
    for item in items:
        render_verdict_row(item, names, is_blocking=is_blocking, group_registry=group_registry)


def _permitted_group_key(item: dict) -> tuple:
    """Items in the Permitted section never diverge by candidate category
    (a divergent item is routed to Category-dependent instead), so the
    primary candidate's own content -- category, verdict, level, source,
    conditions -- fully determines what renders. Two items with an
    identical key render the SAME block, so they are grouped instead of
    repeating it verbatim once per additive (MEASURED: EU_productmain's
    Group I explanation + conditions repeated 4x, word for word)."""
    top = _primary_candidate(item)
    if top is None:
        return ("item", item.get("item_id"))
    return (
        item.get("component_label"),
        top.get("fcs_code"),
        top.get("category_name"),
        top.get("verdict"),
        top.get("max_level_mg_kg"),
        top.get("max_level_basis"),
        top.get("conditions"),
        top.get("source_url"),
        top.get("retrieved_date"),
        tuple(top.get("note_codes") or []),
    )


def _render_permitted_group(
    group: list[dict], names: dict[int, str] | None, group_registry: dict[str, str] | None = None
) -> None:
    top = _primary_candidate(group[0])
    label, bucket = (_verdict_label(top["verdict"], top.get("max_level_mg_kg")) if top else ("Allowed", "permitted"))
    group_note_match = _GROUP_CONDITIONS_RE.match((top or {}).get("conditions") or "")
    if group_note_match:
        level_phrase = _level_phrase((top or {}).get("max_level_mg_kg"))
        label = f"Allowed as part of {group_note_match.group(1)} — {level_phrase}"

    names_html = " · ".join(
        (f"<span class='eu-code'>E{_esc(item['eu_canonical_id'])}</span> " if item.get("eu_canonical_id") else "")
        + _esc(_display_name(item, names))
        for item in group
    )
    where = _category_display(top.get("fcs_code"), top.get("category_name")) if top else ""
    component = group[0].get("component_label")
    where_html = f" in {_esc(component)} ({_esc(where)})" if component and where else (f" in {_esc(where)}" if where else "")

    st.markdown(
        f"<div class='eu-strip-head'><span class='eu-strip-name'>{names_html}</span></div>"
        f"<div class='eu-strip-blocks'><div class='eu-block' data-bucket='{bucket}'>"
        f"<span class='eu-block-verdict'>{_esc(label)}</span></div></div>"
        + (f"<p class='eu-caption'>Applies{where_html}</p>" if where_html else ""),
        unsafe_allow_html=True,
    )
    _render_citation_and_conditions(top, [], group_registry)
    st.markdown("<div class='eu-hairline'></div>", unsafe_allow_html=True)


def render_permitted_section(
    title: str,
    items: list[dict],
    names: dict[int, str] | None = None,
    group_registry: dict[str, str] | None = None,
) -> None:
    """Like render_verdict_section, but items sharing the exact same block
    (see _permitted_group_key) render ONCE, with every additive that
    shares it listed together, instead of repeating the same explanation
    and conditions text once per additive. `group_registry` (see
    render_group_conditions_block) handles the ORTHOGONAL case this
    doesn't: the exact same Group clause appearing under a DIFFERENT
    category or component within this section (a different
    _permitted_group_key, since category/component differ, but the same
    conditions text) -- those groups still render their own head/verdict
    block, but the conditions themselves collapse to a shared-block
    reference."""
    if not items:
        return
    st.markdown(f"<div class='eu-section-title'>{_esc(title)} ({len(items)})</div>", unsafe_allow_html=True)

    groups: dict[tuple, list[dict]] = {}
    order: list[tuple] = []
    for item in items:
        key = _permitted_group_key(item)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)

    for key in order:
        group = groups[key]
        if len(group) == 1:
            render_verdict_row(group[0], names, group_registry=group_registry)
        else:
            _render_permitted_group(group, names, group_registry=group_registry)


def render_out_of_scope(items: list[dict], names: dict[int, str] | None = None) -> None:
    """Flavourings, enzymes, food ingredients -- items never assessed
    against Annex II at all. Collapsed by default (there is nothing
    actionable here), but present and labelled with why, so a reader can
    tell "assessed and cleared" from "never assessed" -- an item missing
    from every other section is not the same as a permitted one."""
    if not items:
        return
    with st.expander(f"Out of scope ({len(items)})", expanded=False):
        st.caption(
            "These items were never assessed against Annex II -- they are not food additives "
            "under this regulation at all."
        )
        for item in items:
            display_name = _display_name(item, names)
            st.markdown(
                f"**{_esc(display_name)}** — {_esc(_out_of_scope_reason(item))}", unsafe_allow_html=True
            )


def render_review_queue(items: list[dict], names: dict[int, str] | None = None) -> None:
    """Unresolved and ambiguous items -- work for a human to complete, not
    an error state."""
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
            st.caption("Possible matches: " + ", ".join(candidates))
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
            level = f"{c['max_level_mg_kg']} mg/kg" if c.get("max_level_mg_kg") is not None else "quantum satis"
            flags_display = ", ".join(_substitute_flag_label(f) for f in c.get("flags") or []) or "—"
            rows.append(
                "<tr>"
                f"<td class='eu-code'>E{_esc(c['eu_canonical_id'])}</td>"
                f"<td>{_esc(c.get('additive_name'))}</td>"
                f"<td>{_esc(', '.join(c.get('shared_functional_classes') or []))}</td>"
                f"<td>{_esc(_verdict_label(c['verdict'], c.get('max_level_mg_kg'))[0])}</td>"
                f"<td class='eu-code'>{_esc(level)}</td>"
                f"<td>{_esc(flags_display)}</td>"
                "</tr>"
            )
        table = (
            "<table class='eu-table'><thead><tr>"
            "<th>EU id</th><th>Name</th><th>Shared functional class</th>"
            "<th>Verdict</th><th>Max level</th><th>Flags</th>"
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


def render_horizon(result: dict) -> None:
    """Regulatory-horizon signals. The coverage caveat (see docs/findings.md
    F-12) is never DROPPED -- silently making a partial dataset read as
    comprehensive is exactly the bug F-12 covers -- but it no longer prints
    unconditionally: three paragraphs explaining the limits of an empty
    result is worse than the empty result. Both caveats live in "About
    these signals", one line short of always-visible; the empty-signals
    case gets a single line that points there."""
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
