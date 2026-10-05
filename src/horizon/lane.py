# DESIGN RULE: pure decision logic -- data in, data out. No file reads, no
# printing, no network, no settings access. Reference data (the curated
# horizon-signals dataset) is passed in as arguments, already loaded by
# src/horizon/load.py. Imports only src.horizon.schemas -- never
# src.extract/, src.category/, src.resolve/, or any engine/classifier/
# resolver module.
"""Regulatory-horizon lane: advisory signals about additives under active
EFSA review, matched from a hand-curated dataset against the additives
assessed in a compliance verdict. Deterministic -- no LLM, no retrieval, no
network.

THE HARD CONSTRAINT: this lane is ADVISORY ONLY. It can raise visibility and
feed the substitute advisor's negative filter (src/substitutes/advisor.py's
horizon_flagged parameter). It can NEVER change a verdict. An EFSA opinion
is not law. There are three time-states an additive's regulatory status can
be in, and only the third belongs here:

    law today                    -> src/rules/engine.py
    adopted, effective later     -> src/rules/engine.py, a temporal branch
    not yet law                  -> HERE, advisory only

Every HorizonSignal carries severity="advisory" and affects_verdict=False,
unconditionally -- there is no code path that sets either differently.

COVERAGE IS PARTIAL, NOT COMPREHENSIVE: this lane was designed to run on an
automated feed (EFSA OpenFoodTox) but that ingestion was abandoned -- see
docs/findings.md F-12. OpenFoodTox 3.0 turned out to carry no E-number field
anywhere, and no CAS crosswalk exists in this project to bridge to one. The
lane runs on a hand-curated dataset instead (data/reference/
horizon_signals.json), covering only additives known to be under review at
curation time. find_horizon_signals() therefore ALWAYS emits a coverage
warning, unconditionally, regardless of whether any signals were found --
a curated list silently read as comprehensive would be worse than no lane
at all.

RECENCY IS THE WHOLE POINT: settled decades-old ADI establishments are not
horizon signals. Only an entry within the last `recency_years` (default 5)
is surfaced as a signal; anything older is excluded but counted in
`warnings`, so a reader can see the substance WAS reviewed, just not
recently.
"""

import hashlib
import json
from datetime import UTC, date, datetime

from src.horizon.schemas import HorizonResult, HorizonSignal

_MATCHED_BY_NAME_FLAG = "matched_by_name"
_DOI_UNVERIFIED_FLAG = "doi_unverified"

_COVERAGE_WARNING = (
    "Coverage is PARTIAL: this dataset is a hand-curated list of additives known to be under "
    "EFSA review at the time of curation, not an automated feed (see docs/findings.md F-12). "
    "Absence of a signal here does NOT mean an additive is not under review."
)


def _normalise_id(raw: str) -> str:
    """'E 955' -> '955', 'INS 470(i)' -> '470(i)' -- identical to
    src/resolve/resolver.py's _normalise_code, duplicated here because that
    module is off the coupling-rule whitelist for src/horizon/ (only
    src/rules/schemas.py may be imported)."""
    text = raw.lower().replace(" ", "")
    if text.startswith("ins"):
        return text[3:]
    if text.startswith("e"):
        return text[1:]
    return text


def _normalise_text(text: str) -> str:
    return " ".join(text.lower().split())


def _data_version(horizon_signals: list[dict]) -> str:
    """A short hash of the horizon-signal records actually used, computed
    from the in-memory data passed in -- no file read, so this stays pure.
    Same approach as src/rules/engine.py's _data_version."""
    digest = hashlib.sha256(json.dumps(horizon_signals, sort_keys=True, default=str).encode("utf-8"))
    return digest.hexdigest()[:12]


def _index_signals(horizon_signals: list[dict]) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """Normalised E-number -> records, and normalised substance name ->
    records -- the second is only consulted as a fallback, per the Matching
    rule (E-number first, name only when it's absent)."""
    by_eu_id: dict[str, list[dict]] = {}
    by_name: dict[str, list[dict]] = {}
    for record in horizon_signals:
        eu_id = record.get("eu_canonical_id")
        if eu_id:
            by_eu_id.setdefault(_normalise_id(str(eu_id)), []).append(record)
        name = record.get("substance_name")
        if name:
            by_name.setdefault(_normalise_text(str(name)), []).append(record)
    return by_eu_id, by_name


def _evaluate_record(
    additive_id: str, record: dict, recency_years: int, today: date, matched_by_name: bool
) -> tuple[HorizonSignal | None, str | None]:
    """One (additive, curated entry) match -> a signal if it is within the
    recency window, or a warning explaining why it was excluded if not.
    Never both, never neither."""
    substance_name = record.get("substance_name") or additive_id
    title = record.get("title") or "(untitled EFSA output)"
    year = record.get("year")
    doi = record.get("doi")
    doi_verified = bool(record.get("doi_verified", False))
    note = record.get("note")
    stage = record.get("stage") or "unknown"
    name_note = " (matched by substance name, not E-number)" if matched_by_name else ""

    if year is None:
        return None, (
            f"{substance_name} ({additive_id}): curated entry {title!r} has no usable year -- "
            f"cannot confirm it is within the {recency_years}-year recency window, so it is not "
            f"shown as a signal{name_note}."
        )

    years_old = today.year - year
    if years_old > recency_years:
        return None, (
            f"{substance_name} ({additive_id}): curated entry {title!r} ({year}) is {years_old} "
            f"years old -- outside the {recency_years}-year recency window, not shown as a "
            f"signal{name_note}."
        )

    flags = []
    if matched_by_name:
        flags.append(_MATCHED_BY_NAME_FLAG)
    if not doi_verified:
        flags.append(_DOI_UNVERIFIED_FLAG)

    signal = HorizonSignal(
        eu_canonical_id=additive_id,
        substance_name=substance_name,
        stage=stage,
        title=title,
        publication_date=str(year),
        doi=doi,
        doi_verified=doi_verified,
        source_url=f"https://doi.org/{doi}" if doi else None,
        years_old=years_old,
        severity="advisory",
        affects_verdict=False,
        note=note,
        flags=flags,
    )
    return signal, None


def find_horizon_signals(
    additive_ids: list[str],
    horizon_signals: list[dict],
    recency_years: int = 5,
    additive_names: dict[str, str] | None = None,
    data_retrieved: str | None = None,
) -> HorizonResult:
    """Advisory horizon signals for `additive_ids` (eu_canonical_id values
    from a compliance verdict, e.g. every item.eu_canonical_id the rule
    engine assessed as an additive).

    `additive_names` is additive_id -> its name (e.g. from
    ItemVerdict.additive_name), used ONLY as the name-match fallback when an
    additive has no E-number match in `horizon_signals` -- callers that
    don't need the fallback can omit it. `data_retrieved` is an opaque
    string the caller supplies (e.g. the dataset's `_meta.retrieved`) and
    this function only echoes into the result, since reading file metadata
    is I/O this module must not do.
    """
    additive_names = additive_names or {}
    checked_ids = sorted(set(additive_ids))
    by_eu_id, by_name = _index_signals(horizon_signals)
    today = datetime.now(UTC).date()

    signals = []
    warnings = [_COVERAGE_WARNING]
    for additive_id in checked_ids:
        matches = by_eu_id.get(_normalise_id(additive_id))
        matched_by_name = False
        if not matches:
            candidate_name = additive_names.get(additive_id)
            if candidate_name:
                matches = by_name.get(_normalise_text(candidate_name))
                matched_by_name = bool(matches)
        if not matches:
            continue

        for record in matches:
            signal, warning = _evaluate_record(additive_id, record, recency_years, today, matched_by_name)
            if signal is not None:
                signals.append(signal)
            if warning is not None:
                warnings.append(warning)

    return HorizonResult(
        signals=signals,
        checked_ids=checked_ids,
        warnings=warnings,
        data_version=_data_version(horizon_signals),
        data_retrieved=data_retrieved,
    )
