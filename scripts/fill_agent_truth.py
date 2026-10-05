"""Fill data/golden/agent_truth.json with hand-decided answers.

Run once:   uv run python scripts/fill_agent_truth.py

Every answer below is a judgement made by reading the item name and the label
it came from. Two rules were applied:

  food_ingredient      the substance is an ordinary food, not a regulated
                       additive under Reg 1333/2008 Annex II. canonical_ins
                       is null because food ingredients have no INS number.

  correctly_declined   the label genuinely does not determine WHICH additive
                       is meant. "Modified cornstarch" could be any of 17
                       modified starches (1400-1452); "calcium phosphate"
                       any of three (341(i)-(iii)); "Stevia" any of four
                       steviol glycoside forms (960a-d). Declining is the
                       correct answer, not a failure.

One judgement call is recorded in the notes rather than hidden: INS 924 is
potassium bromate, which IS identifiable — so the truth is "identified" even
though the agent declined it after search_web hit quota exhaustion. That
counts as a wrongly-declined, which is the honest outcome: an infrastructure
failure, not a reasoning failure.
"""

import json
from pathlib import Path

TRUTH = Path("data/golden/agent_truth.json")

# name_as_declared -> (outcome, canonical_ins, classification, note)
ANSWERS: dict[str, tuple[str, str | None, str | None, str]] = {
    # --- ordinary foods: no INS number, not additives -------------------
    "Spices and Condiments (Contains Chilli)": (
        "identified", None, "food_ingredient",
        "A spice blend. The bracket is a descriptor, not a sub-ingredient.",
    ),
    "Contains Chilli": (
        "identified", None, "food_ingredient",
        "Parenthetical descriptor extracted as an item; chilli is a spice.",
    ),
    "Cocos Solids": (
        "identified", None, "food_ingredient",
        "Cocoa solids. 'Cocos' is a typo printed on the promotional graphic.",
    ),
    "Fractionated Fat": (
        "identified", None, "food_ingredient",
        "A processed fat, not an additive.",
    ),
    "Milk and Milk Solids": (
        "identified", None, "food_ingredient", "Dairy ingredient.",
    ),
    "Hazelnut Pieces": (
        "identified", None, "food_ingredient", "Nut, whole food.",
    ),
    "Hazelnut Paste": (
        "identified", None, "food_ingredient", "Ground nut, whole food.",
    ),
    "Red chilli powder": ("identified", None, "food_ingredient", "Spice."),
    "Aniseed powder": ("identified", None, "food_ingredient", "Spice."),
    "Fenugreek powder": ("identified", None, "food_ingredient", "Spice."),
    "Black pepper powder": ("identified", None, "food_ingredient", "Spice."),
    "Toasted onion powder": ("identified", None, "food_ingredient", "Vegetable powder."),
    "Clove powder": ("identified", None, "food_ingredient", "Spice."),
    "Green cardamom powder": ("identified", None, "food_ingredient", "Spice."),
    "Nutmeg powder": ("identified", None, "food_ingredient", "Spice."),
    "Hydrolysed groundnut protein": (
        "identified", None, "food_ingredient",
        "Protein hydrolysate used as an ingredient, not an Annex II additive.",
    ),

    # --- genuinely undeterminable from the label ------------------------
    "MODIFIED CORNSTARCH": (
        "correctly_declined", None, None,
        "17 modified starches exist (1400-1452). The label does not say which. "
        "Declining is correct; guessing would be wrong.",
    ),
    "CALCIUM PHOSPHATE": (
        "correctly_declined", None, None,
        "341(i), 341(ii) and 341(iii) are separate entries. The label does not "
        "distinguish them.",
    ),
    "Stevia": (
        "correctly_declined", None, None,
        "The additive is steviol glycosides, 960a-d depending on production "
        "method. 'Stevia' alone is the plant and does not determine which.",
    ),

    # --- identifiable, but only via an external source ------------------
    "INS 924": (
        "identified", "924", "additive",
        "Potassium bromate, a flour treatment agent. Absent from codex_ins and "
        "from eu_fip. Identifiable via search; the agent declined only because "
        "search_web hit quota exhaustion, so this scores as wrongly-declined — "
        "an infrastructure failure, not a reasoning failure.",
    ),
}


def main() -> None:
    data = json.loads(TRUTH.read_text(encoding="utf-8"))

    filled = 0
    unmatched: list[str] = []

    for item in data["items"]:
        name = item.get("name_as_declared")
        key = name if name in ANSWERS else None

        # the Mixed label's item has a null name; it is the INS 924 text-input case
        if key is None and name is None and item.get("label") == "Mixed":
            key = "INS 924"

        if key is None:
            unmatched.append(f'{item["label"]}: {name!r}')
            continue

        outcome, ins, classification, note = ANSWERS[key]
        item["answer"] = {
            "outcome": outcome,
            "canonical_ins": ins,
            "classification": classification,
        }
        item["notes"] = note
        filled += 1

    data["_meta"]["review_status"] = "FILLED -- answers decided by hand, 2026-08-02"
    data["_meta"]["method"] = (
        "Each answer was decided by reading the item name and its source label. "
        "Ordinary foods are 'identified' with a null canonical_ins, since food "
        "ingredients have no INS number. Items where the label genuinely does "
        "not determine which additive is meant are 'correctly_declined'."
    )

    TRUTH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"filled  : {filled}")
    if unmatched:
        print(f"UNMATCHED ({len(unmatched)}) -- fill these by hand:")
        for u in unmatched:
            print(f"   {u}")
    else:
        print("unmatched: 0")


if __name__ == "__main__":
    main()