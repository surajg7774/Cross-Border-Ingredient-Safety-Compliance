"""Seed data/golden/resolution/ from data/outputs/resolution/, without ever
overwriting a file that already exists there. Mirrors build_golden.py's
pattern for the extraction stage.

Run once per new label:   uv run python scripts/build_resolution_golden.py

WHY A SCRIPT AND NOT HAND-COPYING
----------------------------------
Copying is mechanical and auditable this way; a corrections mechanism is
included even though empty today, so a future hand-verified fix is recorded
in code (with a reason) instead of becoming an untracked manual edit -- see
build_golden.py's own CORRECTIONS dict for the precedent.

Existing files under data/golden/resolution/ are NEVER touched by this
script. If a golden file needs a correction after review, edit it by hand
(or add a CORRECTIONS entry and delete the file first) and record why.
"""

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings

OUTPUTS = settings.RESOLUTION_OUTPUT_DIR
GOLDEN = settings.RESOLUTION_GOLDEN_DIR

# Each entry: filename -> function(data) -> data. Every correction must have
# a comment saying what was wrong and why. Empty until a review finds one.
CORRECTIONS: dict = {}


def main() -> None:
    GOLDEN.mkdir(parents=True, exist_ok=True)
    created, skipped, corrected = [], [], []

    for src in sorted(OUTPUTS.glob("*.json")):
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
    print(f"\n{GOLDEN} seeded. Review each file against the label image before trusting scores as validation.")


if __name__ == "__main__":
    main()
