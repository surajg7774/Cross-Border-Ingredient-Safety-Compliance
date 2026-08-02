"""CLI entry point: run extraction over a batch of label images and save results."""

import json
import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import typer
from rich.console import Console

from config import settings
from src.extractors.gemini import GeminiExtractor
from src.logging_setup import setup_run_log
from src.pipeline import run_extraction

console = Console()


def main(
    image_path: Annotated[Path, typer.Argument(help="Path to a label image.")],
    model: Annotated[
        str | None, typer.Option("--model", help="Model ID (defaults to PRIMARY_MODEL).")
    ] = None,
    crop: Annotated[
        bool,
        typer.Option(
            "--crop",
            help=(
                "Crop to the detected ingredients panel before extracting "
                "(off by default; see pipeline.py for why)."
            ),
        ),
    ] = False,
    log: Annotated[
        bool, typer.Option("--log/--no-log", help="Tee console output to a log file (default on).")
    ] = True,
) -> None:
    """Run the gate + extract pipeline on one image, print a summary, and save the full JSON."""
    log_path = setup_run_log("extract") if log else None
    model_id = model or settings.PRIMARY_MODEL
    extractor = GeminiExtractor(model_id)
    result = run_extraction(image_path, extractor, crop=crop)

    gate = result["gate"]
    verdict = "FOOD LABEL" if gate["is_food_label"] else "REJECTED"
    console.print(f"[bold]Gate verdict:[/bold] {verdict} (confidence {gate['confidence']:.2f})")

    if result["extraction"] is None:
        console.print(f"[red]Stopped:[/red] {result['stop_reason']}")
    else:
        console.print(f"Languages detected: {', '.join(gate['languages_detected'])}")
        console.print(f"Language selected: {gate['language_selected']}")

        items = result["extraction"]["items"]
        console.print(f"Items found: {len(items)}\n")
        _print_item_tree(items)

        # An empty-items result can still carry a stop_reason (the
        # zero-item guard) — surface it even though extraction isn't None.
        if result["stop_reason"]:
            console.print(f"\n[red]Stopped:[/red] {result['stop_reason']}")

    settings.EXTRACTION_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = settings.EXTRACTION_OUTPUT_DIR / f"{image_path.stem}.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    console.print(f"\nSaved full result to [cyan]{out_path}[/cyan]")

    if log_path:
        console.print(f"Log: {log_path}")


def _print_item_tree(items: list[dict]) -> None:
    """Print items indented by nesting_depth, in declaration order."""
    for item in items:
        indent = "  " * item["nesting_depth"]
        code = f" [{item['declared_code']}]" if item["declared_code"] else ""
        # Skip the role suffix for explicit_prefix items — their verbatim
        # text ("PRESERVATIVE: sorbate") already states the role.
        show_role = item["declared_role"] and item["role_source"] != "explicit_prefix"
        role = f" ({item['declared_role']})" if show_role else ""
        console.print(f"{indent}- {item['verbatim']}{code}{role}")


if __name__ == "__main__":
    typer.run(main)
