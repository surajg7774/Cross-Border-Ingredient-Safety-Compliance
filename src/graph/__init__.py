"""LangGraph orchestration layer over the existing pipeline -- an ADDITIONAL
entry point, not a replacement. Every existing script and app.py keep
working unchanged; nothing here moves compliance logic out of src/category/,
src/resolve/, src/rules/, src/substitutes/, or src/horizon/. Nodes
(src/graph/nodes.py) call the SAME pure functions the scripts call --
run_extraction, parse_declaration, resolve_items, classify, evaluate,
find_substitutes, find_horizon_signals, narrate -- this package only wires
them into a graph with real fan-out (Send, one classify per component) and
real parallel branches (substitutes/horizon), replacing the hand-rolled
pause/resume machinery in app.py and scripts/verdict.py's --category flag
with LangGraph's own interrupt()/Command(resume=...) primitive. See
docs/build_log.md for the measurement (F-06/F-07/F-13/F-14) that makes human
category confirmation a design requirement, not a fallback.
"""
