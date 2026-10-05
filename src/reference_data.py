# DESIGN RULE: this module cleans a raw reference DATASET (eu_fip.json),
# not stage logic -- it belongs to no pipeline stage and every stage-owned
# module (src/rules/, src/substitutes/, src/agent/tools.py) may import it
# without crossing a stage boundary. This is NOT src.rules.engine: nothing
# here decides a verdict, and src/substitutes/advisor.py importing this
# module is not the same as advisor.py importing src.rules.engine (which
# its own DESIGN RULE comment forbids).
"""Loads eu_fip.json and cleans known scraping artifacts, once, so every
consumer (src/rules/engine.py's own index, src/substitutes/advisor.py's
independent row selection, src/agent/tools.py's lookup_eu_fip) sees the
same clean rows, rather than each having to know about the artifacts
itself.

MEASURED, against the full 18,987-row file:

- `conditions` == "&nbsp;" (or any value that is BLANK once the literal
  entity "&nbsp;" or a real non-breaking-space character is stripped) on
  8 rows -- a scraped placeholder for an empty conditions cell. Left as a
  bare "&nbsp;" it is truthy in Python, so it survives every `if
  row.get("conditions")` check, and renders, downstream, as an EMPTY
  markdown list item once merged and numbered alongside a real clause.
  src/rules/engine.py's own `_normalize_row` fixes the identical bug
  independently, for its own index built from the SAME raw file -- kept
  as-is, a safety net, not replaced by this module.
- `additive_name` carries a leaked HTML wrapper on 30 rows, all one
  additive (canonical_id "334"): '<p>Tartaric acid –
  tartrates&nbsp;</p>'. additive_name is shown verbatim everywhere a
  verdict or a substitute candidate is displayed (verdict strips,
  PDF/CSV, substitute suggestion names, the narrator's JSON) -- through
  html.escape, which prints the literal tags rather than rendering them.

Every other field was checked for the same "blank once &nbsp;-stripped"
failure mode and does not exhibit it. `conditions`' OWN embedded HTML
elsewhere in the file (<sub>, <em> -- e.g. chemical-formula subscripts)
is legitimate source formatting and is left untouched; only the
wrapper-and-placeholder pattern specific to `additive_name` "334" is
cleaned.
"""

import json
import re
from pathlib import Path

_BLANK_MARKER_RE = re.compile(r"&nbsp;|\xa0", re.IGNORECASE)
# A value entirely wrapped in a single <p>...</p> -- the leaked-markup
# shape MEASURED above, not a general HTML-stripping rule (conditions'
# legitimate <sub>/<em> markup is untouched because it is never fully
# wrapped this way).
_WRAPPING_P_RE = re.compile(r"^\s*<p>(.*)</p>\s*$", re.IGNORECASE | re.DOTALL)


def _has_content(text: str) -> bool:
    return bool(_BLANK_MARKER_RE.sub("", text).strip())


def _clean_conditions(value: str | None) -> str | None:
    if not value:
        return value
    return value if _has_content(value) else None


def _clean_additive_name(value: str | None) -> str | None:
    if not value:
        return value
    match = _WRAPPING_P_RE.match(value)
    if match:
        value = match.group(1)
    value = _BLANK_MARKER_RE.sub(" ", value)
    cleaned = " ".join(value.split())
    return cleaned or None


def clean_eu_fip_rows(rows: list[dict]) -> list[dict]:
    """Pure -- no file I/O -- so it can be applied to an already-loaded
    list (load_eu_fip below) or to a hand-built list in a test."""
    cleaned = []
    for row in rows:
        row = dict(row)
        if "conditions" in row:
            row["conditions"] = _clean_conditions(row["conditions"])
        if "additive_name" in row:
            row["additive_name"] = _clean_additive_name(row["additive_name"])
        cleaned.append(row)
    return cleaned


def load_eu_fip(path: Path) -> list[dict]:
    """eu_fip.json, cleaned of the known scraping artifacts above -- []
    if the file does not exist, the same fallback every caller's own
    ad-hoc `_load_json(path, [])` used before this replaced it."""
    if not path.exists():
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))
    return clean_eu_fip_rows(rows)
