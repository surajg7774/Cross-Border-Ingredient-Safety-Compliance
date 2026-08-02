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

## F-06 — Category retrieval: query text matters more than retrieval method
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
Hypothesis (motivated by F-06's 0.7847 mean pairwise similarity across 155
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
treated 0.78 as "the ceiling" (F-06) was unsound.

Decision: corpus_mode stays "full" (its existing default); with-component-
name remains the operating config (F-06). strip-framing's small gain is
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
+0.17 on BOTH dense and sparse retrieval (F-06), suggesting literal token
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

### Conclusion across F-06, F-13 and F-14

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