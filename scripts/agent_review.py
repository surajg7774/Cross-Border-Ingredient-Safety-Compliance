"""CLI entry point: ask the review-queue resolver agent (src/agent/) to
propose an identity for every "unresolved" item in a computed compliance
verdict.

    uv run python scripts/agent_review.py data/outputs/verdict/Mixed.json
    uv run python scripts/agent_review.py --all
    uv run python scripts/agent_review.py --all --delay 8
    uv run python scripts/agent_review.py --all --force
    uv run python scripts/agent_review.py data/outputs/verdict/Mixed.json --model gemini-2.0-flash

Reads data/outputs/verdict/<name>.json (required), data/outputs/resolution/
<name>.json, and data/outputs/extraction/<name>.json -- all already written
by scripts/verdict.py/resolve.py/extract.py. Writes
data/outputs/agent/<name>.json. All I/O lives here -- src/agent/ itself
never reads a file, and the agent never writes to a verdict or a
ResolvedItem; see src/agent/resolver_agent.py's module docstring.

NOT part of the main pipeline: this script runs ON DEMAND, after a verdict
already exists, exactly like scripts/substitutes.py and scripts/horizon.py.
Every proposal printed here is a PROPOSAL -- a human confirms it (this CLI
only prints; app.py's review-queue Accept/Reject buttons are what actually
apply one).

PRIMARY_MODEL is quota-exhausted at the time this was written -- --model
defaults to SECONDARY_MODEL, not PRIMARY_MODEL, unlike every other script in
this project.

RATE LIMITS: MEASURED -- a --all run crashed with 429
(GenerateRequestsPerMinutePerProjectPerModel-FreeTier, quotaValue 15) on
gemini-3.5-flash-lite, the concrete model "gemini-flash-lite-latest"
resolves to. Each review item makes several model round trips, so 22 items
back-to-back exceeds 15 requests/minute. Two things address this: the
model-call retry itself now respects the server's own RetryInfo.retryDelay
(src/agent/resolver_agent.py's GeminiLLM, same fix as
src/extractors/gemini.py); and --delay paces requests between items so a
long run stays under the ceiling BY CONSTRUCTION rather than by recovering
from failures. A run that is interrupted or crashes anyway resumes rather
than restarts -- see _scan_target.
"""

import json
import sys
import time
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import typer
from rich.console import Console
from rich.table import Table

from config import settings
from src.agent.resolver_agent import AgentProposal, make_gemini_llm, resolve_review_item
from src.agent.tools import AgentRefs, build_tools
from src.logging_setup import setup_run_log

console = Console()

DEFAULT_DELAY_SECONDS = 5.0
# A rough, empirical per-item processing estimate (2-5 model round trips at
# a few seconds each, measured during development) -- used only to print an
# ESTIMATE of total runtime before a --all run starts, never to pace
# anything itself (--delay is what actually paces the loop).
ESTIMATED_PROCESSING_SECONDS_PER_ITEM = 15.0


def _load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _extraction_name(item: dict) -> str | None:
    return item.get("name_as_declared") or item.get("verbatim")


def _build_review_items(verdict: dict, resolution: dict, extraction: dict) -> list[dict]:
    """One dict per "unresolved" ItemVerdict, joining in the matching
    ResolvedItem (classification, candidates, flags) and extraction item
    (name_as_declared, verbatim, declared_code) -- the shape
    src.agent.resolver_agent.resolve_review_item expects. Never includes
    "category_unknown" items: those need a food-category confirmation
    (the existing category_confirm flow), not an identity proposal."""
    resolved_by_id = {r["item_id"]: r for r in resolution["items"]}
    extraction_by_id = {e["item_id"]: e for e in extraction["extraction"]["items"]}

    items = []
    for item_verdict in verdict["items"]:
        if item_verdict["headline"] != "unresolved":
            continue
        item_id = item_verdict["item_id"]
        resolved = resolved_by_id.get(item_id, {})
        extracted = extraction_by_id.get(item_id, {})
        items.append(
            {
                "item_id": item_id,
                "name_as_declared": extracted.get("name_as_declared"),
                "verbatim": extracted.get("verbatim"),
                "declared_code": extracted.get("declared_code"),
                "classification": resolved.get("classification", item_verdict.get("headline")),
                "candidates": resolved.get("candidates", []),
                "flags": resolved.get("flags", []),
                "component_label": item_verdict.get("component_label"),
            }
        )
    return items


def _build_refs(
    review_item: dict, verdict: dict, extraction: dict, eu_fip: list, codex_ins: list, label_aliases: dict, model_id: str
) -> AgentRefs:
    gate = extraction.get("gate") or {}
    component = review_item.get("component_label") or "(product)"
    confirmed_category = (verdict.get("category_used") or {}).get(component)

    other_names = [
        name
        for e in extraction["extraction"]["items"]
        if e["item_id"] != review_item["item_id"] and (name := _extraction_name(e))
    ]

    return AgentRefs(
        codex_ins=codex_ins,
        eu_fip=eu_fip,
        label_aliases=label_aliases,
        product_name=gate.get("product_name"),
        product_descriptor=gate.get("product_descriptor"),
        confirmed_category=confirmed_category,
        other_ingredient_names=other_names,
        model_id=model_id,
    )


def _print_proposal(name: str, proposal: AgentProposal) -> None:
    display_name = proposal.name_as_declared or f"item {proposal.item_id}"
    if proposal.declined:
        console.print(f"[bold]{name}[/bold] -- [yellow]{display_name}[/yellow]: DECLINED")
        console.print(f"  [yellow]{proposal.decline_reason}[/yellow]")
    else:
        console.print(
            f"[bold]{name}[/bold] -- [cyan]{display_name}[/cyan]: "
            f"PROPOSED {proposal.proposed_classification}"
            + (f" (INS {proposal.proposed_canonical_ins})" if proposal.proposed_canonical_ins else "")
            + f" [confidence: {proposal.confidence}]"
        )
    console.print(f"  {proposal.reasoning}")
    for line in proposal.evidence:
        console.print(f"  [dim]evidence:[/dim] {line}")

    table = Table(show_header=True, header_style="dim")
    table.add_column("Tool")
    table.add_column("Args")
    table.add_column("Result")
    for call in proposal.tool_calls:
        table.add_row(call.get("tool", "?"), json.dumps(call.get("args", {})), call.get("result_summary", ""))
    if proposal.tool_calls:
        console.print(table)
    console.print()


def _write_agent_output(
    out_path: Path, model_id: str, resolved_model_id: str | None, review_items: list[dict], proposals_by_id: dict[int, dict]
) -> None:
    """Written after EVERY item, not batched at the end of a file's loop --
    a crash partway through a file must not lose the items already done
    (see _scan_target/main). `review_items` fixes the ORDER; `proposals_by_id`
    may be a mix of proposals from a previous run (skipped, reused
    verbatim) and ones just computed."""
    settings.AGENT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ordered = [proposals_by_id[item["item_id"]] for item in review_items if item["item_id"] in proposals_by_id]
    payload = {"model_id": model_id, "resolved_model_id": resolved_model_id, "proposals": ordered}
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _scan_target(verdict_path: Path, force: bool) -> dict | None:
    """Everything needed to process one verdict file's review queue, plus
    which of its items are already done -- RESUME, not restart: an item
    already present in data/outputs/agent/<name>.json (by item_id) is
    skipped unless --force, so a run interrupted at item 14 of a --all
    picks back up at item 14, not item 1. Returns None when there is
    nothing to review at all (missing files, or no "unresolved" items)."""
    verdict = _load_json(verdict_path)
    if verdict is None:
        console.print(f"[red]No verdict file at {verdict_path}[/red]")
        return None
    resolution = _load_json(settings.RESOLUTION_OUTPUT_DIR / verdict_path.name)
    extraction = _load_json(settings.EXTRACTION_OUTPUT_DIR / verdict_path.name)
    if resolution is None or extraction is None:
        console.print(f"[yellow]{verdict_path.name}: missing resolution/extraction file, skipping[/yellow]")
        return None

    review_items = _build_review_items(verdict, resolution, extraction)
    if not review_items:
        console.print(f"{verdict_path.stem}: nothing in the review queue -- no unresolved items.\n")
        return None

    out_path = settings.AGENT_OUTPUT_DIR / verdict_path.name
    existing_payload = {} if force else _load_json(out_path, {})
    proposals_by_id = {p["item_id"]: p for p in existing_payload.get("proposals", [])}

    pending = []
    for item in review_items:
        if item["item_id"] in proposals_by_id:
            console.print(
                f"[dim]{verdict_path.stem} -- item {item['item_id']}: already reviewed "
                f"({out_path}) -- skipping (--force to redo)[/dim]"
            )
        else:
            pending.append(item)

    return {
        "verdict_path": verdict_path,
        "verdict": verdict,
        "extraction": extraction,
        "out_path": out_path,
        "review_items": review_items,
        "pending": pending,
        "proposals_by_id": proposals_by_id,
        "resolved_model_id": existing_payload.get("resolved_model_id"),
    }


def main(
    verdict_path: Annotated[
        Path | None, typer.Argument(help="Path to one data/outputs/verdict/*.json file.")
    ] = None,
    all_files: Annotated[
        bool, typer.Option("--all", help="Review the queue for every file in data/outputs/verdict/.")
    ] = False,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Model ID (defaults to SECONDARY_MODEL -- PRIMARY_MODEL is quota-exhausted)."),
    ] = None,
    delay: Annotated[
        float,
        typer.Option("--delay", help="Seconds to wait between review items, to stay under the per-minute rate limit."),
    ] = DEFAULT_DELAY_SECONDS,
    force: Annotated[
        bool, typer.Option("--force", help="Reprocess items that already have a proposal, instead of skipping them.")
    ] = False,
    log: Annotated[
        bool, typer.Option("--log/--no-log", help="Tee console output to a log file (default on).")
    ] = True,
) -> None:
    """Propose identities for one verdict's review queue, or every verdict's with --all."""
    log_path = setup_run_log("agent_review") if log else None
    if not verdict_path and not all_files:
        console.print("[red]Pass a file path, or use --all.[/red]")
        if log_path:
            console.print(f"Log: {log_path}")
        raise typer.Exit(1)

    model_id = model or settings.SECONDARY_MODEL
    eu_fip = _load_json(settings.REFERENCE_DIR / "eu_fip.json", [])
    codex_ins = _load_json(settings.REFERENCE_DIR / "codex_ins.json", [])
    label_aliases = _load_json(settings.REFERENCE_DIR / "label_aliases.json", {})

    if all_files:
        paths = sorted(settings.VERDICT_OUTPUT_DIR.glob("*.json"))
        if not paths:
            console.print(f"[yellow]No files found in {settings.VERDICT_OUTPUT_DIR}[/yellow]")
            if log_path:
                console.print(f"Log: {log_path}")
            raise typer.Exit(1)
    else:
        paths = [verdict_path]

    # Scan every target FIRST -- this is what makes resume possible (each
    # file's already-done proposals are read once, here) and what lets the
    # runtime estimate below reflect the REMAINING work, not the full queue.
    targets = [t for t in (_scan_target(path, force) for path in paths) if t is not None]
    flat_pending = [(t, item) for t in targets for item in t["pending"]]

    if all_files:
        n_files = len({t["verdict_path"] for t, _ in flat_pending})
        if not flat_pending:
            console.print("Nothing to do -- every review item already has a proposal (use --force to redo).")
        else:
            est_seconds = len(flat_pending) * (delay + ESTIMATED_PROCESSING_SECONDS_PER_ITEM)
            console.print(
                f"{len(flat_pending)} review item(s) to process across {n_files} file(s). "
                f"At --delay {delay}s between items plus an estimated "
                f"~{ESTIMATED_PROCESSING_SECONDS_PER_ITEM:.0f}s of model time each, this run should take "
                f"ROUGHLY ~{est_seconds:.0f}s (~{est_seconds / 60:.1f} min) -- an estimate, not a guarantee; "
                "actual model latency and retries vary."
            )

    touched: set[Path] = set()
    for i, (target, review_item) in enumerate(flat_pending):
        refs = _build_refs(review_item, target["verdict"], target["extraction"], eu_fip, codex_ins, label_aliases, model_id)
        tools = build_tools(refs)
        llm = make_gemini_llm(model_id, tools)
        proposal = resolve_review_item(review_item, refs, llm, tools)
        _print_proposal(target["verdict_path"].stem, proposal)

        target["proposals_by_id"][review_item["item_id"]] = proposal.model_dump()
        target["resolved_model_id"] = llm.resolved_model_id or target["resolved_model_id"]
        _write_agent_output(
            target["out_path"], model_id, target["resolved_model_id"], target["review_items"], target["proposals_by_id"]
        )
        touched.add(target["out_path"])

        if i < len(flat_pending) - 1:
            time.sleep(delay)

    for path in sorted(touched):
        console.print(f"Saved to [cyan]{path}[/cyan]")
    if touched:
        console.print()

    if log_path:
        console.print(f"Log: {log_path}")


if __name__ == "__main__":
    typer.run(main)
