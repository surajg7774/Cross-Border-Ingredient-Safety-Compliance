"""Tests for src/reference_data.py -- hand-built dicts, no file I/O for
clean_eu_fip_rows (load_eu_fip's own file-reading wrapper is exercised
separately, against a real temp file)."""

from src.reference_data import clean_eu_fip_rows, load_eu_fip


def _row(canonical_id="300", conditions=None, additive_name="Test Additive"):
    return {
        "canonical_id": canonical_id,
        "additive_name": additive_name,
        "food_category_raw": "9.1.1 unprocessed fish",
        "status": "permitted",
        "max_level_mg_kg": None,
        "max_level_basis": "gmp",
        "conditions": conditions,
        "note_codes": [],
        "source_url": "https://example.com/eu-fip",
        "effective_date": "2020-01-01",
    }


def test_nbsp_only_conditions_normalized_to_none():
    rows = clean_eu_fip_rows([_row(conditions="&nbsp;")])
    assert rows[0]["conditions"] is None


def test_real_conditions_text_untouched():
    rows = clean_eu_fip_rows([_row(conditions="only tuna")])
    assert rows[0]["conditions"] == "only tuna"


def test_conditions_with_embedded_nbsp_and_real_text_keeps_the_text():
    # nbsp appears mid-clause, real content survives on both sides -- not
    # a placeholder, must not be blanked.
    text = "Permitted via Group I&nbsp;; only vegetables"
    rows = clean_eu_fip_rows([_row(conditions=text)])
    assert rows[0]["conditions"] == text


def test_conditions_legitimate_html_subscripts_untouched():
    # <sub>/<em> in eu_fip's own conditions prose (chemical-formula
    # subscripts, emphasis) is real source formatting, not scraping cruft
    # -- must never be stripped.
    text = "Note 4: The maximum level is expressed as P<sub>2</sub>O<sub>5</sub>"
    rows = clean_eu_fip_rows([_row(conditions=text)])
    assert rows[0]["conditions"] == text


def test_additive_name_html_wrapper_and_nbsp_cleaned():
    # MEASURED real row (canonical_id 334): a leaked <p>...</p> wrapper
    # with a trailing &nbsp; before the closing tag.
    rows = clean_eu_fip_rows([_row(additive_name="<p>Tartaric acid – tartrates&nbsp;</p>")])
    assert rows[0]["additive_name"] == "Tartaric acid – tartrates"


def test_additive_name_without_markup_untouched():
    rows = clean_eu_fip_rows([_row(additive_name="Ascorbic acid")])
    assert rows[0]["additive_name"] == "Ascorbic acid"


def test_none_conditions_and_additive_name_pass_through():
    row = _row(conditions=None)
    row["additive_name"] = None
    rows = clean_eu_fip_rows([row])
    assert rows[0]["conditions"] is None
    assert rows[0]["additive_name"] is None


def test_load_eu_fip_missing_file_returns_empty_list(tmp_path):
    assert load_eu_fip(tmp_path / "does_not_exist.json") == []


def test_load_eu_fip_reads_and_cleans_a_real_file(tmp_path):
    import json

    path = tmp_path / "eu_fip.json"
    path.write_text(json.dumps([_row(conditions="&nbsp;")]), encoding="utf-8")
    rows = load_eu_fip(path)
    assert rows[0]["conditions"] is None


def test_clean_eu_fip_rows_does_not_mutate_the_input():
    original = _row(conditions="&nbsp;")
    clean_eu_fip_rows([original])
    assert original["conditions"] == "&nbsp;"
