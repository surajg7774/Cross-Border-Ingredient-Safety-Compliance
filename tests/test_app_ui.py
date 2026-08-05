"""Tests exercising app.py's rendering through streamlit.testing.v1.AppTest --
unlike tests/test_components.py's pure-function tests, these need a real
Streamlit runtime, so they get their own file.
"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

# ---- Email export: server-side SMTP only, no credential form ------------

# A minimal but real ProductVerdict/SubstituteResult/HorizonResult/
# Narration/ReportIdentity set, built the same way tests/test_export.py's
# own fixtures are -- render_export_section (and the to_json/to_csv/to_pdf
# calls inside it) need real pydantic instances, not dicts, since export.py
# (unlike src/ui/components.py) DOES import the schemas.
_EXPORT_SECTION_SCRIPT = """
import app
from src.horizon.schemas import HorizonResult
from src.report.export import ReportIdentity
from src.report.narrator import Narration
from src.rules.schemas import CategoryVerdict, ItemVerdict, ProductVerdict
from src.substitutes.schemas import SubstituteResult

app.smtp_config_from_settings = lambda: __SMTP_CONFIG__

verdict = ProductVerdict(
    items=[
        ItemVerdict(
            item_id=1,
            eu_canonical_id="551",
            additive_name="Silicon dioxide",
            component_label=None,
            by_category=[
                CategoryVerdict(
                    fcs_code="12.2.2",
                    category_name="Seasonings",
                    rank=1,
                    verdict="permitted_qs",
                    max_level_mg_kg=None,
                    max_level_basis="gmp",
                    conditions=None,
                    note_codes=[],
                    source_url=None,
                    retrieved_date=None,
                )
            ],
            headline="permitted_qs",
            category_sensitive=False,
            verdict_certainty="certain",
            flags=[],
        )
    ],
    blocking=[],
    category_conflict=[],
    review_required=[],
    category_sensitive_items=[],
    summary="1 item(s) evaluated.",
    category_used={},
    category_source={},
    warnings=[],
    data_version="test",
)
substitutes = SubstituteResult(suggestions=[], warnings=[])
horizon = HorizonResult(signals=[], checked_ids=[], warnings=[], data_version="test", data_retrieved="2026-08-01")
narration = Narration(summary="Not blocked.", detail={}, model_id="fake-model", unfaithful_claims=[])
identity = ReportIdentity(product_name="Test product", source="test.jpg", category="cat", timestamp="now")

app.render_export_section(verdict, substitutes, horizon, narration, identity)
"""


def test_email_section_absent_when_smtp_not_configured():
    # smtp_config_from_settings() -> None (the .env-absent case) must mean
    # the WHOLE "Email this report" expander is skipped -- not shown
    # disabled, not shown with an explanatory message -- while the
    # downloads above it still render.
    script = _EXPORT_SECTION_SCRIPT.replace("__SMTP_CONFIG__", "None")
    # `import app` pulls in the full pipeline import chain (now including
    # src/horizon/search.py, news.py, news_cache.py) -- comfortably under
    # a real timeout, but occasionally past AppTest's tight 3s default on
    # a cold import; bumped, not a sign of a hang.
    at = AppTest.from_string(script).run(timeout=15)
    assert not at.exception
    assert at.expander == []
    assert len(at.download_button) == 3


def test_email_section_present_when_smtp_configured():
    # The inverse: with all five settings present, the section renders --
    # proves the guard above isn't simply hiding the section unconditionally.
    script = _EXPORT_SECTION_SCRIPT.replace(
        "app.smtp_config_from_settings = lambda: __SMTP_CONFIG__",
        "from src.report.email import SmtpConfig\n"
        "app.smtp_config_from_settings = lambda: SmtpConfig("
        "host='smtp.example.com', port=587, user='u', password='p', sender='reports@example.com')",
    )
    at = AppTest.from_string(script).run(timeout=15)  # see the sibling test's comment on this bump
    assert not at.exception
    assert len(at.expander) == 1
    assert at.expander[0].label == "Email this report"


def test_no_smtp_credential_fields_remain_in_app_source():
    # TASK: no credential fields in the UI at all -- the exact widget calls
    # the old form used, and the session_state keys they were bound to,
    # must be gone from app.py entirely, not merely unreachable.
    source = Path("app.py").read_text(encoding="utf-8")
    removed_snippets = (
        'st.text_input("SMTP host"',
        'st.text_input("Username"',
        'st.text_input("From address"',
        'st.number_input("Port"',
        'st.text_input("Password"',
        "smtp_host",
        "smtp_port",
        "smtp_user",
        "smtp_password",
        "smtp_from",
        "SmtpConfig(",
        "held in this browser session",
    )
    for snippet in removed_snippets:
        assert snippet not in source, f"{snippet!r} should no longer appear in app.py"


# ---- Narration: shown only on success, silent on failure ----------------

# Mirrors app.py's render_results narration block exactly (see the comment
# immediately above `if narration.model_id != "unavailable":` in app.py) --
# render_results itself needs a full verdict/substitute/horizon/extraction
# session_state to run end-to-end (see _EXPORT_SECTION_SCRIPT above for how
# heavy that setup is for a single boolean gate), so this isolates just the
# conditional under test, the same way the horizon tests below isolate
# render_horizon rather than going through render_results.
_NARRATION_SCRIPT = """
import streamlit as st
from src.report.narrator import Narration

narration = Narration(
    summary=__SUMMARY__,
    detail={"Scope": ["Applies EU-wide."]},
    model_id=__MODEL_ID__,
    unfaithful_claims=[],
)

if narration.model_id != "unavailable":
    st.markdown(narration.summary)
    for topic, points in narration.detail.items():
        st.markdown(f"**{topic}**")
        for point in points:
            st.markdown(f"- {point}")
    st.caption(f"Written by {narration.model_id} from the assessment above. It adds no facts.")
    if narration.unfaithful_claims:
        st.warning(
            "The narration above contains claims NOT found in the underlying assessment: "
            + "; ".join(narration.unfaithful_claims)
        )
"""


def test_narration_failure_renders_nothing():
    # model_id == "unavailable" is narrate()'s own fallback signal (see
    # src/report/narrator.py) -- on this screen a system failure must show
    # NOTHING, not a "Narration was unavailable" note or a "Written by
    # unavailable" caption.
    script = _NARRATION_SCRIPT.replace("__SUMMARY__", repr("1 item(s) evaluated.")).replace(
        "__MODEL_ID__", repr("unavailable")
    )
    at = AppTest.from_string(script).run()
    assert not at.exception
    assert at.markdown == []
    assert at.caption == []


def test_narration_success_still_renders():
    # The inverse: a real model_id means the prose, detail, and "Written
    # by" caption all render -- proves the guard isn't hiding narration
    # unconditionally.
    script = _NARRATION_SCRIPT.replace("__SUMMARY__", repr("Not blocked.")).replace(
        "__MODEL_ID__", repr("fake-model")
    )
    at = AppTest.from_string(script).run()
    assert not at.exception
    markdown_html = "\n".join(m.value for m in at.markdown)
    assert "Not blocked." in markdown_html
    assert "Applies EU-wide." in markdown_html
    captions = [c.value for c in at.caption]
    assert "Written by fake-model from the assessment above. It adds no facts." in captions


# ---- Horizon news signals (src/ui/components.py's render_horizon) -------

_NEWS_SIGNAL = {
    "eu_canonical_id": "171",
    "substance_name": "Titanium dioxide",
    "category": "regulatory_review",
    "quoted_span": "EFSA opens review of titanium dioxide",
    "source_url": "https://efsa.europa.eu/example",
    "published_date": "2026-06-01",
    "search_query": "Titanium dioxide food additive EU regulation",
    "retrieved_at": "2026-08-04",
    "severity": "advisory",
    "affects_verdict": False,
    "flags": [],
}


def _render_horizon_script(result: dict) -> str:
    return f"""
from src.ui import components

components.render_horizon({result!r})
"""


def test_horizon_renders_nothing_extra_when_news_signals_absent():
    result = {"signals": [], "checked_ids": [], "warnings": [], "data_version": "test", "data_retrieved": None}
    at = AppTest.from_string(_render_horizon_script(result)).run()
    assert not at.exception
    markdown_html = "\n".join(m.value for m in at.markdown)
    assert "From retrieved news" not in markdown_html


def test_horizon_renders_news_signal_with_quote_source_and_date():
    result = {
        "signals": [],
        "checked_ids": [],
        "warnings": [],
        "data_version": "test",
        "data_retrieved": None,
        "news_signals": [_NEWS_SIGNAL],
    }
    at = AppTest.from_string(_render_horizon_script(result)).run()
    assert not at.exception
    markdown_html = "\n".join(m.value for m in at.markdown)

    assert "From retrieved news" in markdown_html
    assert "not vetted by a person" in markdown_html
    assert "EFSA opens review of titanium dioxide" in markdown_html  # the verbatim quote
    assert "https://efsa.europa.eu/example" in markdown_html  # the source link
    assert "2026-06-01" in markdown_html  # the published date
    assert "Under regulatory review" in markdown_html  # the category label
    assert "E171" in markdown_html


def test_horizon_news_signal_flags_render_as_a_warning_badge():
    flagged_signal = {**_NEWS_SIGNAL, "flags": ["stale_cache_served"]}
    result = {
        "signals": [],
        "checked_ids": [],
        "warnings": [],
        "data_version": "test",
        "data_retrieved": None,
        "news_signals": [flagged_signal],
    }
    at = AppTest.from_string(_render_horizon_script(result)).run()
    assert not at.exception
    markdown_html = "\n".join(m.value for m in at.markdown)
    assert "stale_cache_served" in markdown_html
    assert "eu-badge warn" in markdown_html


# ---- Route news signals (India -> EU, whole-category) -------------------

_ROUTE_NEWS_SIGNAL = {
    "origin": "India",
    "category_name": "Herbs and spices",
    "category": "import_control",
    "quoted_span": "the EU has increased official controls on Indian spice consignments",
    "source_url": "https://ec.europa.eu/example-route",
    "published_date": "2026-05-01",
    "search_query": "Indian Herbs and spices exports EU import rules",
    "retrieved_at": "2026-08-04",
    "severity": "advisory",
    "affects_verdict": False,
    "flags": [],
}


def test_route_news_renders_nothing_when_lane_never_ran():
    # route_news_category is None -- the lane never touched this
    # HorizonResult at all (e.g. the legacy non-graph fallback path, or a
    # provider that was never configured before the field existed).
    result = {"signals": [], "checked_ids": [], "warnings": [], "data_version": "test", "data_retrieved": None}
    at = AppTest.from_string(_render_horizon_script(result)).run()
    assert not at.exception
    markdown_html = "\n".join(m.value for m in at.markdown)
    assert "Import route" not in markdown_html


def test_route_news_shows_what_was_searched_when_nothing_found():
    # The lane RAN (route_news_category is set) but found nothing -- must
    # still say what was searched, not render an absent section.
    result = {
        "signals": [],
        "checked_ids": [],
        "warnings": [],
        "data_version": "test",
        "data_retrieved": None,
        "route_news_signals": [],
        "route_news_category": "Herbs and spices",
    }
    at = AppTest.from_string(_render_horizon_script(result)).run()
    assert not at.exception
    markdown_html = "\n".join(m.value for m in at.markdown)
    assert "Import route" in markdown_html
    captions = [c.value for c in at.caption]
    assert "No recent EU news found about Herbs and spices exports from India in this category." in captions


def test_route_news_renders_a_signal_with_quote_source_and_category_label():
    result = {
        "signals": [],
        "checked_ids": [],
        "warnings": [],
        "data_version": "test",
        "data_retrieved": None,
        "route_news_signals": [_ROUTE_NEWS_SIGNAL],
        "route_news_category": "Herbs and spices",
    }
    at = AppTest.from_string(_render_horizon_script(result)).run()
    assert not at.exception
    markdown_html = "\n".join(m.value for m in at.markdown)

    assert "Import route: India" in markdown_html
    assert "never about one specific additive" in markdown_html
    assert "the EU has increased official controls on Indian spice consignments" in markdown_html
    assert "https://ec.europa.eu/example-route" in markdown_html
    assert "2026-05-01" in markdown_html
    assert "EU import control" in markdown_html  # the category label
    assert "India" in markdown_html and "Herbs and spices" in markdown_html
    # NEVER an eu_canonical_id/E-number -- a route signal is not about one additive.
    assert "eu_canonical_id" not in markdown_html


def test_route_news_flags_render_as_a_warning_badge():
    flagged_signal = {**_ROUTE_NEWS_SIGNAL, "flags": ["stale_cache_served"]}
    result = {
        "signals": [],
        "checked_ids": [],
        "warnings": [],
        "data_version": "test",
        "data_retrieved": None,
        "route_news_signals": [flagged_signal],
        "route_news_category": "Herbs and spices",
    }
    at = AppTest.from_string(_render_horizon_script(result)).run()
    assert not at.exception
    markdown_html = "\n".join(m.value for m in at.markdown)
    assert "stale_cache_served" in markdown_html
    assert "eu-badge warn" in markdown_html


def test_route_news_and_additive_news_render_as_visually_distinct_sections():
    result = {
        "signals": [],
        "checked_ids": [],
        "warnings": [],
        "data_version": "test",
        "data_retrieved": None,
        "news_signals": [_NEWS_SIGNAL],
        "route_news_signals": [_ROUTE_NEWS_SIGNAL],
        "route_news_category": "Herbs and spices",
    }
    at = AppTest.from_string(_render_horizon_script(result)).run()
    assert not at.exception
    markdown_html = "\n".join(m.value for m in at.markdown)
    # Two separate section titles -- a reader is never left to guess which
    # section a given item belongs to.
    assert "Additive news" in markdown_html
    assert "Import route: India" in markdown_html
    assert "Titanium dioxide" in markdown_html  # the additive-scoped signal
    assert "Herbs and spices" in markdown_html  # the route-scoped signal


def test_regulatory_horizon_efsa_lane_never_renders_on_screen():
    # TASK: the hand-curated EFSA "Regulatory horizon" lane matched
    # nothing on every label tried -- removed from the screen entirely,
    # even when result["signals"]/["warnings"] are non-empty (still kept
    # for the PDF/JSON exports -- see render_horizon's own docstring).
    result = {
        "signals": [
            {
                "eu_canonical_id": "955",
                "substance_name": "Sucralose",
                "stage": "efsa_opinion",
                "title": "EFSA opinion on sucralose",
                "publication_date": "2025",
                "doi": "10.2903/j.efsa.2025.0001",
                "doi_verified": True,
                "source_url": "https://doi.org/10.2903/j.efsa.2025.0001",
                "years_old": 1,
                "severity": "advisory",
                "affects_verdict": False,
                "note": None,
                "flags": [],
            }
        ],
        "checked_ids": ["955", "171"],
        "warnings": ["Coverage is PARTIAL: only additives in the curated dataset are checked."],
        "data_version": "test",
        "data_retrieved": "2026-08-01",
        "news_signals": [_NEWS_SIGNAL],
    }
    at = AppTest.from_string(_render_horizon_script(result)).run()
    assert not at.exception
    markdown_html = "\n".join(m.value for m in at.markdown)
    assert "Regulatory horizon" not in markdown_html
    assert "Sucralose" not in markdown_html  # the EFSA (curated) signal
    assert "PARTIAL" not in markdown_html
    assert "Titanium dioxide" in markdown_html  # the retrieved news signal still renders
    assert "From retrieved news" in markdown_html  # the distinct provenance label


def test_horizon_about_these_signals_expander_is_gone():
    # TASK: the "About these signals" expander was removed from the
    # results screen entirely (the disclaimer/warnings it held still
    # reach the PDF/JSON exports -- see render_horizon's own docstring).
    result = {
        "signals": [],
        "checked_ids": [],
        "warnings": ["Some signals could not be verified."],
        "data_version": "test",
        "data_retrieved": None,
        "news_signals": [_NEWS_SIGNAL],
    }
    at = AppTest.from_string(_render_horizon_script(result)).run()
    assert not at.exception
    assert at.expander == []


# ---- Review queue: agent-assisted identity resolution (table) -----------

# Two static items, no proposal asked yet -- exercises app.render_agent_
# review_queue directly with the minimal session_state it actually needs
# for the "not yet asked" render path (agent_proposals/agent_decisions are
# setdefault'd by the function itself); the heavier resolution/extraction/
# verdict state is only touched once a button is actually clicked, which
# none of these tests do (that would mean a real LLM call).
_REVIEW_ITEMS_SCRIPT = """
import app

items = [
    {"item_id": 1, "additive_name": "MODIFIED CORNSTARCH", "component_label": "SEASONING", "flags": []},
    {"item_id": 2, "additive_name": "MODIFIED CORNSTARCH", "component_label": None, "flags": []},
]
app.render_agent_review_queue(items, {}, {})
"""


def test_review_queue_renders_as_a_table_one_row_per_item():
    # TASK: the review queue is a table, one row per real item -- never
    # merged, even when two items share a declared name (see the Where
    # column below), matching the additives table's own row-per-item
    # design.
    at = AppTest.from_string(_REVIEW_ITEMS_SCRIPT).run(timeout=15)  # cold "import app" -- see the email-section tests' comment
    assert not at.exception
    markdown_html = "\n".join(m.value for m in at.markdown)
    assert markdown_html.count("MODIFIED CORNSTARCH") == 2  # header row excluded -- Ingredient is its own column
    assert "Ingredient" in markdown_html and "Possible matches" in markdown_html and "Status" in markdown_html


def test_review_queue_duplicate_names_show_distinct_component_context():
    # MEASURED case: "MODIFIED CORNSTARCH" declared once inside the
    # SEASONING bracket and once at product level -- two genuinely
    # separate items, distinguished by the Where column (its own table
    # column, not folded into the Ingredient cell).
    at = AppTest.from_string(_REVIEW_ITEMS_SCRIPT).run()
    assert not at.exception
    captions = [c.value for c in at.caption]
    assert "SEASONING" in captions
    assert "whole product" in captions


def _review_proposal_script(proposal_literal: str, decision: str | None) -> str:
    decision_line = f'st.session_state.agent_decisions = {{1: {decision!r}}}' if decision else ""
    return f"""
import streamlit as st
import app
from src.agent.resolver_agent import AgentProposal

st.session_state.agent_proposals = {{1: {proposal_literal}}}
{decision_line}

items = [
    {{"item_id": 1, "additive_name": "MODIFIED CORNSTARCH", "component_label": "SEASONING", "flags": []}},
]
app.render_agent_review_queue(items, {{}}, {{}})
"""


_DECLINED_PROPOSAL = (
    "AgentProposal(item_id=1, name_as_declared='MODIFIED CORNSTARCH', proposed_canonical_ins=None, "
    "proposed_classification='unknown', confidence='low', "
    "reasoning='The label merely states MODIFIED CORNSTARCH without specifying the exact chemical "
    "treatment or INS number.', evidence=[], tool_calls=[], declined=True, "
    "decline_reason='The ingredient declaration is generic and can correspond to any of 17 different "
    "Codex INS numbers; there is insufficient information on the label.')"
)

_ACCEPTABLE_PROPOSAL = (
    "AgentProposal(item_id=1, name_as_declared='Silicon dioxide', proposed_canonical_ins='551', "
    "proposed_classification='additive', confidence='high', "
    "reasoning='The declared name unambiguously matches a single Codex INS entry.', "
    "evidence=[], tool_calls=[], declined=False, decline_reason=None)"
)


def test_pending_proposal_row_hides_detail_until_review_is_clicked():
    # TASK: the review queue is a table first -- detail (proposal,
    # reasoning, Accept/Reject) only on demand. A row with a pending
    # proposal must not dump its decline reason or Accept/Reject onto the
    # screen unconditionally; only a "Review" button, until clicked.
    at = AppTest.from_string(_review_proposal_script(_DECLINED_PROPOSAL, None)).run()
    assert not at.exception
    captions = [c.value for c in at.caption]
    assert "Assistant declined" in captions
    assert not any("17 different Codex INS numbers" in c for c in captions)
    assert at.expander == []
    assert [b.label for b in at.button] == ["Ask the assistant about the whole queue", "Review"]


def test_declined_proposal_has_no_why_expander_just_the_reason_once():
    # TASK: the blue decline reason and the "Why" expander said the same
    # thing in different words -- keep ONE. A declined proposal's
    # reasoning adds nothing beyond decline_reason, so it gets no expander.
    at = AppTest.from_string(_review_proposal_script(_DECLINED_PROPOSAL, None)).run()
    at = at.button(key="agent_review_toggle_1").click().run()
    assert not at.exception
    assert at.expander == []
    captions = [c.value for c in at.caption]
    assert any("17 different Codex INS numbers" in c for c in captions)


def test_real_proposal_keeps_its_why_expander():
    # The inverse: a non-declined proposal's reasoning genuinely adds
    # detail the headline doesn't -- its "Why" expander must stay, once
    # "Review" reveals it.
    at = AppTest.from_string(_review_proposal_script(_ACCEPTABLE_PROPOSAL, None)).run()
    at = at.button(key="agent_review_toggle_1").click().run()
    assert not at.exception
    assert len(at.expander) == 1
    assert at.expander[0].label == "Why"


def test_dismissed_decline_shows_next_step_not_a_dead_end_button():
    # TASK: after a decline is dismissed there is nothing left to click --
    # re-asking the assistant would only decline again -- so the button is
    # replaced by the real next step (ask the supplier), not left dangling.
    at = AppTest.from_string(_review_proposal_script(_DECLINED_PROPOSAL, "rejected")).run()
    assert not at.exception
    # The only button left is the section-level "ask about the whole
    # queue" -- nothing item-scoped, since this item has nothing left to
    # click.
    assert [b.label for b in at.button] == ["Ask the assistant about the whole queue"]
    captions = [c.value for c in at.caption]
    assert any("ask the supplier" in c.lower() for c in captions)


def test_rejected_real_proposal_keeps_ask_again_button():
    # TASK: rejecting a real proposal is NOT terminal -- "Ask again" must
    # still be offered, unlike the dismissed-decline case above.
    at = AppTest.from_string(_review_proposal_script(_ACCEPTABLE_PROPOSAL, "rejected")).run()
    assert not at.exception
    assert "Ask again" in [b.label for b in at.button]


# ---- Substitutes (src/ui/components.py's render_substitutes) ------------


def _substitute_candidate(eu_canonical_id, **overrides):
    base = {
        "eu_canonical_id": eu_canonical_id,
        "additive_name": f"Additive {eu_canonical_id}",
        "verdict": "permitted_qs",
        "max_level_mg_kg": None,
        "conditions": None,
        "source_url": None,
        "shared_functional_classes": ["Colour"],
        "flags": [],
    }
    base.update(overrides)
    return base


def _render_substitutes_script(result: dict) -> str:
    return f"""
from src.ui import components

components.render_substitutes({result!r})
"""


def test_substitutes_table_has_no_flags_column():
    # TASK: internal vocabulary ("multiple_provisions_apply: 2, function
    # inferred from sub-types") does not belong in the UI table -- flags
    # stay in the JSON/CSV/PDF exports only.
    result = {
        "suggestions": [
            {
                "item_id": 1,
                "blocked_eu_canonical_id": "999",
                "blocked_name": "Blocked additive",
                "blocked_reason": "not_authorised_eu",
                "fcs_code": "12.2.2",
                "candidates": [
                    _substitute_candidate("100", flags=["classes_from_subtypes", "under_efsa_review"]),
                ],
                "no_candidates_reason": None,
            }
        ],
        "warnings": [],
    }
    at = AppTest.from_string(_render_substitutes_script(result)).run()
    assert not at.exception
    markdown_html = "\n".join(m.value for m in at.markdown)
    assert "<th>Flags</th>" not in markdown_html
    assert "classes_from_subtypes" not in markdown_html
    assert "function inferred from sub-types" not in markdown_html


def test_substitutes_identical_conditions_render_once_not_per_candidate():
    # TASK: five candidates carrying byte-identical Group II conditions
    # text must produce ONE expander for the whole block, not five.
    shared = "Permitted via Group II, Colours; quantum satis."
    result = {
        "suggestions": [
            {
                "item_id": 1,
                "blocked_eu_canonical_id": "999",
                "blocked_name": "Blocked additive",
                "blocked_reason": "not_authorised_eu",
                "fcs_code": "12.2.2",
                "candidates": [
                    _substitute_candidate(str(code), conditions=shared) for code in (100, 101, 102, 103, 104)
                ],
                "no_candidates_reason": None,
            }
        ],
        "warnings": [],
    }
    at = AppTest.from_string(_render_substitutes_script(result)).run()
    assert not at.exception
    assert len(at.expander) == 1
    assert "shared by" in at.expander[0].label


def test_substitutes_genuinely_different_conditions_render_separately():
    result = {
        "suggestions": [
            {
                "item_id": 1,
                "blocked_eu_canonical_id": "999",
                "blocked_name": "Blocked additive",
                "blocked_reason": "not_authorised_eu",
                "fcs_code": "12.2.2",
                "candidates": [
                    _substitute_candidate("100", conditions="Permitted via Group II, Colours; quantum satis."),
                    _substitute_candidate("160a", conditions="Permitted subject to a maximum of 100 mg/kg."),
                ],
                "no_candidates_reason": None,
            }
        ],
        "warnings": [],
    }
    at = AppTest.from_string(_render_substitutes_script(result)).run()
    assert not at.exception
    assert len(at.expander) == 2


def test_substitutes_uncovered_warning_is_not_shown_on_screen():
    # TASK: "N additives could not be assessed as substitutes" is internal
    # codex_ins-coverage bookkeeping -- removed from the screen, kept in
    # the JSON export (SubstituteResult.warnings, untouched).
    result = {
        "suggestions": [
            {
                "item_id": 1,
                "blocked_eu_canonical_id": "999",
                "blocked_name": "Blocked additive",
                "blocked_reason": "not_authorised_eu",
                "fcs_code": "12.2.2",
                "candidates": [_substitute_candidate("100")],
                "no_candidates_reason": None,
            }
        ],
        "warnings": ["E123 excluded -- functional class unknown"],
    }
    at = AppTest.from_string(_render_substitutes_script(result)).run()
    assert not at.exception
    markdown_html = "\n".join(m.value for m in at.markdown)
    captions = [c.value for c in at.caption]
    assert "could not be assessed as substitutes" not in markdown_html
    assert not any("could not be assessed as substitutes" in c for c in captions)
    assert not any("E123 excluded" in c for c in captions)
    assert [e.label for e in at.expander] == []
