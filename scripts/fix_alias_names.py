"""ONE-TIME script: correct label_aliases.json's ins_name field where it mismatches
codex_ins.json's name for the same (already-correct) ins.

Run once:   uv run python scripts/fix_alias_names.py

Only ins_name is ever changed -- alias and ins are left untouched, since the
mismatches are known to be formatting differences (a straight apostrophe where
the PDF has a curly one, or including a parenthetical synonym that
codex_ins.json already splits into its own synonyms field), not wrong lookups.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json

from rich.console import Console

console = Console()
CODEX_INS_PATH = Path("data/reference/codex_ins.json")
ALIASES_PATH = Path("data/reference/label_aliases.json")


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


def main() -> None:
    """Replace ins_name with codex_ins.json's name wherever they mismatch for a valid ins."""
    codex_ins = json.loads(CODEX_INS_PATH.read_text(encoding="utf-8"))
    by_ins = {r["ins"]: r for r in codex_ins}

    doc = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
    aliases = doc["aliases"]

    fixed = 0
    for entry in aliases:
        record = by_ins.get(entry["ins"])
        if record is None:
            continue
        if _normalise(record["name"]) == _normalise(entry["ins_name"]):
            continue
        console.print(f"{entry['alias']!r} ({entry['ins']}): {entry['ins_name']!r} -> {record['name']!r}")
        entry["ins_name"] = record["name"]
        fixed += 1

    not_found = sum(1 for entry in aliases if entry["ins"] not in by_ins)
    doc["_meta"]["review_status"] = "VERIFIED against codex_ins.json 2026-07-31"
    doc["_meta"]["verified_note"] = (
        f"{len(aliases)} aliases, {not_found} NOT FOUND, {fixed} ins_name corrections applied automatically."
    )

    ALIASES_PATH.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"\nFixed {fixed} entries. Wrote {ALIASES_PATH}")


if __name__ == "__main__":
    main()
