"""CLI entry point: bootstrap data/golden/extraction/ from data/outputs/extraction/, without ever overwriting existing golden files."""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console

from config import settings

console = Console()


def main() -> None:
    """Copy every JSON in data/outputs/extraction/ into data/golden/extraction/ that isn't already there."""
    settings.EXTRACTION_GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    copied = []
    skipped = []

    for output_path in sorted(settings.EXTRACTION_OUTPUT_DIR.glob("*.json")):
        golden_path = settings.EXTRACTION_GOLDEN_DIR / output_path.name
        if golden_path.exists():
            skipped.append(output_path.name)
            continue
        shutil.copy2(output_path, golden_path)
        copied.append(output_path.name)

    if copied:
        console.print(f"[green]Copied {len(copied)}:[/green] {', '.join(copied)}")
    else:
        console.print("[yellow]Nothing new to copy.[/yellow]")

    if skipped:
        console.print(f"[dim]Skipped (already exists) {len(skipped)}:[/dim] {', '.join(skipped)}")


if __name__ == "__main__":
    main()
