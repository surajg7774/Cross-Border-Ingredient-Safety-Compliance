"""Prints every entry in data/reference/horizon_signals.json whose DOI has
not yet been hand-verified against efsa.europa.eu, so they can be worked
through one at a time. See docs/findings.md F-12 -- there is no automated
EFSA source to check against, so this is a manual checklist, not a
verifier.

    uv run python scripts/verify_horizon_dois.py

After checking an entry, set its "doi" and "doi_verified": true directly in
data/reference/horizon_signals.json.
"""

import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import typer
from rich.console import Console
from rich.table import Table

from config import settings
from src.horizon.load import load_horizon_signals
from src.logging_setup import setup_run_log

console = Console()


def main(
    log: Annotated[
        bool, typer.Option("--log/--no-log", help="Tee console output to a log file (default on).")
    ] = True,
) -> None:
    log_path = setup_run_log("verify_horizon_dois") if log else None
    path = settings.REFERENCE_DIR / "horizon_signals.json"
    if not path.exists():
        console.print(f"[red]{path} not found.[/red]")
        if log_path:
            console.print(f"Log: {log_path}")
        raise SystemExit(1)

    signals = load_horizon_signals(path)
    unverified = [signal for signal in signals if not signal.get("doi_verified", False)]

    if not unverified:
        console.print("[green]All entries have doi_verified: true.[/green]")
        if log_path:
            console.print(f"Log: {log_path}")
        return

    table = Table(title=f"{len(unverified)} unverified entr{'y' if len(unverified) == 1 else 'ies'}")
    table.add_column("EU id")
    table.add_column("Substance")
    table.add_column("Year")
    table.add_column("Title")
    table.add_column("DOI on file")
    for signal in unverified:
        table.add_row(
            str(signal.get("eu_canonical_id") or "—"),
            str(signal.get("substance_name") or "—"),
            str(signal.get("year") or "—"),
            str(signal.get("title") or "—"),
            str(signal.get("doi") or "—"),
        )
    console.print(table)
    console.print(
        "[yellow]Verify each title and DOI against efsa.europa.eu, then set \"doi\" and "
        '"doi_verified": true in data/reference/horizon_signals.json.[/yellow]'
    )

    if log_path:
        console.print(f"Log: {log_path}")


if __name__ == "__main__":
    typer.run(main)
