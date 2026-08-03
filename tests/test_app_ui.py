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
