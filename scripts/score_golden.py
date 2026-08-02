"""CLI entry point: score data/outputs/extraction/ against hand-corrected data/golden/extraction/ files.

# DO NOT SCORE: declared_role, verbatim, declaration_verbatim, confidence,
# allergen_statements, declaration_statements, footnotes, warnings. These are
# free text and vary between runs (e.g. "Emulsifiers" vs "Emulsifiors") without
# any compliance verdict depending on them, so no metric here compares them.
"""

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import typer
from rich.console import Console
from rich.table import Table

from config import settings
from src.logging_setup import setup_run_log

console = Console()


def normalise_code(code: str) -> str:
    """Normalise a declared_code for cross-notation comparison only.

    Lowercases, strips all whitespace, and removes a leading "ins" or "e"
    prefix, so "INS 470(i)", "470(i)" and "E470(i)" all normalise to the
    same value. Comparison-only — never used to alter extractor output.
    """
    normalised = code.lower().replace(" ", "")
    if normalised.startswith("ins"):
        return normalised[3:]
    if normalised.startswith("e"):
        return normalised[1:]
    return normalised


def code_multiset(items: list[dict]) -> Counter:
    """Multiset of normalised declared_code values, skipping items with no code."""
    return Counter(normalise_code(item["declared_code"]) for item in items if item["declared_code"])


def score_codes(golden_items: list[dict], output_items: list[dict]) -> dict:
    """Multiset recall/precision over declared_code — codes may legitimately repeat."""
    golden_counts = code_multiset(golden_items)
    output_counts = code_multiset(output_items)
    matched = sum((golden_counts & output_counts).values())
    total_golden = sum(golden_counts.values())
    total_output = sum(output_counts.values())
    return {
        "matched": matched,
        "total_golden": total_golden,
        "total_output": total_output,
        "missing": sorted((golden_counts - output_counts).elements()),
        "spurious": sorted((output_counts - golden_counts).elements()),
        "recall": matched / total_golden if total_golden else 1.0,
        "precision": matched / total_output if total_output else 1.0,
    }


def _normalise_name(name: str) -> str:
    return " ".join(name.lower().split()).rstrip(".,;:")


def item_key(item: dict) -> str:
    """Matching key for an item: normalised name_as_declared, else its code, else verbatim."""
    if item["name_as_declared"]:
        return _normalise_name(item["name_as_declared"])
    if item["declared_code"]:
        return f"code:{normalise_code(item['declared_code'])}"
    return f"verbatim:{_normalise_name(item['verbatim'])}"


def item_recall(golden_items: list[dict], output_items: list[dict]) -> float:
    """Fraction of golden items (by item_key, as a multiset) found in output.

    Recall only, not precision — over-extraction is acceptable in this
    system, under-extraction is not.
    """
    golden_keys = Counter(item_key(item) for item in golden_items)
    output_keys = Counter(item_key(item) for item in output_items)
    matched = sum((golden_keys & output_keys).values())
    total = sum(golden_keys.values())
    return matched / total if total else 1.0


def _parent_key(item: dict, items_by_id: dict) -> str:
    """The matching key of an item's parent, or 'root' for top-level items.

    item_id/parent_item_id are assigned per run and unstable across runs —
    structure agreement is judged by the parent's normalised identity, not
    a raw ID.
    """
    parent_id = item["parent_item_id"]
    if parent_id is None:
        return "root"
    parent = items_by_id.get(parent_id)
    return item_key(parent) if parent is not None else "root"


def structure_accuracy(golden_items: list[dict], output_items: list[dict]) -> float:
    """Fraction of items matched by item_key whose nesting_depth and parent agree.

    A repeated key (e.g. two "Sugar" entries) is paired in declaration
    order — an exact bipartite match on duplicates is unnecessary
    complexity for this harness.
    """
    golden_by_id = {item["item_id"]: item for item in golden_items}
    output_by_id = {item["item_id"]: item for item in output_items}

    golden_by_key = defaultdict(list)
    for item in golden_items:
        golden_by_key[item_key(item)].append(item)
    output_by_key = defaultdict(list)
    for item in output_items:
        output_by_key[item_key(item)].append(item)

    agree = 0
    matched = 0
    for key, g_list in golden_by_key.items():
        for g_item, o_item in zip(g_list, output_by_key.get(key, [])):
            matched += 1
            if (
                g_item["nesting_depth"] == o_item["nesting_depth"]
                and _parent_key(g_item, golden_by_id) == _parent_key(o_item, output_by_id)
            ):
                agree += 1
    return agree / matched if matched else 1.0


def stop_category(result: dict) -> str:
    """Classify why (if at all) extraction stopped, from structural gate fields —
    not the free-text stop_reason, which varies in wording between runs."""
    gate = result["gate"]
    if not gate["is_food_label"]:
        return "not_food_label"
    if not gate["has_ingredients_declaration"]:
        return "no_ingredients_declaration"
    if result["extraction"] is None:
        return "unknown_stop"
    if not result["extraction"]["items"]:
        return "zero_items"
    return "extracted"


def score_label(golden: dict, output: dict) -> dict:
    """Score one label's output against its golden file (golden['extraction'] must be set)."""
    golden_items = golden["extraction"]["items"]
    output_items = output["extraction"]["items"] if output["extraction"] is not None else []

    code_score = score_codes(golden_items, output_items)
    return {
        "code_recall": code_score["recall"],
        "code_precision": code_score["precision"],
        "item_recall": item_recall(golden_items, output_items),
        "structure_accuracy": structure_accuracy(golden_items, output_items),
        "missing_codes": code_score["missing"],
        "code_matched": code_score["matched"],
        "code_total_golden": code_score["total_golden"],
    }


def main(
    log: Annotated[
        bool, typer.Option("--log/--no-log", help="Tee console output to a log file (default on).")
    ] = True,
) -> None:
    """Score every data/golden/extraction/ file against its matching data/outputs/extraction/ file."""
    log_path = setup_run_log("score_golden") if log else None
    golden_paths = sorted(settings.EXTRACTION_GOLDEN_DIR.glob("*.json"))
    if not golden_paths:
        console.print(f"[yellow]No golden files found in {settings.EXTRACTION_GOLDEN_DIR}[/yellow]")
        if log_path:
            console.print(f"Log: {log_path}")
        sys.exit(1)

    rows = []
    gate_rows = []
    total_matched = 0
    total_golden_codes = 0

    for golden_path in golden_paths:
        output_path = settings.EXTRACTION_OUTPUT_DIR / golden_path.name
        if not output_path.exists():
            console.print(f"[red]No matching output for {golden_path.name} — skipped[/red]")
            continue

        golden = json.loads(golden_path.read_text(encoding="utf-8"))
        output = json.loads(output_path.read_text(encoding="utf-8"))

        if golden["extraction"] is None:
            golden_cat = stop_category(golden)
            output_cat = stop_category(output)
            gate_rows.append(
                {
                    "label": golden_path.stem,
                    "golden": golden_cat,
                    "output": output_cat,
                    "passed": golden_cat == output_cat,
                }
            )
            continue

        scored = score_label(golden, output)
        scored["label"] = golden_path.stem
        rows.append(scored)
        total_matched += scored["code_matched"]
        total_golden_codes += scored["code_total_golden"]

    table = Table(title="Golden-set extraction scores")
    table.add_column("Label")
    table.add_column("Code recall")
    table.add_column("Code precision")
    table.add_column("Item recall")
    table.add_column("Structure acc.")
    table.add_column("Missing")
    for row in rows:
        table.add_row(
            row["label"],
            f"{row['code_recall']:.2f}",
            f"{row['code_precision']:.2f}",
            f"{row['item_recall']:.2f}",
            f"{row['structure_accuracy']:.2f}",
            ", ".join(row["missing_codes"]) or "—",
        )
    console.print(table)

    if gate_rows:
        gate_table = Table(title="Gate cases (extraction == null in golden)")
        gate_table.add_column("Label")
        gate_table.add_column("Golden category")
        gate_table.add_column("Output category")
        gate_table.add_column("Passed")
        for row in gate_rows:
            verdict = "[green]yes[/green]" if row["passed"] else "[red]NO[/red]"
            gate_table.add_row(row["label"], row["golden"], row["output"], verdict)
        console.print(gate_table)

    overall_recall = total_matched / total_golden_codes if total_golden_codes else 1.0
    console.print(
        f"\n[bold]Aggregate[/bold] — codes in golden: {total_golden_codes}, "
        f"matched: {total_matched}, code recall: {overall_recall:.2f}"
    )

    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = settings.OUTPUT_DIR / "score.csv"
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "label",
                "code_recall",
                "code_precision",
                "item_recall",
                "structure_accuracy",
                "missing_codes",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "label": row["label"],
                    "code_recall": f"{row['code_recall']:.4f}",
                    "code_precision": f"{row['code_precision']:.4f}",
                    "item_recall": f"{row['item_recall']:.4f}",
                    "structure_accuracy": f"{row['structure_accuracy']:.4f}",
                    "missing_codes": "; ".join(row["missing_codes"]),
                }
            )
    console.print(f"Wrote scores to [cyan]{out_path}[/cyan]")

    if log_path:
        console.print(f"Log: {log_path}")

    if overall_recall < 1.0:
        sys.exit(1)


if __name__ == "__main__":
    typer.run(main)
