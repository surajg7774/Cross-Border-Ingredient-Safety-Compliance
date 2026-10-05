"""CLI entry point: turn pasted/typed ingredients text into
data/outputs/extraction/<name>.json -- no API calls, no image required.

    uv run python scripts/extract_text.py --text "Sugar, Colour (INS 143)" --name MyProduct
    uv run python scripts/extract_text.py --file path/to/declaration.txt --name MyProduct
    uv run python scripts/extract_text.py --additives "INS 143, INS 330" --name Formulation1

--text and --file parse a full declaration (src.extract.text_parser.parse_declaration).
--additives parses a bare comma-separated additive list for pre-label
formulation checking (parse_additive_list) -- exactly one of the three is
required. --name sets the output filename stem, required so downstream
stages (resolve, category) can find the file. --description is optional and
is written into gate.product_descriptor for the category stage to use.

All I/O lives here -- src/extract/text_parser.py itself never reads a file,
calls the network, or prints anything.
"""

import json
import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import typer
from rich.console import Console
from rich.tree import Tree

from config import settings
from src.extract.text_parser import parse_additive_list, parse_declaration
from src.logging_setup import setup_run_log
from src.schemas import ExtractedItem, ExtractionResult, GateResult

console = Console()


def _item_label(item: ExtractedItem) -> str:
    parts = []
    if item.name_as_declared:
        parts.append(item.name_as_declared)
    if item.declared_code:
        parts.append(f"[cyan]code: {item.declared_code} ({item.code_system})[/cyan]")
    if item.declared_role:
        parts.append(f"[magenta]role: {item.declared_role} ({item.role_source})[/magenta]")
    return " -- ".join(parts) if parts else item.verbatim


def _print_tree(result: ExtractionResult) -> None:
    tree = Tree("Parsed items")
    nodes: dict[int | None, Tree] = {None: tree}
    for item in sorted(result.items, key=lambda i: i.item_id):
        parent_node = nodes.get(item.parent_item_id, tree)
        nodes[item.item_id] = parent_node.add(_item_label(item))
    console.print(tree)

    if result.unparsed_fragments:
        console.print(
            f"[yellow]{len(result.unparsed_fragments)} fragment(s) could not be parsed "
            f"-- not dropped, see unparsed_fragments in the saved file:[/yellow]"
        )
        for fragment in result.unparsed_fragments:
            console.print(f"  [yellow]{fragment!r}[/yellow]")


def main(
    name: Annotated[str, typer.Option("--name", help="Output filename stem (required).")],
    text: Annotated[str | None, typer.Option("--text", help="A pasted ingredients declaration.")] = None,
    file: Annotated[
        Path | None, typer.Option("--file", help="Path to a .txt file holding the declaration.")
    ] = None,
    additives: Annotated[
        str | None,
        typer.Option("--additives", help="A bare comma-separated additive list (formulation mode)."),
    ] = None,
    description: Annotated[
        str | None, typer.Option("--description", help="Product description -> gate.product_descriptor.")
    ] = None,
    log: Annotated[
        bool, typer.Option("--log/--no-log", help="Tee console output to a log file (default on).")
    ] = True,
) -> None:
    """Parse pasted/typed text into an extraction output, no photograph required."""
    log_path = setup_run_log("extract_text") if log else None
    sources = [value for value in (text, file, additives) if value is not None]
    if len(sources) != 1:
        console.print("[red]Pass exactly one of --text, --file, or --additives.[/red]")
        if log_path:
            console.print(f"Log: {log_path}")
        raise typer.Exit(1)

    if file is not None:
        if not file.exists():
            console.print(f"[red]No such file: {file}[/red]")
            if log_path:
                console.print(f"Log: {log_path}")
            raise typer.Exit(1)
        result = parse_declaration(file.read_text(encoding="utf-8"))
    elif additives is not None:
        result = parse_additive_list(additives)
    else:
        result = parse_declaration(text)

    # Text input bypasses the label-validity gate entirely -- no image was
    # ever checked for is_food_label/has_ingredients_declaration, so the
    # report must say that plainly rather than implying an image was
    # validated. confidence is 1.0 because deterministic parsing is not an
    # estimate, unlike the gate model's derived confidence for image input.
    gate = GateResult(
        is_food_label=True,
        has_ingredients_declaration=True,
        confidence=1.0,
        evidence_found=["text_input"],
        reject_reason=None,
        product_name=name,
        product_descriptor=description,
        languages_detected=["en"],
        language_selected="en",
        ingredients_panel_bbox=None,
        warnings=["input was pasted text, not a photographed label — no gate validation was performed"],
    )
    payload = {"gate": gate.model_dump(), "extraction": result.model_dump(), "stop_reason": None}

    settings.EXTRACTION_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = settings.EXTRACTION_OUTPUT_DIR / f"{name}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    _print_tree(result)
    console.print(f"Saved to [cyan]{out_path}[/cyan]")

    if log_path:
        console.print(f"Log: {log_path}")


if __name__ == "__main__":
    typer.run(main)
