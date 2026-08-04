"""Tests for app.py's pure helper functions -- no Streamlit runtime
required for these (unlike tests/test_app_ui.py's AppTest-based tests),
since they are plain string/dataclass functions. Mirrors the split
tests/test_components.py and tests/test_app_ui.py already establish for
src/ui/components.py.
"""

import app
from src.category.corpus import FoodCategory
from src.category.schemas import CategoryCandidate


def _category(code, name, description=None):
    return FoodCategory(code=code, name=name, description=description, parent_code=None)


def test_short_description_none_when_no_description():
    assert app._short_description(None) is None
    assert app._short_description("") is None


def test_short_description_returns_first_sentence_when_short():
    assert (
        app._short_description("Fruits and vegetables presented fresh from harvest.")
        == "Fruits and vegetables presented fresh from harvest."
    )


def test_short_description_drops_sentences_after_the_first():
    long_desc = "This category covers fresh meat. It excludes offal and other by-products entirely."
    short = app._short_description(long_desc)
    assert short == "This category covers fresh meat."
    assert "offal" not in short


def test_short_description_truncates_a_long_first_sentence_at_a_word_boundary():
    # MEASURED against the real corpus: one description's first sentence
    # alone runs 1087 characters -- first_sentence() alone is not a safe
    # bound for a chooser UI, so a second cut backs it up.
    long_first_sentence = "Meat preparations " + ("including various ingredients " * 20) + "and so on."
    short = app._short_description(long_first_sentence)
    assert len(short) <= app._DESCRIPTION_DISPLAY_LIMIT + 1  # +1 for the trailing ellipsis char
    assert short.endswith("…")
    assert not short[:-1].endswith(" ")  # cut at a word boundary, not mid-word


def test_candidate_option_label_includes_short_description_when_present():
    candidate = CategoryCandidate(code="4.1.1", name="entire fresh fruit and vegetables", similarity=0.63, permitted=True)
    categories = {"4.1.1": _category("4.1.1", "entire fresh fruit and vegetables", "Fruits and vegetables presented fresh from harvest.")}
    label = app._candidate_option_label(candidate, categories)
    assert label == "4.1.1 — entire fresh fruit and vegetables — Fruits and vegetables presented fresh from harvest."


def test_candidate_option_label_name_only_when_description_absent():
    # No placeholder text stands in for a missing description -- this is
    # the 7.2 "Fine bakery wares" case (no description in the source at
    # all) and the 2.3 "Vegetable oil pan spray" case (description
    # excluded as content for a different category) alike.
    candidate = CategoryCandidate(code="7.2", name="Fine bakery wares", similarity=0.63, permitted=True)
    categories = {"7.2": _category("7.2", "Fine bakery wares", None)}
    label = app._candidate_option_label(candidate, categories)
    assert label == "7.2 — Fine bakery wares"
    assert "None" not in label
    assert "—  —" not in label


def test_candidate_option_label_never_shows_a_similarity_score():
    # Regression against the old "{code} — {name}  (score {similarity})"
    # format -- scores moved to the "What was searched" expander.
    candidate = CategoryCandidate(code="15", name="Ready-to-eat savouries and snacks", similarity=0.63, permitted=True)
    categories = {"15": _category("15", "Ready-to-eat savouries and snacks", None)}
    label = app._candidate_option_label(candidate, categories)
    assert "score" not in label.lower()
    assert "0.63" not in label
