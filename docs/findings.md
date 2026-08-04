Finding IDs (F-NN) were assigned chronologically as they came up during the
build, not renumbered afterward -- some numbers below are absent (F-01,
F-08, F-15) because those findings were merged into another entry or
withdrawn before being written up, not because this file is incomplete.
F-06 was briefly a duplicate (two unrelated findings shared the number);
the second was renumbered to F-07, the first unused ID, once noticed. 18
findings are recorded here: F-02 through F-07, F-09 through F-14, and F-16
through F-21.

### Current operating config

**recall@1 = 0.53, recall@3 = 0.82, MRR = 0.66**, `with-component-name`
-- established by F-13/F-14, below. Earlier figures elsewhere in this file
(≈0.60 at F-06, ≈0.46 at F-09) are superseded rounds, retained for the
record, not the current number.

## F-02 — FAO blocks automated GSFA access
403 + robots.txt Disallow /*?id=*. Switched to the official CXG 36-1989 PDF.
Better source: one versioned document, citable revision date, no rate limits.
Parser: nearest-anchor word clustering (no ruling lines in the PDF tables).
668 records, 0 validation failures.

## F-03 — Model self-reported confidence is uncalibrated
Gate returned 0.0 alongside is_food_label=true; every item returned exactly 1.0.
Replaced with deterministic derivation (evidence count / 5) and, in the
resolver, with confidence assigned by which match method succeeded.

## F-04 — String heuristics fail three times on regulatory vocabulary
"ising"→"izing" turned "raising" into "raizing".
"flavour enhancer" singular missed the plural on the label.
substring "flavour" matched "flavour enhancer", marking two real additives
out of scope. Each fix replaced a pattern with an explicit list or an
authoritative lookup (eu_fip membership as the final invariant).

## F-05 — Codex resolves what EU data cannot
EU_productmain, 7 name-only additives: 3/7 resolved against eu_fip alone
(synonyms empty on 18,982 of 18,987 rows). With codex_ins + label_aliases:
6 resolved, 2 correctly deferred as ambiguous. INS 470(i) has no EU
equivalent by number — only a name match reaches 470a.

## F-06 — Category retrieval baseline
recall@1 ≈ 0.60, recall@3 ≈ 0.90 across 10 label-components.
Similarity scores compressed into 0.61–0.70; correct 0.66 vs wrong 0.67.
Intersection filter marked the CORRECT category "not permitted" on Chips,
Parle-G and Ice-cream Outer — under investigation.

## F-07 — Category retrieval: query text matters more than retrieval method
Diagnostic: mean pairwise cosine similarity across 155 unrelated category
documents = 0.7847; rank-1 to rank-10 gap on a real query = 0.0335.
Hypothesis: given that compression, TF-IDF might match or beat embeddings.
Result: TF-IDF 0.24 vs embedding baseline 0.59 — contradicted.
Adding the component's own name to component queries: +0.17 on BOTH methods.
Adding a product description to all queries: no net gain (fixed product
misses, broke component queries). Scoped to product queries only: best MRR
(0.63) but lower recall@3 than component-name alone.
Decision: with-component-name as the operating config.

## Decision — eu_fip.json is the source of record
Two EU permission extracts existed (eu_fip.json, 18,987 rows; eu_fip_joined.json,
18,932). They differ by 53 (additive, category) pairs. eu_fip.json was adopted:
it is the fuller extract and carries the additional fields the pipeline uses
(note_codes, food_category_id, via_group, group_code). eu_fip_joined.json was
not copied into the project.


## F-09 — Upstream retrieval error produced confident false compliance failures
Category recall@1 ≈ 0.46. The rule engine followed rank-1 unconditionally,
so Parle-Gmain reported "3 items NOT PERMITTED" on additives that ARE
permitted in 7.2 Fine bakery wares — the correct category, retrieved at
rank 2. Noodlesraw produced two further false blocks the same way.

Every affected item already carried category_sensitive=True; the summary
ignored it.

Decision: a blocking verdict counts only when category-independent (absence,
prohibition) or when all candidates agree. Category-dependent blocks are
reported separately with the competing outcomes named.

## F-10 — experiments.csv rows silently mixed two ground-truth versions
A chromadb run appeared to beat the logged embedding baseline (0.41/0.65 vs
0.35/0.59). A fresh baseline run against the current truth file matched
chromadb exactly -- the earlier row had been scored against an older version
of data/golden/category_truth.json, before a component-entry edit. Nothing
in experiments.csv marked which truth version a row used.

Decision: log a truth_version column (short sha256 of category_truth.json's
bytes, computed at scoring time); warn when it differs from the last logged
row. Rows with a BLANK truth_version predate this column -- they were scored
against an earlier, unknown version of the truth file and are not comparable
to later rows.

## F-11 — ChromaDB reproduces exact search at additional cost
The original design specified ChromaDB. Scoping to EU-only reduced the
embedded corpus to 155 documents. Implemented as a retrieval_method ablation
using the same cached vectors and cosine metric: rankings are byte-identical
to exhaustive numpy search on every label and component
(0.4118/0.6471/0.5294 both), with ~0.32s index build overhead.

Decision: retained as a measured alternative, not adopted. A vector store's
approximate index earns its cost at scale; at 155 documents exhaustive
search is exact and instant.

## F-12 — OpenFoodTox has no E-number field; automated horizon ingestion abandoned
EFSA's OpenFoodTox 3.0 export (Zenodo record 19388272, "OFT3.0 export
repository.xlsx", 22.6 MB) is an IUCLID relational export: 18 sheets joined
by Document UUID / Parent UUID. Substance identity lives in REF_SUB (7,890
rows) keyed on CAS number and EC inventory entry ("607-406-2@EC");
DossierSubject.Name in DOSSIER (11,613 rows) is a UUID pair, not a substance
name. There is NO E-number field anywhere.

E-number is the identifier this system resolves to. Neither eu_fip.json nor
codex_ins.json carries CAS, so no crosswalk exists. A search for "sucralose"
or "955" returned 82 rows, all CAS-substring collisions (e.g. 252955-10-5),
not real matches.

Decision: automated ingestion was attempted and abandoned. The horizon lane
(src/horizon/) runs on a hand-curated dataset instead
(data/reference/horizon_signals.json); automated OpenFoodTox ingestion is
documented as future work requiring an E-number-to-CAS crosswalk.

## F-13 — Corpus reformatting refuted; the compression metric is not a proxy
Hypothesis (motivated by F-07's 0.7847 mean pairwise similarity across 155
unrelated documents): the EU regulatory descriptions dilute retrieval
signal, and the category name alone would separate better.

Seven corpus_mode variants scored against golden truth (n=17), recall@1 /
recall@3 / MRR:
  with-component-name (full, operating config)    0.53 / 0.82 / 0.66
  baseline (full, no component name)              0.41 / 0.65 / 0.53
  strip-framing                                   0.47 / 0.65 / 0.55
  name-only-with-component-name                   0.41 / 0.71 / 0.55
  name-only                                       0.35 / 0.71 / 0.51
  enriched                                        0.35 / 0.65 / 0.50
  name-plus-examples                              0.35 / 0.59 / 0.47

Refuted. No corpus_mode beats the "full" text it was reformatted from:
name-only-with-component-name (0.41/0.71/0.55) sits well below
with-component-name (0.53/0.82/0.66); name-only and name-plus-examples both
score below plain baseline on recall@1 and MRR. The descriptions carry real
signal.

Separately: name-plus-examples had the LOWEST mean pairwise similarity
(0.7552) of every mode measured AND the worst recall@3 (0.59) — the one
case where the two moved together. strip-framing had a NEGLIGIBLE
similarity delta (-0.0031) but a real, if small, recall@1/MRR gain over
plain baseline (0.47/0.55 vs 0.41/0.53) that the similarity number alone
would have hidden; enriched had a WORSE similarity delta (+0.0260 —
generic functional classes like "flavour enhancer" are common across
categories and add shared vocabulary rather than distinguishing text) but
scored no worse than plain baseline on recall@3. Mean pairwise similarity
is therefore not a valid proxy for retrieval quality; prior reasoning that
treated 0.78 as "the ceiling" (F-07) was unsound.

Decision: corpus_mode stays "full" (its existing default); with-component-
name remains the operating config (F-07). strip-framing's small gain is
within noise at n=17 and far short of with-component-name either way — not
pursued further without a larger truth set.


## F-14 — Retrieval strategy is not the bottleneck either

Following F-13 (corpus reformatting refuted), three retrieval strategies were
implemented as configs and scored against data/golden/category_truth.json
(n=17), compared to the operating config `with-component-name`
(0.53 / 0.82 / 0.66):

    with-component-name (operating)   0.53 / 0.82 / 0.66
    mmr-0.7                           0.53 / 0.82 / 0.66   identical
    mmr-0.5                           0.53 / 0.82 / 0.66   identical
    hybrid RRF (dense + TF-IDF)       0.53 / 0.76 / 0.62   down
    mqr-3 / mqr-5                     incomplete — see below

**MMR is inert on this eval set, and provably so rather than by omission.**
The raw candidate pools DO differ from baseline (verified in the output JSON),
but the difference disappears after the permitted-filter and the top-3 cut.
The case that motivated MMR — Khusmain retrieving 14.1.4, 14.1 and 14, three
levels of one branch — is not common enough across 17 label-components to
move any metric. Either the ancestor-clustering pattern is rarer than assumed,
or diversification displaces candidates the intersection filter would have
promoted anyway.

**Hybrid RRF declined.** Motivation was that `include_component_name` gave
+0.17 on BOTH dense and sparse retrieval (F-07), suggesting literal token
overlap carries signal the dense retriever misses. In practice TF-IDF's
standalone 0.24 is too weak to fuse productively with a 0.82 ranking. At
n=17, −0.06 is one label-component and is within noise, but it moved down in
every measure, not up.

**MQR could not be completed.** Multi-query retrieval hit the Gemini free-tier
`generate_content` daily quota at label 7 of 12. Text-generation quota is
tracked separately from embedding quota, which is why the embedding-only
configs completed and this one did not. The mechanism is implemented and
verified — data/reference/query_paraphrases.json holds real cached paraphrase
pairs and `query_variants` is correctly populated on the seven processed
labels — but it is not scored. Left as open work; the seven cached labels make
a follow-up run cheaper.

### Conclusion across F-06, F-07, F-13 and F-14

Four retrieval methods (embedding, TF-IDF, ChromaDB, hybrid), seven corpus
constructions, and two diversification/fusion strategies have been measured
against ground truth. **None beats the original corpus with the
component-name query augmentation.**

The residual failures do not look like retrieval failures. Parle-Gmain's
query is `REFINED WHEAT FLOUR SUGAR REFINED PALM OIL`, which genuinely
describes a biscuit, a cake and a cracker equally well; Chocolatemain's
`Centre` component missed under all seven corpus modes and all retrieval
strategies. The information required to distinguish these is not present in
the ingredient declaration.

Decision: retrieval work stopped. Category recall@1 = 0.53 is treated as a
property of the task, not a defect to be tuned away. Correctness is protected
by mandatory human confirmation of the category before any verdict is
computed (see the --category override and the UI confirmation step), and by
the rule engine refusing to report a blocking verdict when it depends on an
unconfirmed category.

### Caveat on all of the above

n=17 with AI-drafted, unverified ground truth. Differences under ~0.10 are one
or two label-components. These are directional findings suitable for comparing
configurations against each other, not validated accuracy claims.

## F-16 — Agentic review-queue resolver: 0 wrong proposals
22 review-queue items across the test set, hand-labelled ground truth.
LangGraph tool-calling loop (agent ↔ tools, step cap 5), six tools wrapping
existing reference-data functions plus search. Model: gemini-3.5-flash-lite.

    correct              15
    wrong                 0
    correctly declined    4
    wrongly declined      3

All four genuinely ambiguous items (modified cornstarch ×2, calcium
phosphate, Stevia) were declined rather than guessed — the label does not
determine which of 17 / 3 / 4 candidates is meant.

The three wrongly-declined are infrastructure, not reasoning: one from
search_web quota exhaustion, two from hitting the 5-step cap.

Wrong and declined are reported separately and never combined into a single
accuracy figure. A wrong proposal is dangerous because the human reviewer may
accept it; a decline only leaves work undone. Proposals are advisory — a
human confirms before any resolution is applied.

## F-17 — 9 of 13 stored verdict outputs predate the row-merge fix

`src/rules/engine.py`'s row selection was fixed to MERGE co-applicable eu_fip
rows (distinct conditions concatenated and numbered, lowest level kept,
flagged `multiple_provisions_apply: N`) instead of silently picking one and
discarding the rest, flagged `conflicting_rows` (see `docs/build_log.md`
Section J and its SUPERSEDED note). The fix is live in
`src/rules/row_selection.py`, shared with `src/substitutes/advisor.py`.

Checked every file already on disk against this: 9 of the 13 files in
`data/outputs/verdict/` were generated before the fix and still carry the
old `conflicting_rows` behaviour on 16 item-level flags that current code
would render as `multiple_provisions_apply` with merged clauses instead.
The 9 stale files:

    Chipsmain.json
    Chipsraw.json
    Chocolateraw.json
    E143test.json
    EU_productmain.json
    Khusmain.json
    Mixed.json
    Noodlesraw.json
    Pepsimain.json

(4 files were regenerated after the fix and are current: `Chocolatemain.json`,
`GraphTestChips.json`, `Ice-creammain.json`, `Parle-Gmain.json`.)

Not regenerated: doing so requires re-running extraction (and therefore
label-image model calls) for all 9 products, which costs quota this task was
not given. Any report or figure drawn from the 9 files above should be
treated as reflecting the pre-fix `conflicting_rows` behaviour, not current
code.

## F-18 — LangChain migration stopped at three of four call sites

`src/report/narrator.py`, `src/category/multiquery.py`, and
`src/extractors/gemini.py` (gate + extract) are migrated behind
`src/text_generation.py`'s `TextGenerator`/`VisionGenerator` Protocols,
selected by `settings.MODEL_BACKEND` ("native" default, "langchain"
opt-in). `src/agent/` (the review-queue resolver) was investigated and
DELIBERATELY left native -- not an oversight, not deferred for lack of
time.

**Reason: Gemini 3.x `thought_signature`.** The native `GeminiLLM`
(`src/agent/resolver_agent.py`) appends the SDK's own returned
`response.candidates[0].content` object into `self._contents` verbatim,
so a function-call turn's `thought_signature` round-trips byte-identical
across the multi-turn tool-calling loop. This is load-bearing, not
incidental: Gemini 2.5+ rejects a hand-reconstructed function-call turn
that lacks its signature -- verified directly against the live API before
the current design (holding the raw SDK object rather than a
reconstructed one) was relied on.

`langchain-google-genai==4.3.2` DOES have a real preservation mechanism
for this, checked in its installed source, not assumed: response parsing
stores a function call's signature in
`AIMessage.additional_kwargs["__gemini_function_call_thought_signatures__"]`,
keyed on that tool call's `id` (`chat_models.py`'s response-to-message
conversion), and re-serializing an `AIMessage.tool_calls` back into a
request (`_parse_chat_history`) looks that map up by `id` and reattaches
the real signature. It only works, though, if the CALLER preserves the
real `AIMessage` (or at least its `additional_kwargs` and each tool
call's `id`) across turns. `resolver_agent.py`'s own `LLM` Protocol
deliberately does not: `LLMTurn.tool_calls` carries `name`/`args` only --
no `id`, no `additional_kwargs`. A LangChain implementation built
naturally against that existing, minimal, provider-agnostic shape (rather
than retaining native LangChain message objects as private internal
state, mirroring how `GeminiLLM` retains `self._contents`) would drop
every signature.

**The failure would be SILENT, not a crash.** `_parse_chat_history`
(`chat_models.py`, the "Enforce thought signatures for new Gemini models"
block) injects a hardcoded `DUMMY_THOUGHT_SIGNATURE` onto the first
function-call part missing one, for any model matching
`_is_gemini_3_or_later` -- which this project's configured agent model,
`gemini-3.5-flash-lite`, does. That patch satisfies the API's schema
validation, so a naive migration would not error: no exception, no log,
a plausible-looking proposal returned every time. What degrades is
multi-turn reasoning continuity across tool calls -- precisely the axis
this agent exists to exercise (`resolver_agent.py`'s own DESIGN RULE:
"the model choosing which tool fits THIS item, and how many times, is
the actual justification for a tool-calling loop") -- and precisely what
a mocked-response test cannot measure, since it proves response-parsing
parity for a canned response, not reasoning-quality parity across turns.

**Conclusion:** near-zero benefit (the `LLM` Protocol seam already
provides full provider independence at the type level -- LangChain would
add no new capability visible to `resolve_review_item()` or the LangGraph
loop) against non-trivial, specifically undetectable risk. Stopped
deliberately, not abandoned -- revisit only with a concrete reason to
change the underlying provider, and re-verify the signature-handling
story against whichever `langchain-google-genai` version is current at
that time, not against this finding's version (`4.3.2`).

## F-19 — verdict golden set: draft cross-checked against eu_fip, 43/78 items filled

`verdict_truth_draft.md` (repo root) is an independent AI reading of the five
`data/golden/verdict/` label images -- written without looking at pipeline
output, explicitly not verified against Annex II. Cross-checked line-by-line
against the actual eu_fip rows before filling anything in.

**Pins: 4 of 10 changed from the scaffolder's rank-1 provisional value.**
Parle-Gmain product `15`->`7.2` (eu_fip: E503/E500(ii)/E472e return zero rows
at `15`, clean Group-I matches at `7.2` -- the same retrieval-miss shape
`src/rules/engine.py`'s own docstring already names). Chipsmain product
`2.3`->`15.1` (no scoring impact, no additive sits at product scope here, but
`2.3` "Vegetable oil pan spray" is a wrong match for a bag of chips).
EU_productmain "SEASONING" `12.3`->`12.2.2` (no scoring impact -- Group I
matches cleanly at both -- but `12.2.2` is the conceptually correct bracket,
matching Chipsmain's identical treatment). Ice-creammain "Outer Layer"
`3`->`5.1` (the outer layer is an explicit chocolate coating, not the ice
cream matrix -- and this one DOES change a scored value: E322 soya lecithin
is `permitted_with_conditions` at `3` but `permitted_qs` at `5.1`, a single
row with no conditions text).

**Cross-check outcome, all 78 items across 5 labels:**
- 43 AGREES at HIGH confidence -> filled.
- 12 UNSET (draft declined; eu_fip rows were in fact determinable in most
  cases, but the draft's own discipline was respected -- left UNSET, not
  filled from what the rows show).
- 6 DISAGREES, all the same shape: draft assumed a plain food-ingredient
  resolution (e.g. "Milk and Milk Solids", "Spices and Condiments",
  "Hazelnut Pieces/Paste"), but the actual resolver returned
  `unknown`/`ambiguous` for these -- correct expected headline is
  `unresolved`, not `out_of_scope`, since the golden set scores what the
  engine should do GIVEN the actual (already-fixed) resolution output, not
  a hypothetical perfect one. Left UNSET (DISAGREES items are never filled).
- 8 MISSED: 6 container/compound nodes (e.g. "Seasoning", "Outer Layer")
  never given their own row, plus 2 duplicate-named product-level items
  (EU_productmain's second "Corn maltodextrin" and second "Salt") the
  draft's single row didn't distinguish -- the exact multi-occurrence
  pitfall the draft itself warned about for Ice-creammain's maltitol,
  just missed for its own case.
- 1 real extraction gap found in the process, not a golden-authoring
  matter: Khusmain's `declaration_verbatim` literally starts "Sugar,
  Water, Citric Acid…" but no `item` was ever extracted for "Sugar" --
  confirmed by reading the raw extraction output, not itemized in the
  golden set since there is no item_id to attach it to.

Every filled item's `headline`/`eu_canonical_id`/`blocked` was derived from
the actual eu_fip row(s) at the (now-corrected) pin via
`src.rules.row_selection.select_row` -- e.g. a generic draft "permitted"
claim was resolved to the precise `permitted_with_conditions` /
`permitted_qs` / `permitted_with_limit` enum the row objectively supports,
never asserted beyond what the draft itself claimed HIGH confidence in.

Each golden file now carries a `truth_disclosure` header (`review_status`,
`note`) matching `data/golden/category_truth.json`'s own `_meta` disclosure
pattern -- AI-drafted, cross-checked against eu_fip, NOT verified by a
regulatory expert. Do not treat the 43 filled values as validated ground
truth without independent Annex II review.

## F-20 — ChromaDB adopted as a store option, still not for accuracy

F-11 measured ChromaDB once (identical ranking to numpy, at extra cost) and
did not adopt it. That code (`src/category/chroma_store.py`) was never
deleted -- still present, still passing its own tests, untouched since one
commit on 2026-08-02. Re-adopted now for STORE PROPERTIES (persistence,
metadata filtering), not accuracy -- F-11's conclusion is unchanged and
re-confirmed: a fresh top-3 comparison at real corpus scale (155 documents,
768-dim cached vectors, a real cached query vector) still returns identical
rankings between `embedding_scores` (numpy) and `chroma_scores` (Chroma).

`settings.RETRIEVAL_STORE` ("numpy" default | "chroma") is now wired into
the REAL pipeline (`src/graph/pipeline.py`'s `build_pipeline` ->
`src/graph/nodes.py`'s `make_classify_node`), not just the ablation
harness -- "numpy" leaves `chroma_index=None` and nothing about
`classify_node`'s existing behaviour changes. "chroma" builds a
`PersistentClient` index at `settings.CHROMA_PERSIST_DIR` from the SAME
cached `category_embeddings.json` vectors -- confirmed zero API calls to
build (155/155 cache hits) and confirmed to reuse the persisted index
across repeated `build_pipeline()` calls without rebuilding, via a
corpus_hash (codes + document text + model id + dimensionality) stored in
the collection's own metadata; a mismatch triggers a full rebuild, logged
at INFO, itself zero-API-call and sub-second.

Test coverage extended (`tests/test_category.py`): the existing two
ephemeral-path tests (F-11-era, still passing) plus four new persist-path
tests -- numpy/chroma agree on a real top-3, a matching corpus_hash reuses
rather than rebuilds, a changed corpus_hash rebuilds and logs why, and
`model_id`/`dimensionality` are required (not silently skipped) when
`persist_path` is given.

## F-21 — functional-class pre-filter: no effect alone, one flip combined

Proposed as a genuinely different mechanism from `src/category/filter.py`'s
existing `permitted_in()`: that one re-ranks an ALREADY-RETRIEVED top-10 by
exact additive-ID permission (a post-hoc filter, never removes a
candidate); the new `functional_class_candidates()` restricts the
candidate pool by a coarser signal (shared Codex functional class) BEFORE
`classify()` ever ranks anything -- a genuine pre-filter. Implemented as a
new orthogonal `ExperimentConfig.functional_class_filter` flag
(`scripts/classify_category.py`'s `_classify_file`), so the two compose
without silently double-filtering: config's own docstring and
`filter.py`'s module docstring both document which does what.

Scored three configs against the existing 17-component eval set
(`category_truth.json`), same `truth_version` (`7a2f5babea7c`), cached
embeddings only -- verified 155/155 corpus + 22/22 query embeddings were
already cached (zero API calls) before running anything:

    A. with-component-name (intersection filter only, the baseline)   0.5294 / 0.8235 / 0.6569
    B. functional-class-filter-only (pre-filter only)                 0.5294 / 0.8235 / 0.6569
    C. both-filters                                                   0.5882 / 0.8235 / 0.6863

**Config B: no aggregate effect, but NOT inert.** Diffing every one of the
17 scored rows between A and B shows the pre-filter genuinely changes
predictions -- it flips Parle-Gmain's product-scope query from rank 2
("15") to rank 1 ("7.2", the correct answer, the same "Group I permits it
at 7.2, not at 15" case this project has documented since F-09) -- but it
ALSO flips Chipsmain's "Seasoning" component from rank 1 ("12.2.2",
correct) to rank 2, demoting the right answer by removing a competing
candidate that the intersection filter would otherwise have needed to
correctly re-rank past. One gain, one loss, net zero at n=17.

**Config C: one net flip, mechanistically explained, not a broad win.**
Diffing A against C shows exactly the SAME single change as A-to-B's gain
(Parle-Gmain 15->7.2) with NO corresponding loss -- Chipsmain's "Seasoning"
survives here because the intersection filter (still on) correctly
re-ranks "12.2.2" back to rank 1 via genuine additive-permission matching,
something the pre-filter alone had disturbed. The two filters are
complementary on this one case: the pre-filter helps exactly where the
intersection filter has nothing to work with (Parle-Gmain's product query
has an EMPTY exact-additive-ID intersection, `permitted=False` for every
top-3 candidate in both A and C -- the flip is caused entirely by "15"
being removed from candidacy, not by any re-ranking), and the intersection
filter fixes exactly where the pre-filter alone gets it wrong. The
resulting 0.5294 -> 0.5882 recall@1 change is ONE flipped component out of
17, not a broad or robust win -- squarely inside the "differences under
~0.10 are one or two label-components" noise band F-13 already established
for this eval set size.

**Conclusion: do not adopt on this evidence.** The mechanism is real and
explained, but n=17 cannot distinguish "genuinely useful in this specific
configuration" from "happened to help once here." Logged as a measured,
NOT-adopted alternative, the same discipline F-11 recorded ChromaDB with --
`functional_class_filter` stays off by default
(`ExperimentConfig.functional_class_filter: bool = False`), available only
as the `functional-class-filter-only`/`both-filters` named configs for a
future, larger eval set to revisit.

## F-22 — EU Agri-Food Fraud Network monthly report: a candidate structured enforcement source, not yet pursued

Surfaced incidentally while verifying the Tavily integration for the
horizon news lane (`src/horizon/search.py`, `src/horizon/news.py`) against
the live API: a general web search for an additive can return the
European Commission's own **Agri-Food Fraud Network monthly summary
report** (food.ec.europa.eu), which is a genuinely different KIND of
source from either the curated EFSA dataset (`src/horizon/lane.py`) or
retrieved trade press (`src/horizon/news.py`) -- it is not an opinion
about whether an additive MIGHT become a problem, it is a record that one
already WAS one: actual E-number interceptions, broken down by origin
country and notifying country, including undeclared colours and (in one
month's report) an ethylene-oxide case originating from India.

Why this is worth recording separately rather than folding into the news
lane as just another retrieved result: it is enforcement data, not
commentary, and it is directly relevant to this project's own users (an
Indian manufacturer checking EU compliance) in a way EFSA opinions and
trade press are not -- "was this additive/origin combination actually
intercepted at the EU border" is a more actionable question than "is EFSA
reviewing this additive." It also appears to be **structured** (an origin/
substance/notifying-country table published monthly), not prose to
retrieve-and-classify the way `src/horizon/news.py`'s pipeline handles a
Tavily hit -- which suggests it wants its OWN dedicated parser reading the
Commission's report directly (the same shape of decision RASFF itself
prompted in the horizon-news design report: query a structured EU source
directly rather than hope a general web search lands on it), not a
classify-and-quote treatment built for unstructured retrieval.

**Not pursued.** No code reads this report, no schema represents an
interception record, and no test exercises it -- this finding exists to
record that it was seen and looks promising, not to claim it was
evaluated. Candidate future work: a dedicated parser/schema for Agri-Food
Fraud Network reports, sitting alongside (not inside) both
`src/horizon/lane.py`'s curated EFSA source and `src/horizon/news.py`'s
retrieved-press source, the same three-way separate-provenance shape the
horizon-news design report argued for between the first two.