"""CLI entry point: score data/outputs/category/ against hand-corrected
data/golden/category_truth.json.

    uv run python scripts/score_category.py
    uv run python scripts/score_category.py --config no-parent-inheritance

Reads already-written JSON files only -- no API calls, no embedding. Appends
one row to data/outputs/experiments.csv per run, so scores across configs
accumulate into a results table instead of overwriting each other.
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

from config import settings
from src.category.experiment import CONFIGS
from src.eval.category_metrics import mean_reciprocal_rank, recall_at_k
from src.logging_setup import setup_run_log

console = Console()

DEFAULT_RECALL_AT_3_THRESHOLD = 0.85


def _load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _predictions_by_component(category_output: dict) -> dict[str | None, list[str]]:
    """component_label -> ranked list of predicted category codes (top-3)."""
    return {result["component_label"]: [c["code"] for c in result["top3"]] for result in category_output["results"]}


def score_label(truth_entries: list[dict], category_output: dict | None) -> tuple[list[dict], list[dict]]:
    """Match on (label, component_label). Returns (scored_rows, unscored_rows).

    scored_rows: one per TRUTH entry, matched to the output by component
    label. A truth component missing from the output (or a missing
    category_output entirely) scores as a full miss -- predictions=[], not
    an exception -- and IS counted in the aggregate.

    unscored_rows: one per OUTPUT component that has no matching truth
    entry. Reported, not silently dropped and not counted as a miss --
    this is exactly how the additive-scoping bug stayed invisible: the code
    used to produce only a whole-label query, so there was nothing to
    notice was missing. A component the truth file doesn't know about
    (a future scoping change, a new label) needs to be SEEN, not treated
    as either a hit or a miss it was never told to expect.
    """
    predictions_by_component = _predictions_by_component(category_output) if category_output else {}
    truth_components = {entry["component"] for entry in truth_entries}

    scored_rows = []
    for entry in truth_entries:
        component = entry["component"]
        truth_code = entry["code"]
        predictions = predictions_by_component.get(component, [])
        scored_rows.append(
            {
                "component": component,
                "truth_code": truth_code,
                "predictions": predictions,
                "rank": predictions.index(truth_code) + 1 if truth_code in predictions else None,
                "recall_at_1": recall_at_k(predictions, truth_code, 1),
                "recall_at_3": recall_at_k(predictions, truth_code, 3),
                "mrr": mean_reciprocal_rank(predictions, truth_code),
            }
        )

    unscored_rows = []
    if category_output:
        for component, predictions in predictions_by_component.items():
            if component not in truth_components:
                unscored_rows.append({"component": component, "predictions": predictions})

    return scored_rows, unscored_rows


EXPERIMENTS_CSV_HEADER = [
    "timestamp",
    "stage",
    "config",
    "retrieval_method",
    "recall_at_1",
    "recall_at_3",
    "mrr",
    "n",
    "corpus_mean_similarity",
    "index_build_seconds",
    "truth_version",
]


def _truth_version(truth_path: Path) -> str:
    """Short fingerprint of data/golden/category_truth.json's raw bytes,
    computed fresh at scoring time. Rows logged under different
    truth_versions were scored against different ground truth and are NOT
    comparable, even when their config name matches -- an edit to the truth
    file (e.g. a corrected component entry) silently invalidates every
    earlier row's recall/MRR numbers as a baseline for new ones. 12 hex
    chars of sha256 is enough to distinguish file versions, not a security
    use, so truncation is fine.
    """
    return hashlib.sha256(truth_path.read_bytes()).hexdigest()[:12]


def _warn_if_truth_version_changed(truth_version: str) -> None:
    """The failure this guards against: a chromadb run appeared to beat a
    logged embedding baseline (0.41/0.65 vs 0.35/0.59) purely because the
    baseline row predated an edit to category_truth.json -- a fresh run
    against the SAME truth matched exactly. This surfaces that at scoring
    time instead of leaving it to be discovered by a confusing comparison
    later.
    """
    if not settings.EXPERIMENTS_CSV.exists():
        return
    with settings.EXPERIMENTS_CSV.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return
    last_truth_version = rows[-1].get("truth_version")
    if last_truth_version and last_truth_version != truth_version:
        console.print(
            f"[yellow]truth_version changed since the last logged row: was "
            f"{last_truth_version}, now {truth_version}. category_truth.json was edited -- "
            "this run's row is not comparable to earlier rows.[/yellow]"
        )


def _append_experiment_row(
    config_name: str,
    retrieval_method: str,
    recall_1: float,
    recall_3: float,
    mrr: float,
    n: int,
    corpus_mean_similarity: float | None,
    index_build_seconds: float | None,
    truth_version: str,
) -> None:
    is_new = not settings.EXPERIMENTS_CSV.exists()
    settings.EXPERIMENTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with settings.EXPERIMENTS_CSV.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(EXPERIMENTS_CSV_HEADER)
        writer.writerow(
            [
                datetime.now(UTC).isoformat(),
                "category",
                config_name,
                retrieval_method,
                f"{recall_1:.4f}",
                f"{recall_3:.4f}",
                f"{mrr:.4f}",
                n,
                f"{corpus_mean_similarity:.4f}" if corpus_mean_similarity is not None else "",
                # Blank for "embedding", which builds no separate index --
                # this is the cost side of the tfidf/chromadb comparison.
                f"{index_build_seconds:.4f}" if index_build_seconds is not None else "",
                truth_version,
            ]
        )
    console.print(f"Appended results to [cyan]{settings.EXPERIMENTS_CSV}[/cyan]")


def main(
    config_name: Annotated[
        str, typer.Option("--config", help=f"Tag for the results row: one of {sorted(CONFIGS)}.")
    ] = "baseline",
    log: Annotated[
        bool, typer.Option("--log/--no-log", help="Tee console output to a log file (default on).")
    ] = True,
) -> None:
    """Score every label in data/golden/category_truth.json against data/outputs/category/."""
    log_path = setup_run_log("score_category") if log else None
    if config_name not in CONFIGS:
        console.print(f"[red]Unknown --config {config_name!r}. Choose one of: {sorted(CONFIGS)}[/red]")
        if log_path:
            console.print(f"Log: {log_path}")
        raise typer.Exit(1)

    truth_data = _load_json(settings.CATEGORY_TRUTH_PATH)
    if truth_data is None:
        console.print(f"[red]No truth file at {settings.CATEGORY_TRUTH_PATH}[/red]")
        if log_path:
            console.print(f"Log: {log_path}")
        raise typer.Exit(1)
    threshold = truth_data.get("_meta", {}).get("recall_at_3_threshold", DEFAULT_RECALL_AT_3_THRESHOLD)

    truth_version = _truth_version(settings.CATEGORY_TRUTH_PATH)
    _warn_if_truth_version_changed(truth_version)

    table = Table(title=f"Category scores (config={config_name})")
    table.add_column("Label")
    table.add_column("Component")
    table.add_column("Truth code")
    table.add_column("Rank")
    table.add_column("Top-1 prediction")
    table.add_column("Score")

    unscored_table = Table(title="Unscored -- output components with no matching truth entry")
    unscored_table.add_column("Label")
    unscored_table.add_column("Component")
    unscored_table.add_column("Top-1 prediction")

    all_rows = []
    all_unscored = []
    corpus_mean_similarity = None
    index_build_seconds = None
    for label, truth_entries in truth_data["truth"].items():
        output_path = settings.CATEGORY_OUTPUT_DIR / f"{label}.json"
        category_output = _load_json(output_path)
        if category_output is None:
            console.print(f"[yellow]No category output for {label} -- scored as a full miss[/yellow]")
        else:
            if category_output.get("config") not in (config_name, None):
                console.print(
                    f"[yellow]{label}: output was produced with config "
                    f"{category_output.get('config')!r}, not {config_name!r} -- scoring it anyway, "
                    "but the experiments.csv row may not mean what its config tag says[/yellow]"
                )
            # Per-corpus constants, identical across every label's output
            # file from the same run -- take them from whichever file has
            # them first.
            if corpus_mean_similarity is None:
                corpus_mean_similarity = category_output.get("corpus_mean_similarity")
            if index_build_seconds is None:
                index_build_seconds = category_output.get("index_build_seconds")

        scored_rows, unscored_rows = score_label(truth_entries, category_output)
        for row in scored_rows:
            table.add_row(
                label,
                row["component"] or "(whole label)",
                row["truth_code"],
                str(row["rank"]) if row["rank"] else "MISS",
                row["predictions"][0] if row["predictions"] else "—",
                f"{row['mrr']:.2f}",
            )
            row["label"] = label
            all_rows.append(row)
        for row in unscored_rows:
            unscored_table.add_row(
                label,
                row["component"] or "(whole label)",
                row["predictions"][0] if row["predictions"] else "—",
            )
            all_unscored.append({**row, "label": label})
    console.print(table)
    if all_unscored:
        console.print(unscored_table)
        console.print(
            f"[yellow]{len(all_unscored)} output component(s) have no truth entry -- "
            "not counted in the aggregate below. Add them to data/golden/category_truth.json "
            "if they're a real scoping change.[/yellow]"
        )

    n = len(all_rows)
    recall_1 = sum(r["recall_at_1"] for r in all_rows) / n if n else 0.0
    recall_3 = sum(r["recall_at_3"] for r in all_rows) / n if n else 0.0
    mrr = sum(r["mrr"] for r in all_rows) / n if n else 0.0
    console.print(
        f"\n[bold]Aggregate[/bold] (n={n}) -- recall@1: {recall_1:.2f}, "
        f"recall@3: {recall_3:.2f}, MRR: {mrr:.2f}"
    )

    _append_experiment_row(
        config_name,
        CONFIGS[config_name].retrieval_method,
        recall_1,
        recall_3,
        mrr,
        n,
        corpus_mean_similarity,
        index_build_seconds,
        truth_version,
    )

    if log_path:
        console.print(f"Log: {log_path}")

    if recall_3 < threshold:
        console.print(f"[red]recall@3 {recall_3:.2f} is below the threshold {threshold:.2f}[/red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    typer.run(main)
