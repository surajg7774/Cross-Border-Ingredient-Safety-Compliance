"""CLI entry point: propose substitute additives for every item that BLOCKS
export in a computed compliance verdict.

    uv run python scripts/substitutes.py data/outputs/verdict/Khusmain.json
    uv run python scripts/substitutes.py --all

Reads data/outputs/verdict/<name>.json (required -- run scripts/verdict.py
first), data/reference/eu_fip.json, and data/reference/codex_ins.json.
Writes data/outputs/substitutes/<name>.json. All I/O lives here --
src/substitutes/advisor.py itself never reads a file, calls the network, or
prints anything.
"""

import json
import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import typer
from rich.console import Console
from rich.table import Table

from config import settings
from src.logging_setup import setup_run_log
from src.reference_data import load_eu_fip
from src.rules.schemas import ProductVerdict
from src.substitutes.advisor import find_substitutes

console = Console()


def _load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _load_canonical_ins_map(verdict_path: Path) -> dict[int, str]:
    """item_id -> canonical_ins, from the matching resolution file.
    ItemVerdict does not carry canonical_ins (see src/rules/schemas.py), so
    the substitute advisor needs it joined in from one file upstream here --
    the same pattern scripts/verdict.py uses for display names. Needed
    because eu_canonical_id is null for exactly the additives that block via
    absence from eu_fip (that null is WHY they blocked): INS 143 (Fast Green
    FCF) has no eu_fip row, but the resolver DID identify it (canonical_ins
    "143", method code_exact) -- codex_ins is keyed on that INS number, not
    the (missing) EU id.
    """
    resolution_path = settings.RESOLUTION_OUTPUT_DIR / verdict_path.name
    resolution = _load_json(resolution_path)
    if resolution is None:
        return {}
    return {
        item["item_id"]: item["canonical_ins"] for item in resolution["items"] if item.get("canonical_ins") is not None
    }


def _load_horizon_flagged(verdict_path: Path) -> frozenset[str]:
    """eu_canonical_ids src/horizon/lane.py flagged as under active EFSA
    review, from data/outputs/horizon/<name>.json if scripts/horizon.py has
    been run for this label -- absent file means an empty set, which leaves
    substitute ranking unchanged.
    """
    horizon_path = settings.HORIZON_OUTPUT_DIR / verdict_path.name
    horizon = _load_json(horizon_path)
    if horizon is None:
        return frozenset()
    return frozenset(signal["eu_canonical_id"] for signal in horizon["signals"])


def _substitutes_file(verdict_path: Path, eu_fip: list, codex_ins: list) -> None:
    payload = _load_json(verdict_path)
    if payload is None:
        console.print(f"[red]No verdict file at {verdict_path}[/red]")
        return
    verdict = ProductVerdict.model_validate(payload)
    canonical_ins_by_item = _load_canonical_ins_map(verdict_path)
    horizon_flagged = _load_horizon_flagged(verdict_path)

    result = find_substitutes(verdict, eu_fip, codex_ins, canonical_ins_by_item, horizon_flagged)

    if not result.suggestions:
        console.print(f"{verdict_path.stem}: nothing blocks export -- no substitutes to propose.\n")
    for suggestion in result.suggestions:
        blocked_display = suggestion.blocked_name or suggestion.blocked_eu_canonical_id or f"item {suggestion.item_id}"
        console.print(
            f"[bold]{verdict_path.stem}[/bold] -- item {suggestion.item_id}: "
            f"[red]{blocked_display}[/red] ({suggestion.blocked_reason} in {suggestion.fcs_code or 'unknown category'})"
        )
        if not suggestion.candidates:
            console.print(f"  [yellow]No candidates: {suggestion.no_candidates_reason}[/yellow]\n")
            continue

        table = Table()
        table.add_column("EU id")
        table.add_column("Name")
        table.add_column("Shared classes")
        table.add_column("Verdict")
        table.add_column("Level")
        table.add_column("Flags")
        for candidate in suggestion.candidates:
            level = f"{candidate.max_level_mg_kg} mg/kg" if candidate.max_level_mg_kg is not None else "quantum satis"
            table.add_row(
                candidate.eu_canonical_id,
                candidate.additive_name or "—",
                ", ".join(candidate.shared_functional_classes),
                candidate.verdict,
                level,
                ", ".join(candidate.flags) or "—",
            )
        console.print(table)
        console.print()

    for warning in result.warnings:
        console.print(f"[yellow]{warning}[/yellow]")

    settings.SUBSTITUTES_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = settings.SUBSTITUTES_OUTPUT_DIR / verdict_path.name
    out_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    console.print(f"Saved to [cyan]{out_path}[/cyan]\n")


def main(
    verdict_path: Annotated[
        Path | None, typer.Argument(help="Path to one data/outputs/verdict/*.json file.")
    ] = None,
    all_files: Annotated[
        bool, typer.Option("--all", help="Propose substitutes for every file in data/outputs/verdict/.")
    ] = False,
    log: Annotated[
        bool, typer.Option("--log/--no-log", help="Tee console output to a log file (default on).")
    ] = True,
) -> None:
    """Propose substitutes for one verdict file, or every verdict file with --all."""
    log_path = setup_run_log("substitutes") if log else None
    if not verdict_path and not all_files:
        console.print("[red]Pass a file path, or use --all.[/red]")
        if log_path:
            console.print(f"Log: {log_path}")
        raise typer.Exit(1)

    eu_fip = load_eu_fip(settings.REFERENCE_DIR / "eu_fip.json")
    codex_ins = _load_json(settings.REFERENCE_DIR / "codex_ins.json", [])

    if all_files:
        paths = sorted(settings.VERDICT_OUTPUT_DIR.glob("*.json"))
        if not paths:
            console.print(f"[yellow]No files found in {settings.VERDICT_OUTPUT_DIR}[/yellow]")
            if log_path:
                console.print(f"Log: {log_path}")
            raise typer.Exit(1)
        for path in paths:
            _substitutes_file(path, eu_fip, codex_ins)
    else:
        _substitutes_file(verdict_path, eu_fip, codex_ins)

    if log_path:
        console.print(f"Log: {log_path}")


if __name__ == "__main__":
    typer.run(main)
