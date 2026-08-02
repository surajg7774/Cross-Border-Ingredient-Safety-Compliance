"""CLI entry point: advisory regulatory-horizon signals for the additives
assessed in a compliance verdict.

    uv run python scripts/horizon.py data/outputs/verdict/Pepsimain.json
    uv run python scripts/horizon.py --all

Reads data/outputs/verdict/<name>.json and data/reference/
horizon_signals.json -- a hand-curated dataset, not an automated feed. See
docs/findings.md F-12: EFSA OpenFoodTox 3.0 turned out to carry no E-number
field anywhere, and no CAS crosswalk exists in this project to bridge to
one, so automated ingestion was abandoned. Writes
data/outputs/horizon/<name>.json. All I/O other than the dataset read
(src/horizon/load.py's job) lives here -- src/horizon/lane.py itself never
reads a file, calls the network, or prints anything.

ADVISORY ONLY: these signals never change a verdict. An EFSA opinion is not
law -- see src/horizon/lane.py's module docstring for the full reasoning.
"""

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
from src.horizon.lane import find_horizon_signals
from src.horizon.load import load_horizon_meta, load_horizon_signals
from src.logging_setup import setup_run_log
from src.rules.schemas import ProductVerdict

console = Console()


def _load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _collect_additive_ids(verdict: ProductVerdict) -> tuple[list[str], dict[str, str]]:
    """Every eu_canonical_id this verdict assessed as an additive, plus
    id -> additive_name for the name-match fallback (src/horizon/lane.py's
    E-number match takes priority; a name is only consulted when it fails).
    Every item, not just verdict.blocking -- a horizon signal can be
    relevant for an additive that is currently permitted too.
    """
    ids = []
    names = {}
    for item in verdict.items:
        if item.eu_canonical_id is None:
            continue
        ids.append(item.eu_canonical_id)
        if item.additive_name:
            names[item.eu_canonical_id] = item.additive_name
    return sorted(set(ids)), names


def _horizon_file(verdict_path: Path, horizon_signals: list[dict], data_retrieved: str | None) -> None:
    payload = _load_json(verdict_path)
    if payload is None:
        console.print(f"[red]No verdict file at {verdict_path}[/red]")
        return
    verdict = ProductVerdict.model_validate(payload)
    additive_ids, additive_names = _collect_additive_ids(verdict)

    result = find_horizon_signals(
        additive_ids, horizon_signals, additive_names=additive_names, data_retrieved=data_retrieved
    )

    if result.signals:
        table = Table(title=verdict_path.stem)
        table.add_column("EU id")
        table.add_column("Substance")
        table.add_column("Stage")
        table.add_column("Year")
        table.add_column("Years old")
        table.add_column("DOI")
        for signal in result.signals:
            doi_cell = signal.doi or "—"
            if "doi_unverified" in signal.flags:
                doi_cell = f"[red]{doi_cell} (UNVERIFIED)[/red]"
            table.add_row(
                signal.eu_canonical_id,
                signal.substance_name,
                signal.stage,
                signal.publication_date or "—",
                str(signal.years_old) if signal.years_old is not None else "—",
                doi_cell,
            )
        console.print(table)
    else:
        console.print(f"{verdict_path.stem}: no recent EFSA activity found for the additives checked.")
    console.print(
        "[yellow]Advisory only: these signals do NOT change any compliance verdict. An EFSA "
        "opinion is not law.[/yellow]"
    )

    unverified = [signal for signal in result.signals if "doi_unverified" in signal.flags]
    if unverified:
        console.print(
            f"[red]{len(unverified)} signal(s) have a citation that has NOT been verified against "
            "efsa.europa.eu -- treat as unconfirmed until checked "
            "(run scripts/verify_horizon_dois.py).[/red]"
        )

    for warning in result.warnings:
        console.print(f"[dim]{warning}[/dim]")

    settings.HORIZON_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = settings.HORIZON_OUTPUT_DIR / verdict_path.name
    out_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    console.print(f"Saved to [cyan]{out_path}[/cyan]\n")


def main(
    verdict_path: Annotated[
        Path | None, typer.Argument(help="Path to one data/outputs/verdict/*.json file.")
    ] = None,
    all_files: Annotated[
        bool, typer.Option("--all", help="Check every file in data/outputs/verdict/.")
    ] = False,
    log: Annotated[
        bool, typer.Option("--log/--no-log", help="Tee console output to a log file (default on).")
    ] = True,
) -> None:
    """Check one verdict file, or every verdict file with --all."""
    log_path = setup_run_log("horizon") if log else None
    if not verdict_path and not all_files:
        console.print("[red]Pass a file path, or use --all.[/red]")
        if log_path:
            console.print(f"Log: {log_path}")
        raise typer.Exit(1)

    horizon_path = settings.REFERENCE_DIR / "horizon_signals.json"
    if not horizon_path.exists():
        console.print(f"[red]{horizon_path} not found.[/red]")
        if log_path:
            console.print(f"Log: {log_path}")
        raise typer.Exit(1)
    horizon_signals = load_horizon_signals(horizon_path)
    meta = load_horizon_meta(horizon_path)
    data_retrieved = meta.get("retrieved") or datetime.fromtimestamp(
        horizon_path.stat().st_mtime, tz=UTC
    ).date().isoformat()

    if meta.get("review_status"):
        console.print(f"[yellow]{meta['review_status']}[/yellow]")

    if all_files:
        paths = sorted(settings.VERDICT_OUTPUT_DIR.glob("*.json"))
        if not paths:
            console.print(f"[yellow]No files found in {settings.VERDICT_OUTPUT_DIR}[/yellow]")
            if log_path:
                console.print(f"Log: {log_path}")
            raise typer.Exit(1)
        for path in paths:
            _horizon_file(path, horizon_signals, data_retrieved)
    else:
        _horizon_file(verdict_path, horizon_signals, data_retrieved)

    if log_path:
        console.print(f"Log: {log_path}")


if __name__ == "__main__":
    typer.run(main)
