# DESIGN RULE: every function below WRAPS a reference dataset or an
# existing schema this project already loads elsewhere (codex_ins.json,
# eu_fip.json, label_aliases.json) -- none of them re-derives resolution
# logic src/resolve/resolver.py already owns. A tool only ever looks
# something up and returns plain data with a `source` field; it never
# decides an item's identity. Pure and side-effect-free except search_web,
# which makes one isolated network call and must degrade to
# {"available": False, ...} rather than raise (see its docstring) -- the
# agent loop must be able to keep going, or decline cleanly, when the
# network/quota is unavailable.
"""Tools the review-queue resolver agent (src/agent/resolver_agent.py) may
call. Each is exposed to the model as a google-genai FunctionDeclaration via
build_tools(refs) -> dict[str, ToolSpec], bound once per item to that item's
own AgentRefs -- the model supplies only the small, LLM-controlled arguments
(a code, a term, a query); reference data is closed over, never something
the model has to pass itself.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from rapidfuzz import fuzz

# Same threshold src/resolve/resolver.py uses for its own fuzzy tier --
# consistency, not re-derivation: a food-lexicon or codex-name fuzzy match
# below this score is noise, not a hit, in that module too.
FUZZY_THRESHOLD = 90


def _normalise_code(raw_code: str) -> str:
    """'INS 924' -> '924', 'E471' -> '471' -- copied from
    src/resolve/resolver.py's helper of the same name (a few lines of pure
    string normalisation, not resolution logic, so duplicating it keeps
    src/agent/ independent of src/resolve/'s internals, the same
    independence every other stage boundary in this project already keeps;
    see e.g. src/report/narrator.py's own duplicated _strip_markdown_fences)."""
    text = raw_code.lower().replace(" ", "")
    if text.startswith("ins"):
        return text[3:]
    if text.startswith("e"):
        return text[1:]
    return text


def _normalise_text(text: str) -> str:
    return " ".join(text.lower().split())


def _parent_ins(ins: str) -> str | None:
    """'451(i)' -> '451', '160a' -> '160'. None for a bare number -- same
    normalisation as src/resolve/resolver.py's helper of the same name,
    duplicated for the same independence reason as _normalise_code above."""
    m = re.match(r"^(.+)\(([ivxlc]+)\)$", ins, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.match(r"^(\d+)([a-z])$", ins, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


@dataclass
class AgentRefs:
    """Everything a resolve_review_item() call needs to build tools for ONE
    item -- reference datasets plus that item's own product context. Built
    by the caller (scripts/agent_review.py, app.py) from data already
    loaded/computed for the verdict; src/agent/ reads no file itself."""

    codex_ins: list[dict]
    eu_fip: list[dict]
    label_aliases: dict
    product_name: str | None
    product_descriptor: str | None
    confirmed_category: str | None  # e.g. "12.2.2 (Seasonings and condiments)", or None if unconfirmed
    other_ingredient_names: list[str] = field(default_factory=list)
    model_id: str = ""  # for search_web's own isolated grounded call


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON-schema dict for google.genai.types.FunctionDeclaration
    fn: Callable[..., dict]


# =========================================================================== #
# lookup_codex
# =========================================================================== #
def _lookup_codex(term_or_code: str, codex_ins: list[dict]) -> dict:
    """One INS record: name, synonyms, functional classes, parent -- tried
    as a code first (exact, then parent-widened), then as a name (exact,
    then synonym, then fuzzy) -- the same tier ORDER src/resolve/resolver.py
    uses, so a hit here means the same thing a hit in the real cascade
    would."""
    by_ins = {r["ins"]: r for r in codex_ins}

    normalised = _normalise_code(term_or_code)
    record = by_ins.get(normalised)
    matched_on = "code_exact" if record else None

    if record is None:
        parent_ins = _parent_ins(normalised)
        if parent_ins and parent_ins in by_ins:
            record = by_ins[parent_ins]
            matched_on = "code_parent"

    if record is None:
        key = _normalise_text(term_or_code)
        for r in codex_ins:
            if _normalise_text(r["name"]) == key:
                record, matched_on = r, "name_exact"
                break

    if record is None:
        key = _normalise_text(term_or_code)
        for r in codex_ins:
            if any(_normalise_text(s) == key for s in r["synonyms"]):
                record, matched_on = r, "synonym_exact"
                break

    if record is None:
        best_score, best_record = 0, None
        for r in codex_ins:
            score = fuzz.token_set_ratio(term_or_code, r["name"])
            if score > best_score:
                best_score, best_record = score, r
        if best_record is not None and best_score >= FUZZY_THRESHOLD:
            record, matched_on = best_record, f"fuzzy (score {best_score})"

    if record is None:
        return {"found": False, "queried": term_or_code, "source": "codex_ins.json"}

    return {
        "found": True,
        "queried": term_or_code,
        "matched_on": matched_on,
        "ins": record["ins"],
        "name": record["name"],
        "synonyms": record["synonyms"],
        "functional_classes": record["functional_classes"],
        "parent_ins": record["parent_ins"],
        "source": "codex_ins.json",
    }


# =========================================================================== #
# lookup_eu_fip
# =========================================================================== #
def _lookup_eu_fip(code: str, eu_fip: list[dict]) -> dict:
    """Whether `code` (exact canonical_id match only -- no inference, see
    the module DESIGN RULE) appears in the EU list, and if so its name,
    every distinct permission status seen across its food-category rows,
    and how many such rows exist. Exact match only -- widening to a parent
    or crosswalking by name is src/resolve/resolver.py's job (_crosswalk_to_eu),
    not something this tool re-derives."""
    normalised = _normalise_code(code)
    rows = [r for r in eu_fip if r["canonical_id"] == normalised]
    if not rows:
        return {"found": False, "queried": code, "source": "eu_fip.json"}

    statuses = sorted({r["status"] for r in rows})
    functional_classes = rows[0].get("functional_classes") or []
    return {
        "found": True,
        "queried": code,
        "canonical_id": normalised,
        "additive_name": rows[0]["additive_name"],
        "statuses": statuses,
        "n_category_rows": len(rows),
        "functional_classes": functional_classes,
        "source_url": rows[0].get("source_url"),
        "source": "eu_fip.json",
    }


# =========================================================================== #
# check_food_lexicon
# =========================================================================== #
def _check_food_lexicon(term: str, label_aliases: dict) -> dict:
    """Is `term` a known plain food ingredient, per the hand-maintained
    never_additives lexicon (data/reference/label_aliases.json) -- exact
    match first, then the same fuzzy tier src/resolve/resolver.py's name
    path uses, so "black pepper powder" can still match "black pepper"."""
    terms = label_aliases.get("never_additives", {}).get("terms", [])
    key = _normalise_text(term)

    for known in terms:
        if _normalise_text(known) == key:
            return {
                "is_known_food": True,
                "queried": term,
                "matched_term": known,
                "match_type": "exact",
                "score": 100,
                "source": "label_aliases.json:never_additives",
            }

    best_score, best_term = 0, None
    for known in terms:
        score = fuzz.token_set_ratio(term, known)
        if score > best_score:
            best_score, best_term = score, known
    if best_term is not None and best_score >= FUZZY_THRESHOLD:
        return {
            "is_known_food": True,
            "queried": term,
            "matched_term": best_term,
            "match_type": "fuzzy",
            "score": best_score,
            "source": "label_aliases.json:never_additives",
        }

    return {
        "is_known_food": False,
        "queried": term,
        "matched_term": None,
        "match_type": None,
        "score": best_score or None,
        "source": "label_aliases.json:never_additives",
    }


# =========================================================================== #
# list_family_members
# =========================================================================== #
def _list_family_members(codes: list[str], codex_ins: list[dict]) -> dict:
    """The name/functional classes of every code in `codes`, side by side,
    so the agent can see what it is actually choosing between -- e.g. the
    17 modified-starch candidates (1400-1452) or Stevia's four (960a-d).
    `codes` is normally the item's OWN ambiguous-candidate list
    (ResolvedItem.candidates via get_product_context/the item passed to
    resolve_review_item), not derived by prefix-scanning codex_ins here:
    several of this project's real ambiguous families (e.g. the modified
    starches) do not share a numeric prefix at all, only a curated
    label_aliases.json ambiguous[] entry -- prefix derivation would miss
    them."""
    by_ins = {r["ins"]: r for r in codex_ins}
    members, not_found = [], []
    for code in codes:
        normalised = _normalise_code(code)
        record = by_ins.get(normalised)
        if record is None:
            not_found.append(code)
            continue
        members.append(
            {
                "ins": record["ins"],
                "name": record["name"],
                "synonyms": record["synonyms"],
                "functional_classes": record["functional_classes"],
            }
        )
    return {"queried": codes, "members": members, "not_found": not_found, "source": "codex_ins.json"}


# =========================================================================== #
# get_product_context
# =========================================================================== #
def _get_product_context(refs: AgentRefs) -> dict:
    """The product name, confirmed food category, and the other ingredient
    names on the SAME label -- what lets the agent narrow an ambiguous case
    from what else is declared (e.g. a label whose only other starches are
    corn-derived narrows which modified starch is plausible). Takes no
    model-supplied arguments -- everything here was already known before
    the agent started, so there is nothing for the model to get wrong by
    supplying it."""
    return {
        "product_name": refs.product_name,
        "product_descriptor": refs.product_descriptor,
        "confirmed_category": refs.confirmed_category,
        "other_ingredient_names": refs.other_ingredient_names,
        "source": "extraction + resolution + verdict (this run)",
    }


# =========================================================================== #
# search_web
# =========================================================================== #
def _search_web(query: str, model_id: str) -> dict:
    """For a substance absent from every reference dataset here (e.g. INS
    924, absent from codex_ins.json entirely). Uses Gemini's own built-in
    Google Search grounding (google.genai types.Tool(google_search=...)) --
    the SAME model/SDK/API key every other stage in this project already
    calls, rather than adding a new search-provider dependency this project
    has no existing integration for. This is a SEPARATE, isolated
    generate_content call (Gemini does not allow mixing built-in search
    with custom function-declarations in the same call), never mixed into
    the agent's own tool-calling turn.

    MUST return {"available": False, ...} on any failure -- rate limit,
    quota exhaustion, network error -- never raise. The agent must be able
    to keep going (or decline cleanly) when search is unavailable, exactly
    as narrate() falls back to a deterministic summary rather than crash
    report generation on a model failure (src/report/narrator.py).
    """
    if not model_id:
        return {"available": False, "queried": query, "answer": None, "citations": [], "source": "unavailable: no model_id configured"}

    try:
        from google import genai
        from google.genai import errors, types

        from config import settings

        client = genai.Client(api_key=settings.GOOGLE_API_KEY)
        config = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())])
        response = client.models.generate_content(model=model_id, contents=[query], config=config)
    except errors.APIError as exc:
        reason = "quota exhausted" if exc.code == 429 else f"API error {exc.code}"
        return {"available": False, "queried": query, "answer": None, "citations": [], "source": f"unavailable: {reason}"}
    except Exception as exc:  # noqa: BLE001 -- ANY search failure must degrade, not raise; see docstring
        return {"available": False, "queried": query, "answer": None, "citations": [], "source": f"unavailable: {exc}"}

    if response.text is None:
        return {"available": False, "queried": query, "answer": None, "citations": [], "source": "unavailable: empty response"}

    citations: list[str] = []
    candidate = response.candidates[0] if response.candidates else None
    grounding = getattr(candidate, "grounding_metadata", None) if candidate else None
    if grounding and grounding.grounding_chunks:
        for chunk in grounding.grounding_chunks:
            uri = getattr(getattr(chunk, "web", None), "uri", None)
            if uri:
                citations.append(uri)

    return {
        "available": True,
        "queried": query,
        "answer": response.text,
        "citations": citations,
        "source": "google_search grounding",
    }


# =========================================================================== #
# registry
# =========================================================================== #
def build_tools(refs: AgentRefs) -> dict[str, ToolSpec]:
    """The six tools, each bound to `refs` -- one dict the agent loop can
    both hand to google.genai as FunctionDeclarations (via each ToolSpec's
    name/description/parameters) and dispatch model-requested calls through
    (via each ToolSpec's fn, called with only the model-supplied kwargs)."""
    return {
        "lookup_codex": ToolSpec(
            name="lookup_codex",
            description=(
                "Look up one substance in the Codex INS list (CXG 36-1989) by name or code. "
                "Returns its INS number, name, synonyms, functional classes, and parent code, "
                "or found=false if nothing matches."
            ),
            parameters={
                "type": "OBJECT",
                "properties": {
                    "term_or_code": {
                        "type": "STRING",
                        "description": "An INS/E code (e.g. '924', 'INS 924', 'E471') or a substance name.",
                    }
                },
                "required": ["term_or_code"],
            },
            fn=lambda term_or_code: _lookup_codex(term_or_code, refs.codex_ins),
        ),
        "lookup_eu_fip": ToolSpec(
            name="lookup_eu_fip",
            description=(
                "Look up one EU canonical additive code in the EU Food Improvement Agents "
                "database. Returns its additive name, every distinct permission status seen "
                "across its food-category rows, and how many such rows exist, or found=false "
                "if the code has no exact match (an absent code is itself informative -- it "
                "may mean the substance is not authorised in the EU at all, or that this tool's "
                "code differs from the EU's own numbering)."
            ),
            parameters={
                "type": "OBJECT",
                "properties": {"code": {"type": "STRING", "description": "An EU/INS canonical code, e.g. '341(i)'."}},
                "required": ["code"],
            },
            fn=lambda code: _lookup_eu_fip(code, refs.eu_fip),
        ),
        "check_food_lexicon": ToolSpec(
            name="check_food_lexicon",
            description=(
                "Check whether a declared term is a known plain food ingredient (not a food "
                "additive at all) against a hand-maintained lexicon, e.g. 'black pepper powder' "
                "or 'turmeric'. Returns is_known_food=true/false."
            ),
            parameters={
                "type": "OBJECT",
                "properties": {"term": {"type": "STRING", "description": "The declared ingredient term, verbatim."}},
                "required": ["term"],
            },
            fn=lambda term: _check_food_lexicon(term, refs.label_aliases),
        ),
        "list_family_members": ToolSpec(
            name="list_family_members",
            description=(
                "Look up several INS codes at once (typically an ambiguous item's own candidate "
                "list) and return each one's name and functional classes side by side, so you "
                "can see what you are actually choosing between -- e.g. the 17 modified-starch "
                "candidates, or Stevia's four (960a-d)."
            ),
            parameters={
                "type": "OBJECT",
                "properties": {
                    "codes": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                        "description": "The candidate INS codes to compare, e.g. [\"960a\", \"960b\", \"960c\", \"960d\"].",
                    }
                },
                "required": ["codes"],
            },
            fn=lambda codes: _list_family_members(codes, refs.codex_ins),
        ),
        "get_product_context": ToolSpec(
            name="get_product_context",
            description=(
                "Get the product's name, its confirmed food category (if any), and the names of "
                "the other ingredients declared on the same label -- use this to narrow an "
                "ambiguous case using what else is on the label. Takes no arguments."
            ),
            parameters={"type": "OBJECT", "properties": {}},
            fn=lambda: _get_product_context(refs),
        ),
        "search_web": ToolSpec(
            name="search_web",
            description=(
                "Search the web for a substance that appears in none of the reference datasets "
                "above (e.g. an INS code absent from the Codex list entirely). Returns "
                "available=false, never an error, if search is unavailable (e.g. quota "
                "exhausted) -- check `available` before trusting the answer."
            ),
            parameters={
                "type": "OBJECT",
                "properties": {"query": {"type": "STRING", "description": "The search query."}},
                "required": ["query"],
            },
            fn=lambda query: _search_web(query, refs.model_id),
        ),
    }
