# DESIGN NOTE: _category_code/_parent_codes are duplicated from
# src/rules/engine.py's own (private, underscore-prefixed) helpers of the
# same name, rather than imported -- same reasoning src/substitutes/
# advisor.py and src/category/corpus.py already give for duplicating these
# exact two functions: small, stable, pure, and importing a private name
# out of a pipeline-stage module is fragile regardless of any stage-boundary
# question. If engine.py's widening rule ever changes, this worksheet
# generator's evidence would silently stop matching it -- low risk given
# how long these two have been stable, but worth knowing.
"""CLI entry point: scaffold data/golden/verdict/<label>.json TEMPLATE files
(every scored field left UNSET, never pre-filled from current output) plus a
companion data/golden/verdict/<label>.worksheet.md with the Annex II
evidence needed to fill them in by hand.

    uv run python scripts/init_verdict_golden.py Khusmain Parle-Gmain Chipsmain EU_productmain Ice-creammain

Read-only against the model: every input here (resolution, extraction,
category, eu_fip.json) is already on disk from a previous run. No pipeline
invocation, no API call.

NEVER overwrites an existing golden JSON (same safety rule
build_resolution_golden.py already uses) -- once a human starts filling one
in, this script must not clobber it. The worksheet IS regenerated every run,
since it is pure derived documentation, never hand-edited.

CATEGORIES ARE PINNED, not retrieved fresh, so scoring later isolates the
rule engine from category retrieval (recall@1 measured ~0.53 elsewhere in
this project) -- see docs/findings.md. The pin recorded here is the CURRENT
rank-1 retrieved category for each component, from the existing
data/outputs/category/<label>.json (already on disk) -- PROVISIONAL, marked
as such in the golden file itself. Review it against Annex II same as every
other field; change pinned_categories[*]["code"] if rank-1 was wrong before
treating the golden file as final. Only components that actually have an
additive item under them (eu_canonical_id set) are pinned at all -- a
category pin that no compliance decision depends on is noise, not evidence.
"""

import json
import re
import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import typer
from rich.console import Console

from config import settings
from src.category.classifier import item_component_labels
from src.reference_data import load_eu_fip
from src.resolve.schemas import ResolvedItem

console = Console()

_PAREN_SUFFIX_RE = re.compile(r"\([^)]*\)$")
_LETTER_SUFFIX_RE = re.compile(r"[a-z]$", re.IGNORECASE)

_OUT_OF_SCOPE_REGULATION = {
    "flavouring": "Reg 1334/2008",
    "enzyme": "Reg 1332/2008",
    "food_ingredient": "not an additive",
    "compound": "not an additive (a container/group node, not a substance)",
}


def _category_code(food_category_raw: str | None) -> str:
    if not food_category_raw:
        return ""
    return food_category_raw.split(" ", 1)[0].rstrip(".")


def _parent_codes(code: str) -> list[str]:
    parents = []
    current = code
    m = _PAREN_SUFFIX_RE.search(current)
    if m:
        current = current[: m.start()].strip()
        parents.append(current)
    m2 = _LETTER_SUFFIX_RE.search(current)
    if m2:
        current = current[: m2.start()]
        parents.append(current)
    return parents


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _build_eu_fip_indices(eu_fip: list[dict]) -> tuple[dict, dict]:
    by_id_category: dict[tuple[str, str], list[dict]] = {}
    by_id: dict[str, list[dict]] = {}
    for row in eu_fip:
        by_id.setdefault(row["canonical_id"], []).append(row)
        code = _category_code(row.get("food_category_raw"))
        if code:
            by_id_category.setdefault((row["canonical_id"], code), []).append(row)
    return by_id_category, by_id


def _prohibited_id(canonical_id: str, by_id: dict) -> str | None:
    for candidate_id in (canonical_id, *_parent_codes(canonical_id)):
        if any(row["status"] == "prohibited" for row in by_id.get(candidate_id, [])):
            return candidate_id
    return None


def _row_line(row: dict) -> str:
    level = f"{row['max_level_mg_kg']} mg/kg" if row.get("max_level_mg_kg") is not None else "no numeric cap"
    conditions = (row.get("conditions") or "(none)").replace("\n", " ")
    return f"    - status={row['status']}, level={level}\n      conditions: {conditions}"


def _gather_evidence(eu_canonical_id: str, pinned_fcs_code: str | None, by_id_category: dict, by_id: dict) -> str:
    """Evidence text for one additive item, in the SAME priority order
    src/rules/engine.py's _prohibited_id -> _lookup_candidate (exact, then
    parent-widened, then "appears elsewhere") use, so a human sees exactly
    what the engine would see, in the order it would see it."""
    lines = []

    prohibited_id = _prohibited_id(eu_canonical_id, by_id)
    if prohibited_id:
        lines.append(
            f"  JURISDICTION-WIDE PROHIBITION on {prohibited_id!r} (checked before any category "
            "lookup -- this overrides everything below)."
        )
        return "\n".join(lines)

    if pinned_fcs_code is None:
        lines.append("  NO PIN available for this item's component -- category could not be determined.")
        return "\n".join(lines)

    exact_rows = by_id_category.get((eu_canonical_id, pinned_fcs_code), [])
    if exact_rows:
        lines.append(f"  Exact match: eu_fip rows for ({eu_canonical_id!r}, {pinned_fcs_code!r}):")
        for row in exact_rows:
            lines.append(_row_line(row))
        return "\n".join(lines)

    for parent in _parent_codes(eu_canonical_id):
        widened_rows = by_id_category.get((parent, pinned_fcs_code), [])
        if widened_rows:
            lines.append(
                f"  No exact row for ({eu_canonical_id!r}, {pinned_fcs_code!r}) -- WIDENED to parent "
                f"{parent!r} at the SAME pinned category:"
            )
            for row in widened_rows:
                lines.append(_row_line(row))
            return "\n".join(lines)

    all_ids = (eu_canonical_id, *_parent_codes(eu_canonical_id))
    elsewhere = [row for cid in all_ids for row in by_id.get(cid, [])]
    if elsewhere:
        lines.append(
            f"  No row (exact or parent-widened) at pinned category {pinned_fcs_code!r}. "
            f"{eu_canonical_id!r} DOES appear elsewhere in eu_fip -- likely not_permitted_in_category, "
            "not not_authorised_eu. All rows found, any category:"
        )
        for cid in all_ids:
            for row in by_id.get(cid, []):
                code = _category_code(row.get("food_category_raw"))
                lines.append(f"    - {cid} in {code or '(no category -- prohibition row)'}: status={row['status']}")
    else:
        lines.append(
            f"  No row anywhere in eu_fip for {eu_canonical_id!r} or any parent -- not authorised in the EU "
            "at all, category-independent."
        )
    return "\n".join(lines)


def _structural_note(resolved: ResolvedItem) -> str | None:
    """Why an item is category-INDEPENDENT (out of scope, unresolved, or
    absent from eu_fip), mirroring src/rules/engine.py's _evaluate_item
    steps 1-4 -- informational only (WHICH branch applies, not what the
    branch concludes), so a reviewer doesn't have to re-derive the engine's
    own control flow from scratch. Returns None for an item that needs the
    category-dependent eu_fip lookup below (step 5)."""
    if resolved.classification in _OUT_OF_SCOPE_REGULATION:
        reg = _OUT_OF_SCOPE_REGULATION[resolved.classification]
        return f"OUT OF SCOPE -- {reg}. Not an Annex II lookup."
    if resolved.classification in ("unknown", "ambiguous"):
        return "UNRESOLVED -- the resolver did not identify a substance; category-independent."
    if resolved.classification == "additive" and resolved.eu_canonical_id is None:
        if resolved.canonical_ins is not None:
            return (
                f"ABSENT FROM eu_fip (Codex identified INS {resolved.canonical_ins}, no EU crosswalk entry) -- "
                "category-independent, not_authorised_eu by absence, per src/rules/engine.py's "
                "_evaluate_item step 2."
            )
        return "UNRESOLVED -- no canonical identity found at all; category-independent."
    return None  # additive with an eu_canonical_id -- category-dependent, see eu_fip evidence


def _scaffold_label(label: str, eu_fip_indices: tuple[dict, dict]) -> None:
    by_id_category, by_id = eu_fip_indices

    resolution = _load_json(settings.RESOLUTION_OUTPUT_DIR / f"{label}.json")
    extraction = _load_json(settings.EXTRACTION_OUTPUT_DIR / f"{label}.json")
    category = _load_json(settings.CATEGORY_OUTPUT_DIR / f"{label}.json")
    if resolution is None:
        console.print(f"[red]{label}: no data/outputs/resolution/{label}.json -- skipped.[/red]")
        return
    if extraction is None or extraction.get("extraction") is None:
        console.print(f"[red]{label}: no data/outputs/extraction/{label}.json (or gated out) -- skipped.[/red]")
        return

    extraction_items = extraction["extraction"]["items"]
    resolved_items = [ResolvedItem.model_validate(item) for item in resolution["items"]]
    resolved_by_id = {item.item_id: item for item in resolved_items}
    extraction_by_id = {item["item_id"]: item for item in extraction_items}
    item_labels = item_component_labels(extraction_items, resolved_by_id)

    rank1_by_component: dict[str | None, dict | None] = {}
    if category is not None:
        for result in category["results"]:
            key = result["component_label"] if result["query"]["scope"] == "component" else None
            top3 = result.get("top3") or []
            rank1_by_component[key] = top3[0] if top3 else None

    # Pin only components that actually gate an additive item's category
    # lookup -- see module docstring.
    pinned_categories: dict[str, dict] = {}
    for resolved in resolved_items:
        if resolved.classification != "additive" or resolved.eu_canonical_id is None:
            continue
        component_label = item_labels.get(resolved.item_id)
        key = component_label if component_label is not None else "(product)"
        if key in pinned_categories:
            continue
        pin = rank1_by_component.get(component_label)
        pinned_categories[key] = (
            {"code": pin["code"], "name": pin["name"]} if pin else {"code": None, "name": None}
        )

    # ---- golden JSON template: every scored field UNSET, never prefilled ----
    golden_path = settings.VERDICT_GOLDEN_DIR / f"{label}.json"
    if golden_path.exists():
        console.print(f"[yellow]{label}: golden file already exists at {golden_path} -- not overwritten.[/yellow]")
    else:
        golden_items = [
            {
                "item_id": resolved.item_id,
                "verbatim": extraction_by_id.get(resolved.item_id, {}).get("verbatim"),
                "headline": "UNSET",
                "eu_canonical_id": "UNSET",
                "blocked": "UNSET",
            }
            for resolved in resolved_items
        ]
        golden_doc = {
            "label": label,
            "pin_status": (
                "PROVISIONAL -- each pinned_categories entry's \"code\"/\"name\" was copied from the current "
                "rank-1 retrieved category (data/outputs/category/*.json), NOT yet human-confirmed. Review "
                "against Annex II same as every scored field below; edit the \"code\"/\"name\" directly if "
                "rank-1 was wrong. Keys are component_label, or the literal string \"(product)\" for the "
                "product-scope query -- the same shape scripts/verdict.py's --category override and "
                "ProductVerdict.category_used already use."
            ),
            "pinned_categories": pinned_categories,
            "items": golden_items,
        }
        settings.VERDICT_GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(json.dumps(golden_doc, indent=2), encoding="utf-8")
        console.print(f"[green]{label}: wrote golden template {golden_path}[/green]")

    # ---- worksheet: always regenerated, never hand-edited ----
    lines = [f"# Verdict golden worksheet -- {label}", ""]
    lines.append(
        "Pinned categories (PROVISIONAL -- rank-1 retrieved, review before trusting):"
    )
    for key, pin in pinned_categories.items():
        lines.append(f"  - {key}: {pin['code']!r} ({pin['name']})" if pin["code"] else f"  - {key}: NO CANDIDATE RETRIEVED")
    lines.append("")

    for resolved in resolved_items:
        ext = extraction_by_id.get(resolved.item_id, {})
        verbatim = ext.get("verbatim") or ext.get("name_as_declared") or "(no text)"
        component_label = item_labels.get(resolved.item_id)
        pin_key = component_label if component_label is not None else "(product)"
        pin = pinned_categories.get(pin_key)

        lines.append(f"## item_id={resolved.item_id}")
        lines.append(f"- verbatim: {verbatim!r}")
        lines.append(f"- classification: {resolved.classification}")
        lines.append(f"- resolved eu_canonical_id: {resolved.eu_canonical_id!r}  canonical_ins: {resolved.canonical_ins!r}")
        lines.append(f"- component: {component_label!r}  pinned category: {pin_key!r} -> {pin}")

        note = _structural_note(resolved)
        if note:
            lines.append(f"- {note}")
        else:
            pinned_code = pin["code"] if pin else None
            lines.append("- eu_fip evidence:")
            lines.append(_gather_evidence(resolved.eu_canonical_id, pinned_code, by_id_category, by_id))
        lines.append("")

    worksheet_path = settings.VERDICT_GOLDEN_DIR / f"{label}.worksheet.md"
    settings.VERDICT_GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    worksheet_path.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"[green]{label}: wrote worksheet {worksheet_path}[/green]")


def main(
    labels: Annotated[list[str], typer.Argument(help="Label names, e.g. Khusmain Parle-Gmain (no .json).")],
) -> None:
    """Scaffold a golden verdict template + worksheet for each given label."""
    eu_fip = load_eu_fip(settings.REFERENCE_DIR / "eu_fip.json")
    indices = _build_eu_fip_indices(eu_fip)
    for label in labels:
        _scaffold_label(label, indices)


if __name__ == "__main__":
    typer.run(main)
