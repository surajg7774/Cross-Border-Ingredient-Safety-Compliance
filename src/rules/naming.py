# DESIGN RULE: pure display-name derivation -- data in, data out. No file
# reads, no settings access, no model calls. Imports only src.rules.schemas.
# This module exists because additive_name is a DISPLAY field that every
# downstream surface (the narrator's JSON, substitutes' blocked_name, the
# verdict strip, every export) treats as its first-choice name, while
# src/rules/engine.py can only ever populate it from eu_fip -- which is
# structurally null for exactly the items that matter most (see below).
# Backfilling must therefore happen ONCE, immediately after evaluate(), on
# EVERY path that produces a verdict. It previously lived in app.py alone,
# which meant the LangGraph path (src/graph/nodes.py) ran narrate() and
# find_substitutes() against an UNENRICHED verdict -- the narration said
# "an additive without a declared name or E-number" while the screen,
# enriched afterwards, showed "Fast Green FCF (INS 143)" for the same item.
"""Display-name backfill for a finished ProductVerdict.

src/rules/engine.py sets ItemVerdict.additive_name from the eu_fip row for
the item's eu_canonical_id. An item BLOCKED as not_authorised_eu has, by
construction, no eu_canonical_id (that absence is precisely why it blocks),
so its additive_name is always None coming out of evaluate(). Codex still
knows the substance -- INS 143 is Fast Green FCF -- and the label itself
always carries the declared wording, so a name is recoverable; it just is
not recoverable inside the rules engine, which has neither codex_ins nor
the extraction payload and should not gain them.

enrich_additive_names() is idempotent: it only fills names that are still
empty, so calling it twice on the same verdict is harmless.
"""

from src.rules.schemas import ProductVerdict


def extraction_names(extraction_payload: dict) -> dict[int, str]:
    """item_id -> the label's own declared wording, for items with no
    eu_fip and no Codex name to draw on. Same fallback scripts/verdict.py
    uses."""
    items = extraction_payload["extraction"]["items"]
    names: dict[int, str] = {}
    for item in items:
        name = item.get("name_as_declared") or item.get("verbatim")
        if name:
            names[item["item_id"]] = name
    return names


def enrich_additive_names(
    verdict: ProductVerdict,
    extraction_names_by_item: dict[int, str],
    canonical_ins_by_item: dict[int, str],
    codex_names: dict[str, str],
) -> ProductVerdict:
    """A copy of `verdict` with every item's additive_name backfilled:

        additive_name = eu_fip name (already set, kept as-is)
                        else Codex INS name (e.g. INS 143 -> "Fast Green FCF",
                             absent from eu_fip -- that absence is WHY it
                             blocks, but Codex still knows the substance)
                        else name_as_declared / verbatim (the label's own
                             wording, via `extraction_names_by_item`)

    additive_name is a pure display field -- nothing in src/rules/ or
    src/substitutes/ branches on whether it is None (eu_fip presence is
    signalled by eu_canonical_id/flags instead) -- so enriching ONCE, before
    find_substitutes()/narrate() ever see the verdict, fixes every
    downstream surface without changing any of their own code: they already
    treat additive_name as their first-choice name.

    An item resolved via the CODEX fallback tier gets its code folded into
    the name itself -- "Fast Green FCF (INS 143)", not just "Fast Green
    FCF" -- because the code is how a user cross-references their own
    specification, and ItemVerdict deliberately does not carry
    canonical_ins (see src/substitutes/advisor.py's docstring), so this is
    the one place with both the Codex name AND the code in hand at once.
    An item WITH an eu_canonical_id already gets its E-number shown
    separately (the verdict strip's own code_html) -- folding it into the
    name too would duplicate it, so only the Codex-fallback tier does this.

    Idempotent: an item whose additive_name is already set is returned
    untouched, so a second call on an enriched verdict is a no-op.
    """
    items = []
    for item in verdict.items:
        name = item.additive_name
        if not name:
            canonical_ins = canonical_ins_by_item.get(item.item_id)
            codex_name = codex_names.get(canonical_ins) if canonical_ins else None
            if codex_name:
                name = f"{codex_name} (INS {canonical_ins})"
        if not name:
            name = extraction_names_by_item.get(item.item_id)
        if name and name != item.additive_name:
            item = item.model_copy(update={"additive_name": name})
        items.append(item)
    return verdict.model_copy(update={"items": items})
