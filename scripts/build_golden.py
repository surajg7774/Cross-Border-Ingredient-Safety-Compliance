"""Build data/golden/extraction/ from data/outputs/extraction/, applying explicit corrections.

Run once:   uv run python scripts/build_golden.py

WHY A SCRIPT AND NOT HAND-EDITING
---------------------------------
The extraction outputs were audited against the label images and found to be
correct on every additive code (44/44) and every gate decision (3/3). Only one
substantive correction was needed. Applying that correction in code rather than
by hand keeps the golden set auditable: every difference between output and
golden is listed below with a reason, instead of being an untracked manual edit.

After this runs, data/golden/extraction/ is FROZEN. Never regenerate it —
re-running extraction overwrites data/outputs/extraction/ only. If a golden
file genuinely needs to change later, edit it by hand and record why.

WHAT THIS GOLDEN SET DOES AND DOES NOT MEASURE
----------------------------------------------
Because golden is seeded from output, the first score will be near 1.00. That
is a BASELINE, not a validation. Its value is regression detection: if a prompt
change in a later step drops an additive, the score falls and the run fails.
"""

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings

OUTPUTS = settings.EXTRACTION_OUTPUT_DIR
GOLDEN = settings.EXTRACTION_GOLDEN_DIR


# --------------------------------------------------------------------------
# CORRECTIONS
# --------------------------------------------------------------------------
# Each entry: filename -> function(data) -> data
# Every correction must have a comment saying what was wrong and why.

def fix_icecream(data: dict) -> dict:
    """Ice-creammain: remove the product NAME from declaration_statements.

    The model put "CHOCO HAZELNUT CRUNCH ICE CREAM BAR" into
    declaration_statements. That is the product name printed as a heading above
    the declaration, not a regulatory statement about the product. The other
    three entries are correct:
      - "Product Category: Medium Fat Ice Cream"  (FSSAI category declaration)
      - "CONTAINS PERMITTED NATURAL COLOUR(S) ..." (unnamed additive class —
        compliance-critical, since the EU requires colours to be named)
      - "Polyols may have laxative effect."       (mandatory warning statement)
    """
    stmts = data["extraction"]["declaration_statements"]
    data["extraction"]["declaration_statements"] = [
        s for s in stmts if s != "CHOCO HAZELNUT CRUNCH ICE CREAM BAR"
    ]
    return data


CORRECTIONS = {
    "Ice-creammain.json": fix_icecream,
}


# --------------------------------------------------------------------------
# OPEN JUDGEMENT CALLS — NOT applied. Decide these yourself and edit by hand.
# --------------------------------------------------------------------------
# 1. Chips ("+Spices and Condiments (Contains Chilli)")
#    "Contains Chilli" is extracted as a child item at depth 2. It is a
#    descriptor, not a sub-ingredient. LEFT AS IS because over-extraction is
#    acceptable in this system (a spare item costs a manual review; a missing
#    one is a missed additive). Remove it if you prefer a stricter convention.
#
# 2. "Flavours (Natural, Nature Identical and Artificial ...)"
#    Chocolateraw nests the bracket contents as a child item; Chocolatemain
#    does not. Same construction, two conventions. Flavourings fall under
#    Reg 1334/2008, not Annex II, so neither affects a verdict. Pick one and
#    apply it to both files if you want consistency.
#
# 3. Allergen statement splitting
#    Chocolatemain splits into ["Contains Milk, Wheat, Soy.", "May Contain
#    Barley"]; Chocolateraw keeps one string. Not scored, so harmless — but
#    worth settling if you ever score allergen output.
#
# 4. Chocolatemain "Cocos Solids" / "Emulsifiors"
#    VERIFIED AGAINST THE IMAGE: these typos are printed on the promotional
#    graphic. The model transcribed faithfully. KEPT deliberately — the golden
#    set records what the label says, not what it should have said.


def main() -> None:
    GOLDEN.mkdir(parents=True, exist_ok=True)
    created, skipped, corrected = [], [], []

    for src in sorted(OUTPUTS.glob("*.json")):
        if src.name == "score.csv":
            continue
        dst = GOLDEN / src.name

        if dst.exists():
            skipped.append(src.name)
            continue

        if src.name in CORRECTIONS:
            data = json.loads(src.read_text(encoding="utf-8"))
            data = CORRECTIONS[src.name](data)
            dst.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            corrected.append(src.name)
        else:
            shutil.copy2(src, dst)
            created.append(src.name)

    print(f"copied unchanged : {len(created)}")
    for n in created:
        print(f"   {n}")
    print(f"copied CORRECTED : {len(corrected)}")
    for n in corrected:
        print(f"   {n}")
    if skipped:
        print(f"skipped (exists) : {len(skipped)}")
        for n in skipped:
            print(f"   {n}")
    print("\ndata/golden/extraction/ is now the frozen reference. Do not regenerate it.")


if __name__ == "__main__":
    main()