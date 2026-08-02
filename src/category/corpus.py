# DESIGN RULE: pure parsing and text assembly only -- no file reads, no
# embedding calls, no printing. Reference data (the parsed contents of
# food_categories.json, and -- for corpus_mode="enriched" only -- eu_fip and
# codex_ins) is passed in as an argument, never loaded here. The
# eu_fip/codex_ins join helpers below duplicate a handful of small pure
# functions from src/substitutes/advisor.py (parent-code widening, the
# codex_ins parent-row-with-empty-classes fallback) rather than importing
# that module, matching this project's established pattern of independent
# pure modules over cross-domain imports (see advisor.py's own DESIGN RULE
# comment for the same reasoning in the other direction).
"""Build the searchable document corpus from the 155 EU food categories."""

import re
from collections import Counter
from dataclasses import dataclass

from src.category.experiment import CONFIGS, ExperimentConfig

_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_PAREN_CODE_RE = re.compile(r"\(\s*(\d+(?:\.\d+)*)\s*\)")

# Sentence boundary: a period/!/? followed by whitespace and an uppercase
# letter or "(" -- deliberately does NOT split on "e.g. cow" (lowercase
# after the space), so an abbreviation mid-example does not fragment the
# example clause it is part of.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")
_EXAMPLE_MARKER_RE = re.compile(r"e\.g\.|for example|examples include|such as", re.IGNORECASE)
# Sentence-INITIAL only (the caller applies this per split sentence, never
# mid-string) -- "Includes chocolate-coated wafers..." must NOT match this;
# only the exact stock-filler phrases do.
_FRAMING_RE = re.compile(r"^(this category (?:covers|includes|comprises)|includes all other)\s*", re.IGNORECASE)

_PAREN_SUFFIX_RE = re.compile(r"\([^)]*\)$")
_LETTER_SUFFIX_RE = re.compile(r"[a-z]$", re.IGNORECASE)
ENRICHED_CLASS_CAP = 10


@dataclass
class FoodCategory:
    """One EU food category: its own text, and where it sits in the tree."""

    code: str
    name: str
    description: str | None  # None if absent in the source, or excluded by validate_corpus
    parent_code: str | None


@dataclass
class ValidationFlag:
    """A category whose description references codes from a different top-level tree."""

    code: str
    referenced_codes: list[str]


def _field(record: dict, identifier: str) -> str | None:
    for child in record["childrenValues"]:
        if child["valueIdentifier"] == identifier:
            return child["value"]
    return None


def _parent_code(code: str) -> str | None:
    if "." not in code:
        return None
    return code.rsplit(".", 1)[0]


def _clean_html(text: str) -> str:
    """Strip <br /> and other tags, and \\r -- descriptions are lightly HTML-formatted."""
    text = _BR_RE.sub(" ", text)
    text = _TAG_RE.sub("", text)
    text = text.replace("\r", " ")
    return " ".join(text.split())


def parse_food_categories(raw: list[dict]) -> list[FoodCategory]:
    """Parse food_categories.json's childrenValues records into FoodCategory rows."""
    categories = []
    for record in raw:
        code = _field(record, "refDataFoodCategoryLevel")
        # Measured gap: category 13.1.4 has a code and a description but no
        # printed name at all (refDataFoodCategoryEN is null) -- fall back
        # to the code rather than embedding a None into the document text.
        name = _field(record, "refDataFoodCategoryEN") or code
        desc = _field(record, "refDataFoodCategoryDesc")
        categories.append(
            FoodCategory(
                code=code,
                name=name,
                description=_clean_html(desc) if desc else None,
                parent_code=_parent_code(code),
            )
        )
    return categories


def validate_corpus(categories: list[FoodCategory]) -> list[ValidationFlag]:
    """Find descriptions that describe an entirely different category.

    Extracts dotted codes written in parentheses, e.g. "(14.1.1)", but keeps
    only matches that are themselves real category codes in this corpus -- a
    bare number in parentheses is just as likely a footnote marker ("(77)")
    or an Article reference ("Article 18(1)") as a category cross-reference,
    and restricting to known codes rules those out without guessing at a
    denylist. A category is flagged only when EVERY remaining match belongs
    to a top-level number different from the category's own -- e.g. category
    15's description is entirely about category 14 (waters, juices,
    beverages), a genuine data error in the source: embedded as-is it would
    make category 15 (savoury snacks) retrieve for beverage queries.
    """
    valid_codes = {c.code for c in categories}
    flags = []
    for category in categories:
        if not category.description:
            continue
        matches = [m for m in _PAREN_CODE_RE.findall(category.description) if m in valid_codes]
        if not matches:
            continue
        own_top = category.code.split(".")[0]
        referenced_tops = {m.split(".")[0] for m in matches}
        if own_top not in referenced_tops:
            flags.append(ValidationFlag(category.code, sorted(set(matches))))
    return flags


def _ancestors(category: FoodCategory, by_code: dict[str, FoodCategory]) -> list[FoodCategory]:
    """Walk up the dotted-code parent chain, nearest ancestor first."""
    chain = []
    current = category
    while current.parent_code:
        parent = by_code[current.parent_code]
        chain.append(parent)
        current = parent
    return chain


def _example_clause(description: str) -> str | None:
    """The first sentence carrying an example marker ("e.g.", "for
    example", "Examples include", "such as"), trimmed -- a self-contained,
    grammatical clause rather than a truncated fragment. None if the
    description has no such marker. MEASURED against the real corpus: 49 of
    132 descriptions contain one of these markers somewhere (union across
    all four); this returns the sentence for each of those, name-only text
    for the rest.
    """
    for sentence in _SENTENCE_SPLIT_RE.split(description):
        if _EXAMPLE_MARKER_RE.search(sentence):
            return sentence.strip()
    return None


def _strip_framing(text: str) -> str:
    """`text` with each sentence's LEADING stock-filler phrase removed --
    "This category covers/includes/comprises", "Includes all other" --
    applied per sentence (never mid-sentence), so "Includes chocolate-
    coated wafers..." at a sentence start is left untouched: it introduces
    real content, it just happens to start with the same word as the filler
    phrase "Includes all other [products not covered above]"."""
    sentences = _SENTENCE_SPLIT_RE.split(text)
    stripped = [_FRAMING_RE.sub("", sentence, count=1).strip() for sentence in sentences]
    return " ".join(s for s in stripped if s)


def _parent_codes(code: str) -> list[str]:
    """Successive parent widenings of an INS/EU code, nearest first --
    identical in behaviour to src/substitutes/advisor.py's _parent_codes
    ("160b(i)" -> ["160b", "160"]), duplicated here for the same reason
    that module duplicates it from src/rules/engine.py: this module stays
    independent, importing no other domain module."""
    parents = []
    current = code
    paren_match = _PAREN_SUFFIX_RE.search(current)
    if paren_match:
        current = current[: paren_match.start()].strip()
        parents.append(current)
    letter_match = _LETTER_SUFFIX_RE.search(current)
    if letter_match:
        current = current[: letter_match.start()]
        parents.append(current)
    return parents


def _food_category_code(food_category_raw: str | None) -> str:
    """"12.2.2 Seasonings and condiments" -> "12.2.2" -- identical to
    src/category/filter.py's _category_code, duplicated for the same
    independent-pure-module reason as _parent_codes above."""
    if not food_category_raw:
        return ""
    return food_category_raw.split(" ", 1)[0].rstrip(".")


def _codex_children_by_parent(codex_ins: list[dict]) -> dict[str, list[dict]]:
    children: dict[str, list[dict]] = {}
    for row in codex_ins:
        parent = row.get("parent_ins")
        if parent:
            children.setdefault(parent, []).append(row)
    return children


def _codex_functional_classes(
    canonical_id: str, codex_by_ins: dict[str, dict], codex_children_by_parent: dict[str, list[dict]]
) -> list[str] | None:
    """Same lookup, and the same CXG 36 "empty parent row, classes live on
    the sub-types" fallback, as src/substitutes/advisor.py's
    _codex_functional_classes -- duplicated here (without the
    inherited-from-subtypes flag, unneeded for corpus text) rather than
    imported, for the same independent-pure-module reason as _parent_codes.
    """
    for candidate_id in (canonical_id, *_parent_codes(canonical_id)):
        row = codex_by_ins.get(candidate_id)
        if row is None:
            continue
        classes = row.get("functional_classes") or []
        if classes:
            return classes
        return sorted(
            {
                cls
                for child in codex_children_by_parent.get(candidate_id, [])
                for cls in child.get("functional_classes") or []
            }
        )
    return None


def _functional_classes_by_id(eu_fip: list[dict], codex_ins: list[dict]) -> dict[str, list[str]]:
    """canonical_id -> distinct Codex functional classes, for every id that
    appears anywhere in eu_fip. eu_fip's own functional_classes field wins
    when populated; falls back to codex_ins (INS-matched, parent-widened,
    union-of-subtypes) for ids whose eu_fip rows carry none at all --
    MEASURED: 82 canonical ids are empty in EVERY eu_fip row (e.g. "170
    Calcium carbonates" -- CXG 36 records its classes on sub-types 170(i)/
    170(ii), not the parent row), the same gap
    src.substitutes.advisor.find_substitutes already works around.
    """
    classes_by_id: dict[str, list[str]] = {}
    for row in eu_fip:
        cid = row["canonical_id"]
        if cid not in classes_by_id and row.get("functional_classes"):
            classes_by_id[cid] = row["functional_classes"]

    codex_by_ins = {row["ins"]: row for row in codex_ins}
    codex_children = _codex_children_by_parent(codex_ins)
    for cid in {row["canonical_id"] for row in eu_fip} - set(classes_by_id):
        classes = _codex_functional_classes(cid, codex_by_ins, codex_children)
        if classes:
            classes_by_id[cid] = classes
    return classes_by_id


def _category_enriched_clause(code: str, eu_fip: list[dict], classes_by_id: dict[str, list[str]]) -> str | None:
    """"Typically permits: <classes>." -- the distinct functional classes of
    every additive PERMITTED in this category, ranked by how many of those
    additives share each class (most-shared first, alphabetical tie-break
    for determinism), capped at ENRICHED_CLASS_CAP. None when nothing is
    permitted in this category, or none of what's permitted resolves to any
    functional class at all.
    """
    permitted_ids = {
        row["canonical_id"]
        for row in eu_fip
        if row.get("status") == "permitted" and _food_category_code(row.get("food_category_raw")) == code
    }
    counts: Counter[str] = Counter()
    for additive_id in permitted_ids:
        counts.update(classes_by_id.get(additive_id, []))
    if not counts:
        return None
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    top = [cls.lower() for cls, _ in ranked[:ENRICHED_CLASS_CAP]]
    return "Typically permits: " + ", ".join(top) + "."


def build_documents(
    categories: list[FoodCategory],
    config: ExperimentConfig = CONFIGS["baseline"],
    eu_fip: list[dict] | None = None,
    codex_ins: list[dict] | None = None,
) -> dict[str, str]:
    """One embeddable text per category, shaped by config.corpus_mode (see
    ExperimentConfig's docstring for the full description of each mode).
    "full" (the default, unchanged) is: own code + name + description, plus
    (when config.parent_inheritance) the name and description of every
    ancestor. 23 categories have no description of their own -- e.g. "7.2
    Fine bakery wares" is three words, too thin to match an ingredient list
    against -- and need the parent chain's text to be matchable at all;
    config.parent_inheritance=False is the ablation that measures that.

    eu_fip/codex_ins are only read for corpus_mode="enriched" -- every other
    mode ignores them (and both default to None, since only that one mode
    needs reference data beyond food_categories.json).
    """
    if config.corpus_mode == "name-only":
        return {c.code: f"{c.code} -- {c.name}" for c in categories}

    if config.corpus_mode == "name-plus-examples":
        documents = {}
        for c in categories:
            parts = [c.code, c.name]
            clause = _example_clause(c.description) if c.description else None
            if clause:
                parts.append(clause)
            documents[c.code] = " -- ".join(parts)
        return documents

    if config.corpus_mode == "enriched":
        if eu_fip is None or codex_ins is None:
            raise ValueError("corpus_mode='enriched' requires eu_fip and codex_ins to be passed in")
        classes_by_id = _functional_classes_by_id(eu_fip, codex_ins)
        documents = {}
        for c in categories:
            parts = [c.code, c.name]
            clause = _category_enriched_clause(c.code, eu_fip, classes_by_id)
            if clause:
                parts.append(clause)
            documents[c.code] = " -- ".join(parts)
        return documents

    # "full" and "strip-framing" -- identical shape (own text, plus the
    # parent chain when config.parent_inheritance), differing only in
    # whether each description is stripped of leading framing phrases
    # before being appended.
    strip = config.corpus_mode == "strip-framing"
    by_code = {c.code: c for c in categories}
    documents = {}
    for category in categories:
        parts = [category.code, category.name]
        if category.description:
            parts.append(_strip_framing(category.description) if strip else category.description)
        if config.parent_inheritance:
            for ancestor in _ancestors(category, by_code):
                parts.append(ancestor.name)
                if ancestor.description:
                    parts.append(_strip_framing(ancestor.description) if strip else ancestor.description)
        documents[category.code] = " -- ".join(parts)
    return documents


def build_corpus(
    raw: list[dict],
    config: ExperimentConfig = CONFIGS["baseline"],
    eu_fip: list[dict] | None = None,
    codex_ins: list[dict] | None = None,
) -> tuple[dict[str, FoodCategory], dict[str, str], list[ValidationFlag]]:
    """Parse, validate, and assemble the embeddable documents in one pass.

    Returns (categories by code -- for display/lookup, documents by code --
    for embedding, validation flags -- for the caller to report). A flagged
    category's description is excluded from both the returned FoodCategory
    and the embedded document text; its name and parent chain are kept.

    eu_fip/codex_ins are passed straight through to build_documents -- only
    needed, and only read, when config.corpus_mode == "enriched".
    """
    categories = parse_food_categories(raw)
    flags = validate_corpus(categories)
    flagged_codes = {f.code for f in flags}
    cleaned = [
        FoodCategory(
            code=c.code,
            name=c.name,
            description=None if c.code in flagged_codes else c.description,
            parent_code=c.parent_code,
        )
        for c in categories
    ]
    by_code = {c.code: c for c in cleaned}
    documents = build_documents(cleaned, config, eu_fip, codex_ins)
    return by_code, documents, flags
