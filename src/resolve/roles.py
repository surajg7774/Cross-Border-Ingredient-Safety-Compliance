"""Normalise a label's declared_role into canonical Codex functional classes.

Measured against 16 real declared_role values pulled from this project's own
label extractions: 11 matched the standard's `purposes` vocabulary by plain
exact lookup. The rules below exist to close the other 5 gaps -- they were
built from those specific failures, not designed in the abstract:

    "MOISTURE RETAINING AGENT"           -> Humectant
    "Emulsifying and Stabilizing Agent"  -> Emulsifier + Stabilizer
    "Permitted Emulsifying"              -> Emulsifier
    "Leavening Agents"                   -> Raising agent
    "Stabilising Agents"                 -> Stabilizer

A later pass over real labels also caught "RAISING AGENTS" -> null and
"EMULSIFIER OF VEGETABLE ORIGIN" -> null; see the UK/US map and the " of "
rule below for why.
"""

import re

# UK -> US spelling, as an EXPLICIT map of whole words, not a suffix pattern.
# A substring rule ("ising" -> "izing") looks appealing but is wrong: both
# "stabilising" and "raising" end in "ising", and only one of them is a UK
# spelling variant. Applying the substring rule turned "raising agents" into
# the nonsense "raizing agents", which matched nothing.
_UK_TO_US = {
    "stabilising": "stabilizing",
    "stabiliser": "stabilizer",
    "stabilisers": "stabilizers",
}

# Real labels use verb forms ("emulsifying") where the standard uses the noun
# ("emulsifier"). "gelling" maps to "gelling agent" because that -- not bare
# "gelling" -- is the actual purpose string Section 2 uses for that class.
_VERB_TO_NOUN = {
    "emulsifying": "emulsifier",
    "stabilizing": "stabilizer",
    "thickening": "thickener",
    "gelling": "gelling agent",
}

# Terms real labels use that the FAO vocabulary simply does not contain, even
# after every rule below. Measured, not guessed -- see the module docstring.
_EXTRAS = {
    "leavening agent": "Raising agent",        # US/regional term for "raising agent"
    "moisture retaining agent": "Humectant",   # FAO says "moisture-retention agent"
}


def _uk_to_us(text: str) -> str:
    """Apply _UK_TO_US to whole words only -- never as a substring replacement."""
    return " ".join(_UK_TO_US.get(word, word) for word in text.split(" "))


def _build_purpose_index(functional_classes: list[dict]) -> dict[str, str]:
    """purpose phrase (normalised) -> class name."""
    index: dict[str, str] = {}
    for entry in functional_classes:
        for purpose in entry["purposes"]:
            key = purpose.lower().replace("-", " ")
            index.setdefault(key, entry["class"])
    return index


def _strip_trailing_agent(text: str) -> str:
    return re.sub(r"\s+agents?$", "", text)


def _resolve_fragment(fragment: str, purpose_index: dict[str, str]) -> str | None:
    fragment = fragment.strip()
    if not fragment:
        return None
    if fragment in purpose_index:
        return purpose_index[fragment]

    verb_form = _strip_trailing_agent(fragment)
    mapped = _VERB_TO_NOUN.get(verb_form)
    if mapped and mapped in purpose_index:
        return purpose_index[mapped]

    return _EXTRAS.get(fragment)


def normalise_role(raw_role: str | None, functional_classes: list[dict]) -> list[str]:
    """Normalise one declared_role string into zero or more canonical Codex classes.

    `functional_classes` is the parsed contents of functional_classes.json,
    passed in by the caller -- this function never loads reference data itself.
    Applied in order:
      1. lowercase, collapse whitespace
      2. strip a leading "permitted "
      3. strip a trailing " of ..." qualifier, e.g. "Emulsifier of Vegetable
         Origin" -> "Emulsifier" -- done BEFORE splitting on " and " below,
         so a qualifier phrase can never be mistaken for a second class
      4. UK -> US spelling, via an explicit whole-word map (see _UK_TO_US)
      5. singularise a trailing "s"
      6. treat "-" and " " as equivalent
      7. verb -> noun (applied per fragment, see (8))
      8. split compound phrases on " and " before lookup, e.g.
         "emulsifying and stabilizing agent" -> ["Emulsifier", "Stabilizer"]
      9. an explicit extras map, for terms not in the FAO vocabulary at all
    """
    if not raw_role:
        return []

    text = " ".join(raw_role.lower().split())  # 1
    text = text.removeprefix("permitted ")  # 2
    text = text.split(" of ", 1)[0]  # 3
    text = _uk_to_us(text)  # 4
    text = text.removesuffix("s")  # 5
    text = text.replace("-", " ")  # 6

    purpose_index = _build_purpose_index(functional_classes)

    classes: list[str] = []
    for fragment in text.split(" and "):  # 8 (7 and 9 happen inside _resolve_fragment)
        resolved = _resolve_fragment(fragment, purpose_index)
        if resolved and resolved not in classes:
            classes.append(resolved)
    return classes
