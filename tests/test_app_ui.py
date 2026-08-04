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
    at = AppTest.from_string(script).run()
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
    at = AppTest.from_string(script).run()
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
