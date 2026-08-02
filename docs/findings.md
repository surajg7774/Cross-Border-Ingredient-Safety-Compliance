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