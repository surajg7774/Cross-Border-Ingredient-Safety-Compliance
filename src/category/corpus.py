# DESIGN RULE: pure parsing and text assembly only -- no file reads, no
# embedding calls, no printing. Reference data (the parsed contents of
# food_categories.json) is passed in as an argument, never loaded here.
"""Build the searchable document corpus from the 155 EU food categories."""

import re
from dataclasses import dataclass

from src.category.experiment import CONFIGS, ExperimentConfig

_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_PAREN_CODE_RE = re.compile(r"\(\s*(\d+(?:\.\d+)*)\s*\)")


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


def build_documents(categories: list[FoodCategory], config: ExperimentConfig = CONFIGS["baseline"]) -> dict[str, str]:
    """One embeddable text per category: own code + name + description, plus
    (when config.parent_inheritance) the name and description of every
    ancestor. 23 categories have no description of their own -- e.g. "7.2
    Fine bakery wares" is three words, too thin to match an ingredient list
    against -- and need the parent chain's text to be matchable at all;
    config.parent_inheritance=False is the ablation that measures that.
    """
    by_code = {c.code: c for c in categories}
    documents = {}
    for category in categories:
        parts = [category.code, category.name]
        if category.description:
            parts.append(category.description)
        if config.parent_inheritance:
            for ancestor in _ancestors(category, by_code):
                parts.append(ancestor.name)
                if ancestor.description:
                    parts.append(ancestor.description)
        documents[category.code] = " -- ".join(parts)
    return documents


def build_corpus(
    raw: list[dict], config: ExperimentConfig = CONFIGS["baseline"]
) -> tuple[dict[str, FoodCategory], dict[str, str], list[ValidationFlag]]:
    """Parse, validate, and assemble the embeddable documents in one pass.

    Returns (categories by code -- for display/lookup, documents by code --
    for embedding, validation flags -- for the caller to report). A flagged
    category's description is excluded from both the returned FoodCategory
    and the embedded document text; its name and parent chain are kept.
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
    documents = build_documents(cleaned, config)
    return by_code, documents, flags
