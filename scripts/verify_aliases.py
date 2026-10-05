"""CLI entry point: verify data/reference/label_aliases.json against codex_ins.json.

label_aliases.json is hand-written (DRAFT status lives in its _meta.review_status)
and must never be edited by this script -- it only checks aliases[], ambiguous[]
and never_additives against codex_ins.json and reports PASS / MISMATCH / NOT FOUND
(and the equivalent for the other two sections). Corrections are made by hand.
"""

import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json

import typer
from rich.console import Console
from rich.table import Table

from src.logging_setup import setup_run_log

console = Console()
CODEX_INS_PATH = Path("data/reference/codex_ins.json")
ALIASES_PATH = Path("data/reference/label_aliases.json")


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


def _check_aliases(aliases: list[dict], by_ins: dict[str, dict]) -> dict[str, int]:
    table = Table(title=f"aliases[] ({len(aliases)} entries)")
    table.add_column("Alias")
    table.add_column("INS")
    table.add_column("Stated ins_name")
    table.add_column("Result")

    counts = {"PASS": 0, "MISMATCH": 0, "NOT FOUND": 0}
    for entry in aliases:
        record = by_ins.get(entry["ins"])
        if record is None:
            result = "[red]NOT FOUND[/red]"
            counts["NOT FOUND"] += 1
        elif _normalise(record["name"]) == _normalise(entry["ins_name"]):
            result = "[green]PASS[/green]"
            counts["PASS"] += 1
        else:
            result = f"[yellow]MISMATCH[/yellow] (actual: {record['name']!r})"
            counts["MISMATCH"] += 1
        table.add_row(entry["alias"], entry["ins"], entry["ins_name"], result)

    console.print(table)
    console.print(
        f"aliases[] -- [bold]PASS[/bold]: {counts['PASS']}   "
        f"[bold]MISMATCH[/bold]: {counts['MISMATCH']}   "
        f"[bold]NOT FOUND[/bold]: {counts['NOT FOUND']}\n"
    )
    return counts


def _check_ambiguous(ambiguous: list[dict], by_ins: dict[str, dict]) -> dict[str, int]:
    table = Table(title=f"ambiguous[] ({len(ambiguous)} entries)")
    table.add_column("Alias")
    table.add_column("Candidates found")
    table.add_column("Missing candidates")

    counts = {"all_found": 0, "some_missing": 0}
    for entry in ambiguous:
        candidates = entry["candidates"]
        missing = [c for c in candidates if c not in by_ins]
        if missing:
            counts["some_missing"] += 1
            found_col = f"[yellow]{len(candidates) - len(missing)}/{len(candidates)}[/yellow]"
            missing_col = ", ".join(missing)
        else:
            counts["all_found"] += 1
            found_col = f"[green]{len(candidates)}/{len(candidates)}[/green]"
            missing_col = "—"
        table.add_row(entry["alias"], found_col, missing_col)

    console.print(table)
    console.print(
        f"ambiguous[] -- [bold]all candidates found[/bold]: {counts['all_found']}   "
        f"[bold]missing at least one[/bold]: {counts['some_missing']}\n"
    )
    return counts


def _check_never_additives(never_additives: dict, name_lookup: dict[str, list[tuple]]) -> dict[str, int]:
    terms = never_additives["terms"]
    table = Table(title=f"never_additives (\"terms\", {len(terms)} entries)")
    table.add_column("Term")
    table.add_column("Status")

    counts = {"ok": 0, "flagged": 0}
    for term in terms:
        hits = name_lookup.get(_normalise(term), [])
        if hits:
            counts["flagged"] += 1
            shown = ", ".join(f"{ins} {name!r}" for ins, name in hits)
            status = f"[yellow]APPEARS AS INS NAME:[/yellow] {shown}"
        else:
            counts["ok"] += 1
            status = "[green]ok[/green]"
        table.add_row(term, status)

    console.print(table)
    console.print(
        f"never_additives -- [bold]ok[/bold]: {counts['ok']}   "
        f"[bold]flagged (appears as a real INS name)[/bold]: {counts['flagged']}\n"
    )
    return counts


def main(
    log: Annotated[
        bool, typer.Option("--log/--no-log", help="Tee console output to a log file (default on).")
    ] = True,
) -> None:
    """Check label_aliases.json's aliases[], ambiguous[] and never_additives against codex_ins.json."""
    log_path = setup_run_log("verify_aliases") if log else None
    codex_ins = json.loads(CODEX_INS_PATH.read_text(encoding="utf-8"))
    by_ins = {r["ins"]: r for r in codex_ins}
    name_lookup: dict[str, list[tuple]] = {}
    for r in codex_ins:
        name_lookup.setdefault(_normalise(r["name"]), []).append((r["ins"], r["name"]))

    doc = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
    review_status = doc.get("_meta", {}).get("review_status", "")
    if "draft" not in review_status.lower():
        console.print(
            f"[yellow]Warning:[/yellow] _meta.review_status is {review_status!r}, "
            "not DRAFT -- verifying it anyway."
        )

    _check_aliases(doc["aliases"], by_ins)
    _check_ambiguous(doc["ambiguous"], by_ins)
    _check_never_additives(doc["never_additives"], name_lookup)

    if log_path:
        console.print(f"Log: {log_path}")


if __name__ == "__main__":
    typer.run(main)
