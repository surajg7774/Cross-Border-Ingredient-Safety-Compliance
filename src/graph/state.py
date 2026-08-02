# DESIGN RULE: this module only describes the SHAPE of state that flows
# between graph nodes -- no compliance logic, no I/O. See src/graph/__init__.py.
"""LangGraph state schema for the pipeline orchestration layer."""

import operator
from typing import Annotated, TypedDict


def _merge_dicts(left: dict, right: dict) -> dict:
    """Reducer for state["category"]: the classify fan-out dispatches one
    classify_node invocation per component query (via Send), and each
    returns a DISJOINT one-entry dict (its own component_label -> that
    query's CategoryResult). LangGraph requires an explicit reducer for any
    state key more than one node in the same superstep can write to, or it
    raises InvalidUpdateError -- plain dict union is enough since real key
    collisions are not expected (every classify_node writes its own,
    distinct component_label key)."""
    return {**left, **right}


class PipelineState(TypedDict):
    label_path: str | None
    text_input: str | None
    name: str
    description: str | None
    extraction: dict | None
    resolution: dict | None
    # component_label -> that query's CategoryResult (as a dict). The
    # whole-label ("product"-scope) query uses the key "(product)" --
    # never None, since None survives less predictably than a plain string
    # through the checkpointer's own serialization and matches the same
    # "(product)" sentinel scripts/verdict.py's own CLI table already
    # displays for a null component_label.
    category: Annotated[dict, _merge_dicts]
    confirmed_categories: dict[str, str]  # same component_label keys -> the confirmed fcs_code
    verdict: dict | None
    substitutes: dict | None
    horizon: dict | None
    narration: dict | None
    # Populated by a node's own try/except, never by an uncaught exception
    # propagating out of a node -- see each node's docstring in nodes.py.
    # Annotated for the same reason as "category": substitutes_node and
    # horizon_node run in the same superstep and could each fail
    # independently.
    errors: Annotated[list[str], operator.add]
