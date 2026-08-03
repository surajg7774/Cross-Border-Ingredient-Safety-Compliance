# DESIGN RULE: this is the ONE place in the system where a model produces
# user-facing prose. narrate() REPHRASES a finished ProductVerdict (plus
# substitutes/horizon) into plain English -- it never looks anything up. It
# must never import src.rules.engine, src.category/, src.resolve/, or any
# reference-data loader: the only inputs are the three finished result
# objects passed in, exactly as src/horizon/lane.py and src/substitutes/
# advisor.py are barred from importing src/rules/engine.py. If a change
# here ever needs eu_fip or codex_ins, that is a sign the change belongs
# upstream, in the deterministic stages, not here.
"""LLM narration of a finished compliance assessment.

narrate() sends the FINISHED verdict/substitutes/horizon as JSON to a model
and asks it to rephrase them in plain English -- summary and detail only,
no new facts. A deterministic FAITHFULNESS CHECK runs after every
generation: every E-number, mg/kg figure, and dotted category code the
model wrote is checked against the JSON it was given, and anything that
doesn't appear there is a claim the model invented. That claim is reported
in `unfaithful_claims`, never silently dropped -- an unfaithful narration
is a finding about the model, and hiding it would defeat the point of
checking at all.

If the model call fails outright (network, rate limit exhausted, malformed
response), narrate() falls back to the deterministic ProductVerdict.summary
that src/rules/engine.py already computed, with model_id="unavailable" --
a report must always be producible without the model.
"""

import json
import logging
import re

from pydantic import BaseModel

from src.horizon.schemas import HorizonResult
from src.model_call import strip_markdown_fences
from src.rules.schemas import ProductVerdict
from src.substitutes.schemas import SubstituteResult
from src.text_generation import get_text_generator

log = logging.getLogger("report.narrator")

# Matches "E551", "E 551", "E160a", "E1105(i)" -- group(1) is the bare code
# with the "E" prefix stripped, for comparison against eu_canonical_id
# values in the source JSON (which never carry the "E" prefix themselves).
_E_NUMBER_RE = re.compile(r"\bE\s?(\d{3,4}[a-z]?(?:\(\s*[ivxlc]+\s*\))?)\b", re.IGNORECASE)
# "20000 mg/kg" -> group(1) "20000", compared against max_level_mg_kg values.
_MG_KG_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*mg\s*/\s*kg\b", re.IGNORECASE)
# "12.2.2", "7.2" -- an EU food category system code, compared against
# fcs_code values.
_CATEGORY_CODE_RE = re.compile(r"\b\d{1,2}(?:\.\d{1,2}){1,3}\b")

_PROMPT_RULES = """You are writing a short briefing from a completed EU food-additive compliance \
assessment. The reader has about 20 seconds and needs to know whether to worry. You are \
REPHRASING ONLY:

- Do NOT add facts, do NOT look anything up, do NOT infer anything beyond what is stated in the \
JSON below.
- Every E-number, level (mg/kg), category code (e.g. 12.2.2) and date in your output MUST appear, \
verbatim, somewhere in the JSON provided.
- Never write "banned". The correct phrasing is "not authorised as a food additive in the EU".
- Never claim the product clears for export -- a label declares presence of an additive, not the \
dosage actually used.
- If an item's flags include "category_unconfirmed" (as opposed to "category_confirmed_by_user"), \
say plainly that its food category was not confirmed by a person.
- Every item already carries its best available display name in "additive_name", already including \
its code where one applies (e.g. "Fast Green FCF (INS 143)", "Silicon dioxide" for an item whose \
eu_canonical_id you should show separately as E<id>) -- use the name exactly as given, and always \
include its code. Never write "Item 8" or a bare id.
- Explain jargon briefly, inline, the first time each term appears:
    "quantum satis"       -> "no numeric limit; use only as much as needed"
    "Group I additive"    -> "a general-purpose additive permitted broadly"
    "carry-over"          -> "present via an ingredient rather than added directly"
  Every food category code must be followed by its name in brackets the first time it is used.

WRITING STYLE -- reviewers keep flagging violations of this, follow it exactly:
- Short sentences WITHIN a bullet -- but ONE BULLET PER ADDITIVE, or per GROUP of additives that \
share the same finding, never one bullet per fact. Combining two or three clauses into a single \
bullet is correct and expected: "E470a is permitted in 12.2.2 (seasonings and condiments) as a \
Group I additive, at quantum satis." is ONE bullet, not four.
- GROUP additives that share the same category, verdict, and level/basis into ONE bullet naming \
all of them together: "E470a, E627, E631, E330 and E471 are all permitted in 12.2.2 (seasonings \
and condiments) at quantum satis as Group I additives." Then add a SEPARATE bullet ONLY for \
whichever additive genuinely DIFFERS from the group: "E551 (silicon dioxide) is capped at \
20000 mg/kg, unlike the others." Do not give every additive its own bullet when several share the \
exact same finding -- a six-additive "What is permitted" topic should produce roughly 2-4 bullets \
total, not six, and never twenty-four.
- Boilerplate that is true for every item in a topic -- that conditions apply and the full text is \
below, which food category was confirmed, when the data was retrieved -- belongs in ONE line at \
the END of that topic's list, stated once. Never repeat it per bullet, and never state the \
confirmed category more than once per topic.
- Lead with the answer, not the reasoning: "This product cannot ship to the EU as declared." or \
"No blocking issues found." THEN say what stops it, or what to check, in that order.
- Say what the reader should DO ("confirm the category before relying on this", "check the source \
link for current status"), not what the regulation says.
- A conditions clause (e.g. "Permitted via Group I, Additives; E 420, E 421... may not be used") \
often names OTHER additives that belong to the same legal group but are NOT necessarily in this \
product. Do NOT list or describe those other additives by name -- they are boilerplate about the \
group, not a finding about this product. Write "conditions apply -- see below" instead of \
reproducing the clause.
- Mention every item from the JSON exactly ONCE (or once as part of a group bullet), in the single \
topic below that best fits it. A permitted_with_conditions item belongs ONLY under "What is \
permitted" -- do NOT also list it under "What needs review" just because verdict.review_required \
includes it: review_required exists so a human remembers to read the conditions text already shown \
under "What is permitted", not because the item needs its own separate write-up.

Respond with RAW JSON ONLY, no markdown code fences, matching exactly this shape:
{"summary": "2-3 short sentences: the bottom line first, then what stops it (or confirms nothing \
does), then what to do next if anything applies.",
 "detail": {"<topic label>": ["<one bullet per additive or group of additives>", "..."], ...}}

"detail" is a JSON OBJECT, not a string. Use ONLY the topic labels below, in this order, and \
include ONLY the ones that actually apply to this assessment -- when a topic has nothing to say, \
omit its key entirely, do not write an empty list or "none":
    "What is blocked"       -- items with headline not_authorised_eu or not_permitted_in_category
    "What is permitted"     -- items with a permitted_* headline -- GROUPED per the rule above, \
plus one closing line at most for shared boilerplate (conditions, category, retrieval date)
    "What needs review"     -- ONLY unresolved items, category_unknown items, and category-dependent \
items -- never a permitted_with_conditions item (see the rule above)
    "Possible substitutes"  -- candidate replacements for blocked items, if any
    "Regulatory horizon"    -- EFSA signals, if any, including the partial-coverage caveat
Each value is a list of bullets -- one per additive or group, plus at most one closing boilerplate \
line -- never one entry per individual fact or clause.

Here is the completed assessment, as JSON. This is your ONLY source of facts -- everything you \
write must be traceable to it:

"""


class Narration(BaseModel):
    summary: str
    # topic label ("What is blocked", ...) -> short sentences, one per
    # item/point -- a JSON object, not a markdown string. MEASURED BUG this
    # fixes: when detail was typed `str`, a model that (reasonably, given
    # the topic-per-key structure requested) returned a JSON object for it
    # anyway got silently coerced via str(dict), rendering literally as
    # "{'What is blocked': [...], ...}" on screen. Structuring the type
    # itself removes the mismatch instead of trying to out-prompt it.
    detail: dict[str, list[str]]
    model_id: str
    unfaithful_claims: list[str]


def _coerce_detail(raw: object) -> dict[str, list[str]]:
    """Validate/normalise the model's "detail" value into topic ->
    list-of-sentences. Raises if the TOP level isn't a JSON object at all
    (a real format break narrate() should fall back on) -- but tolerates
    the common, harmless drift of a single string instead of a one-item
    list under a given topic, wrapping it rather than discarding it."""
    if not isinstance(raw, dict):
        raise TypeError(f"expected 'detail' to be a JSON object, got {type(raw).__name__}")
    detail: dict[str, list[str]] = {}
    for key, value in raw.items():
        if isinstance(value, list):
            detail[str(key)] = [str(v) for v in value]
        else:
            detail[str(key)] = [str(value)]
    return detail


def _flatten_detail(detail: dict[str, list[str]]) -> str:
    return "\n".join(item for items in detail.values() for item in items)


def _build_prompt(verdict: ProductVerdict, substitutes: SubstituteResult, horizon: HorizonResult) -> str:
    payload = {
        "verdict": json.loads(verdict.model_dump_json()),
        "substitutes": json.loads(substitutes.model_dump_json()),
        "horizon": json.loads(horizon.model_dump_json()),
    }
    return _PROMPT_RULES + json.dumps(payload, indent=2)


def _call_model(prompt: str, model_id: str) -> str:
    """Delegates to whichever src.text_generation.TextGenerator
    settings.MODEL_BACKEND selects -- "native" (default, the exact
    google-genai call path this function used inline before) or
    "langchain" (opt-in). See src/text_generation.py's module docstring
    for the retry policy, and for what the langchain backend cannot fully
    reproduce."""
    return get_text_generator().complete(prompt, model_id)


def _faithfulness_check(
    narration_text: str, verdict: ProductVerdict, substitutes: SubstituteResult, horizon: HorizonResult
) -> list[str]:
    """Deterministic, post-generation: every E-number, mg/kg figure, and
    dotted category code mentioned in `narration_text` must appear
    somewhere in the JSON the model was actually given. Anything that
    doesn't is reported here -- never silently dropped, since an
    unfaithful narration is itself a finding worth showing the user."""
    source_text = verdict.model_dump_json() + substitutes.model_dump_json() + horizon.model_dump_json()

    seen: set[str] = set()
    claims: list[str] = []

    def _flag(claim: str) -> None:
        if claim not in seen:
            seen.add(claim)
            claims.append(claim)

    for match in _E_NUMBER_RE.finditer(narration_text):
        code = match.group(1).replace(" ", "")
        if code not in source_text:
            _flag(f"E{code} (not found in the assessment)")

    for match in _MG_KG_RE.finditer(narration_text):
        number = match.group(1)
        if number not in source_text:
            _flag(f"{number} mg/kg (not found in the assessment)")

    for match in _CATEGORY_CODE_RE.finditer(narration_text):
        code = match.group(0)
        if code not in source_text:
            _flag(f"category {code} (not found in the assessment)")

    return claims


def narrate(
    verdict: ProductVerdict, substitutes: SubstituteResult, horizon: HorizonResult, model_id: str
) -> Narration:
    """Rephrase a finished compliance assessment into plain English.

    Touches only the three result objects passed in -- no file reads, no
    reference data, no network calls beyond the model itself. Falls back to
    the deterministic verdict.summary (model_id="unavailable") on ANY
    failure -- a bad response, a network error, an exhausted retry budget
    -- so a report is always producible without the model.
    """
    prompt = _build_prompt(verdict, substitutes, horizon)
    try:
        raw = _call_model(prompt, model_id)
        data = json.loads(strip_markdown_fences(raw))
        summary = str(data["summary"])
        detail = _coerce_detail(data["detail"])
    except Exception as exc:  # noqa: BLE001 -- ANY failure (network, rate limit, bad JSON, missing key,
        # wrong "detail" shape) must fall back to the deterministic summary, never crash report
        # generation -- see the module docstring. Logged, not silent: model_id="unavailable" on its own
        # tells a reader THAT it fell back, never WHY -- a run whose narration.summary happens to match
        # verdict.summary verbatim is otherwise undiagnosable (nothing else records the failure).
        log.warning("narrate() falling back to the deterministic summary: %s: %s", type(exc).__name__, exc)
        return Narration(
            summary=verdict.summary,
            detail={"Note": ["Narration was unavailable -- showing the deterministic summary only."]},
            model_id="unavailable",
            unfaithful_claims=[],
        )

    unfaithful_claims = _faithfulness_check(f"{summary}\n{_flatten_detail(detail)}", verdict, substitutes, horizon)
    return Narration(summary=summary, detail=detail, model_id=model_id, unfaithful_claims=unfaithful_claims)
