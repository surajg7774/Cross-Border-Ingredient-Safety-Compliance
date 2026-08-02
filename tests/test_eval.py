"""Tests for src/eval/category_metrics.py. Hand-built rankings, no fixture files."""

from src.eval.category_metrics import mean_reciprocal_rank, recall_at_k


def test_recall_at_k_hit_within_k():
    predictions = ["5.1", "5.2", "5"]
    assert recall_at_k(predictions, "5.2", k=3) == 1.0
    assert recall_at_k(predictions, "5.2", k=1) == 0.0  # correct, but not ranked first


def test_recall_at_k_miss():
    predictions = ["5.1", "5.2", "5"]
    assert recall_at_k(predictions, "14.1.4", k=3) == 0.0


def test_recall_at_k_empty_predictions():
    assert recall_at_k([], "5.1", k=3) == 0.0


def test_mrr_first_place():
    assert mean_reciprocal_rank(["5.1", "5.2", "5"], "5.1") == 1.0


def test_mrr_second_place():
    assert mean_reciprocal_rank(["5.1", "5.2", "5"], "5.2") == 0.5


def test_mrr_third_place():
    predictions = ["5.1", "5.2", "5"]
    assert mean_reciprocal_rank(predictions, "5") == 1.0 / 3.0


def test_mrr_absent_contributes_zero():
    # The correct answer never appears in the ranking at all -- must
    # contribute 0.0, not raise (e.g. ValueError from a bare .index() call).
    predictions = ["5.1", "5.2", "5"]
    assert mean_reciprocal_rank(predictions, "14.1.4") == 0.0


def test_mrr_empty_predictions_contributes_zero():
    assert mean_reciprocal_rank([], "5.1") == 0.0
