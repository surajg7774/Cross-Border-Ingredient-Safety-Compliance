"""CLI entry point: compute the EU Annex II compliance verdict for a
resolved (and, where available, categorized) label.

    uv run python scripts/verdict.py data/outputs/resolution/Chipsmain.json
    uv run python scripts/verdict.py --all

Reads data/outputs/resolution/<name>.json (required) and
data/outputs/category/<name>.json (optional -- absent for text-input labels,
since text input skips the category stage entirely). Writes
data/outputs/verdict/<name>.json. All I/O lives here -- src/rules/engine.py
itself never reads a file, calls the network, or prints anything.
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
from src.category.classifier import item_component_labels
from src.category.corpus import parse_food_categories
from src.category.schemas import CategoryCandidate, CategoryResult
from src.logging_setup import setup_run_log
from src.reference_data import load_eu_fip
from src.resolve.schemas import ResolvedItem
from src.rules.engine import evaluate
from src.rules.schemas import ItemVerdict

console = Console()


def _load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _load_category_names() -> dict[str, str]:
    """code -> name for every EU food category, for validating --category."""
    raw = _load_json(settings.REFERENCE_DIR / "food_categories.json", [])
    return {category.code: category.name for category in parse_food_categories(raw)}


def _parse_category_overrides(
    raw_overrides: list[str], category_names: dict[str, str]
) -> dict[str | None, CategoryCandidate]:
    """--category values into component_label (None = product scope) -> the
    single confirmed CategoryCandidate that REPLACES that query's retrieved
    top-3. Two forms: a bare code ("7.2") applies to the product-scope
    query; "component=code" ("Seasoning=12.2.2") applies to that named
    component's query only.

    Every code is validated against data/reference/food_categories.json --
    an unknown code is an error (not a guess), reported without dumping all
    155 known codes.
    """
    overrides: dict[str | None, CategoryCandidate] = {}
    for raw in raw_overrides:
        if "=" in raw:
            component_label, code = (part.strip() for part in raw.split("=", 1))
        else:
            component_label, code = None, raw.strip()
        if code not in category_names:
            console.print(
                f"[red]Unknown category code {code!r} -- not found in "
                "data/reference/food_categories.json.[/red]"
            )
            raise typer.Exit(1)
        overrides[component_label] = CategoryCandidate(
            code=code, name=category_names[code], similarity=1.0, permitted=True
        )
    return overrides


def _build_item_category_map(
    resolved_items: list[ResolvedItem],
    category_results: list[CategoryResult],
    overrides: dict[str | None, CategoryCandidate],
    item_labels: dict[int, str | None],
) -> tuple[dict[int, CategoryResult], frozenset[int]]:
    """item_id -> the CategoryResult that covers it, matched by the
    component label resolved for THAT item (item_labels, built by
    src.category.classifier.item_component_labels via the parent_item_id
    ancestor walk -- see its docstring). NEVER matched via eu_canonical_id:
    a substance id is not unique per declaration, so two items sharing one
    (e.g. maltitol declared in both a product's Inner Layer and its Outer
    Layer/Chocolate Paste) must resolve to DIFFERENT CategoryResults, and
    joining on the substance id instead collapses them onto whichever
    component query happened to be processed last.

    Falls back to the product-scope result (if the label produced one) for
    an item whose own component has no query (nothing under it needed a
    category, or the item is itself product-scope).

    Applies `overrides` BEFORE the join: a query whose scope/component_label
    matches an override key has its top3 REPLACED with the single confirmed
    candidate, and every item that resolves to it is returned in the second
    element (confirmed_item_ids) so evaluate() flags it
    category_confirmed_by_user instead of category_unconfirmed.
    """
    confirmed_labels: set[str | None] = set()
    resolved_results = []
    for result in category_results:
        key = result.component_label if result.query.scope == "component" else None
        if key in overrides:
            result = result.model_copy(update={"top3": [overrides[key]]})
            confirmed_labels.add(key)
        resolved_results.append(result)
    category_results = resolved_results

    used_override_keys = confirmed_labels & set(overrides)
    for unused_key in set(overrides) - used_override_keys:
        label = unused_key or "(product)"
        console.print(
            f"[yellow]--category override for {label!r} did not match any retrieved "
            "component -- check the component name.[/yellow]"
        )

    product_result = next((r for r in category_results if r.query.scope == "product"), None)
    by_component_label: dict[str, CategoryResult] = {
        result.component_label: result for result in category_results if result.query.scope == "component"
    }

    mapping: dict[int, CategoryResult] = {}
    confirmed_item_ids: set[int] = set()
    for item in resolved_items:
        component_label = item_labels.get(item.item_id)
        if component_label is not None and component_label in by_component_label:
            chosen = by_component_label[component_label]
        elif product_result is not None:
            chosen = product_result
        else:
            continue
        mapping[item.item_id] = chosen
        key = chosen.component_label if chosen.query.scope == "component" else None
        if key in confirmed_labels:
            confirmed_item_ids.add(item.item_id)
    return mapping, frozenset(confirmed_item_ids)


def _truncate(text: str | None, length: int = 40) -> str:
    if not text:
        return "—"
    return text if len(text) <= length else text[: length - 1] + "…"


# Display-only shortening for the "Category outcome" column -- the JSON
# keeps the full verdict labels.
_SHORT_VERDICT = {
    "permitted_qs": "permitted",
    "permitted_with_limit": "permitted",
    "permitted_with_conditions": "permitted",
    "not_permitted_in_category": "not_permitted",
    "not_authorised_eu": "not_authorised",
}


def _competing_outcome(item: ItemVerdict) -> str:
    """"11.1 not_permitted / 7.2 permitted" -- surfaces exactly which
    candidate categories disagree, so a category_dependent verdict is
    visible without opening the JSON. Empty when every candidate agrees.
    """
    if item.verdict_certainty != "category_dependent" or not item.by_category:
        return ""
    return " / ".join(
        f"{cv.fcs_code} {_SHORT_VERDICT.get(cv.verdict, cv.verdict)}" for cv in item.by_category
    )


def _load_extraction_items(resolution_path: Path) -> list[dict]:
    """The raw extraction item dicts (nesting_depth, parent_item_id,
    name_as_declared, ...) for the matching label -- the source of truth
    for item_component_labels' ancestor walk. Empty for a missing
    extraction file (e.g. text input, which skips the extraction stage's
    image path entirely) or a gated-out label."""
    extraction_path = settings.EXTRACTION_OUTPUT_DIR / resolution_path.name
    extraction = _load_json(extraction_path)
    if not extraction or extraction.get("extraction") is None:
        return []
    return extraction["extraction"]["items"]


def _load_extraction_names(extraction_items: list[dict]) -> dict[int, str]:
    """item_id -> name_as_declared (falling back to verbatim) -- for
    DISPLAY only. ItemVerdict.additive_name comes from eu_fip and is null
    for out-of-scope items (flavourings, enzymes, food ingredients) and
    for additives absent from eu_fip, so the table would otherwise print
    "item 4" instead of "Natural Khus Flavour". Best-effort: an item with
    neither field set is simply absent from this map.
    """
    names = {}
    for item in extraction_items:
        name = item.get("name_as_declared") or item.get("verbatim")
        if name:
            names[item["item_id"]] = name
    return names


def _verdict_file(
    resolution_path: Path, eu_fip: list[dict], overrides: dict[str | None, CategoryCandidate]
) -> None:
    resolution = _load_json(resolution_path)
    if resolution is None:
        console.print(f"[red]No resolution file at {resolution_path}[/red]")
        return
    resolved_items = [ResolvedItem.model_validate(item) for item in resolution["items"]]
    extraction_items = _load_extraction_items(resolution_path)
    extraction_names = _load_extraction_names(extraction_items)
    resolved_by_id = {item.item_id: item for item in resolved_items}
    item_labels = item_component_labels(extraction_items, resolved_by_id)

    category_path = settings.CATEGORY_OUTPUT_DIR / resolution_path.name
    category_payload = _load_json(category_path)
    if category_payload is None:
        # Text input skips the category stage entirely (scripts/extract_text.py
        # never calls classify_category.py). Items score category_unknown
        # UNLESS the additive is absent from eu_fip altogether -- absence is
        # category-independent, so src/rules/engine.py computes that verdict
        # without needing a category at all.
        console.print(
            f"[yellow]{resolution_path.name}: no category output -- text input, or the "
            "category stage was not run. Items score category_unknown unless the additive "
            "is absent from eu_fip entirely.[/yellow]"
        )
        category_results = []
    else:
        category_results = [CategoryResult.model_validate(r) for r in category_payload["results"]]

    item_category_map, confirmed_item_ids = _build_item_category_map(
        resolved_items, category_results, overrides, item_labels
    )
    verdict = evaluate(resolved_items, item_category_map, eu_fip, confirmed_item_ids)

    table = Table(title=resolution_path.stem)
    table.add_column("Item")
    table.add_column("EU id")
    table.add_column("Component")
    table.add_column("Rank-1 category")
    table.add_column("Headline")
    table.add_column("Level")
    table.add_column("Conditions")
    table.add_column("Category outcome")

    for item in verdict.items:
        top = item.by_category[0] if item.by_category else None
        level = f"{top.max_level_mg_kg} mg/kg" if top and top.max_level_mg_kg is not None else "—"
        display_name = item.additive_name or extraction_names.get(item.item_id) or f"item {item.item_id}"
        table.add_row(
            display_name,
            item.eu_canonical_id or "—",
            item.component_label or "(product)",
            top.fcs_code if top else "—",
            item.headline,
            level,
            _truncate(top.conditions if top else None),
            _competing_outcome(item),
        )
    console.print(table)
    console.print(f"\n{verdict.summary}\n")

    settings.VERDICT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = settings.VERDICT_OUTPUT_DIR / resolution_path.name
    out_path.write_text(verdict.model_dump_json(indent=2), encoding="utf-8")
    console.print(f"Saved to [cyan]{out_path}[/cyan]\n")


def main(
    resolution_path: Annotated[
        Path | None, typer.Argument(help="Path to one data/outputs/resolution/*.json file.")
    ] = None,
    all_files: Annotated[
        bool, typer.Option("--all", help="Compute verdicts for every file in data/outputs/resolution/.")
    ] = False,
    category: Annotated[
        list[str] | None,
        typer.Option(
            "--category",
            help=(
                "Confirm a food category, replacing retrieval for that query: a bare code "
                "(e.g. 7.2) applies to the product query, 'component=code' (e.g. "
                "'Seasoning=12.2.2') applies to that component only. Repeatable."
            ),
        ),
    ] = None,
    log: Annotated[
        bool, typer.Option("--log/--no-log", help="Tee console output to a log file (default on).")
    ] = True,
) -> None:
    """Compute one verdict, or every verdict with --all."""
    log_path = setup_run_log("verdict") if log else None
    if not resolution_path and not all_files:
        console.print("[red]Pass a file path, or use --all.[/red]")
        if log_path:
            console.print(f"Log: {log_path}")
        raise typer.Exit(1)
    if all_files and category:
        console.print(
            "[yellow]--category with --all applies the SAME override to every label -- "
            "almost never what you want, since category codes are label-specific. Pass a "
            "single resolution file path instead if you meant one product.[/yellow]"
        )

    eu_fip = load_eu_fip(settings.REFERENCE_DIR / "eu_fip.json")
    overrides = {}
    if category:
        overrides = _parse_category_overrides(category, _load_category_names())

    if all_files:
        paths = sorted(settings.RESOLUTION_OUTPUT_DIR.glob("*.json"))
        if not paths:
            console.print(f"[yellow]No files found in {settings.RESOLUTION_OUTPUT_DIR}[/yellow]")
            if log_path:
                console.print(f"Log: {log_path}")
            raise typer.Exit(1)
        for path in paths:
            _verdict_file(path, eu_fip, overrides)
    else:
        _verdict_file(resolution_path, eu_fip, overrides)

    if log_path:
        console.print(f"Log: {log_path}")


if __name__ == "__main__":
    typer.run(main)
