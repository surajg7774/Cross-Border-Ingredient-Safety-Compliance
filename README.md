# EU Additive Compliance Checker

Given a photo of a food product label (or pasted ingredients text), this
system extracts the declared ingredients and additives, identifies each one
against reference EU and Codex data, and reports whether the product's
additives are permitted in the food category it belongs to under EU
Regulation 1333/2008 — citing the source and any conditions for every
verdict, never a bare yes/no. It's built for someone doing that check by
hand today: a compliance reviewer, a food manufacturer or importer
preparing a label, or anyone who needs a defensible answer with a paper
trail, not just a guess.

## How this works

Models sit at the **edges**: one reads the label photo, one suggests which
EU food category a component belongs to, one rephrases the finished result
in plain English. None of them decides whether an additive is legal.

The **middle is a deterministic lookup**: identity resolution and the
compliance verdict are table joins and hard-coded rules against reference
data (`src/resolve/`, `src/rules/`) — no model call, no confidence score,
same input always gives the same output.

Between the two sits a **mandatory human checkpoint**, at the one place
measurement showed automation wasn't good enough to skip: confirming which
food category applies. Everything downstream — the verdict, substitutes,
horizon signals — depends on that confirmation.

### Why the human gate is mandatory

Automatic category retrieval is measured at recall@1 = 0.53 and recall@3 =
0.82 (`docs/build_log.md`, `docs/findings.md` F-06/F-07/F-13/F-14) — on the best
of more than twenty retrieval configurations tried (four retrieval methods,
seven corpus constructions, two diversification/fusion strategies). None
beat the original setup. The conclusion, after that work: the ambiguity is
in the ingredient declarations themselves, not the retrieval method — more
tuning was not expected to move the number, so retrieval work stopped and
the number is treated as a property of the task.

Since food category is outcome-determining (Annex II permissions are
category-dependent), a wrong category silently produces a wrong verdict.
So the confirmation screen never restricts a reviewer to the three
retrieved candidates: alongside the top-3 radio choice, it offers a
searchable dropdown over **all 155 EU food categories**
(`app.py:938-941`). That override matters concretely — at recall@3 = 0.82,
18% of components have the correct category outside the top-3 entirely, so
without it those components could never be confirmed correctly no matter
what the reviewer picked.

## The six stages

Each stage reads the previous stage's JSON and writes its own; a stage
imports only its own schemas and the previous stage's, so adding a field
upstream can't silently break something downstream.

1. **Extraction** — `src/extractors/` (a label photo, via Gemini) and
   `src/extract/` (pasted declaration text, deterministic parsing) — two
   different directories for two different input paths into the same
   extracted-items shape. Emits observations only: what the label says,
   no compliance judgment.
2. **Resolution** — `src/resolve/` — a deterministic cascade (exact code →
   name/synonym/alias/food-lexicon → fuzzy, family-guarded against
   look-alike suffixes) turns a declared name into a canonical identity.
   No model call.
3. **Category classification + confirmation** — `src/category/` retrieves
   candidate EU food categories by embedding similarity; a human confirms
   one before anything downstream runs (see above).
4. **Compliance verdict** — `src/rules/` — a deterministic Annex II lookup:
   (additive, confirmed category) → permitted / conditional / blocked,
   with a source citation and the conditions text, verbatim. No model, no
   confidence score.
5. **Advisory lanes** — `src/substitutes/` (replacement candidates for
   anything blocked) and `src/horizon/` (EFSA signals on additives under
   active review) run off the verdict, in parallel. Both deterministic;
   neither can change the verdict.
6. **Reporting** — `src/report/` — a model rephrases the already-finished
   assessment into a plain-English summary (`narrator.py`); CSV/PDF/JSON
   export and email stay deterministic templating (`export.py`,
   `email.py`).

`src/graph/` orchestrates 1–6 as a LangGraph pipeline (a `Send` fan-out for
per-component classification, `interrupt()`/`Command(resume=...)` at step
3, parallel branches at step 5). `src/ui/` is the Streamlit front end
driving the same pipeline. `src/agent/` is a separate, on-demand
LangGraph tool-calling loop that investigates review-queue items a human
requests help on — never part of the automatic six-stage run.

### Extraction: crop+upscale is off by default

Cropping to the detected ingredients panel and upscaling was added as an
accuracy step, on the assumption that small print needs more pixels.
Measured across 10 labels with `gemini-flash-lite-latest`, it produced
identical extraction on every label where it engaged, and on one label
(`Chocolateraw.jpeg`) it mis-located the panel and returned 0 items where
skipping the crop returned 25 items including all 6 additive codes. No
measured recall gain, one measured total failure — hence off by default
(`run_extraction(..., crop=False)`; `scripts/extract.py --crop` to enable).

## Quickstart

1. Install [uv](https://docs.astral.sh/uv/), then install dependencies:

   ```
   uv sync
   ```

2. Copy `.env.example` to `.env` and fill in `GOOGLE_API_KEY`,
   `PRIMARY_MODEL`, and `SECONDARY_MODEL` (verify current free-tier model
   IDs at [aistudio.google.com](https://aistudio.google.com/) first — the
   placeholders in `.env.example` are not guaranteed current).

3. Run the app:

   ```
   uv run streamlit run app.py
   ```

4. Or run the same LangGraph pipeline headlessly:

   ```
   uv run python scripts/run_pipeline.py data/labels/Parle-Gmain.jpeg
   uv run python scripts/run_pipeline.py --text "Sugar, Colour (INS 143)" --name Test
   uv run python scripts/run_pipeline.py data/labels/Khusmain.png --auto-confirm
   ```

   Without `--auto-confirm`, a paused run prints the retrieved top-3 per
   component and prompts for a choice (blank = accept rank-1) — the CLI
   equivalent of the app's confirmation screen — then resumes.

5. Run tests:

   ```
   uv run pytest
   ```

6. Lint:

   ```
   uv run ruff check .
   ```

### Extraction is cached

Every extraction call (`src/extractors/gemini.py`) is cached to disk under
`.cache/`, keyed by a hash of the image bytes, model ID, pass name, and
prompt text — so re-running the same image against the same model and
prompt is a cache hit, not a new API call. This is gitignored, so a fresh
clone starts with an empty cache; once you've run a label once, re-running
it (or re-running the test suite, which exercises real cached fixtures)
costs no quota.

## Measured results

| What | Measured | Result |
|---|---|---|
| Category retrieval (recall@1 / recall@3 / MRR, n=17 across 9 labels, DRAFT/unverified truth) | `docs/findings.md` F-06/F-07/F-13/F-14 | **0.53 / 0.82 / 0.66** — the operating config; best of 20+ configurations tried |
| Extraction / resolution accuracy | golden sets exist (`data/golden/extraction/`, `data/golden/resolution/`) and scoring scripts exist (`scripts/score_golden.py`, `scripts/score_resolution.py`) | no aggregate score is committed — reproduce locally |
| Agentic review-queue resolver (n=22, hand-filled truth) | `docs/findings.md` F-16 | **15 correct, 0 wrong, 4 correctly declined, 3 wrongly declined** |
| End-to-end pipeline, real label image, real API calls | — | **not measured** — exercised only structurally, via mocked tests (`docs/build_log.md`) |

The agent's three wrongly-declined cases are infrastructure limits (one
`search_web` quota exhaustion, two hit the 5-step cap), not reasoning
failures — every genuinely ambiguous item (two "modified cornstarch"
entries, calcium phosphate, Stevia) was correctly declined rather than
guessed.

## Limitations

- **Category ground truth is small and unverified.** n=17 label-components
  across 9 labels, AI-drafted and not independently re-checked against the
  EU category text — directional for comparing configurations, not a
  validated accuracy claim.
- **Retrieval work is stopped, deliberately.** recall@1 = 0.53 is treated
  as a property of ambiguous ingredient declarations, not a defect still
  being chased — correctness is protected by the mandatory human
  confirmation instead (see above), not by further tuning.
- **Multi-query retrieval is implemented but unscored.** It hit the Gemini
  free-tier daily quota mid-run and was left as open work.
- **The LangGraph pipeline has never been run end-to-end against a real
  label image with live API calls** — only exercised structurally through
  the mocked test suite.
- **Regulatory horizon coverage is partial.** Automated ingestion from
  OpenFoodTox was abandoned (no E-number field to key on), so the horizon
  lane runs off a smaller, hand-curated signal set.
- **Crop+upscale accuracy was measured on 10 labels**, one of which failed
  completely — too small a sample to generalize beyond "off by default."

## Further reading

- [`PROJECT_DOCUMENTATION.md`](PROJECT_DOCUMENTATION.md) — the full project
  write-up: what the system does, the data shapes at every stage, every
  experiment, and what went wrong along the way.
- [`docs/findings.md`](docs/findings.md) — dated, numbered experiment
  write-ups (retrieval, corpus construction, the agent resolver, and more).
- [`docs/build_log.md`](docs/build_log.md) — the full build log these
  numbers and decisions are drawn from.
- Architecture diagrams (`.drawio`, open in [diagrams.net](https://app.diagrams.net/)):
  [`docs/eu_compliance_architecture.drawio`](docs/eu_compliance_architecture.drawio),
  [`docs/eu_compliance_data_and_experiments.drawio`](docs/eu_compliance_data_and_experiments.drawio),
  [`docs/eu_compliance_walkthrough.drawio`](docs/eu_compliance_walkthrough.drawio).
