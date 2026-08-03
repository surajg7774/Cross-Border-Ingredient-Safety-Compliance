# DESIGN NOTE: _build_item_category_map is imported from scripts/verdict.py
# (a sibling script, importable because Python adds a running script's own
# directory to sys.path[0] -- true for every invocation this project
# documents, "uv run python scripts/score_verdict.py"), not duplicated.
# Unlike src/rules/engine.py's _category_code/_parent_codes (small, stable,
# pure -- see scripts/init_verdict_golden.py's own DESIGN NOTE for why THOSE
# are duplicated), this is 40+ lines of item/category join logic that must
# stay byte-identical to what scripts/verdict.py and app.py's --category
# actually do, or a golden-scored verdict silently stops meaning what the
# real pipeline produces. That is exactly the kind of duplicate-logic drift
# this project has already been burned by once (src/substitutes/advisor.py's
# stale row-selection copy, see docs/findings.md) -- reused, not repeated.
"""CLI entry point: score a FRESHLY-COMPUTED, PINNED verdict against
data/golden/verdict/ files.

    uv run python scripts/score_verdict.py

For each data/golden/verdict/<label>.json, loads the already-on-disk
data/outputs/resolution/<label>.json and data/outputs/category/<label>.json
(no pipeline run, no API call -- both are pure local reads) and computes
src.rules.engine.evaluate() itself, with confirmed_item_ids/category
overrides built from the golden file's pinned_categories -- NOT by reading
data/outputs/verdict/<label>.json, which reflects UNCONFIRMED retrieval, not
the pin. Scoring against the wrong verdict would defeat the entire reason
the categories are pinned (isolating the rule engine from category
retrieval, recall@1 measured ~0.53 elsewhere in this project).

SCORED_FIELDS mirrors scripts/score_resolution.py's pattern exactly:

    headline         -- the verdict category itself (permitted_qs /
                         permitted_with_limit / permitted_with_conditions /
                         not_permitted_in_category / not_authorised_eu /
                         out_of_scope / unresolved / category_unknown).
                         Deterministic given (resolved item, pinned
                         category, eu_fip) -- the actual compliance
                         conclusion.
    eu_canonical_id   -- WHICH substance the headline was judged against. A
                         correct-looking headline against the wrong
                         substance is not a correct verdict.
    blocked           -- item_id in ProductVerdict.blocking. THE single most
                         consequential downstream fact (does this item stop
                         the product), and NOT simply derivable from
                         headline text alone: blocking also requires
                         verdict_certainty == "certain", so scoring it
                         separately catches a class of bug headline-alone
                         would miss.

# DO NOT SCORE: conditions (free-text legal prose, already measured
# inconsistent/duplicated across eu_fip rows -- see docs/build_log.md
# Section J and its SUPERSEDED note), summary (a rendering, not a fact),
# source_url/retrieved_date (citation metadata about the eu_fip row, not a
# compliance judgment the engine made), max_level_mg_kg/max_level_basis
# (deterministic and compliance-bearing, deliberately left OUT of v1 to
# keep the hand-authoring burden down for a 5-label bootstrap -- a
# reasonable v2 addition, not scored here).

Any item whose golden value for a given field is the literal string
"UNSET" is SKIPPED for that field (not scored as a mismatch) -- the truth
set is authored incrementally, and a scorer that treats "not yet decided"
as "wrong" would be useless until every field of every item was filled in.
Skip counts are reported so partial coverage is visible, not silent.

Regression check: exits non-zero if this run's overall accuracy is LOWER
than the last row already in data/outputs/verdict_score.csv (append-only,
one row per run) -- not a fixed 1.0 threshold the way
scripts/score_golden.py's/score_resolution.py's `< 1.0` checks are; a
partially-authored truth set makes a fixed threshold meaningless (the
first-ever run against an all-UNSET golden file is vacuously 1.0, telling
you nothing). No prior row means nothing to regress against -- the run
just establishes the baseline. A truth_version change since the last row
(the golden files were edited -- more fields filled in, a pin corrected)
is WARNED, not blocked, same reasoning scripts/score_category.py already
gives for its own truth_version check: accuracy compared across different
truth is not apples-to-apples, but that is information for a human, not
grounds to refuse to record the run.
"""

import csv
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import typer
from rich.console import Console
from rich.table import Table
from verdict import _build_item_category_map

from config import settings
from src.category.classifier import item_component_labels
from src.category.schemas import CategoryCandidate, CategoryResult
from src.logging_setup import setup_run_log
from src.reference_data import load_eu_fip
from src.resolve.schemas import ResolvedItem
from src.rules.engine import evaluate

console = Console()

SCORED_FIELDS = ("headline", "eu_canonical_id", "blocked")
UNSET = "UNSET"

VERDICT_SCORE_CSV = settings.OUTPUT_DIR / "verdict_score.csv"
CSV_HEADER = [
    "timestamp",
    "labels_scored",
    "total_items",
    *[f"{field}_scored" for field in SCORED_FIELDS],
    *[f"{field}_acc" for field in SCORED_FIELDS],
    "overall_scored",
    "overall_skipped",
    "overall_accuracy",
    "truth_version",
]


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _overrides_from_pins(pinned_categories: dict[str, dict]) -> dict[str | None, CategoryCandidate]:
    """pinned_categories (JSON: component_label or "(product)" -> {"code",
    "name"}) into the dict[str | None, CategoryCandidate] shape
    _build_item_category_map expects -- the same shape scripts/verdict.py's
    --category builds, just read from the golden file instead of parsed
    from a CLI string. A pin with code=None (no rank-1 candidate was ever
    retrieved for that component) is skipped -- there is nothing to
    override with."""
    overrides = {}
    for key, pin in pinned_categories.items():
        if pin.get("code") is None:
            continue
        component_label = None if key == "(product)" else key
        overrides[component_label] = CategoryCandidate(
            code=pin["code"], name=pin.get("name") or pin["code"], similarity=1.0, permitted=True
        )
    return overrides


def _compute_pinned_verdict(label: str, pinned_categories: dict[str, dict], eu_fip: list[dict]):
    """The ProductVerdict for `label`, computed locally from already-on-disk
    resolution/extraction/category output with the golden file's pins
    applied -- no pipeline run, no API call. Returns None if the resolution
    file is missing (nothing to score against)."""
    resolution = _load_json(settings.RESOLUTION_OUTPUT_DIR / f"{label}.json")
    if resolution is None:
        return None
    extraction = _load_json(settings.EXTRACTION_OUTPUT_DIR / f"{label}.json")
    extraction_items = extraction["extraction"]["items"] if extraction and extraction.get("extraction") else []
    category_payload = _load_json(settings.CATEGORY_OUTPUT_DIR / f"{label}.json")
    category_results = (
        [CategoryResult.model_validate(r) for r in category_payload["results"]] if category_payload else []
    )

    resolved_items = [ResolvedItem.model_validate(item) for item in resolution["items"]]
    resolved_by_id = {item.item_id: item for item in resolved_items}
    item_labels = item_component_labels(extraction_items, resolved_by_id)

    overrides = _overrides_from_pins(pinned_categories)
    item_category_map, confirmed_item_ids = _build_item_category_map(
        resolved_items, category_results, overrides, item_labels
    )
    return evaluate(resolved_items, item_category_map, eu_fip, confirmed_item_ids)


def _actual_values(verdict) -> dict[int, dict]:
    """item_id -> {field: value} for every SCORED_FIELDS, off a freshly
    computed ProductVerdict. "blocked" is NOT an ItemVerdict field -- it is
    item_id in verdict.blocking, computed here, once, the same way for
    every item (see this module's docstring on why it is scored separately
    from headline)."""
    blocking = set(verdict.blocking)
    return {
        item.item_id: {
            "headline": item.headline,
            "eu_canonical_id": item.eu_canonical_id,
            "blocked": item.item_id in blocking,
        }
        for item in verdict.items
    }


def score_label(golden_items: list[dict], actual_by_id: dict[int, dict]) -> dict:
    """Per-field accuracy over SCORED_FIELDS, matched by item_id, skipping
    any field whose golden value is still UNSET. A golden item_id missing
    from the computed verdict entirely counts as a mismatch on every
    SCORED (non-UNSET) field -- same reasoning scripts/score_resolution.py
    already gives: a missing id means resolution changed under the golden
    file's feet, a real regression, not noise."""
    correct = dict.fromkeys(SCORED_FIELDS, 0)
    scored = dict.fromkeys(SCORED_FIELDS, 0)
    skipped = dict.fromkeys(SCORED_FIELDS, 0)
    mismatches = []
    for golden_item in golden_items:
        actual = actual_by_id.get(golden_item["item_id"])
        for field in SCORED_FIELDS:
            golden_value = golden_item[field]
            if golden_value == UNSET:
                skipped[field] += 1
                continue
            scored[field] += 1
            actual_value = actual[field] if actual else None
            if actual_value == golden_value:
                correct[field] += 1
            else:
                mismatches.append(
                    {"item_id": golden_item["item_id"], "field": field, "golden": golden_value, "actual": actual_value}
                )
    return {"correct": correct, "scored": scored, "skipped": skipped, "mismatches": mismatches}


def _truth_version(golden_paths: list[Path]) -> str:
    """Short fingerprint over every golden verdict file's raw bytes,
    computed fresh at scoring time -- same construction as
    scripts/score_category.py's _truth_version, extended to a directory of
    files (sorted for determinism) instead of one. Rows logged under
    different truth_versions were scored against different ground truth
    (more fields filled in, a pin corrected) and are not directly
    comparable, even when accuracy moved."""
    digest = hashlib.sha256()
    for path in sorted(golden_paths):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def _last_csv_row() -> dict | None:
    if not VERDICT_SCORE_CSV.exists():
        return None
    with VERDICT_SCORE_CSV.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows[-1] if rows else None


def _append_csv_row(row: dict) -> None:
    is_new = not VERDICT_SCORE_CSV.exists()
    VERDICT_SCORE_CSV.parent.mkdir(parents=True, exist_ok=True)
    with VERDICT_SCORE_CSV.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def main(
    log: Annotated[
        bool, typer.Option("--log/--no-log", help="Tee console output to a log file (default on).")
    ] = True,
) -> None:
    log_path = setup_run_log("score_verdict") if log else None
    golden_paths = sorted(settings.VERDICT_GOLDEN_DIR.glob("*.json"))
    if not golden_paths:
        console.print(f"[yellow]No golden files found in {settings.VERDICT_GOLDEN_DIR}[/yellow]")
        if log_path:
            console.print(f"Log: {log_path}")
        sys.exit(1)

    eu_fip = load_eu_fip(settings.REFERENCE_DIR / "eu_fip.json")

    rows = []
    totals_correct = dict.fromkeys(SCORED_FIELDS, 0)
    totals_scored = dict.fromkeys(SCORED_FIELDS, 0)
    totals_skipped = dict.fromkeys(SCORED_FIELDS, 0)
    total_items = 0

    for golden_path in golden_paths:
        label = golden_path.stem
        golden = json.loads(golden_path.read_text(encoding="utf-8"))

        verdict = _compute_pinned_verdict(label, golden.get("pinned_categories", {}), eu_fip)
        if verdict is None:
            console.print(f"[red]{label}: no data/outputs/resolution/{label}.json -- skipped.[/red]")
            continue

        actual_by_id = _actual_values(verdict)
        scored = score_label(golden["items"], actual_by_id)

        row = {
            "label": label,
            "total": len(golden["items"]),
            "mismatches": scored["mismatches"],
            "correct": scored["correct"],
            "scored": scored["scored"],
            "skipped": scored["skipped"],
        }
        for field in SCORED_FIELDS:
            totals_correct[field] += scored["correct"][field]
            totals_scored[field] += scored["scored"][field]
            totals_skipped[field] += scored["skipped"][field]
        total_items += len(golden["items"])
        rows.append(row)

    table = Table(title="Golden-set verdict scores (pinned categories)")
    table.add_column("Label")
    for field in SCORED_FIELDS:
        table.add_column(f"{field} acc")
        table.add_column(f"{field} skipped")
    table.add_column("Mismatches")
    for row in rows:
        cells = []
        for field in SCORED_FIELDS:
            acc = row["correct"][field] / row["scored"][field] if row["scored"][field] else None
            cells.append(f"{acc:.2f}" if acc is not None else "—")
            cells.append(str(row["skipped"][field]))
        table.add_row(row["label"], *cells, str(len(row["mismatches"])))
    console.print(table)

    for row in rows:
        if not row["mismatches"]:
            continue
        mismatch_table = Table(title=f"{row['label']} mismatches")
        mismatch_table.add_column("item_id")
        mismatch_table.add_column("field")
        mismatch_table.add_column("golden")
        mismatch_table.add_column("actual")
        for m in row["mismatches"]:
            mismatch_table.add_row(str(m["item_id"]), m["field"], str(m["golden"]), str(m["actual"]))
        console.print(mismatch_table)

    overall_correct = sum(totals_correct.values())
    overall_scored = sum(totals_scored.values())
    overall_skipped = sum(totals_skipped.values())
    overall_accuracy = overall_correct / overall_scored if overall_scored else 1.0
    field_summary = ", ".join(
        f"{field}: {(totals_correct[field] / totals_scored[field] if totals_scored[field] else 1.0):.2f} "
        f"({totals_scored[field]} scored, {totals_skipped[field]} skipped)"
        for field in SCORED_FIELDS
    )
    console.print(
        f"\n[bold]Aggregate[/bold] -- items: {total_items}, {field_summary}, "
        f"overall: {overall_accuracy:.2f} ({overall_scored} scored, {overall_skipped} skipped)"
    )
    if overall_scored == 0:
        console.print(
            "[yellow]Every scored field on every item is still UNSET -- nothing was actually compared. "
            "Fill in data/golden/verdict/*.json against the matching .worksheet.md files.[/yellow]"
        )

    truth_version = _truth_version(golden_paths)
    last_row = _last_csv_row()
    if last_row and last_row.get("truth_version") and last_row["truth_version"] != truth_version:
        console.print(
            f"[yellow]truth_version changed since the last logged row: was {last_row['truth_version']}, "
            f"now {truth_version}. Golden verdict files were edited -- this run's accuracy is not directly "
            "comparable to earlier rows.[/yellow]"
        )

    csv_row = {
        "timestamp": datetime.now(UTC).isoformat(),
        "labels_scored": len(rows),
        "total_items": total_items,
        **{
            f"{field}_scored": totals_scored[field]
            for field in SCORED_FIELDS
        },
        **{
            f"{field}_acc": (
                f"{(totals_correct[field] / totals_scored[field]):.4f}" if totals_scored[field] else ""
            )
            for field in SCORED_FIELDS
        },
        "overall_scored": overall_scored,
        "overall_skipped": overall_skipped,
        "overall_accuracy": f"{overall_accuracy:.4f}",
        "truth_version": truth_version,
    }
    _append_csv_row(csv_row)
    console.print(f"Appended run to [cyan]{VERDICT_SCORE_CSV}[/cyan]")

    if log_path:
        console.print(f"Log: {log_path}")

    if last_row and last_row.get("overall_accuracy"):
        last_accuracy = float(last_row["overall_accuracy"])
        if overall_accuracy < last_accuracy:
            console.print(
                f"[red]REGRESSION: overall accuracy {overall_accuracy:.4f} is below the last recorded run "
                f"({last_accuracy:.4f}).[/red]"
            )
            sys.exit(1)


if __name__ == "__main__":
    typer.run(main)
