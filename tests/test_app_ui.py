"""Tests exercising app.py's rendering through streamlit.testing.v1.AppTest --
unlike tests/test_components.py's pure-function tests, these need a real
Streamlit runtime, so they get their own file.
"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

_DOSAGE_ITEM = {
    "by_category": [
        {
            "fcs_code": "3",
            "category_name": "edible ices",
            "rank": 1,
            "verdict": "permitted_with_limit",
            "max_level_mg_kg": 100.0,
            "max_level_basis": "mg/kg",
            "conditions": None,
            "note_codes": [],
            "source_url": None,
            "retrieved_date": None,
        }
    ],
    "flags": [],
}


def _render_caveats_script(narration_model_id: str, narration_summary: str) -> str:
    """A minimal script reproducing render_results' narration -> caveats
    sequence -- narration.summary/model_id are rendered exactly as
    render_results renders them (same st.markdown/st.caption calls, same
    text app.py itself would produce for this model_id), then
    components.render_verdict_caveats is called with an item carrying a
    real max_level_mg_kg. The ONLY thing that varies between calls of this
    helper is the narration state; the item list (and therefore the
    caveat) never changes."""
    written_by = f"Written by {narration_model_id} from the assessment above. It adds no facts."
    return f"""
import streamlit as st
from src.ui import components

st.markdown({narration_summary!r})
st.caption({written_by!r})
components.render_verdict_caveats([{_DOSAGE_ITEM!r}])
"""


def test_dosage_caveat_renders_when_narration_succeeded():
    at = AppTest.from_string(
        _render_caveats_script("gemini-3.5-flash", "This product is not blocked.")
    ).run()
    captions = [c.value for c in at.caption]
    assert any("dosage" in c.lower() for c in captions)


def test_dosage_caveat_renders_when_narration_fell_back():
    # narrate()'s own fallback shape (src/report/narrator.py): model_id
    # "unavailable", summary set to verdict.summary verbatim. The caveat
    # must render identically here -- it does not read narration at all,
    # so there is nothing in this branch that COULD suppress it, but this
    # is the guard against a future change accidentally coupling the two.
    fallback_summary = "12 item(s) evaluated. No item was found not permitted."
    at = AppTest.from_string(_render_caveats_script("unavailable", fallback_summary)).run()
    captions = [c.value for c in at.caption]
    assert any("dosage" in c.lower() for c in captions)


def test_dosage_caveat_identical_regardless_of_narration_state():
    at_success = AppTest.from_string(
        _render_caveats_script("gemini-3.5-flash", "This product is not blocked.")
    ).run()
    at_fallback = AppTest.from_string(
        _render_caveats_script("unavailable", "12 item(s) evaluated.")
    ).run()

    dosage_success = next(c.value for c in at_success.caption if "dosage" in c.value.lower())
    dosage_fallback = next(c.value for c in at_fallback.caption if "dosage" in c.value.lower())
    assert dosage_success == dosage_fallback


# ---- Group I/II/... shared-block dedup (app.py's render_results wiring) ---

# Two whitespace variants of the exact same clause, exactly as eu_fip
# actually stores it in more than one form (see
# tests/test_components.py's _normalize_conditions tests for the measured
# variant count against the real data).
_GROUP_CLAUSE_A = "Permitted via Group I, Additives; ML = quantum satis; except E 425 ML = 10000 mg/kg."
_GROUP_CLAUSE_B = "Permitted via Group I, Additives; ML = quantum satis; except E 425 ML =\n10000 mg/kg."


def _group_conditions_item(eu_id, component, fcs_code, category_name, conditions):
    return {
        "eu_canonical_id": eu_id,
        "component_label": component,
        "flags": [],
        "confirmed_fcs_code": fcs_code,
        "by_category": [
            {
                "fcs_code": fcs_code,
                "category_name": category_name,
                "rank": 1,
                "verdict": "permitted_qs",
                "max_level_mg_kg": None,
                "max_level_basis": None,
                "conditions": conditions,
                "note_codes": [],
                "source_url": f"https://example.org/{eu_id}",
                "retrieved_date": "2020-01-01",
            }
        ],
    }


def _render_group_conditions_script() -> str:
    item_a = _group_conditions_item("300", "CRISPS", "14.1.4", "Flavoured drinks", _GROUP_CLAUSE_A)
    item_b = _group_conditions_item("301", "SEASONING", "12.2.2", "Seasonings", _GROUP_CLAUSE_B)
    return f"""
import streamlit as st
from src.ui import components

item_a = {item_a!r}
item_b = {item_b!r}
registry = components.render_group_conditions_block([item_a, item_b])
components.render_permitted_section("Permitted", [item_a, item_b], group_registry=registry)
"""


def test_group_conditions_render_once_with_a_reference_for_the_repeat():
    at = AppTest.from_string(_render_group_conditions_script()).run()
    assert not at.exception

    markdown_html = "\n".join(m.value for m in at.markdown)
    # The full clause (either whitespace variant collapses to this once
    # normalized) appears exactly once -- in the shared block -- not once
    # per additive that carries it.
    assert markdown_html.count("Permitted via Group I, Additives; ML = quantum satis") == 1
    # Both additives are still traceable: each links back to the shared
    # block instead of the text vanishing outright.
    assert (
        markdown_html.count(
            "see the <a href='#group-conditions-1'>Group I, Additives conditions above</a>"
        )
        == 2
    )
    # The shared block's anchor exists so that link actually resolves.
    assert "id='group-conditions-1'" in markdown_html
    # Every item keeps its OWN citation -- collapsing conditions to a
    # reference must not touch source/date, which differ per item.
    assert "https://example.org/300" in markdown_html
    assert "https://example.org/301" in markdown_html


def test_group_conditions_block_absent_when_no_item_carries_one():
    script = """
from src.ui import components

item = {
    "eu_canonical_id": "1",
    "component_label": None,
    "flags": [],
    "confirmed_fcs_code": "5.2",
    "by_category": [
        {
            "fcs_code": "5.2",
            "category_name": "Cocoa",
            "rank": 1,
            "verdict": "permitted_qs",
            "max_level_mg_kg": None,
            "max_level_basis": None,
            "conditions": "only tuna",
            "note_codes": [],
            "source_url": None,
            "retrieved_date": None,
        }
    ],
}
registry = components.render_group_conditions_block([item])
assert registry == {}
"""
    at = AppTest.from_string(script).run()
    assert not at.exception
    assert at.markdown == []


# ---- A4/A5: conditions text must never split on a raw newline, and a
# merged clause's numbering must start at 1, not 2 -- MEASURED real strings
# from data/outputs/verdict/ (Chipsmain E551, Ice-creammain Group I, and
# Pepsimain E150d/E330), not constructed text. Each is rendered through
# render_group_conditions_block, the same public entry point
# test_group_conditions_render_once_with_a_reference_for_the_repeat above
# uses, so this exercises the real call path, not just the pure helper. ---

# eu_fip's own "\n" mid-clause word-wrap -- NOT a clause boundary.
# MEASURED: previously rendered inline as "...Period of application:" with
# "until 31 July 2014" hidden behind a "Conditions of use" expander.
_PEPSI_E150D_CONDITIONS = (
    "Permitted via Group II, Colours at quantum satis; excluding chocolate milk and malt "
    "products  Period of application:\nuntil 31 July 2014"
)
# MEASURED: previously rendered inline as "...E 968 may" with "not be used
# except where specifically provided..." hidden in the expander.
_PEPSI_E330_CONDITIONS = (
    "Permitted via Group I, Additives; E 420, E 421, E 953, E 965, E 966 and E 967 may not be "
    "used  E 968 may\nnot be used except where specifically provided for in this food category"
)
# MEASURED: our OWN eu_fip.json data for this row ends exactly here, with no
# "\n" anywhere -- this case was never actually mis-split (partition("\n")
# on a string with no "\n" returns it whole); it is included because it is
# one of the four reported strings and must be confirmed to render whole,
# not because it exercises the split logic differently from any other
# single, un-merged clause.
_CHIPS_E551_CONDITIONS = (
    "Permitted via Silicon dioxide - silicates; only seasoning  Period of application:  from 1 "
    "February 2014; Note 1: The additives may be added individually or in combination"
)
# MEASURED, genuinely merged (2 distinct eu_fip rows for E 965/968 in
# Ice-creammain): previously rendered clause 1 inline as plain text and
# clause 2 in the expander via st.write(), which read the literal "2) "
# marker as a CommonMark ordered-list start -- a list beginning at "2."
# with no "1." anywhere.
_ICE_CREAM_GROUP_I_CONDITIONS = (
    "1) Permitted via Group I, Additives; ML = quantum satis; except E 425 ML = 10000 mg/kg; "
    "E 620 to E 625, ML =\n10000 mg/kg individually or in combination, expressed as glutamic "
    "acid;\nE 626 to E 635, ML = 500 mg/kg individually or in combination, expressed\nas "
    "guanylic acid.\n\n2) Permitted via Group IV, Polyols; only energy-reduced or with no "
    "added sugar"
)


def _render_single_conditions_script(eu_id: str, conditions: str) -> str:
    # render_verdict_row, not render_group_conditions_block: the latter
    # only renders items whose conditions are a PURE Group clause
    # (_pure_group_match) -- Chipsmain E551 (a named-substance clause, not
    # a Group one) and the Ice-cream merged 2-clause string (correctly
    # excluded by _pure_group_match's own merged-clause guard) would both
    # render NOTHING through it. render_verdict_row calls
    # _render_conditions_body unconditionally for any item's own
    # conditions (no group_registry passed here), so it exercises the
    # actual code path every item's conditions go through by default.
    item = _group_conditions_item(eu_id, None, "14.1.4", "Flavoured drinks", conditions)
    return f"""
from src.ui import components

item = {item!r}
components.render_verdict_row(item)
"""


def test_pepsi_e150d_conditions_render_whole_never_split_or_hidden():
    at = AppTest.from_string(
        _render_single_conditions_script("150d", _PEPSI_E150D_CONDITIONS)
    ).run()
    assert not at.exception
    markdown_html = "\n".join(m.value for m in at.markdown)
    # Both halves of the OLD lead/rest split appear TOGETHER, in the same
    # markdown call -- not one inline and the other missing from this join.
    assert "Period of application:" in markdown_html
    assert "until 31 July 2014" in markdown_html
    assert len(at.expander) == 0  # nothing hidden -- no "Conditions of use" expander at all


def test_pepsi_e330_conditions_render_whole_never_split_or_hidden():
    at = AppTest.from_string(
        _render_single_conditions_script("330", _PEPSI_E330_CONDITIONS)
    ).run()
    assert not at.exception
    markdown_html = "\n".join(m.value for m in at.markdown)
    assert "E 968 may" in markdown_html
    assert "not be used except where specifically provided" in markdown_html
    assert len(at.expander) == 0


def test_chips_e551_conditions_render_whole():
    at = AppTest.from_string(
        _render_single_conditions_script("551", _CHIPS_E551_CONDITIONS)
    ).run()
    assert not at.exception
    markdown_html = "\n".join(m.value for m in at.markdown)
    assert "Note 1: The additives may be added individually or in combination" in markdown_html
    assert len(at.expander) == 0


def test_ice_cream_group_i_merged_clauses_render_as_one_list_starting_at_1():
    at = AppTest.from_string(
        _render_single_conditions_script("965", _ICE_CREAM_GROUP_I_CONDITIONS)
    ).run()
    assert not at.exception
    markdown_html = "\n".join(m.value for m in at.markdown)
    # Both clauses present, together -- clause 2 is not hidden in an expander.
    assert "Permitted via Group I, Additives; ML = quantum satis" in markdown_html
    assert "Permitted via Group IV, Polyols; only energy-reduced" in markdown_html
    assert len(at.expander) == 0
    # Rendered as one native ordered list, not two differently-styled
    # blocks -- exactly one <ol>, two <li>s.
    assert markdown_html.count("<ol") == 1
    assert markdown_html.count("<li>") == 2
    # The literal "2) " marker text is stripped before display -- the ONLY
    # numbering the reader sees is the <ol>'s own, which starts at 1 by
    # construction; the raw marker must not survive as visible text.
    assert "2) Permitted via Group IV" not in markdown_html


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


def test_horizon_efsa_table_and_news_signals_both_render_and_stay_distinct():
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
        "warnings": [],
        "data_version": "test",
        "data_retrieved": "2026-08-01",
        "news_signals": [_NEWS_SIGNAL],
    }
    at = AppTest.from_string(_render_horizon_script(result)).run()
    assert not at.exception
    markdown_html = "\n".join(m.value for m in at.markdown)
    assert "Sucralose" in markdown_html  # the EFSA (curated) signal
    assert "Titanium dioxide" in markdown_html  # the retrieved news signal
    assert "From retrieved news" in markdown_html  # the distinct provenance label
