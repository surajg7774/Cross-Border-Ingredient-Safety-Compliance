"""Tests exercising app.py's rendering through streamlit.testing.v1.AppTest --
unlike tests/test_components.py's pure-function tests, these need a real
Streamlit runtime, so they get their own file.
"""

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
