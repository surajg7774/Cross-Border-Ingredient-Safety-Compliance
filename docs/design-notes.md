# EU Additive Compliance — design decisions

## Scope
EU only. Codex INS = normalization backbone. Dropped openFDA recall (US-only).
Kept substitute advisor + geopolitical horizon lane.

## Core rule
Annex II is category-dependent — verdicts need (E-number, FCS code), never
E-number alone. Food category is a required input, not optional.

## Architecture
Label → validity gate → parse ingredients → resolve identity (Codex) →
food category (Annex II Part D) → rule engine → verdict.
Model-assisted stages are all UPSTREAM of the verdict. Rule engine has no LLM.

## Key boundaries
- Extractor emits OBSERVATIONS only (no is_additive, no canonical_id)
- Resolver = identity (what is it?) — has confidence, uses fuzzy matching
- Rule engine = permission (is it legal here?) — no confidence, pure join
- Annex II Part E lookup is a DB join, never vector search
- Embeddings only for: food category, footnotes, substitute ranking,
  horizon matching, resolver tier 5 (with deterministic verification)

## Three time-states
- Law today → rule engine
- Adopted, effective later → rule engine, temporal branch (sunset date)
- Not yet law → horizon lane, advisory only, can never change a verdict

## Resolver logic
Code path (regex → OCR correction via confusion map → index lookup)
Name path: exact → synonym → FOOD LEXICON → fuzzy → embedding → unresolved
(food lexicon must sit ABOVE fuzzy, or turmeric matches E100)
Family guard: E472a-f, E160a-f, E306-309, E620-625 → force manual review
Confidence <0.80 never reaches the rule engine.

## Accuracy priorities
Recall > precision (missed additive = false "clear to export" = dangerous).
Two-pass: gate+crop, then extract from upscaled single-language block.
Validate every E-number against the index; edit-distance correct O↔0, l↔1.
Dual-read consensus: union results, flag disagreements.

## Status
Step 0 done — uv, structure, config.py verified.
gemini-2.5-flash (primary) / gemini-2.5-flash-lite (secondary)
Step 1 prompt ready — extraction stage only.
Next: add 8-10 label photos to data/labels/, run Step 1.