"""Tests for the code-matching logic in scripts.score_golden."""

from scripts.score_golden import normalise_code, score_codes


def test_normalise_code_treats_notations_as_equal():
    assert normalise_code("INS 470(i)") == normalise_code("470(i)") == normalise_code("E470(i)")


def test_multiset_comparison_counts_repeated_code_twice():
    golden_items = [{"declared_code": "442"}, {"declared_code": "442"}]
    output_items = [{"declared_code": "442"}, {"declared_code": "442"}]
    result = score_codes(golden_items, output_items)
    assert result["matched"] == 2
    assert result["total_golden"] == 2
    assert result["recall"] == 1.0


def test_missing_code_is_reported():
    golden_items = [{"declared_code": "E100"}, {"declared_code": "E471"}]
    output_items = [{"declared_code": "E471"}]
    result = score_codes(golden_items, output_items)
    assert result["missing"] == ["100"]
    assert result["recall"] == 0.5
