"""CLI entry point: score data/outputs/agent/ (scripts/agent_review.py's
proposals) against hand-corrected data/golden/agent_truth.json.

    uv run python scripts/score_agent.py

Reads already-written JSON files only -- no API calls. Appends one row to
data/outputs/experiments.csv (stage="agent") per run, extending its header
with agent-specific columns the first time this script writes to a file
that only has scripts/score_category.py's own header (see
_ensure_agent_columns) -- every existing row is preserved, padded with
blanks for the new columns, so the file stays one single, ever-growing log
rather than two incompatible ones.

Reports four figures, kept SEPARATE, never combined into one accuracy
number (per the task): proposals correct, proposals wrong (the number that
matters most -- a wrong proposal is worse than a decline, because a human
reviewer may accept it without checking), items correctly declined, and
items wrongly declined (the label DID determine an identity and the agent
gave up anyway).
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
from src.logging_setup import setup_run_log

console = Console()


def _load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _normalise_ins(code: str | None) -> str | None:
    """Same normalisation src/agent/tools.py's _normalise_code applies --
    duplicated for the same stage-independence reason every other small
    helper in this project is (see e.g. src/report/narrator.py's own
    duplicated _strip_markdown_fences)."""
    if code is None:
        return None
    text = code.lower().replace(" ", "")
    if text.startswith("ins"):
        text = text[3:]
    elif text.startswith("e"):
        text = text[1:]
    return text


def _load_proposals_by_label() -> tuple[dict[str, dict[int, dict]], dict[str, str]]:
    by_label: dict[str, dict[int, dict]] = {}
    model_id_by_label: dict[str, str] = {}
    for path in sorted(settings.AGENT_OUTPUT_DIR.glob("*.json")):
        payload = _load_json(path, {})
        by_label[path.stem] = {p["item_id"]: p for p in payload.get("proposals", [])}
        model_id_by_label[path.stem] = payload.get("model_id", "")
    return by_label, model_id_by_label


def score_item(truth_item: dict, proposal: dict | None) -> str:
    """One of "correct", "wrong", "correctly_declined", "wrongly_declined",
    or "unscored" (no agent proposal exists yet for this item)."""
    if proposal is None:
        return "unscored"

    outcome = truth_item["answer"]["outcome"]
    declined = proposal["declined"]

    if outcome == "correctly_declined":
        return "correctly_declined" if declined else "wrong"

    # outcome == "identified"
    if declined:
        return "wrongly_declined"
    same_ins = _normalise_ins(proposal["proposed_canonical_ins"]) == _normalise_ins(truth_item["answer"]["canonical_ins"])
    same_class = proposal["proposed_classification"] == truth_item["answer"]["classification"]
    return "correct" if (same_ins and same_class) else "wrong"


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
AGENT_COLUMNS = ["n_correct", "n_wrong", "n_correctly_declined", "n_wrongly_declined"]


def _ensure_agent_columns(path: Path) -> list[str]:
    """The full header this run should write under: the file's own existing
    header (scripts/score_category.py's, if the file already exists) plus
    any AGENT_COLUMNS it doesn't already have, appended at the end. If the
    file's header is missing any of them, every existing row is rewritten
    padded with "" for the new columns -- a CSV cannot have rows wider than
    its header, and this file is meant to accumulate every stage's rows in
    ONE place (see scripts/score_category.py's own truth_version comment on
    why comparability across rows matters here)."""
    if not path.exists():
        return EXPERIMENTS_CSV_HEADER + AGENT_COLUMNS

    with path.open(encoding="utf-8") as f:
        existing_header = next(csv.reader(f), [])

    missing = [c for c in AGENT_COLUMNS if c not in existing_header]
    if not missing:
        return list(existing_header)

    full_header = list(existing_header) + missing
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=full_header, restval="")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    console.print(
        f"[yellow]Note:[/yellow] {path} was missing column(s) {missing} -- every existing row was "
        "rewritten with them blank, so the file stays one comparable log."
    )
    return full_header


def _truth_version(truth_path: Path) -> str:
    return hashlib.sha256(truth_path.read_bytes()).hexdigest()[:12]


def _append_experiment_row(model_id: str, counts: dict[str, int], truth_version: str) -> None:
    settings.EXPERIMENTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    header = _ensure_agent_columns(settings.EXPERIMENTS_CSV)
    row = {
        "timestamp": datetime.now(UTC).isoformat(),
        "stage": "agent",
        "config": model_id,
        "n": counts["correct"] + counts["wrong"] + counts["correctly_declined"] + counts["wrongly_declined"],
        "truth_version": truth_version,
        "n_correct": counts["correct"],
        "n_wrong": counts["wrong"],
        "n_correctly_declined": counts["correctly_declined"],
        "n_wrongly_declined": counts["wrongly_declined"],
    }
    with settings.EXPERIMENTS_CSV.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header, restval="")
        writer.writerow(row)
    console.print(f"Appended results to [cyan]{settings.EXPERIMENTS_CSV}[/cyan]")


def main(
    log: Annotated[
        bool, typer.Option("--log/--no-log", help="Tee console output to a log file (default on).")
    ] = True,
) -> None:
    """Score every reviewed item in data/golden/agent_truth.json against data/outputs/agent/."""
    log_path = setup_run_log("score_agent") if log else None

    truth = _load_json(settings.AGENT_TRUTH_PATH)
    if truth is None:
        console.print(f"[red]No truth file at {settings.AGENT_TRUTH_PATH}[/red]")
        if log_path:
            console.print(f"Log: {log_path}")
        raise typer.Exit(1)

    proposals_by_label, model_id_by_label = _load_proposals_by_label()
    if not proposals_by_label:
        console.print(f"[yellow]No agent output found in {settings.AGENT_OUTPUT_DIR} -- run scripts/agent_review.py first.[/yellow]")
        if log_path:
            console.print(f"Log: {log_path}")
        raise typer.Exit(1)

    table = Table(title="Agent proposal scores")
    table.add_column("Label")
    table.add_column("Item")
    table.add_column("Name")
    table.add_column("Truth")
    table.add_column("Proposal")
    table.add_column("Result")

    counts = {"correct": 0, "wrong": 0, "correctly_declined": 0, "wrongly_declined": 0}
    unreviewed = 0
    unscored = []
    model_ids: set[str] = set()

    for truth_item in truth["items"]:
        if truth_item["answer"]["outcome"] is None:
            unreviewed += 1
            continue

        label, item_id = truth_item["label"], truth_item["item_id"]
        proposal = proposals_by_label.get(label, {}).get(item_id)
        result = score_item(truth_item, proposal)

        if result == "unscored":
            unscored.append(truth_item)
            continue

        counts[result] += 1
        model_id_for_label = model_id_by_label.get(label)
        if model_id_for_label:
            model_ids.add(model_id_for_label)

        truth_display = (
            "correctly declines"
            if truth_item["answer"]["outcome"] == "correctly_declined"
            else f"{truth_item['answer']['classification']} ({truth_item['answer']['canonical_ins'] or '—'})"
        )
        proposal_display = (
            f"DECLINED: {proposal['decline_reason']}"
            if proposal["declined"]
            else f"{proposal['proposed_classification']} ({proposal['proposed_canonical_ins'] or '—'})"
        )
        result_style = {"correct": "green", "correctly_declined": "green", "wrong": "red", "wrongly_declined": "yellow"}[
            result
        ]
        table.add_row(
            label,
            str(item_id),
            truth_item["name_as_declared"] or "—",
            truth_display,
            proposal_display,
            f"[{result_style}]{result}[/{result_style}]",
        )

    console.print(table)
    if unreviewed:
        console.print(f"[dim]{unreviewed} item(s) in the truth file have not been reviewed yet (answer.outcome is null) -- skipped, not scored.[/dim]")
    if unscored:
        console.print(
            f"[yellow]{len(unscored)} reviewed item(s) have no matching agent proposal -- run "
            "scripts/agent_review.py for their label(s):[/yellow] "
            + ", ".join(f"{t['label']}#{t['item_id']}" for t in unscored)
        )

    console.print(
        f"\n[bold]correct:[/bold] {counts['correct']}   "
        f"[bold red]wrong:[/bold red] {counts['wrong']}   "
        f"[bold]correctly declined:[/bold] {counts['correctly_declined']}   "
        f"[bold yellow]wrongly declined:[/bold yellow] {counts['wrongly_declined']}"
    )
    console.print(
        "[dim]\"wrong\" is the number that matters most -- a human reviewer may accept a wrong "
        "proposal without checking it. Never combined into one accuracy figure with the others.[/dim]"
    )

    truth_version = _truth_version(settings.AGENT_TRUTH_PATH)
    model_id = next(iter(model_ids - {""}), "unknown") if model_ids else "unknown"
    _append_experiment_row(model_id, counts, truth_version)

    if log_path:
        console.print(f"Log: {log_path}")


if __name__ == "__main__":
    typer.run(main)
