# DESIGN RULE: this is the FILE I/O boundary in src/horizon/ -- it opens and
# reads a real file, the same role src/category/embedder.py plays for its
# stage. src/horizon/lane.py, the decision logic, stays pure (data in, data
# out, no printing); this module exists precisely so lane.py never has to
# know how to read a file. src/horizon/search.py is a SEPARATE, network I/O
# boundary for the (not-yet-wired) news-retrieval lane -- see that module's
# docstring; this one is no longer the only I/O in the package, just the
# only FILE I/O.
"""Loader for the curated regulatory-horizon signal dataset at
data/reference/horizon_signals.json.

Hand-curated from EFSA published opinions and European Commission calls for
data -- NOT an automated ingestion of EFSA OpenFoodTox. See docs/findings.md
F-12 for why: EFSA OpenFoodTox 3.0 (Zenodo record 19388272) turned out to be
an 18-sheet IUCLID relational export joined by Document UUID / Parent UUID.
Substance identity lives in REF_SUB, keyed on CAS number and EC inventory
entry; DossierSubject.Name in DOSSIER is a UUID pair, not a substance name.
There is no E-number field anywhere in the export, and neither eu_fip.json
nor codex_ins.json carries CAS, so no crosswalk exists to bridge the two --
a "sucralose"/"955" search against the real export returned 82 rows, all
CAS-substring collisions, not real matches. Automated OpenFoodTox ingestion
is documented as future work requiring an E-number-to-CAS crosswalk.

The dataset this module reads is therefore a hand-maintained JSON file, not
a downloaded workbook. Its `_meta` block records provenance, review status
and coverage; every entry's `doi_verified` starts false until checked by
hand against efsa.europa.eu (see scripts/verify_horizon_dois.py).
"""

import json


def load_horizon_signals(path) -> list[dict]:
    """Read the curated dataset at `path` and return its "signals" array --
    one raw dict per entry: {"eu_canonical_id", "substance_name", "stage",
    "year", "title", "doi", "doi_verified", "note"}. Matching to specific
    additives, recency filtering, and every other DECISION (as opposed to a
    plain read) happens in src/horizon/lane.py, not here.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("signals", [])


def load_horizon_meta(path) -> dict:
    """The dataset's `_meta` block (source, review_status, retrieved,
    coverage) -- informational only, for a CLI to print. Never consulted by
    src/horizon/lane.py's pure matching logic."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("_meta", {})
