# DESIGN RULE: this module PROPOSES; it never decides. resolve_review_item()
# returns an AgentProposal -- nothing here writes to a verdict, changes
# eu_canonical_id on a ResolvedItem, or removes an item from the review
# queue. A human confirms every proposal (scripts/agent_review.py prints it
# for a person to read; app.py's Accept/Reject buttons are the only things
# that ever apply one). Same discipline as category confirmation
# (docs/build_log.md): the system's own measurements show these are exactly
# the cases it could not resolve deterministically, so a probabilistic
# proposal is acceptable ONLY behind human confirmation.
#
# WHY THIS IS AN AGENT, NOT ANOTHER PIPELINE STAGE: a fixed pipeline runs
# the same steps on every item. Each review-queue item needs a DIFFERENT
# tool -- an ambiguous family needs list_family_members, a plain-food miss
# needs check_food_lexicon, a substance absent from every reference dataset
# needs search_web. The model choosing which tool fits THIS item, and how
# many times, is the actual justification for a tool-calling loop instead
# of a chain -- see docs/build_log.md.
"""The review-queue resolver agent: a small LangGraph tool-calling loop
(agent node <-> tool node, step-capped at MAX_STEPS) that investigates one
unresolved/ambiguous item and either proposes an identity or declines.

    def resolve_review_item(item, refs, llm, tools) -> AgentProposal

`item` is a plain dict for ONE review-queue item, joining what the caller
already has from extraction + resolution (never re-read here):
    {"item_id": int, "name_as_declared": str | None, "verbatim": str | None,
     "declared_code": str | None, "classification": str,
     "candidates": list[str], "flags": list[str]}

`refs` is a src.agent.tools.AgentRefs (kept for building the initial
prompt's context; the SAME refs the caller already used to build `tools`).
`llm` implements the LLM protocol below (start/send_tool_results) --
production code uses GeminiLLM (make_gemini_llm), tests use a plain mock,
no API calls. `tools` is src.agent.tools.build_tools(refs)'s return value.

DECLINING IS A SUCCESS, not a failure -- see _build_prompt and
AgentProposal.declined. A proposal with an empty `evidence` list is
rejected and forced to declined=True regardless of what the model claimed,
whether or not it set declined itself (see _parse_proposal).
"""

import json
import time
from dataclasses import dataclass, field
from typing import Literal, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from src.agent.tools import ToolSpec

MAX_STEPS = 5
MAX_RATE_LIMIT_RETRIES = 5
INITIAL_BACKOFF_SECONDS = 2.0
# Added to the server's own RetryInfo.retryDelay before sleeping -- a small
# safety margin, not a guess at the delay itself (see _retry_delay_seconds).
RETRY_DELAY_MARGIN_SECONDS = 1.0
# Same retryable set as src/extractors/gemini.py and src/report/narrator.py:
# 429 = rate limited, 500/503 = transient server-side overload.
RETRYABLE_STATUS_CODES = {429, 500, 503}

_CLASSIFICATIONS = ("additive", "food_ingredient", "flavouring", "enzyme", "unknown")
_CONFIDENCES = ("high", "medium", "low")


class AgentProposal(BaseModel):
    item_id: int
    name_as_declared: str | None
    proposed_canonical_ins: str | None
    proposed_classification: Literal["additive", "food_ingredient", "flavouring", "enzyme", "unknown"]
    confidence: Literal["high", "medium", "low"]
    reasoning: str  # plain English, 1-3 sentences
    evidence: list[str]  # which tools were called and what they returned, in the model's own words
    tool_calls: list[dict]  # full trace: {"tool": name, "args": {...}, "result_summary": "..."}
    declined: bool
    decline_reason: str | None


# =========================================================================== #
# LLM adapter interface -- production (GeminiLLM) vs. test mocks
# =========================================================================== #
@dataclass
class LLMTurn:
    """One model turn: either a request to call tools, or a final answer."""

    tool_calls: list[dict] = field(default_factory=list)  # [{"name": str, "args": dict}, ...]
    text: str | None = None  # the final JSON-as-text answer, when tool_calls is empty


class LLM(Protocol):
    def start(self, prompt: str) -> LLMTurn:
        """Send the initial task prompt; returns the first turn."""
        ...

    def send_tool_results(self, results: list[dict]) -> LLMTurn:
        """results: [{"name": str, "args": dict, "result": dict}, ...], in the
        same order as the tool_calls just requested. Returns the next turn."""
        ...


def _retry_delay_seconds(exc) -> float | None:
    """The server's own explicit wait time from a 429's RetryInfo detail --
    identical logic to src/extractors/gemini.py's helper of the same name,
    duplicated rather than imported (see GeminiLLM's own docstring on why
    its retry/backoff is a duplicate, not a shared import). See that
    module's docstring for the measured incident and the exact error shape
    this parses (exc.details["error"]["details"] -- a list of typed detail
    objects, one of which may be {"@type": ".../RetryInfo", "retryDelay":
    "49s"})."""
    details = getattr(exc, "details", None)
    if not isinstance(details, dict):
        return None
    error = details.get("error", details)
    for detail in error.get("details") or []:
        if not isinstance(detail, dict):
            continue
        if str(detail.get("@type", "")).endswith("RetryInfo"):
            raw = detail.get("retryDelay")
            if isinstance(raw, str) and raw.endswith("s"):
                try:
                    return float(raw[:-1])
                except ValueError:
                    return None
    return None


class GeminiLLM:
    """LLM backed by google.genai's native function calling. Retry/backoff
    mirrors src/extractors/gemini.py's own _call_model -- duplicated, not
    imported, per this project's stage-independence convention (see e.g.
    src/report/narrator.py's own duplicated retry loop).

    Holds conversation state (self._contents) across start()/
    send_tool_results() calls for ONE resolve_review_item() run -- start()
    resets it, so the SAME GeminiLLM instance is safe to reuse across
    multiple items in a loop (scripts/agent_review.py does this: the tool
    *schema* -- names/descriptions/parameters -- is identical for every
    item even though each item's tools are bound to different AgentRefs).
    """

    def __init__(self, model_id: str, tools: dict[str, ToolSpec]):
        self.model_id = model_id
        # The CONCRETE model version the server actually used, e.g.
        # "gemini-3.5-flash-lite" when model_id is the alias
        # "gemini-flash-lite-latest" -- None until the first successful
        # call. Read by the caller (scripts/agent_review.py) after
        # resolve_review_item() returns, so a result can be traced to a
        # specific model version, not just the alias that was requested.
        self.resolved_model_id: str | None = None
        self._tool_names = set(tools)
        self._declarations = [
            {"name": t.name, "description": t.description, "parameters": t.parameters} for t in tools.values()
        ]
        self._client = None
        self._config = None
        self._contents: list = []

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        from google import genai
        from google.genai import types

        from config import settings

        self._client = genai.Client(api_key=settings.GOOGLE_API_KEY)
        declarations = [types.FunctionDeclaration(**d) for d in self._declarations]
        self._config = types.GenerateContentConfig(tools=[types.Tool(function_declarations=declarations)])

    def start(self, prompt: str) -> LLMTurn:
        from google.genai import types

        self._ensure_client()
        self._contents = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]
        return self._call()

    def send_tool_results(self, results: list[dict]) -> LLMTurn:
        from google.genai import types

        self._ensure_client()
        parts = [types.Part.from_function_response(name=r["name"], response={"result": r["result"]}) for r in results]
        self._contents.append(types.Content(role="user", parts=parts))
        return self._call()

    def _call(self) -> LLMTurn:
        from google.genai import errors

        delay = INITIAL_BACKOFF_SECONDS
        response = None
        for attempt in range(MAX_RATE_LIMIT_RETRIES):
            try:
                response = self._client.models.generate_content(
                    model=self.model_id, contents=self._contents, config=self._config
                )
                break
            except errors.APIError as exc:
                if exc.code in RETRYABLE_STATUS_CODES and attempt < MAX_RATE_LIMIT_RETRIES - 1:
                    retry_delay = _retry_delay_seconds(exc)
                    if retry_delay is not None:
                        time.sleep(retry_delay + RETRY_DELAY_MARGIN_SECONDS)
                    else:
                        time.sleep(delay)
                        delay *= 2
                    continue
                raise
        if response is None:
            raise RuntimeError("unreachable")

        self.resolved_model_id = getattr(response, "model_version", None) or self.resolved_model_id

        # Preserve the SDK's own content object (including thought_signature)
        # for multi-turn continuity -- Gemini 2.5+ rejects a hand-reconstructed
        # function-call turn that lacks it (verified directly against the
        # live API before relying on this).
        if response.candidates:
            self._contents.append(response.candidates[0].content)

        calls = response.function_calls or []
        if calls:
            return LLMTurn(tool_calls=[{"name": c.name, "args": dict(c.args or {})} for c in calls])
        return LLMTurn(text=response.text)


def make_gemini_llm(model_id: str, tools: dict[str, ToolSpec]) -> GeminiLLM:
    return GeminiLLM(model_id, tools)


# =========================================================================== #
# proposal parsing -- empty evidence is invalid, declining is a success
# =========================================================================== #
def _strip_fences(text: str | None) -> str:
    if not text:
        return ""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.removesuffix("```").strip()
    return text


def _declined_dict(reason: str, reasoning: str = "") -> dict:
    return {
        "proposed_canonical_ins": None,
        "proposed_classification": "unknown",
        "confidence": "low",
        "reasoning": reasoning,
        "evidence": [],
        "declined": True,
        "decline_reason": reason,
    }


def _parse_proposal(text: str | None, tool_calls_trace: list[dict]) -> dict:
    """The model's final-turn text into a proposal dict -- forced to
    declined=True (regardless of what the model itself claimed) whenever:
    the text isn't valid JSON, `evidence` is empty or missing, or no tool
    was ever called at all in this run. An empty-evidence proposal is
    invalid per this module's docstring; this is where that is enforced."""
    if not text:
        return _declined_dict("model produced no final answer")

    try:
        raw = json.loads(_strip_fences(text))
    except json.JSONDecodeError:
        return _declined_dict("final answer was not valid JSON")
    if not isinstance(raw, dict):
        return _declined_dict("final answer was not a JSON object")

    evidence = raw.get("evidence")
    # A list of blank/whitespace-only strings is truthy (`if not evidence`
    # passes) but has nothing to show -- rendered as empty bullets in the
    # UI. Filtered here, at parse time, not left for the renderer to
    # paper over: an evidence list that is blank once stripped is exactly
    # as invalid as one that was empty to begin with.
    evidence = [str(e).strip() for e in evidence] if evidence else []
    evidence = [e for e in evidence if e]
    if not evidence:
        return _declined_dict("proposal had no supporting evidence", reasoning=str(raw.get("reasoning") or ""))
    if not tool_calls_trace:
        return _declined_dict(
            "no tool was called -- nothing to cite as evidence", reasoning=str(raw.get("reasoning") or "")
        )

    declined = bool(raw.get("declined", False))
    classification = raw.get("proposed_classification")
    confidence = raw.get("confidence")
    return {
        "proposed_canonical_ins": raw.get("proposed_canonical_ins"),
        "proposed_classification": classification if classification in _CLASSIFICATIONS else "unknown",
        "confidence": confidence if confidence in _CONFIDENCES else "low",
        "reasoning": str(raw.get("reasoning") or ""),
        "evidence": evidence,
        "declined": declined,
        "decline_reason": str(raw["decline_reason"]) if declined and raw.get("decline_reason") else None,
    }


def _summarise(result: dict) -> str:
    """A one-line summary of a tool result for the trace -- the full result
    already lives in `messages`/tool_results for the model itself; the
    trace is for a human skimming AgentProposal.tool_calls."""
    text = json.dumps(result, default=str)
    return text if len(text) <= 200 else text[:199] + "…"


def _review_reason(item: dict) -> str:
    candidates = item.get("candidates") or []
    if candidates:
        return f"ambiguous -- {len(candidates)} candidate(s), the label alone does not say which: {', '.join(candidates)}"
    return "unresolved -- no candidate identity was found at all"


def _build_prompt(item: dict, refs) -> str:
    declared_name = item.get("name_as_declared") or item.get("verbatim") or "(no name recorded)"
    return f"""You are resolving ONE item from a food-label compliance review queue -- an item the \
deterministic pipeline could not identify on its own. Use the tools available to you to \
investigate, then either PROPOSE an identity or DECLINE.

Item under review:
  Declared name: {declared_name}
  Declared code: {item.get("declared_code") or "(none)"}
  Current classification: {item.get("classification")}
  Why it needs review: {_review_reason(item)}
  Product: {refs.product_name or "(unknown)"}

DECLINING IS PREFERRED TO GUESSING. If the label genuinely does not give enough information to \
narrow this down, say so -- a correct decline is a SUCCESS, not a failure. A WRONG proposal is \
worse than a decline, because a human reviewer may accept it without independently checking.

You MUST call at least one tool and cite what it told you as evidence. A final answer with an \
empty evidence list, or one where no tool was ever called, will be rejected and treated as a \
decline regardless of what you write.

When you are ready to answer -- with a proposal OR a decision to decline -- respond with RAW \
JSON ONLY, no markdown fences, no further tool calls in that same turn, matching exactly this \
shape:
{{"proposed_canonical_ins": "<INS code, or null if declining or the item is not an additive>",
 "proposed_classification": "additive" | "food_ingredient" | "flavouring" | "enzyme" | "unknown",
 "confidence": "high" | "medium" | "low",
 "reasoning": "1-3 plain-English sentences",
 "evidence": ["<what a specific tool call told you, that supports this>", "..."],
 "declined": true | false,
 "decline_reason": "<why, if declined -- otherwise null>"}}
"""


# =========================================================================== #
# graph: agent <-> tools, step-capped
# =========================================================================== #
class _AgentState(TypedDict):
    step: int
    pending_tool_calls: list[dict]
    tool_results: list[dict]
    tool_calls_trace: list[dict]
    proposal: dict | None


def _make_agent_node(llm: LLM, prompt: str):
    def agent_node(state: _AgentState) -> dict:
        turn = llm.start(prompt) if state["step"] == 0 else llm.send_tool_results(state["tool_results"])
        step = state["step"] + 1

        if turn.tool_calls:
            return {"step": step, "pending_tool_calls": turn.tool_calls, "tool_results": []}

        proposal = _parse_proposal(turn.text, state["tool_calls_trace"])
        return {"step": step, "pending_tool_calls": [], "proposal": proposal}

    return agent_node


def _make_tool_node(tools: dict[str, ToolSpec]):
    def tool_node(state: _AgentState) -> dict:
        results, trace_additions = [], []
        for call in state["pending_tool_calls"]:
            name, args = call.get("name"), dict(call.get("args") or {})
            spec = tools.get(name)
            if spec is None:
                result = {"error": f"unknown tool {name!r}"}
            else:
                try:
                    result = spec.fn(**args)
                except Exception as exc:  # noqa: BLE001 -- a bad tool call feeds back into the loop, never crashes it
                    result = {"error": str(exc)}
            results.append({"name": name, "args": args, "result": result})
            trace_additions.append({"tool": name, "args": args, "result_summary": _summarise(result)})
        return {
            "tool_results": results,
            "tool_calls_trace": state["tool_calls_trace"] + trace_additions,
            "pending_tool_calls": [],
        }

    return tool_node


def _step_cap_node(state: _AgentState) -> dict:
    return {
        "proposal": _declined_dict(
            f"step cap ({MAX_STEPS}) reached without a final answer",
            reasoning="The agent used its full tool-call budget without reaching a conclusion.",
        )
    }


def _route(state: _AgentState) -> str:
    if state["proposal"] is not None:
        return END
    if state["step"] >= MAX_STEPS:
        return "step_cap"
    return "tools"


def _build_graph(llm: LLM, tools: dict[str, ToolSpec], prompt: str):
    graph = StateGraph(_AgentState)
    graph.add_node("agent", _make_agent_node(llm, prompt))
    graph.add_node("tools", _make_tool_node(tools))
    graph.add_node("step_cap", _step_cap_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", _route, ["tools", "step_cap", END])
    graph.add_edge("tools", "agent")
    graph.add_edge("step_cap", END)
    return graph.compile()


# =========================================================================== #
# entry point
# =========================================================================== #
def resolve_review_item(item: dict, refs, llm: LLM, tools: dict[str, ToolSpec]) -> AgentProposal:
    """Investigate one review-queue item and return a proposal for a human
    to confirm or reject. Never mutates `item` -- it is only ever read, to
    build the initial prompt (_build_prompt) and to fill in the identity
    fields (item_id, name_as_declared) the model is never asked to
    re-derive."""
    prompt = _build_prompt(item, refs)
    graph = _build_graph(llm, tools, prompt)

    initial_state: _AgentState = {
        "step": 0,
        "pending_tool_calls": [],
        "tool_results": [],
        "tool_calls_trace": [],
        "proposal": None,
    }
    final_state = graph.invoke(initial_state)

    proposal_fields = final_state["proposal"] or _declined_dict("agent loop ended without a proposal")
    return AgentProposal(
        item_id=item["item_id"],
        name_as_declared=item.get("name_as_declared"),
        tool_calls=final_state["tool_calls_trace"],
        **proposal_fields,
    )
