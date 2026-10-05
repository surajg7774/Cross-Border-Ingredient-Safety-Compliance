# DESIGN RULE: pure functions over plain lists/strings passed in as
# arguments -- no file reads, no aggregation across rows. Aggregation
# (across all label-component pairs, not per label) is the caller's job.
"""Retrieval metrics for the category stage: recall@k and reciprocal rank,
scored per query (one label, or one composite component)."""


def recall_at_k(predictions: list[str], truth: str, k: int) -> float:
    """1.0 if `truth` appears among the first k of `predictions`, else 0.0."""
    return 1.0 if truth in predictions[:k] else 0.0


def mean_reciprocal_rank(predictions: list[str], truth: str) -> float:
    """This query's reciprocal-rank contribution: 1/rank of `truth` in
    `predictions` (1-indexed), or 0.0 if `truth` is absent.

    Named for the metric it feeds, not what it computes alone -- the MEAN
    over all label-component pairs is the caller's job (aggregate across
    every component, not per label: a composite label contributes one
    reciprocal rank per component, not one for the whole label).
    """
    if truth in predictions:
        return 1.0 / (predictions.index(truth) + 1)
    return 0.0
