# DESIGN RULE: this package proposes; it never decides. Nothing under
# src/agent/ writes to a verdict, changes eu_canonical_id on a ResolvedItem,
# or removes an item from the review queue -- see src/agent/resolver_agent.py's
# module docstring. Every lookup in src/agent/tools.py wraps an EXISTING
# pure function/reference dataset; no lookup is reimplemented here.
"""The review-queue resolver agent: proposes an identity for an item the
deterministic pipeline could not resolve, for a human to confirm or reject.
See docs/build_log.md for why this is an AGENT (tool selection varies per
item) rather than another fixed pipeline stage."""
