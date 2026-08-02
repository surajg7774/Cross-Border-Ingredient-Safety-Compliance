# EU Additive Compliance Checker

A capstone project that checks food product labels against EU food additive
regulations. Given a photo of a product label, it extracts the declared
ingredients and additives, resolves them against reference EU additive data,
and reports any compliance issues — separating raw extraction (what the label
says) from compliance judgment (what the regulations require).

## Setup

1. Install [uv](https://docs.astral.sh/uv/) if you haven't already.
2. Install dependencies:

   ```
   uv sync
   ```

3. Copy `.env.example` to `.env` and fill in your Google API key and model
   IDs (verify current free-tier model IDs in Google AI Studio first).

4. Run extraction on a single label image:

   ```
   uv run python scripts/extract.py data/labels/foo.jpg
   uv run python scripts/extract.py data/labels/foo.jpg --model gemini-2.5-flash
   ```

   Prints the gate verdict, detected/selected languages, and an indented
   tree of extracted items, and writes the full JSON result to
   `data/outputs/extraction/foo.json`.

5. Compare PRIMARY_MODEL against SECONDARY_MODEL over every image in
   `data/labels/` (no golden data needed yet — the two models are compared
   against each other):

   ```
   uv run python scripts/compare_models.py
   ```

   Prints a per-image table (item counts, items found by only one model,
   `declared_code` disagreements) and total wall time per model, and writes
   `data/outputs/comparison.csv`.

6. Bootstrap and score against a golden set (see "Scoring against the golden
   set" below for what the numbers mean).

7. Run tests:

   ```
   uv run pytest
   ```

8. Lint:

   ```
   uv run ruff check .
   ```

## Scoring against the golden set

1. Run extraction over your label images (step 4 above) so
   `data/outputs/extraction/` is populated.
2. Bootstrap `data/golden/extraction/` from those outputs — this only
   copies files that aren't already in `data/golden/extraction/`, so it's
   safe to re-run and will never clobber a file you've hand-corrected:

   ```
   uv run python scripts/init_golden.py
   ```

3. Hand-correct each copy in `data/golden/extraction/` against the actual
   label photo — fix any wrong codes, missed items, or bad nesting. This is
   your ground truth.
4. Score the current `data/outputs/extraction/` against it:

   ```
   uv run python scripts/score_golden.py
   ```

What the numbers mean, per label:

- **Code recall** — the fraction of additive codes in the golden file that
  the extractor also found (matched as a multiset, so a code repeated twice
  must be found twice). This is the primary metric: a missed additive code
  is the dangerous failure in this system, since it silently reads
  downstream as "no compliance issue."
- **Code precision** — the fraction of the extractor's codes that are
  actually in the golden file (extra/spurious codes).
- **Item recall** — the fraction of golden ingredient items (matched by
  name, or by code when there's no name) that the extractor also found.
  Recall only — over-extraction is fine, under-extraction isn't.
- **Structure accuracy** — of the items that matched, the fraction whose
  nesting depth and parent ingredient also matched.
- **Missing** — the specific codes present in the golden file but absent
  from the output; the most actionable column when recall isn't 1.0.

Labels where the gate stopped extraction entirely (no ingredients
declaration visible) are reported separately as "gate cases": pass/fail on
whether the output stopped for the same reason as the golden file, not
mixed into the metrics above.

The script exits with code 1 if aggregate code recall is below 1.0, so it
can be used as a regression check as prompts change.

## Resolving extracted items

Turns extraction output (what a label says) into canonical identities (what
those substances are). Deterministic — no LLM, no API calls, no network;
every decision is a lookup or a hard-coded rule against the reference data in
`data/reference/` (`codex_ins.json`, `functional_classes.json`,
`label_aliases.json`, `eu_fip.json`).

```
uv run python scripts/resolve.py data/outputs/extraction/Parle-Gmain.json
uv run python scripts/resolve.py --all
```

Prints a table (name/code, canonical INS, EU id, classification, method,
confidence, flags) and writes `data/outputs/resolution/<name>.json`.

The cascade, in order: an exact/parent match on `declared_code` if present,
else a strict-order name path (exact name → synonym → alias → food lexicon →
ambiguous-candidates → fuzzy, in that order — the food lexicon must sit above
fuzzy or "turmeric powder" fuzzy-matches an additive). A family guard then
forces classification to `"ambiguous"` for any non-exact-code match landing
in a family that differs by one suffix character (e.g. 472a–f) — a high
fuzzy score against one member of a look-alike family is noise, not
evidence. Classification (`additive` / `food_ingredient` / `flavouring` /
`enzyme` / `unknown` / `ambiguous`) follows, then the EU crosswalk
(`canonical_ins` → `eu_canonical_id`): exact match, then parent, then a
name-based match within the family for cases like Codex `470(i)` → EU
`470a` (flagged `crosswalk_by_name`, confidence capped at 0.75 — this is
inference, not a lookup). Enzymes (Codex 1101 series) and flavourings are
expected to be absent from the EU list — that's `eu_canonical_id: null` with
no flag, not a compliance failure.

## Stage boundaries

Each stage reads the previous stage's JSON from disk and writes its own.
A stage may import its own schemas and the previous stage's schemas —
nothing else. `src/resolve/` must never import from `src/extractors/`.
Later stages reference extraction items by `item_id` rather than copying
or subclassing `ExtractedItem`, so adding a field to extraction cannot
break resolution.

## Project status

- [x] Step 0 — Scaffolding: project structure, environment, dependencies
- [x] Step 1 — Extraction: pull additive data from label images
- [x] Step 2 — Resolver: match extracted names against EU additive reference data
- [ ] Step 3 — Compliance logic: flag violations against EU regulations
- [ ] Step 4 — Evaluation: compare against golden hand-corrected labels
- [ ] Step 5 — Reporting / CLI polish

### Notes

Crop+upscale to the detected ingredients panel is **off by default**
(`run_extraction(..., crop=False)`, `scripts/extract.py --crop` to enable).
Measured across 10 labels with `gemini-flash-lite-latest`, it produced
identical extraction on every label where it engaged, and on one label
(`Chocolateraw.jpeg`) it mis-located the panel and returned 0 items where
skipping the crop returned 25 items including all 6 additive codes. No
measured recall gain, one measured total failure — hence off by default.
