"""CLI convenience: print the last N lines of the most recently written run
log (data/outputs/logs/*.log), so a short excerpt can be grabbed without
opening the file.

    uv run python scripts/tail_log.py
    uv run python scripts/tail_log.py --lines 100
"""

import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import typer
from rich.console import Console

from config import settings

console = Console()


def main(
    lines: Annotated[
        int, typer.Option("--lines", "-n", help="Number of lines to print, from the end.")
    ] = 50,
) -> None:
    """Print the last `lines` lines of the most recent file in data/outputs/logs/."""
    log_paths = sorted(settings.LOG_OUTPUT_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime)
    if not log_paths:
        console.print(f"[yellow]No log files found in {settings.LOG_OUTPUT_DIR}[/yellow]")
        raise typer.Exit(1)

    latest = log_paths[-1]
    all_lines = latest.read_text(encoding="utf-8").splitlines()
    tail = all_lines[-lines:]

    console.print(f"[bold]{latest}[/bold] (last {len(tail)} of {len(all_lines)} line(s))\n")
    # Plain print, not console.print -- log content is arbitrary already-
    # plain text (ANSI stripped by src/logging_setup.py), and must not be
    # re-parsed as rich markup (a literal "[" in the data could otherwise
    # be misread as a style tag).
    for line in tail:
        print(line)


if __name__ == "__main__":
    typer.run(main)
