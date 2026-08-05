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
# list is worse than a family description -- MEASURED against data/
# reference/label_aliases.json: "modified starch"/"modified cornstarch"
# both resolve to 17 candidates spanning E1400-E1452. A code range ("E1400
# -E1452") tells a reader with no regulatory training nothing about what
# the substances ARE -- see _shared_family_word below.
_MANY_CANDIDATES = 6


def _shared_family_word(names: list[str]) -> str | None:
    """The chemical-family stem every one of `names` shares, e.g. "starch"
    for the 17 real codex_ins names behind "MODIFIED CORNSTARCH" (Dextrins
    roasted starch, Monostarch phosphate, Distarch phosphate, ...) -- a
    SUBSTRING match, not a whole-word one, since the stem is fused into a
    compound word in most of those names ("Distarch", "Monostarch" never
    contain "starch" as an isolated token). None when the names have no
    word of 4+ letters in common (nothing to say, so the caller falls back
    to a plain count)."""
    if len(names) < 2:
        return None
    candidate_words = {w for w in re.split(r"[^A-Za-z]+", names[0].lower()) if len(w) >= 4}
    shared = [w for w in candidate_words if all(w in name.lower() for name in names[1:])]
    if not shared:
        return None
    return max(shared, key=len)


def _pluralize(word: str) -> str:
    return f"{word}es" if word.endswith(("ch", "sh", "ss", "x", "z")) else f"{word}s"


def candidates_phrase(codes: list[str], ins_names: dict[str, str] | None = None) -> str:
    """`codes` (bare INS codes) as a reader-facing phrase -- real names,
    comma-joined, when there are few; when there are many, the shared
    family word from their real names ("17 different starches"), NEVER a
    code range (E1400-E1452 says what the substances are numbered, not
    what they are). A code with no entry in `ins_names` falls back to
    "E{code}" rather than disappearing -- an unnamed candidate is still a
    real candidate."""
    if not codes:
        return ""
    lookup = ins_names or {}
    if len(codes) >= _MANY_CANDIDATES:
        names = [lookup[code] for code in codes if code in lookup]
        family = _shared_family_word(names) if len(names) == len(codes) else None
        if family:
            return f"{len(codes)} different {_pluralize(family)}"
        return f"{len(codes)} possible matches"
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
    row to look anything up against).

    Where: the component name when the item has one, the category
    display (name + code) when it doesn't -- NEVER both. MEASURED BUG
    this replaces: "Seasoning: Seasonings and condiments (12.2.2)" showed
    the component AND the category name together, and for a component
    like "Seasoning" the two are near-duplicates of each other -- the
    component label already tells the reader which part of the product
    this is about, so the category name added nothing, just repetition."""
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
        component = item.get("component_label")
        where = component or _category_display(top.get("fcs_code"), top.get("category_name"))

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


def render_additives_table(items: list[dict], names: dict[int, str] | None = None) -> None:
    """The primary results view, one row per additive: Additive / Status /
    Maximum amount / In force since / Where -- worst-first
    (_STATUS_SEVERITY), so a reader sees what needs a decision before
    what doesn't. Replaces the old Blocking/Category-dependent/Permitted
    sections and their colour-block strip: those three headline-derived
    buckets are still exactly what determines a row's Status text, just
    read off one plain column instead of three differently-labelled
    sections a reader had to already understand the difference between.

    The table ONLY -- no per-row expander, no conditions text, no source
    link, no category-divergence detail on screen at all. That detail
    (the legal basis for every verdict) lives in the PDF/JSON exports
    instead (src/report/export.py's _item_flowables / verdict.model_dump()),
    never on the results screen: a compliance officer defending a decision
    downloads the report, they do not scroll a live page for it.

    No same-block grouping (contrast the old render_permitted_section):
    "one row per additive" is literal."""
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
    """Retrieved-and-classified news items, in their OWN section -- the
    hand-curated EFSA "Regulatory horizon" lane that used to sit above
    this no longer renders at all (it matched nothing on every label
    tried; see render_horizon's own docstring), so this needs its own
    section title rather than relying on that header for context. Still a
    genuinely different KIND of source from that curated lane (src/
    horizon/schemas.py's NewsSignal docstring: secondary reporting,
    machine-retrieved and machine-classified against a verbatim-quote
    check, never a hand-curated primary regulatory document) -- the
    provenance line names that plainly, every time, not just in an
    expander. Renders nothing when there is nothing to show; this is
    additional coverage, never a required section."""
    if not news_signals:
        return

    st.markdown("<div class='eu-section-title'>Additive news</div>", unsafe_allow_html=True)
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
    """Retrieved news only -- additive-scoped (_render_news_signals, its
    own "Additive news" section) and route-scoped (_render_route_news,
    "Import route: India -> EU"). The hand-curated EFSA "Regulatory
    horizon" lane (result["signals"], data/reference/horizon_signals.json,
    5 entries) is DELIBERATELY not rendered here at all -- it matched
    nothing on every label tried so far, so the section only ever showed
    an empty table or the "no signals" caption, never a real result. The
    lane itself is not deleted: result["signals"]/result["warnings"] are
    still returned by find_horizon_signals and still reach the PDF/JSON
    exports in full (src/report/export.py's _horizon_flowables) -- only
    its screen (here) and narration (src/report/narrator.py's
    _build_prompt) presence is gone."""
    _render_news_signals(result.get("news_signals") or [])
    _render_route_news(result.get("route_news_signals") or [], result.get("route_news_category"))
