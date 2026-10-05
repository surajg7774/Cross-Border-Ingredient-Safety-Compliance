# DESIGN RULE: pure transforms -- data in (three finished result objects
# plus a Narration), bytes/str out. No file writes, no network, no
# settings access; the caller (app.py) owns writing the result to disk or
# offering it as a download. Never imports src.category/, src.resolve/, or
# any reference-data loader -- an export renders what evaluate()/
# find_substitutes()/find_horizon_signals()/narrate() already decided, it
# never decides anything itself. Display names are the one exception worth
# noting: app.py backfills ItemVerdict.additive_name (eu_fip name, else
# Codex name, else the name printed on the label) BEFORE calling any
# function here, so every name-fallback tier this module would otherwise
# need is already resolved on the object it receives -- see app.py's
# _enrich_additive_names.
"""Export a finished compliance report as JSON, CSV, or PDF.

THE PDF IS THE ARTIFACT THAT GETS FORWARDED. Once it leaves this app it
travels alone -- nobody re-opens the UI to check what it left out. A PDF
that drops a caveat (an unconfirmed category, a missing source, the dosage
warning, category divergence, the horizon lane's partial-coverage notice,
an unverified DOI, an unfaithful narration claim) is worse than no PDF at
all, because it reads as complete when it isn't.

NOT defined relative to the results screen (src/ui/components.py)
anymore: the screen was deliberately stripped down to metric cards, the
additives table, and collapsed out-of-scope -- it shows LESS than this
module does, on purpose (a compliance officer defending a decision
downloads the report; they do not scroll a live page for it). This
module's job is to be a COMPLETE, STANDALONE record of what evaluate()/
find_substitutes()/find_horizon_signals()/narrate() actually decided --
judged against those objects directly, never against what currently
happens to be on screen.
"""

import csv
import html
import io
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from src.horizon.schemas import HorizonResult
from src.report.narrator import Narration
from src.rules.schemas import ItemVerdict, ProductVerdict
from src.substitutes.schemas import SubstituteResult

# Same institutional palette as src/ui/styles.py's light values -- a PDF has
# no dark-mode concept, so there is only one palette to carry over.
_INK = colors.HexColor("#14161A")
_MUTED = colors.HexColor("#6E7178")
_RULE = colors.HexColor("#D8D6D0")
_PERMITTED = colors.HexColor("#2F6B4F")
_BLOCKED = colors.HexColor("#9B2C2C")
_CONFLICT = colors.HexColor("#8A6D1F")

_VERDICT_LABELS: dict[str, str] = {
    "permitted_qs": "permitted",
    "permitted_with_limit": "permitted, max level applies",
    "permitted_with_conditions": "permitted, conditions apply",
    "not_permitted_in_category": "not permitted in this category",
    "not_authorised_eu": "not authorised as a food additive in the EU",
    "out_of_scope": "out of Annex II scope",
    "unresolved": "not identified",
    "category_unknown": "no food category available",
}
_PERMITTING_HEADLINES = {"permitted_qs", "permitted_with_limit", "permitted_with_conditions"}
_REVIEW_HEADLINES = {"unresolved", "category_unknown"}

# Same wording as src/ui/components.py's blocking-reason line -- WHY an
# item blocks, not just that it does. "prohibited" (a jurisdiction-wide
# ban) and absence from the EU list are different findings and read
# differently to a manufacturer: one is a dead end everywhere, the other
# might still work in a different formulation elsewhere in the EU.
_PROHIBITED_REASON = "prohibited -- removed from the EU permitted list"
_NOT_AUTHORISED_REASON = (
    "not authorised -- this additive does not appear in the EU list of permitted food "
    "additives, in any food category"
)

# governing_regulation value (see src/rules/engine.py's _OUT_OF_SCOPE_REGULATION) -> the
# plain-language reason src/ui/components.py also shows for an out-of-scope item.
_OUT_OF_SCOPE_REASONS: dict[str, str] = {
    "Reg 1334/2008": "flavouring -- regulated under Reg 1334/2008, not the additives regulation",
    "Reg 1332/2008": "enzyme -- Reg 1332/2008",
    "not an additive": "food ingredient -- not an additive",
}

# Internal flag name -> the plain phrase src/ui/components.py also shows.
# The raw flag stays in the JSON export (to_json) -- only display changes.
_SUBSTITUTE_FLAG_LABELS: dict[str, str] = {
    "classes_from_subtypes": "function inferred from sub-types",
    "adds_labelling_obligation": "requires a warning label",
    "under_efsa_review": "under EFSA review",
}


def _verdict_label(verdict: str) -> str:
    return _VERDICT_LABELS.get(verdict, verdict.replace("_", " "))


def _display_name(item: ItemVerdict) -> str:
    if item.additive_name:
        return item.additive_name
    if item.eu_canonical_id:
        return f"E{item.eu_canonical_id}"
    return f"item {item.item_id}"


def _blocking_reason(item: ItemVerdict) -> str:
    return _PROHIBITED_REASON if "prohibited" in item.flags else _NOT_AUTHORISED_REASON


def _out_of_scope_reason(item: ItemVerdict) -> str:
    for flag in item.flags:
        if flag.startswith("governing_regulation:"):
            governing = flag.split(":", 1)[1].strip()
            return _OUT_OF_SCOPE_REASONS.get(governing, governing)
    return "not an additive"


def _substitute_flag_label(flag: str) -> str:
    return _SUBSTITUTE_FLAG_LABELS.get(flag, flag)


@dataclass(frozen=True)
class ReportIdentity:
    """Product identity block -- the SAME four fields shown on the results
    screen (app.py), at the top of the CSV, and in the PDF header. app.py
    is the only place that constructs one: it needs session-state facts
    (the uploaded filename, the run timestamp) this module has no access
    to and must not -- it stays a pure transform.
    """

    product_name: str
    source: str  # a filename, or "pasted text"
    category: str  # e.g. "Product: 14.1.4 (Flavoured drinks)"
    timestamp: str


# =========================================================================== #
# JSON
# =========================================================================== #
def to_json(
    verdict: ProductVerdict,
    substitutes: SubstituteResult,
    horizon: HorizonResult,
    narration: Narration,
    agent_review: dict | None = None,
) -> str:
    """The full structured result, including the narration -- nothing
    dropped. Every field the UI reads from these four objects is here.

    `agent_review` (item_id -> {"decision": ..., **AgentProposal fields})
    is the review-queue assistant's full trace -- tool_calls, evidence,
    reasoning -- for every item a human asked it about, however that
    request was resolved. It exists ONLY in app.py's st.session_state
    (src.agent.resolver_agent.AgentProposal is never part of ProductVerdict
    or any other object this module already receives), so app.py builds
    and passes it explicitly; this module does not know how to construct
    it and never reaches into session state itself. None/omitted (the
    default) means no item was ever sent to the assistant this run -- the
    key is left out of the payload entirely rather than written as `null`
    or `{}`, so its absence is unambiguous. Deliberately JSON-only: CSV
    rows and the PDF are both human-facing summary documents, and a
    multi-step tool-call trace does not belong in either -- see app.py's
    render_export_section for the same reasoning applied to the UI these
    formats mirror."""
    payload = {
        "verdict": verdict.model_dump(),
        "substitutes": substitutes.model_dump(),
        "horizon": horizon.model_dump(),
        "narration": narration.model_dump(),
    }
    if agent_review:
        payload["agent_review"] = agent_review
    return json.dumps(payload, indent=2, ensure_ascii=False)


# =========================================================================== #
# CSV
# =========================================================================== #
_CSV_HEADER = [
    "item",
    "eu_id",
    "component",
    "category",
    "verdict",
    "level_mg_kg",
    "conditions",
    "source_url",
    "flags",
]


def to_csv(verdict: ProductVerdict, identity: ReportIdentity | None = None) -> str:
    """One row per assessed item -- every item in verdict.items, including
    out-of-scope and unresolved ones, so the CSV is a complete audit trail,
    not just the additives that happened to get a category verdict.
    `identity`, when given, is written as leading "# key: value" comment
    rows before the header -- the same product-identity block shown on the
    results screen and in the PDF header.
    """
    buffer = io.StringIO()
    if identity is not None:
        buffer.write(f"# Product: {identity.product_name}\n")
        buffer.write(f"# Source: {identity.source}\n")
        buffer.write(f"# Category: {identity.category}\n")
        buffer.write(f"# Generated: {identity.timestamp}\n")

    writer = csv.writer(buffer)
    writer.writerow(_CSV_HEADER)

    for item in verdict.items:
        top = item.by_category[0] if item.by_category else None
        writer.writerow(
            [
                _display_name(item),
                item.eu_canonical_id or "",
                item.component_label or "",
                top.fcs_code if top else "",
                item.headline,
                top.max_level_mg_kg if top and top.max_level_mg_kg is not None else "",
                top.conditions if top else "",
                top.source_url if top else "",
                "; ".join(item.flags),
            ]
        )
    return buffer.getvalue()


# =========================================================================== #
# PDF
# =========================================================================== #
def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "eu-title", parent=base["Title"], textColor=_INK, alignment=TA_LEFT, fontSize=18, spaceAfter=2,
        ),
        "meta": ParagraphStyle("eu-meta", parent=base["Normal"], textColor=_MUTED, fontSize=9, spaceAfter=10),
        "h2": ParagraphStyle(
            "eu-h2", parent=base["Heading2"], textColor=_INK, fontSize=12, spaceBefore=14, spaceAfter=6,
        ),
        "body": ParagraphStyle("eu-body", parent=base["Normal"], textColor=_INK, fontSize=10, spaceAfter=4, leading=14),
        "caption": ParagraphStyle("eu-caption", parent=base["Normal"], textColor=_MUTED, fontSize=8.5, spaceAfter=6, leading=12),
        "warning": ParagraphStyle(
            "eu-warning", parent=base["Normal"], textColor=_CONFLICT, fontSize=9, spaceAfter=6, leading=13,
        ),
        "item_name": ParagraphStyle("eu-item-name", parent=base["Normal"], textColor=_INK, fontSize=10.5, fontName="Helvetica-Bold", spaceAfter=1),
    }


def _verdict_colour(verdict: str) -> colors.Color:
    if verdict in _PERMITTING_HEADLINES:
        return _PERMITTED
    if verdict in ("not_authorised_eu", "not_permitted_in_category"):
        return _BLOCKED
    return _MUTED


def _is_ancestor_code(candidate_code: str, of_code: str) -> bool:
    """True if `candidate_code` is a dotted-code ancestor of `of_code` --
    "14.1" is an ancestor of "14.1.4"; "14.1.4" is not an ancestor of
    "14.1" (directional) nor of itself. Duplicated from what src/ui/
    components.py used before the results-screen simplification removed
    its own copy -- this module must not import the UI layer (see this
    module's own DESIGN RULE), so the PDF needs its own."""
    return candidate_code != of_code and of_code.startswith(candidate_code + ".")


def _diverging_candidates(item: ItemVerdict) -> list:
    """The category candidates this item's verdict GENUINELY depends on --
    empty whenever every real (non-ancestor) candidate agrees, or there is
    only one to begin with. item.by_category[0] (the same candidate
    _item_flowables' own `top` uses -- the confirmed one when the item was
    confirmed, since a confirmed item's by_category has exactly one entry
    at that point; otherwise rank-1 from retrieval) is the reference
    candidate; anything that is its ANCESTOR (Khusmain's real case:
    retrieved candidates "14.1.4", "14.1", "14" -- the latter two are not
    alternatives, they CONTAIN 14.1.4, and eu_fip has no row for a parent
    code, so it would otherwise evaluate a meaningless "not permitted in
    this category") is filtered out before any divergence check runs.
    MUST see the same picture a screen reader of the (now removed) verdict
    strip / per-row expander would have -- this is the ONE thing that
    screen used to show that the results screen shows nowhere at all
    anymore; the PDF is where it survives."""
    if not item.by_category:
        return []
    reference_code = item.by_category[0].fcs_code
    candidates = [cv for cv in item.by_category if not _is_ancestor_code(cv.fcs_code, reference_code)]
    if len(candidates) <= 1 or len({cv.verdict for cv in candidates}) <= 1:
        return []
    return candidates


def _item_flowables(item: ItemVerdict, styles: dict, *, is_blocking: bool = False) -> list:
    """Every caveat the results screen carries for one item: category +
    verdict, WHY it blocks (for blocking items), the in-force date
    (relabelled, not "retrieved"), a real source-URL link or an explicit
    "no source URL" note, an unconfirmed-category note where it applies,
    the conditions text in full (never truncated), and -- NOT shown on
    the results screen at all since its simplification, see src/ui/
    components.py's render_additives_table -- which OTHER category this
    verdict would have been under, when candidates genuinely diverge
    (_diverging_candidates)."""
    flowables = [Paragraph(f"{_display_name(item)}  (E{item.eu_canonical_id})" if item.eu_canonical_id else _display_name(item), styles["item_name"])]
    if item.component_label:
        flowables.append(Paragraph(f"in {item.component_label}", styles["caption"]))

    if not item.by_category:
        flowables.append(
            Paragraph(f"<font color='{_verdict_colour(item.headline).hexval()}'>{_verdict_label(item.headline)}</font>", styles["body"])
        )
        if is_blocking:
            flowables.append(Paragraph(_blocking_reason(item), styles["caption"]))
        flowables.append(Spacer(1, 6))
        return flowables

    top = item.by_category[0]
    colour = _verdict_colour(top.verdict)
    flowables.append(
        Paragraph(
            f"<font color='{colour.hexval()}'>{top.fcs_code} -- {_verdict_label(top.verdict)}</font>",
            styles["body"],
        )
    )
    if is_blocking:
        flowables.append(Paragraph(_blocking_reason(item), styles["caption"]))

    if "category_unconfirmed" in item.flags:
        flowables.append(Paragraph("Category not confirmed by a person -- retrieved automatically.", styles["warning"]))

    diverging = _diverging_candidates(item)
    if diverging:
        flowables.append(
            Paragraph("This verdict depends on which part of the product it applies to:", styles["caption"])
        )
        for cv in diverging:
            where = f"{cv.category_name} ({cv.fcs_code})" if cv.category_name else cv.fcs_code
            flowables.append(Paragraph(f"{_verdict_label(cv.verdict)} in {where}.", styles["caption"]))

    if top.retrieved_date:
        flowables.append(Paragraph(f"In force since {top.retrieved_date}.", styles["caption"]))

    if top.source_url:
        flowables.append(Paragraph(f"Source: <link href='{top.source_url}'>{top.source_url}</link>", styles["caption"]))
    else:
        flowables.append(Paragraph("No source URL on this record.", styles["caption"]))

    if top.conditions:
        flowables.append(Paragraph(f"Conditions: {top.conditions}", styles["body"]))
        if top.note_codes:
            flowables.append(Paragraph("Note codes: " + ", ".join(top.note_codes), styles["caption"]))

    flowables.append(Spacer(1, 6))
    return flowables


def _section(title: str, items: list[ItemVerdict], styles: dict, *, is_blocking: bool = False) -> list:
    if not items:
        return []
    flowables = [Paragraph(f"{title} ({len(items)})", styles["h2"]), HRFlowable(width="100%", color=_RULE, thickness=0.5)]
    for item in items:
        flowables.extend(_item_flowables(item, styles, is_blocking=is_blocking))
    return flowables


def _out_of_scope_flowables(verdict: ProductVerdict, styles: dict) -> list:
    """Items never assessed against Annex II at all -- flavourings,
    enzymes, food ingredients. Listed WITH the reason, so a reader can tell
    "assessed and cleared" from "never assessed" -- see
    src/ui/components.py's render_out_of_scope."""
    items = [item for item in verdict.items if item.headline == "out_of_scope"]
    if not items:
        return []
    flowables = [Paragraph(f"Out of scope ({len(items)})", styles["h2"]), HRFlowable(width="100%", color=_RULE, thickness=0.5)]
    flowables.append(
        Paragraph(
            "These items were never assessed against Annex II -- they are not food additives "
            "under this regulation at all.",
            styles["caption"],
        )
    )
    for item in items:
        flowables.append(Paragraph(f"{_display_name(item)} -- {_out_of_scope_reason(item)}", styles["body"]))
    return flowables


def _substitutes_flowables(substitutes: SubstituteResult, styles: dict) -> list:
    if not substitutes.suggestions:
        return []
    flowables = [Paragraph("Substitutes", styles["h2"]), HRFlowable(width="100%", color=_RULE, thickness=0.5)]
    flowables.append(Paragraph("Candidates require real formulation validation. They are not recommendations.", styles["caption"]))
    for suggestion in substitutes.suggestions:
        name = suggestion.blocked_name or suggestion.blocked_eu_canonical_id or f"item {suggestion.item_id}"
        flowables.append(Paragraph(f"{name} blocked ({suggestion.blocked_reason})", styles["item_name"]))
        if not suggestion.candidates:
            flowables.append(Paragraph(suggestion.no_candidates_reason or "No candidates found.", styles["caption"]))
            flowables.append(Spacer(1, 6))
            continue
        rows = [["EU id", "Name", "Verdict", "Max level", "Flags"]]
        for c in suggestion.candidates:
            level = f"{c.max_level_mg_kg} mg/kg" if c.max_level_mg_kg is not None else "quantum satis"
            flags_display = ", ".join(_substitute_flag_label(f) for f in c.flags) or "—"
            rows.append([f"E{c.eu_canonical_id}", c.additive_name or "—", _verdict_label(c.verdict), level, flags_display])
        table = Table(rows, hAlign="LEFT", colWidths=[22 * mm, 40 * mm, 45 * mm, 25 * mm, 35 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("TEXTCOLOR", (0, 0), (-1, -1), _INK),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.5, _RULE),
                    ("LINEBELOW", (0, 1), (-1, -1), 0.25, _RULE),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        flowables.append(table)
        # Per-candidate conditions/source -- previously invisible in the
        # PDF entirely, same fix as src/ui/components.py's per-candidate
        # expander.
        for c in suggestion.candidates:
            if not c.conditions and not c.source_url:
                continue
            label = c.additive_name or f"E{c.eu_canonical_id}"
            if c.conditions:
                flowables.append(Paragraph(f"{label} conditions: {c.conditions}", styles["caption"]))
            if c.source_url:
                flowables.append(Paragraph(f"{label} source: <link href='{c.source_url}'>{c.source_url}</link>", styles["caption"]))
        flowables.append(Spacer(1, 8))
    for warning in substitutes.warnings:
        flowables.append(Paragraph(warning, styles["caption"]))
    return flowables


def _horizon_flowables(horizon: HorizonResult, styles: dict) -> list:
    """Always rendered, signals or not -- the partial-coverage warning must
    survive into the PDF even when there is nothing else to say (see
    docs/findings.md F-12: absence of a signal is not absence of review).
    The coverage caveat is rendered BEFORE the "no recent activity" line,
    not after -- reading the reassurance first and the caveat second
    undersells the caveat."""
    flowables = [Paragraph("Regulatory horizon", styles["h2"]), HRFlowable(width="100%", color=_RULE, thickness=0.5)]
    flowables.append(
        Paragraph(
            "Advisory only. These signals never change the compliance verdict above -- an EFSA "
            "opinion is not law. Additives can be re-assessed by EFSA years before the law "
            "changes; these are early signals, not current requirements.",
            styles["caption"],
        )
    )
    for warning in horizon.warnings:
        flowables.append(Paragraph(warning, styles["caption"]))

    if not horizon.signals:
        flowables.append(Paragraph("No recent EFSA activity found for the additives checked.", styles["caption"]))
    else:
        for signal in horizon.signals:
            unverified = "doi_unverified" in signal.flags
            doi_text = f"{signal.doi or '—'}" + (" (UNVERIFIED)" if unverified else "")
            flowables.append(
                Paragraph(
                    f"{signal.substance_name} (E{signal.eu_canonical_id}) -- {signal.stage}, "
                    f"{signal.publication_date or '—'} -- DOI: {doi_text}",
                    styles["warning"] if unverified else styles["body"],
                )
            )
    return flowables


def _review_queue_flowables(verdict: ProductVerdict, styles: dict) -> list:
    items = [item for item in verdict.items if item.headline in _REVIEW_HEADLINES]
    if not items:
        return []
    flowables = [Paragraph(f"Review queue ({len(items)})", styles["h2"]), HRFlowable(width="100%", color=_RULE, thickness=0.5)]
    flowables.append(
        Paragraph("These items need a human decision to complete the audit.", styles["caption"])
    )
    for item in items:
        reason = "no food category available" if item.headline == "category_unknown" else "not identified"
        candidates = [f.split(":", 1)[1].strip() for f in item.flags if f.startswith("candidate:")]
        flowables.append(Paragraph(f"{_display_name(item)} -- {reason}", styles["item_name"]))
        if candidates:
            flowables.append(Paragraph("Possible matches: " + ", ".join(candidates), styles["caption"]))
        else:
            flowables.append(Paragraph("No candidate match found -- needs manual identification.", styles["caption"]))
    return flowables


def to_pdf(
    verdict: ProductVerdict,
    substitutes: SubstituteResult,
    horizon: HorizonResult,
    narration: Narration,
    identity: ReportIdentity | None = None,
) -> bytes:
    """Render the full report as a PDF -- the product identity block,
    source URL and in-force date on every verdict, WHY a blocking verdict
    blocks, an unconfirmed-category note where that applies, which OTHER
    category a verdict would have been under when candidates genuinely
    diverge (_diverging_candidates), the dosage caveat (carried inside
    verdict.summary), out-of-scope items with their reason, the horizon
    lane's partial-coverage warning (before the reassurance, not after),
    any unverified-DOI warning, and the narration's model_id plus any
    unfaithful_claims.

    NOT the same as the results screen (src/ui/components.py) any more --
    the screen was deliberately stripped down to metric cards, the
    additives table, and collapsed out-of-scope, with every per-additive
    detail (conditions text, source, in-force date, category divergence)
    removed from it entirely. This function is now the ONLY place that
    detail is rendered at all -- see the module docstring: the PDF is the
    artifact that gets forwarded, and a compliance officer defending a
    decision downloads this, they do not scroll a live page for it.
    """
    styles = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm
    )

    story = []
    title = identity.product_name if identity else "EU Additive Compliance Report"
    story.append(Paragraph(title, styles["title"]))
    if identity is not None:
        story.append(
            Paragraph(
                f"Source: {identity.source}  |  Category: {identity.category}  |  Run: {identity.timestamp}",
                styles["meta"],
            )
        )
    else:
        generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        story.append(Paragraph(f"Generated {generated}", styles["meta"]))

    # Narration -- prominent, above the item sections, exactly as on screen.
    # Escaped (unlike the deterministic eu_fip-derived text elsewhere in
    # this file): model output is less predictable than reference data, and
    # a raw "<" or "&" would otherwise break reportlab's markup parser.
    story.append(Paragraph(html.escape(narration.summary), styles["body"]))
    for topic, points in narration.detail.items():
        story.append(Paragraph(f"<b>{html.escape(topic)}</b>", styles["body"]))
        for point in points:
            story.append(Paragraph(f"• {html.escape(point)}", styles["body"]))
    story.append(
        Paragraph(
            f"Written by {narration.model_id} from the assessment below. It adds no facts.",
            styles["caption"],
        )
    )
    if narration.unfaithful_claims:
        story.append(
            Paragraph(
                "The narration above contains claims NOT found in the underlying assessment: "
                + "; ".join(narration.unfaithful_claims),
                styles["warning"],
            )
        )
    story.append(Spacer(1, 4))

    # The deterministic summary, verbatim -- never rewritten, carries the
    # dosage caveat and the category-retrieval caveat when they apply.
    story.append(Paragraph(verdict.summary, styles["caption"]))
    story.append(HRFlowable(width="100%", color=_RULE, thickness=0.75))

    items_by_id = {item.item_id: item for item in verdict.items}
    blocking = [items_by_id[i] for i in verdict.blocking]
    conflict = [items_by_id[i] for i in verdict.category_conflict]
    permitted = [
        item
        for item in verdict.items
        if item.headline in _PERMITTING_HEADLINES
        and item.item_id not in verdict.blocking
        and item.item_id not in verdict.category_conflict
    ]

    story.extend(_section("Blocking", blocking, styles, is_blocking=True))
    story.extend(_section("Category-dependent", conflict, styles))
    story.extend(_section("Permitted", permitted, styles))
    story.extend(_substitutes_flowables(substitutes, styles))
    story.extend(_horizon_flowables(horizon, styles))
    story.extend(_review_queue_flowables(verdict, styles))
    story.extend(_out_of_scope_flowables(verdict, styles))

    doc.build(story)
    return buffer.getvalue()
