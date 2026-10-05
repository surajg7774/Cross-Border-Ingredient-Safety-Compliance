"""CLI entry point: resolve extraction items into canonical identities.

    uv run python scripts/resolve.py data/outputs/extraction/Chipsmain.json
    uv run python scripts/resolve.py --all

Loads the reference datasets, calls src.resolve.resolver.resolve_items, writes
data/outputs/resolution/<name>.json, and prints a summary table. All I/O lives
here -- src/resolve/ itself never reads a file or prints anything.
"""

import hashlib
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
from src.resolve.resolver import References, resolve_items

console = Console()


def _load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _index_version(paths: list[Path]) -> str:
    """A short hash of the reference files' contents, so a stale resolution is
    detectable (and re-runnable) once any reference dataset changes."""
    digest = hashlib.sha256()
    for path in paths:
        if path.exists():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def _load_references() -> References:
    codex_ins_path = settings.REFERENCE_DIR / "codex_ins.json"
    functional_classes_path = settings.REFERENCE_DIR / "functional_classes.json"
    label_aliases_path = settings.REFERENCE_DIR / "label_aliases.json"
    eu_fip_path = settings.REFERENCE_DIR / "eu_fip.json"

    if not eu_fip_path.exists():
        console.print(
            "[yellow]Note:[/yellow] data/reference/eu_fip.json not found -- "
            "the EU crosswalk (Section E) will be a no-op for this run."
        )

    return References(
        codex_ins=_load_json(codex_ins_path, []),
        functional_classes=_load_json(functional_classes_path, []),
        label_aliases=_load_json(label_aliases_path, {}),
        eu_fip=load_eu_fip(eu_fip_path),
        index_version=_index_version(
            [codex_ins_path, functional_classes_path, label_aliases_path, eu_fip_path]
        ),
    )


def _warn_if_stale(out_path: Path, current_index_version: str) -> None:
    """A resolution file written under an older reference version is easy to
    mistake for a fresh (and wrong) result -- warn before overwriting it."""
    if not out_path.exists():
        return
    try:
        old = json.loads(out_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    old_version = old.get("index_version")
    if old_version and old_version != current_index_version:
        console.print(
            f"[yellow]Note:[/yellow] {out_path.name} was last resolved under reference "
            f"version {old_version!r} (now {current_index_version!r}) -- overwriting with "
            "the current references."
        )


def _resolve_file(extraction_path: Path, refs: References) -> None:
    extraction = json.loads(extraction_path.read_text(encoding="utf-8"))
    if extraction["extraction"] is None:
        console.print(f"[yellow]{extraction_path.name}: no extraction to resolve (gate stopped)[/yellow]")
        return

    items = extraction["extraction"]["items"]
    result = resolve_items(items, refs)
    items_by_id = {item["item_id"]: item for item in items}

    table = Table(title=extraction_path.name)
    table.add_column("Name / code")
    table.add_column("INS")
    table.add_column("EU id")
    table.add_column("Classification")
    table.add_column("Method")
    table.add_column("Conf.")
    table.add_column("Flags")
    table.add_column("Candidates")

    for resolved in result.items:
        item = items_by_id[resolved.item_id]
        label = item.get("declared_code") or item.get("name_as_declared") or item.get("verbatim")
        table.add_row(
            label,
            resolved.canonical_ins or "—",
            resolved.eu_canonical_id or "—",
            resolved.classification,
            resolved.resolution_method,
            f"{resolved.resolution_confidence:.2f}",
            ", ".join(resolved.flags) or "—",
            ", ".join(resolved.candidates) or "—",
        )
    console.print(table)

    settings.RESOLUTION_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = settings.RESOLUTION_OUTPUT_DIR / extraction_path.name
    _warn_if_stale(out_path, refs.index_version)
    out_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    console.print(f"Saved to [cyan]{out_path}[/cyan]\n")


def main(
    extraction_path: Annotated[
        Path | None, typer.Argument(help="Path to one data/outputs/extraction/*.json file.")
    ] = None,
    all_files: Annotated[
        bool, typer.Option("--all", help="Resolve every file in data/outputs/extraction/.")
    ] = False,
    log: Annotated[
        bool, typer.Option("--log/--no-log", help="Tee console output to a log file (default on).")
    ] = True,
) -> None:
    """Resolve one extraction file, or every extraction file with --all."""
    log_path = setup_run_log("resolve") if log else None
    if not extraction_path and not all_files:
        console.print("[red]Pass a file path, or use --all.[/red]")
        if log_path:
            console.print(f"Log: {log_path}")
        raise typer.Exit(1)

    refs = _load_references()

    if all_files:
        paths = sorted(settings.EXTRACTION_OUTPUT_DIR.glob("*.json"))
        if not paths:
            console.print(f"[yellow]No files found in {settings.EXTRACTION_OUTPUT_DIR}[/yellow]")
            if log_path:
                console.print(f"Log: {log_path}")
            raise typer.Exit(1)
        for path in paths:
            _resolve_file(path, refs)
    else:
        _resolve_file(extraction_path, refs)

    if log_path:
        console.print(f"Log: {log_path}")


if __name__ == "__main__":
    typer.run(main)
