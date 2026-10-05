"""The resolution cascade: turns one extraction item into a canonical identity.

Pure and deterministic -- no file reads, no printing, no LLM, no network.
Every reference dataset the cascade needs is passed in via `References`;
callers (scripts/resolve.py) own all I/O.
"""

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from src.resolve.roles import normalise_role
from src.resolve.schemas import ResolutionResult, ResolvedItem

FUZZY_THRESHOLD = 90

# C. Family guard -- these families differ by one suffix character and are
# separate legal entries. A fuzzy or name-based match landing on one of them
# is not evidence of which one; it is noise that happens to score well.
# Hard-coded, per the resolver spec.
_GUARDED_SUFFIX_FAMILIES = {
    "472": "abcdef",
    "160": "abcdef",
    "150": "abcd",
}
_GUARDED_PAREN_FAMILIES = {
    "341": ("(i)", "(ii)", "(iii)"),
    "339": ("(i)", "(ii)", "(iii)"),
}
_GUARDED_BARE_RANGES = {
    "306", "307", "308", "309",
    "620", "621", "622", "623", "624", "625",
}


def _guarded_family_members() -> set[str]:
    members = set(_GUARDED_BARE_RANGES)
    for base, letters in _GUARDED_SUFFIX_FAMILIES.items():
        members.update(base + letter for letter in letters)
    for base, suffixes in _GUARDED_PAREN_FAMILIES.items():
        members.update(base + suffix for suffix in suffixes)
    return members


_GUARDED_FAMILY_MEMBERS = _guarded_family_members()

_ENZYME_INS_PREFIX = "1101("


@dataclass
class References:
    """The reference datasets the resolver looks things up against.

    Loaded and owned by the caller -- src/resolve/ never reads a file itself.
    eu_fip defaults to empty: data/reference/eu_fip.json does not exist in
    this project yet, so the EU crosswalk (Section E) is a stub until it does.
    """

    codex_ins: list[dict]
    functional_classes: list[dict]
    label_aliases: dict
    eu_fip: list[dict] = field(default_factory=list)
    index_version: str = "unversioned"


@dataclass
class _Outcome:
    """Intermediate result of the code or name resolution path."""

    canonical_ins: str | None = None
    method: str = "unresolved"
    confidence: float = 0.0
    flags: list[str] = field(default_factory=list)
    matched_on: str | None = None
    classification: str | None = None  # set only by an early exit (food lexicon / ambiguous)
    candidates: list[str] = field(default_factory=list)  # ambiguous[] candidate INS numbers


# =========================================================================== #
# normalisation helpers
# =========================================================================== #
def _normalise_code(raw_code: str) -> str:
    """'INS 470(i)' -> '470(i)', '503 ( ii )' -> '503(ii)', 'E471' -> '471'."""
    text = raw_code.lower().replace(" ", "")
    if text.startswith("ins"):
        return text[3:]
    if text.startswith("e"):
        return text[1:]
    return text


def _normalise_text(text: str) -> str:
    return " ".join(text.lower().split())


def _parent_ins(ins: str) -> str | None:
    """One level up: '451(i)' -> '451', '160a' -> '160'. None for a bare number.

    Re-implemented locally rather than imported from src/codex/ -- only
    reference DATA crosses a stage boundary here, never code.
    """
    m = re.match(r"^(.+)\(([ivxlc]+)\)$", ins, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.match(r"^(\d+)([a-z])$", ins, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


# =========================================================================== #
# reference indices
# =========================================================================== #
def _build_indices(refs: References) -> dict:
    by_ins = {r["ins"]: r for r in refs.codex_ins}

    by_name = {_normalise_text(r["name"]): r["ins"] for r in refs.codex_ins}

    by_synonym = {}
    for r in refs.codex_ins:
        for synonym in r["synonyms"]:
            by_synonym.setdefault(_normalise_text(synonym), r["ins"])

    by_alias = {_normalise_text(a["alias"]): a for a in refs.label_aliases.get("aliases", [])}
    by_ambiguous = {
        _normalise_text(a["alias"]): a for a in refs.label_aliases.get("ambiguous", [])
    }
    never_additives = {
        _normalise_text(term)
        for term in refs.label_aliases.get("never_additives", {}).get("terms", [])
    }

    # eu_fip.json has one row per (additive x food category), so many rows
    # share a canonical_id -- keep just one representative name per id.
    eu_by_id: dict[str, str] = {}
    for row in refs.eu_fip:
        eu_by_id.setdefault(row["canonical_id"], row["additive_name"])

    return {
        "by_ins": by_ins,
        "by_name": by_name,
        "by_synonym": by_synonym,
        "by_alias": by_alias,
        "by_ambiguous": by_ambiguous,
        "never_additives": never_additives,
        "eu_by_id": eu_by_id,
    }


# =========================================================================== #
# A. code path
# =========================================================================== #
def _resolve_by_code(declared_code: str, indices: dict) -> _Outcome:
    normalised = _normalise_code(declared_code)
    if normalised in indices["by_ins"]:
        return _Outcome(normalised, "code_exact", 0.98, [], normalised)

    parent = _parent_ins(normalised)
    if parent and parent in indices["by_ins"]:
        return _Outcome(parent, "code_parent", 0.90, ["widened_to_parent"], parent)

    return _Outcome()


# =========================================================================== #
# B. name path -- strict order
# =========================================================================== #
def _fuzzy_match(name: str, by_ins: dict) -> tuple[str, int] | None:
    best_ins, best_score = None, 0
    for ins, record in by_ins.items():
        score = fuzz.token_set_ratio(name, record["name"])
        if score > best_score:
            best_score, best_ins = score, ins
    if best_score >= FUZZY_THRESHOLD:
        return best_ins, best_score
    return None


def _resolve_by_name(name: str, indices: dict) -> _Outcome:
    key = _normalise_text(name)

    if key in indices["by_name"]:
        return _Outcome(indices["by_name"][key], "codex_name", 0.98, [], name)

    if key in indices["by_synonym"]:
        return _Outcome(indices["by_synonym"][key], "codex_name", 0.95, [], name)

    if key in indices["by_alias"]:
        entry = indices["by_alias"][key]
        return _Outcome(entry["ins"], "alias", 0.92, [], entry["alias"])

    # The food lexicon (never_additives) MUST run before fuzzy matching below.
    # If it ran after, "turmeric powder" would fuzzy-match some additive name
    # before this exact-term check ever got the chance to catch it first.
    # Confidence 0.95: a food-lexicon hit is a CERTAIN classification, not an
    # uncertain one -- the system knows "sugar" is not an additive.
    if key in indices["never_additives"]:
        return _Outcome(
            method="food_lexicon", confidence=0.95, matched_on=name, classification="food_ingredient"
        )

    if key in indices["by_ambiguous"]:
        entry = indices["by_ambiguous"][key]
        return _Outcome(
            method="ambiguous",
            flags=["ambiguous_candidates"],
            matched_on=name,
            classification="ambiguous",
            candidates=list(entry["candidates"]),
        )

    fuzzy = _fuzzy_match(name, indices["by_ins"])
    if fuzzy:
        ins, score = fuzzy
        return _Outcome(ins, "fuzzy", 0.80, ["fuzzy_match"], f"{name} (score {score})")

    return _Outcome()


# =========================================================================== #
# C. family guard
# =========================================================================== #
def _needs_family_guard(canonical_ins: str, method: str) -> bool:
    """True if this resolution must be forced to ambiguous.

    An exact code match (the label literally printed "472c") is trustworthy
    by definition -- the guard exists for resolutions that INFERRED which
    family member was meant (name, alias, fuzzy, or a widened parent code).
    """
    return canonical_ins in _GUARDED_FAMILY_MEMBERS and method != "code_exact"


# =========================================================================== #
# D. classification
# =========================================================================== #
def _is_enzyme(canonical_ins: str | None, codex_functional_classes: list[str]) -> bool:
    if canonical_ins and canonical_ins.startswith(_ENZYME_INS_PREFIX):
        return True
    return any("enzyme" in c.lower() for c in codex_functional_classes)


# "Flavour Enhancers" (plural) is a real Codex functional class -- MSG,
# disodium 5'-guanylate (627), disodium 5'-inosinate (631) are all declared
# under it. The first version of this check only excluded the singular
# "Flavour enhancer", so the plural slipped through as a Reg 1334 flavouring
# and skipped Annex II compliance checking entirely for two real additives.
_FLAVOUR_ENHANCER_TERMS = {
    "flavour enhancer",
    "flavour enhancers",
    "flavor enhancer",
    "flavor enhancers",
}


def _is_flavouring(declared_role: str | None, item_name: str) -> bool:
    role = (declared_role or "").strip().lower()
    if role in _FLAVOUR_ENHANCER_TERMS:
        return False  # a genuine Codex additive class, not a Reg 1334 flavouring
    text = f"{role} {item_name}".lower()
    return "flavour" in text or "flavoring" in text


def _has_role_class_mismatch(
    normalised_roles: list[str], codex_functional_classes: list[str]
) -> bool:
    """True when the label's declared role and Codex's own functional class for
    the resolved substance disagree -- e.g. label says "Flour treatment agent"
    but Codex lists 1101(ii) papain as a "Flavour enhancer". This does not
    change the classification or confidence; it is a signal for the report,
    surfacing the possibility that the label names the wrong sub-type.
    """
    if not normalised_roles or not codex_functional_classes:
        return False
    return not any(role in codex_functional_classes for role in normalised_roles)


# =========================================================================== #
# E. EU crosswalk
# =========================================================================== #
_LEADING_DIGITS_RE = re.compile(r"^\d+")


def _eu_family_pattern(ins: str) -> re.Pattern:
    """Match eu_fip canonical_ids that are sub-variants of the same base number as
    `ins` -- "470" matches "470a", "470(i)", or bare "470", never "4700"."""
    m = _LEADING_DIGITS_RE.match(ins)
    prefix = re.escape(m.group(0) if m else ins)
    return re.compile(rf"^{prefix}(\(|[a-z]|$)")


def _crosswalk_to_eu(
    canonical_ins: str | None, codex_name: str | None, eu_by_id: dict[str, str], classification: str
) -> tuple[str | None, list[str], float | None]:
    """Returns (eu_canonical_id, flags, confidence_cap). confidence_cap is None
    unless this crosswalk itself introduces uncertainty beyond the codex
    resolution's own confidence (crosswalk_by_name only).
    """
    if canonical_ins is None:
        return None, [], None

    if not eu_by_id:
        return None, ["eu_fip_unavailable"], None

    if canonical_ins in eu_by_id:
        return canonical_ins, [], None

    parent = _parent_ins(canonical_ins)
    if parent and parent in eu_by_id:
        return parent, ["eu_widened_to_parent"], None

    # Codex and the EU use different sub-numbering schemes for the same
    # families -- Codex roman numerals ("470(i)") vs EU letters ("470a") --
    # so a code-only match never finds these. Compare names within the family
    # instead. Flagged and confidence-capped: this is inference, not a lookup.
    pattern = _eu_family_pattern(canonical_ins)
    candidates = {cid: name for cid, name in eu_by_id.items() if pattern.match(cid)}
    if candidates and codex_name:
        best_id, best_score = None, 0
        for cid, name in candidates.items():
            score = fuzz.token_set_ratio(codex_name, name)
            if score > best_score:
                best_score, best_id = score, cid
        if best_id and best_score > 0:
            return best_id, ["crosswalk_by_name"], 0.75

    if classification in ("enzyme", "flavouring"):
        return None, [], None  # absence is expected under a different regulation, not a gap
    return None, ["not_in_eu_list"], None


# =========================================================================== #
# per-item resolution
# =========================================================================== #
def _resolve_item(item: dict, refs: References, indices: dict, parent_ids: set[int]) -> ResolvedItem:
    declared_code = item.get("declared_code")
    name = item.get("name_as_declared") or item.get("verbatim") or ""

    outcome = _resolve_by_code(declared_code, indices) if declared_code else _resolve_by_name(name, indices)
    flags = list(outcome.flags)
    confidence = outcome.confidence

    if outcome.canonical_ins and _needs_family_guard(outcome.canonical_ins, outcome.method):
        outcome = _Outcome(
            outcome.canonical_ins,
            outcome.method,
            0.5,
            [*flags, "family_guard"],
            outcome.matched_on,
            classification="ambiguous",
        )
        flags = outcome.flags
        confidence = outcome.confidence

    codex_record = indices["by_ins"].get(outcome.canonical_ins) if outcome.canonical_ins else None
    codex_functional_classes = codex_record["functional_classes"] if codex_record else []

    # A resolved Codex identity ALWAYS wins over the label's own wording:
    # Codex only lists additives and explicitly excludes flavourings, so if
    # the item resolved in Codex at all, it is not a Reg 1334 flavouring no
    # matter what the declared_role text says (INS 627/631 are declared
    # under "Flavour Enhancers" -- a real Codex class -- and are additives).
    # The role-string heuristic below is a fallback ONLY for items that did
    # NOT resolve in Codex: that is the genuine flavouring case, since a
    # true flavouring has no INS number to resolve in the first place.
    if outcome.classification == "ambiguous":
        classification = "ambiguous"
    elif outcome.classification == "food_ingredient":
        if item.get("declared_role"):
            classification = "ambiguous"
            confidence = 0.0  # uncertain again once a role is present -- not the certain 0.95 case
            flags.append("colouring_foodstuff_candidate")
        else:
            classification = "food_ingredient"
    elif outcome.canonical_ins is not None:
        classification = "enzyme" if _is_enzyme(outcome.canonical_ins, codex_functional_classes) else "additive"
    elif _is_flavouring(item.get("declared_role"), name):
        # Certain that it IS a flavouring, just not which one -- and
        # flavourings fall under Reg 1334/2008, not Annex II, so there is no
        # Annex II verdict to reach. Routing this to manual review would ask
        # a human to confirm something already out of scope.
        classification = "flavouring"
        confidence = 0.95
        flags.append("out_of_annex_ii_scope")
    else:
        classification = "unknown"

    normalised_roles = normalise_role(item.get("declared_role"), refs.functional_classes)
    normalised_role = ", ".join(normalised_roles) if normalised_roles else None

    if _has_role_class_mismatch(normalised_roles, codex_functional_classes):
        flags.append("role_class_mismatch")

    codex_name = codex_record["name"] if codex_record else None
    eu_canonical_id, eu_flags, eu_confidence_cap = _crosswalk_to_eu(
        outcome.canonical_ins, codex_name, indices["eu_by_id"], classification
    )
    flags.extend(eu_flags)
    if eu_confidence_cap is not None:
        confidence = min(confidence, eu_confidence_cap)

    resolution_method = outcome.method

    # Hard invariant, not another string heuristic: eu_fip membership is
    # authoritative for scope. A heuristic on the label's wording must never
    # override the regulation's own list -- if the item is in eu_fip, it is
    # regulated under Annex II by definition and cannot be "flavouring" or
    # "enzyme", no matter what upstream heuristic said otherwise.
    if eu_canonical_id is not None and classification in ("flavouring", "enzyme"):
        classification = "additive"
        if "out_of_annex_ii_scope" in flags:
            flags.remove("out_of_annex_ii_scope")
        flags.append("flavouring_check_overruled")

    # A compound is a container ("SEASONING (spices, salt, ...)"), not an
    # unknown substance -- sending it to manual review wastes reviewer
    # attention on something the label already explained. Detected purely
    # structurally (some other item's parent_item_id points at this one), so
    # this runs after -- and can override -- every lookup-based outcome
    # above, EXCEPT one that already resolved as a genuine additive.
    if item["item_id"] in parent_ids and classification != "additive":
        classification = "compound"
        resolution_method = "structural"
        confidence = 0.95

    return ResolvedItem(
        item_id=item["item_id"],
        canonical_ins=outcome.canonical_ins,
        eu_canonical_id=eu_canonical_id,
        classification=classification,
        normalised_role=normalised_role,
        codex_functional_classes=codex_functional_classes,
        resolution_method=resolution_method,
        resolution_confidence=confidence,
        flags=flags,
        candidates=outcome.candidates,
        matched_on=outcome.matched_on,
    )


# =========================================================================== #
# F. confidence bands
# =========================================================================== #
def routing_band(confidence: float) -> str:
    """Which confidence band a resolution falls into, and how it should be routed."""
    if confidence >= 0.95:
        return "straight_through"
    if confidence >= 0.80:
        return "review_flag"
    if confidence > 0.0:
        return "never_to_rule_engine"
    return "manual_review"


# =========================================================================== #
# entry point
# =========================================================================== #
def resolve_items(items: list[dict], refs: References) -> ResolutionResult:
    """Resolve a list of extraction items into canonical identities.

    Pure: takes data in, returns data out. No file reads, no printing.
    """
    indices = _build_indices(refs)
    parent_ids = {item["parent_item_id"] for item in items if item.get("parent_item_id") is not None}
    resolved = [_resolve_item(item, refs, indices, parent_ids) for item in items]
    return ResolutionResult(items=resolved, warnings=[], index_version=refs.index_version)
