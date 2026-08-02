"""Tests for the bbox sanitiser in src.pipeline."""

from src.pipeline import _sanitise_bbox


def test_swapped_x_coordinates_are_repaired():
    result = _sanitise_bbox([0.8, 0.1, 0.2, 0.6])
    assert result == [0.2, 0.1, 0.8, 0.6]


def test_values_outside_unit_range_are_clamped():
    result = _sanitise_bbox([-0.5, -0.2, 1.5, 1.2])
    assert result == [0.0, 0.0, 1.0, 1.0]


def test_degenerate_sliver_box_returns_none():
    assert _sanitise_bbox([0.1, 0.1, 0.11, 0.9]) is None


def test_none_passes_through_as_none():
    assert _sanitise_bbox(None) is None


def test_valid_box_returned_unchanged():
    bbox = [0.1, 0.2, 0.8, 0.9]
    assert _sanitise_bbox(bbox) == bbox
