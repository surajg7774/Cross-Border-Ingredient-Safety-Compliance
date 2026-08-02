"""Category stage: retrieves the EU food categories a label's declared
composition plausibly belongs to, then narrows by which categories permit
every additive resolved on the label together. Embeddings + a deterministic
filter -- no LLM re-ranking. See README "Stage boundaries" for the coupling
rules this package follows.
"""
