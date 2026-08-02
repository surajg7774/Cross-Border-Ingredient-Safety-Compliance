# DESIGN NOTE: this module implements the SAME rules as EXTRACT_PROMPT
# (src/prompts.py) -- functional-class group headings, explicit-prefix
# colons, bracketed nesting, code-form/code_system detection -- but as
# plain deterministic string parsing, not a model call. Trade-off: no
# model means no cost, no latency, and perfect reproducibility (the same
# text always parses the same way), but it cannot handle the messier
# constructions a real PHOTOGRAPHED label contains -- line-break
# hyphenation, footnote markers, mixed layouts, or the "is this a single
# bracketed alternate name or a genuine sub-ingredient?" ambiguity
# EXTRACT_PROMPT resolves by guessing (rule 1). Pasted/typed text is clean
# in a way a photograph never is, which is what makes this tractable at
# all as a deterministic parser.
#
# Pure and deterministic -- no file reads, no printing, no network. Both
# entry points take text only; there is no reference-data argument to
# thread through (unlike src/resolve/ or src/category/), so the functional-
# class vocabulary below is hardcoded rather than loaded from
# data/reference/functional_classes.json.
"""Deterministic parser for pasted/typed ingredients text.

Two entry points:
    parse_declaration(text)    -- a full declaration, as printed on a label
    parse_additive_list(text)  -- a bare comma-separated list of additives,
                                   for pre-label formulation checking
"""

import re
from itertools import count

from src.schemas import ExtractedItem, ExtractionResult

# Illustrative functional-class vocabulary from EXTRACT_PROMPT rule 3,
# reduced to singular base forms -- matched case-insensitively after
# stripping a leading "permitted " and a trailing plural "s"
# (_is_functional_class does the normalising).
_FUNCTIONAL_CLASS_TERMS = {
    "raising agent",
    "emulsifier",
    "colour",
    "color",
    "preservative",
    "thickener",
    "anticaking agent",
    "acidity regulator",
    "flavour enhancer",
    "flavor enhancer",
    "stabiliser",
    "stabilizer",
    "humectant",
    "leavening agent",
    "flour treatment agent",
    "moisture retaining agent",
    "sequestrant",
    "antioxidant",
    "firming agent",
    "glazing agent",
    "propellant",
    "sweetener",
    "gelling agent",
    "bulking agent",
    "foaming agent",
    "emulsifying salt",
    "carbonating agent",
}

# Matches: E322, E 322, 322, INS 322, 470(i), 472e, 1101(ii), 160b(i) --
# an optional E/INS prefix, the number, then an optional letter suffix and/
# or an optional roman-numeral parenthetical, in either order of presence.
_CODE_RE = re.compile(
    r"^(?P<prefix>INS|E)?\s*(?P<number>\d{3,4})(?P<letter>[a-z])?(?P<roman>\([ivx]+\))?$",
    re.IGNORECASE,
)
# "<phrase>(<body>)" or "<phrase>[<body>]" spanning to the end of the
# entry -- phrase itself must hold no brackets, so this matches exactly
# one TRAILING bracketed group, never a mid-string aside.
_TRAILING_BRACKET_RE = re.compile(r"^(?P<phrase>[^()\[\]]+)[(\[](?P<body>.*)[)\]]\s*$")
# A leading "<phrase>: <rest>" -- phrase holds no colon or bracket, so this
# only fires on a genuine functional-class prefix at the START of the
# fragment, never a colon buried inside nested content (which is only ever
# seen via the recursive split, already scoped to its own fragment).
_COLON_RE = re.compile(r"^(?P<phrase>[^:()\[\]]+):\s*(?P<rest>.+)$")


def _is_functional_class(phrase: str) -> bool:
    text = " ".join(phrase.strip().lower().split())
    text = text.removeprefix("permitted ")
    text = text.removesuffix("s")
    text = text.replace("-", " ")
    return text in _FUNCTIONAL_CLASS_TERMS


def _match_code(text: str) -> str | None:
    """code_system if `text` (already stripped) is EXACTLY one code token, else None."""
    match = _CODE_RE.match(text.strip())
    if not match:
        return None
    prefix = (match.group("prefix") or "").upper()
    if prefix == "INS":
        return "INS"
    if prefix == "E":
        return "E"
    return "bare"


def _split_top_level(text: str) -> list[str]:
    """Split on commas at bracket depth 0 only -- "Colour (INS 143, INS 110)"
    stays one fragment because its inner comma sits at depth 1."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in text:
        if ch in "([":
            depth += 1
            current.append(ch)
        elif ch in ")]":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _split_group_items(text: str) -> list[str]:
    """Split a group heading's bracket contents on comma, "&", or the word
    "and" -- EXTRACT_PROMPT rule 3 treats all three as the same separator."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in text:
        if ch in "([":
            depth += 1
            current.append(ch)
        elif ch in ")]":
            depth -= 1
            current.append(ch)
        elif ch in ",&" and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))

    items = []
    for part in parts:
        for piece in re.split(r"\band\b", part, flags=re.IGNORECASE):
            piece = piece.strip()
            if piece:
                items.append(piece)
    return items


def _is_balanced(text: str) -> bool:
    depth = 0
    for ch in text:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _parse_simple_item(text: str) -> tuple[str | None, str | None, str]:
    """A bare code, a "name (code)" pair, or a plain name.

    Returns (name_as_declared, declared_code, code_system). The "name
    (code)" case -- e.g. "Citric Acid (INS 330)" -- is what keeps a
    single inline code from being mistaken for a genuine bracketed
    breakdown (see _parse_entry): the bracket holds exactly one token and
    that token IS a code, so it belongs on this same item, not a child.
    """
    text = text.strip()
    system = _match_code(text)
    if system:
        return None, text, system

    match = _TRAILING_BRACKET_RE.match(text)
    if match:
        body = match.group("body").strip()
        system = _match_code(body)
        if system:
            return match.group("phrase").strip(), body, system

    return text, None, "none"


def _make_item(
    item_id: int,
    position: int,
    nesting_depth: int,
    parent_item_id: int | None,
    verbatim: str,
    name_as_declared: str | None,
    declared_role: str | None,
    role_source: str,
    declared_code: str | None,
    code_system: str,
) -> ExtractedItem:
    return ExtractedItem(
        item_id=item_id,
        position=position,
        nesting_depth=nesting_depth,
        parent_item_id=parent_item_id,
        verbatim=verbatim,
        name_as_declared=name_as_declared,
        declared_role=declared_role,
        role_source=role_source,
        declared_code=declared_code,
        code_system=code_system,
        percentage=None,  # not in the given rule set -- plain text carries no percentage rule here
        emphasis=False,  # plain text carries no styling
        footnote_marker=None,  # plain text carries no styling
        confidence=1.0,  # parsing is deterministic, not estimated
    )


def _parse_entry(
    entry: str,
    position: int,
    nesting_depth: int,
    parent_item_id: int | None,
    items: list[ExtractedItem],
    unparsed: list[str],
    item_ids: count,
) -> None:
    entry = entry.strip()
    if not entry:
        return
    if not _is_balanced(entry):
        # Unmatched brackets can't be trusted to structurally parse --
        # e.g. a truncated paste ("Chocolate (Cocoa, Milk") -- so this
        # fragment is reported, not guessed at or silently dropped.
        unparsed.append(entry)
        return

    # Rule: functional class + colon -> explicit_prefix. Checked BEFORE the
    # bracket rules below, so "Preservative: Sodium Benzoate (INS 211)"
    # is recognised as colon-prefixed first; only the remainder after the
    # colon is then checked for its own inline code.
    colon_match = _COLON_RE.match(entry)
    if colon_match and _is_functional_class(colon_match.group("phrase")):
        role = colon_match.group("phrase").strip()
        name, code, system = _parse_simple_item(colon_match.group("rest"))
        items.append(
            _make_item(
                next(item_ids), position, nesting_depth, parent_item_id, entry,
                name, role, "explicit_prefix", code, system,
            )
        )
        return

    bracket_match = _TRAILING_BRACKET_RE.match(entry)

    # Rule: functional class + bracket -> group_heading, one item per code/
    # name in the bracket, all sharing this entry's position.
    if bracket_match and _is_functional_class(bracket_match.group("phrase")):
        role = bracket_match.group("phrase").strip()
        for fragment in _split_group_items(bracket_match.group("body")):
            system = _match_code(fragment)
            name, code = (None, fragment) if system else (fragment, None)
            system = system or "none"
            items.append(
                _make_item(
                    next(item_ids), position, nesting_depth, parent_item_id, fragment,
                    name, role, "group_heading", code, system,
                )
            )
        return

    # Rule: ingredient + bracket -> nesting, UNLESS the bracket holds
    # exactly one token that is itself a code ("Citric Acid (INS 330)"),
    # in which case it's the SAME item's code, not a child.
    if bracket_match:
        name = bracket_match.group("phrase").strip()
        fragments = _split_top_level(bracket_match.group("body"))
        if len(fragments) == 1:
            system = _match_code(fragments[0])
            if system:
                items.append(
                    _make_item(
                        next(item_ids), position, nesting_depth, parent_item_id, entry,
                        name, None, "none", fragments[0].strip(), system,
                    )
                )
                return
        parent_id = next(item_ids)
        items.append(
            _make_item(
                parent_id, position, nesting_depth, parent_item_id, entry,
                name, None, "none", None, "none",
            )
        )
        for fragment in fragments:
            _parse_entry(fragment, position, nesting_depth + 1, parent_id, items, unparsed, item_ids)
        return

    # Fallback: a bare code, a "name (code)" pair, or a plain name.
    name, code, system = _parse_simple_item(entry)
    items.append(
        _make_item(
            next(item_ids), position, nesting_depth, parent_item_id, entry,
            name, None, "none", code, system,
        )
    )


def parse_declaration(text: str) -> ExtractionResult:
    """Parse a full pasted ingredients declaration, as printed on a label.

    See the module docstring for the rules and their trade-off against the
    vision-model prompt (src/prompts.py's EXTRACT_PROMPT) this mirrors.
    """
    items: list[ExtractedItem] = []
    unparsed: list[str] = []
    item_ids = count()

    for position, entry in enumerate(_split_top_level(text)):
        _parse_entry(entry, position, 0, None, items, unparsed, item_ids)

    return ExtractionResult(
        declaration_verbatim=text.strip(),
        language="en",
        items=items,
        unparsed_fragments=unparsed,
        warnings=[],
        allergen_statements=[],
        footnotes={},
        declaration_statements=[],
    )


def parse_additive_list(text: str) -> ExtractionResult:
    """Parse a bare comma-separated list of additives -- pre-label
    formulation checking, before any label exists. Every entry becomes one
    item at nesting_depth 0; no functional-class or nesting rules apply,
    since a formulation list has no declaration structure to read. A name
    with no code resolves by the name path downstream, exactly as a
    label-derived item would.
    """
    items: list[ExtractedItem] = []
    unparsed: list[str] = []
    item_ids = count()

    for position, entry in enumerate(_split_top_level(text)):
        if not _is_balanced(entry):
            unparsed.append(entry)
            continue
        name, code, system = _parse_simple_item(entry)
        items.append(
            _make_item(next(item_ids), position, 0, None, entry, name, None, "none", code, system)
        )

    return ExtractionResult(
        declaration_verbatim=text.strip(),
        language="en",
        items=items,
        unparsed_fragments=unparsed,
        warnings=[],
        allergen_statements=[],
        footnotes={},
        declaration_statements=[],
    )
