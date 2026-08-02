"""CLI entry point: run PRIMARY_MODEL and SECONDARY_MODEL side by side and diff their output."""

import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.table import Table

from config import settings
from src.extractors.gemini import GeminiExtractor
from src.pipeline import run_extraction

console = Console()
LABELS_DIR = Path("data/labels")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
CSV_FIELDNAMES = [
    "image",
    "primary_count",
    "secondary_count",
    "only_primary",
    "only_secondary",
    "code_disagreements",
    "error",
]


def _item_key(item: dict) -> str:
    return (item["verbatim"] or "").strip().lower()


def _compare_items(items_a: list[dict], items_b: list[dict]) -> dict:
    """Diff two models' items by matching on verbatim text (no golden data to match against)."""
    by_a = {_item_key(item): item for item in items_a}
    by_b = {_item_key(item): item for item in items_b}

    only_a = sorted(by_a.keys() - by_b.keys())
    only_b = sorted(by_b.keys() - by_a.keys())
    code_disagreements = sorted(
        key
        for key in by_a.keys() & by_b.keys()
        if by_a[key]["declared_code"] != by_b[key]["declared_code"]
    )
    return {
        "count_a": len(items_a),
        "count_b": len(items_b),
        "only_a": only_a,
        "only_b": only_b,
        "code_disagreements": code_disagreements,
    }


def main() -> None:
    """Run every image in data/labels/ through both models and compare their output."""
    image_paths = sorted(p for p in LABELS_DIR.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    if not image_paths:
        console.print(f"[yellow]No images found in {LABELS_DIR}[/yellow]")
        return

    extractors = {
        "primary": GeminiExtractor(settings.PRIMARY_MODEL),
        "secondary": GeminiExtractor(settings.SECONDARY_MODEL),
    }
    wall_time = {"primary": 0.0, "secondary": 0.0}

    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = settings.OUTPUT_DIR / "comparison.csv"
    # Written incrementally (header first, one row per image as it completes)
    # so an interrupted run still leaves usable data on disk.
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()

        for image_path in image_paths:
            console.rule(image_path.name)
            results = {}
            try:
                for label, extractor in extractors.items():
                    start = time.monotonic()
                    results[label] = run_extraction(image_path, extractor)
                    wall_time[label] += time.monotonic() - start
            except Exception as exc:  # noqa: BLE001 — isolate any failure to one image
                # A model failing on an image is a result worth recording,
                # not a gap in the data.
                console.print(f"[red]Skipped — extraction failed: {exc}[/red]")
                writer.writerow(
                    {
                        "image": image_path.name,
                        "primary_count": "",
                        "secondary_count": "",
                        "only_primary": "",
                        "only_secondary": "",
                        "code_disagreements": "",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                f.flush()
                continue

            # Either model may have stopped before extraction (not a food
            # label, or no ingredients declaration visible) or produced a
            # non-null stop_reason even with items present (the zero-item
            # guard) — treat all of these the same for comparison purposes.
            primary, secondary = results["primary"], results["secondary"]
            if (
                primary["extraction"] is None
                or secondary["extraction"] is None
                or primary["stop_reason"]
                or secondary["stop_reason"]
            ):
                console.print(
                    f"[yellow]Skipped — primary: {primary['stop_reason']!r}, "
                    f"secondary: {secondary['stop_reason']!r}[/yellow]"
                )
                continue

            items_a = results["primary"]["extraction"]["items"]
            items_b = results["secondary"]["extraction"]["items"]
            diff = _compare_items(items_a, items_b)

            table = Table(title=image_path.name)
            table.add_column("Metric")
            table.add_column(settings.PRIMARY_MODEL)
            table.add_column(settings.SECONDARY_MODEL)
            table.add_row("Item count", str(diff["count_a"]), str(diff["count_b"]))
            table.add_row(
                "Only in this model",
                ", ".join(diff["only_a"]) or "—",
                ", ".join(diff["only_b"]) or "—",
            )
            console.print(table)
            if diff["code_disagreements"]:
                console.print(
                    f"[red]declared_code disagreements:[/red] {', '.join(diff['code_disagreements'])}"
                )
            else:
                console.print("[green]No declared_code disagreements[/green]")

            writer.writerow(
                {
                    "image": image_path.name,
                    "primary_count": diff["count_a"],
                    "secondary_count": diff["count_b"],
                    "only_primary": "; ".join(diff["only_a"]),
                    "only_secondary": "; ".join(diff["only_b"]),
                    "code_disagreements": "; ".join(diff["code_disagreements"]),
                    "error": "",
                }
            )
            f.flush()

    console.print(
        f"\n[bold]Total wall time[/bold] — primary: {wall_time['primary']:.1f}s, "
        f"secondary: {wall_time['secondary']:.1f}s"
    )
    console.print(f"Wrote comparison to [cyan]{out_path}[/cyan]")


if __name__ == "__main__":
    main()
