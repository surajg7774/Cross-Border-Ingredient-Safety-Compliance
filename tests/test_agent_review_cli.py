"""Tests for scripts/agent_review.py's resume/--force logic (_scan_target).

No API calls, no LLM/agent involved -- these test the file-scanning/
skip-detection layer directly, against tmp_path fixtures monkeypatched onto
config.settings (the same pattern tests/test_email.py already uses for
settings attributes).
"""

import json
from pathlib import Path

import pytest

from config import settings
from scripts.agent_review import _scan_target


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def label_files(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "VERDICT_OUTPUT_DIR", tmp_path / "verdict")
    monkeypatch.setattr(settings, "RESOLUTION_OUTPUT_DIR", tmp_path / "resolution")
    monkeypatch.setattr(settings, "EXTRACTION_OUTPUT_DIR", tmp_path / "extraction")
    monkeypatch.setattr(settings, "AGENT_OUTPUT_DIR", tmp_path / "agent")

    verdict = {
        "items": [
            {"item_id": 1, "headline": "unresolved", "component_label": None},
            {"item_id": 2, "headline": "unresolved", "component_label": None},
        ],
        "category_used": {},
    }
    resolution = {
        "items": [
            {"item_id": 1, "classification": "unknown", "candidates": [], "flags": []},
            {"item_id": 2, "classification": "unknown", "candidates": [], "flags": []},
        ]
    }
    extraction = {
        "gate": {"product_name": "Test"},
        "extraction": {
            "items": [
                {"item_id": 1, "name_as_declared": "Item One", "verbatim": "Item One", "declared_code": None},
                {"item_id": 2, "name_as_declared": "Item Two", "verbatim": "Item Two", "declared_code": None},
            ]
        },
    }

    _write_json(tmp_path / "verdict" / "Test.json", verdict)
    _write_json(tmp_path / "resolution" / "Test.json", resolution)
    _write_json(tmp_path / "extraction" / "Test.json", extraction)
    return tmp_path


def _existing_proposal(item_id: int, name: str) -> dict:
    return {
        "item_id": item_id,
        "name_as_declared": name,
        "proposed_canonical_ins": None,
        "proposed_classification": "unknown",
        "confidence": "low",
        "reasoning": "a previous run's reasoning",
        "evidence": ["a previous run's evidence"],
        "tool_calls": [],
        "declined": True,
        "decline_reason": "a previous run's decline reason",
    }


def test_all_skips_items_already_present_in_output(label_files):
    existing = {
        "model_id": "m",
        "resolved_model_id": "m-v1",
        "proposals": [_existing_proposal(1, "Item One")],
    }
    _write_json(label_files / "agent" / "Test.json", existing)

    target = _scan_target(label_files / "verdict" / "Test.json", force=False)

    assert [item["item_id"] for item in target["pending"]] == [2]
    assert set(target["proposals_by_id"]) == {1}
    assert target["resolved_model_id"] == "m-v1"


def test_force_reprocesses_items_already_present_in_output(label_files):
    existing = {
        "model_id": "m",
        "resolved_model_id": "m-v1",
        "proposals": [_existing_proposal(1, "Item One")],
    }
    _write_json(label_files / "agent" / "Test.json", existing)

    target = _scan_target(label_files / "verdict" / "Test.json", force=True)

    assert {item["item_id"] for item in target["pending"]} == {1, 2}
    assert target["proposals_by_id"] == {}


def test_no_existing_output_means_everything_is_pending(label_files):
    target = _scan_target(label_files / "verdict" / "Test.json", force=False)

    assert {item["item_id"] for item in target["pending"]} == {1, 2}
    assert target["proposals_by_id"] == {}


def test_all_items_already_done_means_nothing_pending(label_files):
    existing = {
        "model_id": "m",
        "resolved_model_id": "m-v1",
        "proposals": [_existing_proposal(1, "Item One"), _existing_proposal(2, "Item Two")],
    }
    _write_json(label_files / "agent" / "Test.json", existing)

    target = _scan_target(label_files / "verdict" / "Test.json", force=False)

    assert target["pending"] == []
    assert set(target["proposals_by_id"]) == {1, 2}
