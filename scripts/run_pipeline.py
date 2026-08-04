"""CLI entry point: run the LangGraph pipeline (src/graph/) end to end.

    uv run python scripts/run_pipeline.py data/labels/Parle-Gmain.jpeg
    uv run python scripts/run_pipeline.py --text "Sugar, Colour (INS 143)" --name Test
    uv run python scripts/run_pipeline.py data/labels/Khusmain.png --auto-confirm

An ADDITIONAL entry point, not a replacement for the standalone scripts
(extract.py, resolve.py, classify_category.py, verdict.py, substitutes.py,
horizon.py) -- this one graph run does the same work those do in sequence,
via src/graph/pipeline.py's Send fan-out (one classify per component) and
parallel substitutes/horizon branches, pausing for human category
confirmation via LangGraph's interrupt()/Command(resume=...) instead of a
--category flag applied after the fact. Writes each stage's output to the
SAME directory the matching standalone script would (data/outputs/extraction,
resolution, category, verdict, substitutes, horizon), so graph output and
script output are interchangeable files -- e.g. `scripts/score_category.py`
can score a category file this script wrote exactly as it would one
`scripts/classify_category.py` wrote.

Without --auto-confirm, a paused run prints the retrieved top-3 per
component and prompts for a choice (blank = accept rank-1), the same
information --category or app.py's confirmation screen would show, then
resumes. With --auto-confirm, every query accepts rank-1 without pausing --
for batch runs where nothing is watching for a prompt.
"""

import json
import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import typer
from langgraph.types import Command
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from config import settings
from src.graph.pipeline import DEFAULT_CATEGORY_CONFIG, build_pipeline
from src.logging_setup import setup_run_log

console = Console()


def _empty_state(label_path: str | None, text_input: str | None, name: str, description: str | None) -> dict:
    return {
        "label_path": label_path,
        "text_input": text_input,
        "name": name,
        "description": description,
        "extraction": None,
        "resolution": None,
        "category": {},
        "confirmed_categories": {},
        "verdict": None,
        "substitutes": None,
        "horizon": None,
        "news_signals": None,
        "narration": None,
        "errors": [],
    }


def _prompt_for_confirmation(payload: dict[str, list[dict]]) -> dict[str, str]:
    """Print the retrieved top-3 per component, ask for a choice (blank =
    rank-1), and return {component_label: chosen_code} -- the shape
    Command(resume=...) expects."""
    resume: dict[str, str] = {}
    for label, candidates in payload.items():
        table = Table(title=f"Confirm category -- {label}")
        table.add_column("#")
        table.add_column("Code")
        table.add_column("Name")
        table.add_column("Score")
        for i, candidate in enumerate(candidates, start=1):
            table.add_row(str(i), candidate["code"], candidate["name"], f"{candidate['score']:.4f}")
        console.print(table)

        if not candidates:
            console.print(f"[yellow]{label}: nothing retrieved, nothing to confirm.[/yellow]")
            continue

        choice = Prompt.ask(
            f"Category code for {label!r} (blank = accept rank-1: {candidates[0]['code']})", default=""
        ).strip()
        resume[label] = choice or candidates[0]["code"]
    return resume


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"Saved to [cyan]{path}[/cyan]")


def _write_outputs(name: str, result: dict, description_source: str) -> None:
    if result.get("extraction") is not None:
        _write_json(settings.EXTRACTION_OUTPUT_DIR / f"{name}.json", result["extraction"])
    if result.get("resolution") is not None:
        _write_json(settings.RESOLUTION_OUTPUT_DIR / f"{name}.json", result["resolution"])
    if result.get("category"):
        category_payload = {
            "config": DEFAULT_CATEGORY_CONFIG.name,
            "retrieval_method": DEFAULT_CATEGORY_CONFIG.retrieval_method,
            "strategy": "none",
            "corpus_mean_similarity": None,
            "index_build_seconds": None,
            "description_source": description_source,
            "results": list(result["category"].values()),
        }
        _write_json(settings.CATEGORY_OUTPUT_DIR / f"{name}.json", category_payload)
    if result.get("verdict") is not None:
        _write_json(settings.VERDICT_OUTPUT_DIR / f"{name}.json", result["verdict"])
    if result.get("substitutes") is not None:
        _write_json(settings.SUBSTITUTES_OUTPUT_DIR / f"{name}.json", result["substitutes"])
    if result.get("horizon") is not None:
        _write_json(settings.HORIZON_OUTPUT_DIR / f"{name}.json", result["horizon"])


def main(
    label_path: Annotated[Path | None, typer.Argument(help="Path to a label image.")] = None,
    text: Annotated[str | None, typer.Option("--text", help="A pasted ingredients declaration.")] = None,
    name: Annotated[
        str | None, typer.Option("--name", help="Output filename stem. Required with --text.")
    ] = None,
    description: Annotated[
        str | None, typer.Option("--description", help="Product description, improves category matching.")
    ] = None,
    auto_confirm: Annotated[
        bool,
        typer.Option("--auto-confirm", help="Accept rank-1 for every category query without pausing."),
    ] = False,
    log: Annotated[
        bool, typer.Option("--log/--no-log", help="Tee console output to a log file (default on).")
    ] = True,
) -> None:
    """Run the full pipeline on one label image or pasted text, via the graph."""
    log_path = setup_run_log("run_pipeline") if log else None

    if (label_path is None) == (text is None):
        console.print("[red]Pass exactly one of a label image path, or --text.[/red]")
        if log_path:
            console.print(f"Log: {log_path}")
        raise typer.Exit(1)
    if text is not None and not name:
        console.print("[red]--name is required with --text (sets the output filename stem).[/red]")
        if log_path:
            console.print(f"Log: {log_path}")
        raise typer.Exit(1)

    run_name = name or label_path.stem
    description_source = "cli" if description else "none"

    console.print("Loading reference data and embedding the category corpus…")
    graph, _refs = build_pipeline(auto_confirm=auto_confirm)

    state = _empty_state(
        str(label_path) if label_path else None, text, run_name, description
    )
    config = {"configurable": {"thread_id": run_name}}
    result = graph.invoke(state, config)

    while "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        resume = _prompt_for_confirmation(payload)
        result = graph.invoke(Command(resume=resume), config)

    for error in result.get("errors", []):
        console.print(f"[red]{error}[/red]")

    _write_outputs(run_name, result, description_source)

    if result.get("verdict"):
        console.print(f"\n{result['verdict']['summary']}\n")
    if result.get("narration"):
        console.print(f"[bold]{result['narration']['summary']}[/bold]")

    if log_path:
        console.print(f"Log: {log_path}")


if __name__ == "__main__":
    typer.run(main)
