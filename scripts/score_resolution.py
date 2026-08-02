"""CLI entry point: score data/outputs/resolution/ against hand-corrected
data/golden/resolution/ files.

    uv run python scripts/score_resolution.py

# DO NOT SCORE: resolution_confidence, flags, matched_on, resolution_method,
# normalised_role, codex_functional_classes, candidates. These are internal
# diagnostics that legitimately change as the resolver's heuristics improve,
# without changing whether a verdict is correct -- only the three fields a
# compliance decision actually depends on are scored here, strictly.
"""

import csv
import json
import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import typer
from rich.console import Console
from rich.table import Table

from config import settings
from src.logging_setup import setup_run_log

console = Console()

SCORED_FIELDS = ("canonical_ins", "eu_canonical_id", "classification")


def score_file(golden_items: list[dict], output_items: list[dict]) -> dict:
    """Strict per-field accuracy over SCORED_FIELDS, matched by item_id.

    A golden item_id missing from output entirely counts as a mismatch on
    every scored field, not a skip -- the golden extraction it was resolved
    from is frozen, so a missing id means the resolver dropped or renumbered
    an item, a real regression, not noise.
    """
    output_by_id = {item["item_id"]: item for item in output_items}
    correct = dict.fromkeys(SCORED_FIELDS, 0)
    mismatches = []
    for golden_item in golden_items:
        output_item = output_by_id.get(golden_item["item_id"])
        for field in SCORED_FIELDS:
            output_value = output_item[field] if output_item else None
            if output_value == golden_item[field]:
                correct[field] += 1
            else:
                mismatches.append(
                    {
                        "item_id": golden_item["item_id"],
                        "field": field,
                        "golden": golden_item[field],
                        "output": output_value,
                    }
                )
    return {"correct": correct, "total": len(golden_items), "mismatches": mismatches}


def main(
    log: Annotated[
        bool, typer.Option("--log/--no-log", help="Tee console output to a log file (default on).")
    ] = True,
) -> None:
    log_path = setup_run_log("score_resolution") if log else None
    golden_paths = sorted(settings.RESOLUTION_GOLDEN_DIR.glob("*.json"))
    if not golden_paths:
        console.print(f"[yellow]No golden files found in {settings.RESOLUTION_GOLDEN_DIR}[/yellow]")
        if log_path:
            console.print(f"Log: {log_path}")
        sys.exit(1)

    rows = []
    totals = dict.fromkeys(SCORED_FIELDS, 0)
    total_items = 0

    for golden_path in golden_paths:
        output_path = settings.RESOLUTION_OUTPUT_DIR / golden_path.name
        if not output_path.exists():
            console.print(f"[red]No matching output for {golden_path.name} -- skipped[/red]")
            continue

        golden = json.loads(golden_path.read_text(encoding="utf-8"))
        output = json.loads(output_path.read_text(encoding="utf-8"))
        scored = score_file(golden["items"], output["items"])

        row = {"label": golden_path.stem, "total": scored["total"], "mismatches": scored["mismatches"]}
        for field in SCORED_FIELDS:
            row[f"{field}_acc"] = scored["correct"][field] / scored["total"] if scored["total"] else 1.0
            totals[field] += scored["correct"][field]
        total_items += scored["total"]
        rows.append(row)

    table = Table(title="Golden-set resolution scores")
    table.add_column("Label")
    for field in SCORED_FIELDS:
        table.add_column(field)
    table.add_column("Mismatches")
    for row in rows:
        table.add_row(
            row["label"],
            *[f"{row[f'{field}_acc']:.2f}" for field in SCORED_FIELDS],
            str(len(row["mismatches"])),
        )
    console.print(table)

    for row in rows:
        if not row["mismatches"]:
            continue
        mismatch_table = Table(title=f"{row['label']} mismatches")
        mismatch_table.add_column("item_id")
        mismatch_table.add_column("field")
        mismatch_table.add_column("golden")
        mismatch_table.add_column("output")
        for m in row["mismatches"]:
            mismatch_table.add_row(str(m["item_id"]), m["field"], str(m["golden"]), str(m["output"]))
        console.print(mismatch_table)

    overall_correct = sum(totals.values())
    overall_total = total_items * len(SCORED_FIELDS)
    overall_accuracy = overall_correct / overall_total if overall_total else 1.0
    field_summary = ", ".join(
        f"{field}: {(totals[field] / total_items if total_items else 1.0):.2f}" for field in SCORED_FIELDS
    )
    console.print(f"\n[bold]Aggregate[/bold] -- items: {total_items}, {field_summary}, overall: {overall_accuracy:.2f}")

    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = settings.OUTPUT_DIR / "resolution_score.csv"
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["label", *[f"{field}_acc" for field in SCORED_FIELDS], "mismatches", "total"]
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "label": row["label"],
                    **{f"{field}_acc": f"{row[f'{field}_acc']:.4f}" for field in SCORED_FIELDS},
                    "mismatches": len(row["mismatches"]),
                    "total": row["total"],
                }
            )
    console.print(f"Wrote scores to [cyan]{out_path}[/cyan]")

    if log_path:
        console.print(f"Log: {log_path}")

    if overall_accuracy < 1.0:
        sys.exit(1)


if __name__ == "__main__":
    typer.run(main)
