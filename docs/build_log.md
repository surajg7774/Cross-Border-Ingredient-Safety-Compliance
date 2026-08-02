# Build log

## 2026-08-03 — Rate-limit handling for the agent loop

### Why

MEASURED: `scripts/agent_review.py --all` crashed with a 429 --
`GenerateRequestsPerMinutePerProjectPerModel-FreeTier`, quotaValue 15, on
`gemini-3.5-flash-lite` (the concrete model `gemini-flash-lite-latest`
resolves to). A per-minute limit, not the exhausted daily one -- each
review item makes several model round trips (tool calls, then a final
answer), so 22 items back-to-back exceeds 15 requests/minute easily. Our
own fixed exponential backoff (starting at 2s) could undershoot the
server's actual ask by a wide margin.

### What changed

- **`_retry_delay_seconds`** (new, duplicated in both
  `src/extractors/gemini.py` and `src/agent/resolver_agent.py` -- same
  stage-independence convention every other small retry/parsing helper in
  this project already follows). Parses the server's own
  `RetryInfo.retryDelay` out of a 429's error details and sleeps that
  duration plus a 1s margin; falls back to the existing exponential
  backoff only when the detail is absent (a plain 500/503 usually carries
  no `RetryInfo` at all). Confirmed the exact error shape LIVE before
  writing this -- deliberately spammed real requests against
  `SECONDARY_MODEL` until hitting the real 429, not guessed from
  documentation: `exc.details` is
  `{"error": {..., "details": [..., {"@type":
  ".../google.rpc.RetryInfo", "retryDelay": "49s"}]}}`. Applied to both
  places that retry model calls: `GeminiExtractor._call_model` (every
  extraction/gate call in the project) and `GeminiLLM._call` (the agent's
  own tool-calling loop) -- `search_web`'s own isolated grounded call
  (`src/agent/tools.py`) is deliberately left as-is: it already degrades
  to `available=False` immediately on any failure by design, and retrying
  there would work against the "keep the agent loop moving" intent (see
  the previous entry). `src/report/narrator.py`/`src/category/embedder.py`
  share the same fixed-backoff gap and were NOT touched -- out of this
  task's explicit scope, flagged here as a candidate for a follow-up.
- **`scripts/agent_review.py`** -- `--delay SECONDS` (default 5) paces the
  loop between review items so a long `--all` run stays under the
  per-minute ceiling BY CONSTRUCTION, not by recovering from a crash.
  Prints an estimated total runtime before a `--all` run starts (item
  count x (`--delay` + an empirical ~15s/item processing estimate),
  labelled as an estimate, not a guarantee). `--force` reprocesses items
  that already have a proposal; without it, `_scan_target` checks
  `data/outputs/agent/<name>.json` FIRST and skips any item already
  present there -- RESUME, not restart. Output is now written
  INCREMENTALLY after every single item (`_write_agent_output`), not
  batched at the end of a file's loop, so a crash partway through a
  9-item file only loses the one item in flight, not the whole file.
  `resolved_model_id` (the CONCRETE model version the server actually
  used, e.g. `"gemini-3.5-flash-lite"`, read off
  `response.model_version`) is recorded in the output JSON alongside the
  requested `model_id` (the alias, e.g. `"gemini-flash-lite-latest"`) --
  `GeminiLLM.resolved_model_id` is set after every successful call and
  read by the CLI after `resolve_review_item()` returns.

### Tests added

- `tests/test_gemini_extractor_retry.py` (4) -- `_retry_delay_seconds`
  parses the real captured error shape and returns `None` when
  `RetryInfo` is absent; a 429 with `retryDelay` sleeps exactly that
  duration + margin (mocked `time.sleep`, mocked client, no network); a
  429/500 without `retryDelay` falls back to `INITIAL_BACKOFF_SECONDS`.
- `tests/test_agent.py` (+2) -- the same two behaviours for `GeminiLLM`,
  plus confirms `resolved_model_id` is captured from the mocked
  response's `model_version`.
- `tests/test_agent_review_cli.py` (4, new file) -- `_scan_target` against
  `tmp_path` fixtures (settings monkeypatched, same pattern
  `tests/test_email.py` already uses): an item already present in the
  output file is skipped and its existing proposal carried forward;
  `force=True` reprocesses everything and discards prior proposals; no
  existing output means everything is pending; every item already done
  means nothing is pending.

**Total: 262 tests passing** (252 before this task + 10 new). `ruff check
.` unchanged: 27 pre-existing errors, all in `src/codex/run_codex.py`,
unrelated to this task.

### Verified, not just written

- Deliberately spammed `SECONDARY_MODEL` with rapid requests to trigger a
  REAL 429 before writing any code, to see the exact error shape rather
  than assume it -- confirmed `quotaValue: "15"`,
  `quotaId: "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"`, and a
  `RetryInfo.retryDelay` field, matching the task's own measured report
  exactly.
- Confirmed `response.model_version` is the resolved concrete model
  (requesting the alias `"gemini-flash-lite-latest"` returned
  `"gemini-3.5-flash-lite"`) against the live API before wiring it in.
- **Ran `scripts/agent_review.py --all --delay 3` for real, start to
  finish**, against the actual 18 still-pending review items across 5
  files (4 already done from the prior entry's testing were correctly
  skipped first). The printed estimate
  ("18 review item(s)... ROUGHLY ~324s (~5.4 min)") was accurate. The run
  completed with ZERO crashes despite `search_web` hitting real quota
  exhaustion repeatedly throughout (handled exactly as designed --
  `available: false`, the agent kept going or declined cleanly every
  time) -- a genuine `GraphTestChips` and a genuine `Noodlesraw` item both
  hit the step cap for real during this run, not simulated. Every touched
  file's `resolved_model_id` was written correctly.
- **Re-ran `scripts/agent_review.py --all` immediately after** -- printed
  "Nothing to do -- every review item already has a proposal", confirming
  full resume/skip correctness against real output files, not just the
  unit tests.
- `uv run pytest` -- 262 passed. `uv run ruff check .` -- unchanged
  pre-existing count.

### Anything unexpected

- None of the task's four numbered requirements needed any trade-off or
  reinterpretation -- the measured error report matched the live-captured
  shape exactly, and the existing `_scan_target`/incremental-write design
  (needed anyway for correctness) turned out to make the ETA estimate MORE
  accurate for free, since it naturally excludes already-done items rather
  than counting the full queue.

## 2026-08-02 — Agentic review-queue resolver (src/agent/)

### Why

The pipeline ends with items a human must resolve -- 22 of them across the
real test set (scanned directly from data/outputs/verdict/*.json's
headline=="unresolved" items, excluding one scratch/test label; see "What
changed" below), and each needs a genuinely DIFFERENT approach: 17
candidate modified starches need narrowing, Stevia needs narrowing among 4
production methods, INS 924 is absent from codex_ins.json entirely and
needs an external lookup, "Hazelnut Pieces" needs a food-vs-additive
judgement, eight spice-powder items need a lexicon judgement the exact
label wording didn't already match. A fixed pipeline runs the same steps
on every item regardless. This is the one place in the system where TOOL
SELECTION varies per item -- the model choosing which of six tools fits
THIS item, and how many times, is what makes it an agent rather than
another chain, per the task's own framing.

### Safety

Same discipline as category confirmation: the agent PROPOSES, a human
CONFIRMS. `resolve_review_item()` never writes to a verdict, never changes
`eu_canonical_id` on a `ResolvedItem`, and never removes an item from the
review queue by itself -- verified directly (see Tests). `app.py`'s
Accept/Reject buttons are the only things that ever apply a proposal, and
Accept touches exactly the ONE item's resolution, never any other.

### What changed

- **`src/agent/tools.py`** -- six tools, each wrapping an existing
  reference dataset this project already loads elsewhere (`codex_ins.json`,
  `eu_fip.json`, `label_aliases.json`) or the caller's own already-computed
  context; none reimplements resolution logic `src/resolve/resolver.py`
  already owns. `lookup_codex`, `lookup_eu_fip`, `check_food_lexicon`,
  `list_family_members`, `get_product_context`, `search_web`. Every tool
  returns plain data with a `source` field.
  `list_family_members` takes a LIST of candidate codes, not a bare parent
  code to prefix-scan from, per its own docstring: several of this
  project's real ambiguous families (the 17 modified-starch candidates)
  share no numeric prefix at all, only a curated `label_aliases.json`
  `ambiguous[]` entry -- prefix derivation would miss them; the item's own
  `candidates` list is what actually works for every real case.
  `search_web` uses Gemini's own built-in Google Search grounding
  (`google.genai` `types.Tool(google_search=...)`) rather than adding a new
  search-provider dependency this project has no existing integration for
  -- the same client/API key every other stage already calls. Verified
  live that it degrades to `{"available": False, ...}` on quota exhaustion
  rather than raising (see Verified) -- this happened for real, unprompted,
  during testing.
- **`src/agent/resolver_agent.py`** -- `resolve_review_item(item, refs,
  llm, tools) -> AgentProposal`, a small LangGraph `StateGraph` (agent node
  <-> tools node, step-capped at `MAX_STEPS = 5`). `AgentProposal` carries
  `declined: bool` as a first-class outcome, not an error path --
  DECLINING IS A SUCCESS when the label genuinely doesn't determine an
  answer, and the prompt says so explicitly. A proposal with an empty
  `evidence` list, or one where no tool was ever called at all, is forced
  to `declined=True` regardless of what the model itself claimed
  (`_parse_proposal`). No LangChain: the `LLM` protocol
  (`start`/`send_tool_results` -> `LLMTurn`) and `GeminiLLM` talk to
  `google.genai`'s native function calling directly, the same pattern
  `src/extractors/gemini.py`/`src/report/narrator.py` already use for
  retry/backoff, duplicated rather than imported for the same
  stage-independence reason those two already duplicate it from each
  other.
- **`scripts/agent_review.py`** -- CLI: one verdict file or `--all`.
  Prints name/proposal/confidence/reasoning/tool-trace per item, writes
  `data/outputs/agent/<name>.json`. `--model` defaults to
  `SECONDARY_MODEL`, not `PRIMARY_MODEL` (quota-exhausted). Added
  `AGENT_OUTPUT_DIR`/`AGENT_TRUTH_PATH` to `config.py`.
- **`app.py`** review-queue section: the queue is now split by headline --
  `"unresolved"` items (identity resolution) get a NEW
  `render_agent_review_queue` with "Ask the assistant" per item and for
  the whole queue, showing the proposal's reasoning/evidence/tool-trace
  plus Accept/Reject; `"category_unknown"` items are untouched, still
  rendered by the existing `components.render_review_queue` (a food
  category confirmation is a different flow entirely). Accepting a
  proposal WITH a proposed INS code re-runs the SAME `resolve_items`
  cascade with `declared_code` overridden to that code -- so
  `eu_canonical_id`/functional classes are derived by the real cascade,
  never hand-computed in `app.py`. Accepting a proposal with no code
  (e.g. `food_ingredient`) sets the classification directly. Either way
  `_recompute_verdict_after_resolution_change` re-runs `evaluate()` +
  substitutes + horizon (all free, no API call) for the WHOLE verdict, but
  deliberately does NOT re-run narration, which costs a real model call --
  the deterministic `verdict.summary` shown above it is refreshed, which
  is the part that matters. The agent is NOT in the main graph/pipeline
  path -- every entry point is on-demand, after a verdict already exists.
- **`data/golden/agent_truth.json`** -- generated (not hand-written) from
  the real 22-item scan described above: label, item_id, name_as_declared,
  declared_code, review_reason, and a blank `answer: {outcome,
  canonical_ins, classification}` for the user to fill in by hand.
  `GraphTestChips` (byte-identical extraction to `Chipsmain` -- scratch
  data from the earlier LangGraph task, not a curated label) is excluded
  and the exclusion is recorded in `_meta`.
- **`scripts/score_agent.py`** -- reports `correct` / `wrong` /
  `correctly_declined` / `wrongly_declined`, always separately, never
  combined into one accuracy number (a wrong proposal is worse than a
  decline, since a human may accept it without checking). Skips items
  whose `answer.outcome` is still null (not yet reviewed) rather than
  scoring them as misses. Appends one row to `data/outputs/experiments.csv`
  with `stage="agent"` -- the file already had
  `scripts/score_category.py`'s own header; `_ensure_agent_columns`
  migrates it in place (every existing row rewritten with the four new
  columns blank) rather than starting a second, incompatible file, the
  first time it's missing them.

### Tests added

- `tests/test_agent.py` (6, all required by the task) -- both the LLM and
  every tool are mocked (`MockLLM` scripts a fixed turn sequence;
  `ToolSpec.fn` is a `Mock()`), no API calls anywhere in the file: the
  agent calls `list_family_members` for an ambiguous item (asserted via
  `Mock.assert_called_once_with`); calls `check_food_lexicon` for an
  unresolved plain-food item; a proposal with empty `evidence` is rejected
  and forced to `declined=True` regardless of what the model claimed; the
  step cap halts a loop that keeps requesting tools at exactly
  `MAX_STEPS` model calls, never more; the agent never mutates the input
  `item` dict (asserted via a `copy.deepcopy` snapshot); a declined
  proposal is returned normally, not raised.

**Total: 252 tests passing** (246 before this task + 6 new). `ruff check .`
unchanged: 27 pre-existing errors, all in `src/codex/run_codex.py`,
unrelated to this task.

### Verified, not just written

- Checked the INSTALLED `google-genai` (2.16.0) function-calling shape
  live against the real API BEFORE writing `resolver_agent.py`: confirmed
  `response.function_calls`, `types.Part.from_function_response`, and that
  Gemini 2.5 REJECTS a hand-reconstructed function-call turn missing its
  `thought_signature` (400 INVALID_ARGUMENT, reproduced directly) -- this
  is why `GeminiLLM` preserves the SDK's own `response.candidates[0].content`
  object across turns rather than re-serializing a plain-dict message
  history, and why the `LLM` protocol is `start()`/`send_tool_results()`
  (conversation state owned by the adapter) rather than a stateless
  `messages` list threaded through LangGraph state.
- Ran `resolve_review_item` against the REAL `SECONDARY_MODEL` (no mocks)
  for two real review-queue items: **INS 924** (Mixed) -- the agent tried
  `lookup_codex`, `lookup_eu_fip`, `search_web` (which returned
  `available=false, "quota exhausted"` -- a REAL quota hit during testing,
  handled exactly as designed, not simulated), then `check_food_lexicon`,
  and correctly DECLINED with real evidence. **Hazelnut Pieces**
  (Ice-creammain) -- the agent called `check_food_lexicon` on the full
  declared name, got a weak match, retried on its own initiative with just
  `"hazelnut"`, got an exact hit, and correctly proposed
  `food_ingredient`/high confidence.
- Ran `scripts/agent_review.py data/outputs/verdict/Mixed.json` end to end
  for real -- output matched the direct-call test above, written to
  `data/outputs/agent/Mixed.json`.
- `app.py`'s new UI logic verified via `AppTest` against REAL
  Ice-creammain extraction/resolution/category/verdict data (no full
  pipeline re-run needed -- loaded straight from
  `data/outputs/*/Ice-creammain.json`), with `resolve_review_item` mocked
  at its SOURCE module (`src.agent.resolver_agent.resolve_review_item`,
  not `app.resolve_review_item` -- the latter silently does nothing, since
  Streamlit re-executes app.py's imports fresh on every rerun, a mistake
  caught by cross-checking the mock's canned answer against what the REAL
  model would say for the same item before trusting the first pass).
  Confirmed: accepting a proposal WITHOUT a code (Hazelnut Pieces ->
  `food_ingredient`) moves that item's headline from `unresolved` to
  `out_of_scope`, updates ONLY that item's resolution (every other item's
  resolution dict compared byte-for-byte, unchanged), and records the
  decision. Accepting a proposal WITH a code (Stevia -> `960a`) re-derives
  `eu_canonical_id="960a"` matching a direct `resolve_items` call with that
  code, and produces headline `permitted_with_conditions`. Reject leaves
  the resolution completely unchanged (compared byte-for-byte) and records
  only the decision.
- `scripts/score_agent.py` run against the real (still-blank)
  `agent_truth.json`: correctly reports all 22 items as "not yet
  reviewed", scores nothing, and still appends a zero-count row --
  migrating `experiments.csv`'s header in place (existing `category`/
  `corpus_diagnostic` rows preserved, padded blank for the four new
  columns).
- `uv run pytest` -- 252 passed. `uv run ruff check .` -- unchanged
  pre-existing count.

### Anything unexpected

- **The empty-evidence rule caught a real near-miss during testing.**
  Early in manual testing, `patch("app.resolve_review_item", ...)` (the
  wrong mock target -- see above) let the REAL model run unmocked in what
  was meant to be an offline check; it happened to answer
  `food_ingredient` for Hazelnut Pieces either way, which is why the
  mistake wasn't obvious from the assertion result alone and needed a
  second look at *why* the test ran suspiciously fast for a live network
  call.
- **`search_web` hit real quota exhaustion unprompted during testing**,
  which turned out to be a genuine, useful test of the "must degrade, not
  raise" requirement rather than something that needed to be simulated.
- Did not run `scripts/agent_review.py --all` across the full 22-item test
  set for real (only the single-file `Mixed.json` run, plus two direct
  `resolve_review_item` calls) -- 22 items at up to 5 model calls each was
  judged not worth the time/quota cost given the mechanism was already
  validated end to end three separate ways (direct calls, the CLI, and the
  UI). `data/outputs/agent/` will fill in properly the first time someone
  runs `--all` for real.

### Why

The prior entry added `src/graph/` specifically to replace hand-rolled
pause/resume with LangGraph's `interrupt()`/`Command(resume=...)` -- but only
`scripts/run_pipeline.py` actually used it. `app.py` still ran its own
version of the thing the graph exists to replace: results stashed in
`st.session_state`, a `stage` marker, and a full script rerun to advance.
Streamlit's own flow -- show options, wait for a click, continue -- maps
directly onto `interrupt()`/`Command(resume=...)`, so this closes that gap.

### What changed

`app.py` now drives `src/graph/pipeline.py` as its PRIMARY path for both
input tabs, with the entire previous implementation kept, unmodified, as an
automatic fallback (see Constraints below). The three screens
(`render_input`, `render_category_confirmation`, `render_results`) needed
**zero changes** -- the new graph-backed functions populate the exact same
`st.session_state` keys, in the exact same types
(`extraction`: dict, `resolution`: `ResolutionResult`,
`category_results`: `list[CategoryResult]`, `verdict`/`preview_verdict`:
`ProductVerdict`, etc.) the legacy functions already did, so the render
functions and the "Back to category confirmation" button needed no
awareness of which backend produced them.

- **`_get_graph()`** -- `build_pipeline(auto_confirm=False)` behind
  `@st.cache_resource`, the same pattern already used three times in this
  file for the embedder/corpus/extractor. Verified (not assumed) that this
  actually holds across reruns: a throwaway `st.cache_resource`-decorated
  probe run through `AppTest.run()` three times in a row returned the
  identical object and incremented its call counter exactly once. A fresh
  checkpointer per rerun would have silently lost every paused thread.
- **`_invoke_fresh_graph_attempt()`** -- runs extract → resolve → classify →
  confirm on a NEW thread (`{run_id}-{attempt}`) each time it's called,
  reaching the SAME interrupt fresh. This is the one piece of real design
  work beyond wiring: verified directly (two throwaway scripts against the
  installed langgraph, before writing any app.py code) that
  `Command(resume=...)` resolves a paused checkpoint's interrupt exactly
  once -- invoking it a second time at the same checkpoint does NOT
  re-pause with a new value, it just returns the first resume's already-
  computed result. So re-confirming after "Back" needs a fresh thread, not
  a replay of the old one. This costs no extra API call: `GeminiExtractor`
  and `GeminiEmbedder` each cache by exact input on disk already, so
  identical input on a new thread is a cache hit.
- **`_finalise_graph(choices)`** -- resumes the current thread's interrupt
  (starting a fresh attempt first if this is a re-confirmation, via
  `graph_resumed`), then reads verdict/substitutes/horizon/narration
  straight off the returned state -- `src/graph/nodes.py` already ran
  evaluate/find_substitutes/find_horizon_signals/narrate. Only the
  DISPLAY-only steps `_finalise` already did outside of any node
  (additive-name backfill via `_enrich_additive_names`, the
  pre-confirmation preview verdict for the verdict-strip merge) are
  repeated here, unchanged, since nothing in `src/graph/` does UI display
  work.
- **`_ordered_category_results(result)`** -- the graph's
  `state["category"]` is a dict (keyed by component_label, merged via
  `Send` fan-out), not the declaration-ordered list the screens expect.
  Calls `build_queries` again -- pure, already imported, never rescored --
  purely to recover the same order the direct pipeline's list was already
  in.
- Category resume keys use `PRODUCT_SCOPE_KEY` (`"(product)"`, imported
  from `src.graph.nodes`), not `None`, to match `confirmed_categories`'
  own convention -- translated from `render_category_confirmation`'s
  existing `None`-keyed `choices` dict inside `_finalise_graph`, so
  `_apply_category_confirmations`/`render_category_confirmation` needed no
  changes either.

### Constraints honoured

- **`scripts/run_pipeline.py` and every standalone script are untouched.**
  Nothing in `src/graph/` changed; `app.py` only gained new call sites into
  it.
- **Fallback, not a crash, on any graph failure.** Every graph entry point
  is wrapped by a `_dispatch_*` function: `_dispatch_run_from_image`,
  `_dispatch_run_from_text`, `_dispatch_confirm`. Each tries the graph
  path, and on ANY exception (build failure, node error surfaced via
  `state["errors"]`, an unexpected re-interrupt) calls `_graph_fallback`
  (sets `graph_broken`, shown once via `st.warning`, so a systemic failure
  -- e.g. no `GOOGLE_API_KEY` -- doesn't retry and fail slowly on every
  click) and falls through to the ORIGINAL, unmodified
  `_run_pipeline_from_image`/`_run_pipeline_from_text`/`_finalise` in the
  same request. This works cleanly specifically because the graph path
  populates `st.session_state` in the legacy path's own types (see above)
  -- a graph success followed by a `_finalise_graph` failure can fall back
  to legacy `_finalise` mid-flow using data already in the right shape.
  Whether a screening is graph-backed is tracked implicitly by
  `"graph_config" in st.session_state`, not a separate boolean, so a
  fallback screening can never accidentally be treated as resumable.
- **Back still works, including recomputing on a different choice.** "Back
  to category confirmation" itself needed no change -- it already just
  flips `stage` back without discarding `category_results`, which is
  backend-agnostic. Re-confirming a SECOND time (the actual "different
  choice" case) is what needed `_invoke_fresh_graph_attempt`'s fresh-thread
  handling, described above.

### Verified, not just written

- `AppTest` (Streamlit's own headless test harness) end to end against the
  REAL app.py, REAL graph, REAL `data/labels/Parle-Gmain.jpeg` (no mocks):
  upload → Run screening → accept rank-1 for both queries (product 11.1,
  component 11.2) → Confirm and continue → **3 blocking items
  ([7, 8, 11])** → Back to category confirmation → override the product
  query to 7.2 via the existing "Choose another category" selectbox →
  Confirm and continue → **0 blocking items**. Exactly the task's Verify
  scenario, and `graph_config` was present (not fallen back) at both
  results screens.
- Cross-checked against `scripts/run_pipeline.py
  data/labels/Parle-Gmain.jpeg --auto-confirm`: byte-identical summary
  line ("3 item(s) NOT PERMITTED (item_id [7, 8, 11])... product = 11.1").
  `data/outputs/*/Parle-Gmain.json` (gitignored) were restored to their
  prior 7.2-confirmed state afterward via `scripts/verdict.py --category
  7.2` + `scripts/substitutes.py`/`scripts/horizon.py`.
- Fallback path, separately, in a fresh process (so `st.cache_resource`
  had no prior successful graph cached): patched
  `src.graph.pipeline.build_pipeline` to raise, ran the same Parle-Gmain
  flow through `AppTest`. Confirmed: `graph_config` absent throughout,
  `graph_broken` set, and the LEGACY path alone still reached results with
  the same correct outcome (blocking `[7, 8, 11]` at rank-1) -- the demo
  surface degrades, it does not break.
- `uv run pytest` -- 246 passed (unchanged from the prior entry; no new
  test file was added for this task, since `tests/test_graph.py` already
  covers `src/graph/` and app.py is UI glue exercised above via `AppTest`
  instead).
- `uv run ruff check .` -- unchanged: 27 pre-existing errors, all in
  `src/codex/run_codex.py`; `app.py` itself is clean.

### Anything unexpected

- **`Command(resume=...)` cannot re-pause an already-resolved checkpoint
  with a different value.** Assumed at first that "Back" could simply
  invoke the ORIGINAL paused thread a second time with a new choice
  (genuine LangGraph "time travel"). A throwaway script proved otherwise:
  resuming the same checkpoint twice silently returns the FIRST resume's
  result both times, ignoring the second value. This is why
  `_invoke_fresh_graph_attempt` exists and why re-confirmation needs a
  fresh thread rather than a replay -- worth flagging since it's the one
  piece of this task that didn't work the way the LangGraph docs'
  "time-travel" framing suggests it should from a config with an explicit
  past `checkpoint_id`.
- Resource duplication, accepted deliberately: the graph-backed path
  (`build_pipeline`) and the legacy path (`_load_references`/
  `_load_category_scorer`/`_get_extractor`) each load their own copy of
  every reference dataset and embed their own copy of the category corpus.
  Keeping them fully independent is what makes the fallback a REAL
  fallback -- sharing state would mean a broken graph could take the
  legacy path down with it.

## 2026-08-02 — LangGraph orchestration layer over the existing pipeline

### Why

Category recall@1 is measured at 0.53. Across F-06, F-13, and F-14, four
retrieval methods, seven corpus constructions, and two fusion strategies
were measured against ground truth and none beat the operating config.
Human confirmation of the food category is therefore a design requirement,
not a fallback -- and that confirmation was hand-rolled twice: a
`--category` CLI flag applied AFTER a verdict was computed
(`scripts/verdict.py`), and `app.py`'s manual pause/resume through
`st.session_state` plus a stage marker and a full script rerun. LangGraph's
`interrupt()` and `Command(resume=...)`, with durable checkpointing, is a
built-in primitive for exactly that pattern. Composite products also have
2-4 components each needing their own embedding call (real fan-out via
`Send`), and substitutes/horizon both consume the verdict independently
(real parallel branches) -- neither was expressed as such anywhere before.

### What changed

Added `src/graph/` -- an ADDITIONAL entry point, not a replacement. Every
existing script (`extract.py`, `extract_text.py`, `resolve.py`,
`classify_category.py`, `verdict.py`, `substitutes.py`, `horizon.py`) and
`app.py` needed no changes at all: every node below calls the SAME pure
function those scripts already call, nothing moved out of `src/category/`,
`src/resolve/`, `src/rules/`, `src/substitutes/`, or `src/horizon/`.

- **`src/graph/state.py`** -- `PipelineState` TypedDict. `category` and
  `errors` are `Annotated` with reducers (a dict-merge, `operator.add`)
  since the classify fan-out and the substitutes/horizon parallel branches
  each write those keys within the same superstep -- LangGraph raises
  `InvalidUpdateError` on an un-reduced key more than one node can write to
  in one step.
- **`src/graph/nodes.py`** -- one thin node per stage (`extract_node`,
  `resolve_node`, `classify_node`, `confirm_node`, `verdict_node`,
  `substitutes_node`, `horizon_node`, `narrate_node`), each a factory
  closing over reference data loaded once at graph-construction time, never
  re-read per node. `make_classify_dispatch` is the `path` function for
  `add_conditional_edges` -- one `Send("classify", {...})` per component
  query, real concurrent dispatch, or straight to `confirm` when there's
  nothing to classify. `make_confirm_node(auto_confirm)` calls
  `interrupt()` with `{component_label: [top-3 candidates]}` by default, or
  accepts rank-1 for every query when `auto_confirm=True` (batch runs,
  tests, no database). Every node's body is wrapped in try/except so a
  stage failure lands in `state["errors"]` instead of crashing the graph.
  `_join_category_results` here is a third independent copy of the
  item-id -> `CategoryResult` join that `scripts/verdict.py`'s
  `_build_item_category_map` and `app.py`'s `_apply_category_confirmations`
  already duplicate -- kept as its own copy rather than a shared import,
  matching the reasoning both of those functions' own docstrings already
  give for why they don't share one either.
- **`src/graph/pipeline.py`** -- `build_pipeline()` loads every reference
  dataset and embeds the category corpus ONCE, builds one closure per node,
  and wires `extract -> resolve -> [Send fan-out: classify per component]
  -> confirm -> verdict -> [substitutes | horizon in parallel] -> narrate
  -> END`, compiled with `InMemorySaver()` -- no database; state does not
  survive past the process, the same durability `app.py`'s session-state
  pause already had.
- **`scripts/run_pipeline.py`** -- new CLI entry point. Runs the graph;
  while `"__interrupt__" in result`, prints the retrieved top-3 per
  component and prompts for a choice (blank = accept rank-1), then resumes
  with `Command(resume=...)`. Writes every stage's output to the same
  directory the matching standalone script would
  (`data/outputs/{extraction,resolution,category,verdict,substitutes,horizon}`),
  so a file this script writes is interchangeable with one the equivalent
  standalone script wrote.

### Files touched

- `src/graph/__init__.py`, `state.py`, `nodes.py`, `pipeline.py` (new)
- `scripts/run_pipeline.py` (new)
- `tests/test_graph.py` (new)
- `pyproject.toml` / `uv.lock` (added `langgraph>=1.2.10`)

### Tests added

- `tests/test_graph.py` (6) -- every function a node calls that would
  otherwise need a real API key, network access, or reference data
  (`run_extraction`, `resolve_items`, `embedding_scores`, `classify`,
  `evaluate`, `find_substitutes`, `find_horizon_signals`, `narrate`) is
  monkeypatched on `src.graph.nodes`'s own imported names -- no API calls
  anywhere in the file. `build_queries` itself is left unmocked (pure,
  already covered in `tests/test_category.py`, and it's what actually
  produces the fan-out -- mocking it would mean never really testing the
  `Send` dispatch). A test-local `_build_test_graph` wires the same
  topology as `build_pipeline` with the real node factories over a
  3-component fixture product. Covers: end-to-end run with
  `auto_confirm=True`; a 3-component product dispatches exactly 3 classify
  `Send`s; `interrupt()` pauses (`"__interrupt__"` present,
  `graph.get_state(config).next == ("confirm",)`) and
  `Command(resume=...)` populates `confirmed_categories`; a confirmed
  category reaches `evaluate()` as an override on the correct item while an
  unconfirmed component's item still sees the retrieved top-3 unchanged;
  substitutes and horizon both run and both reach narrate; a
  `resolve_items` exception lands in `state["errors"]` without crashing the
  graph.

**Total: 246 tests passing** (240 before this task + 6 new). `ruff check .`
unchanged: 27 pre-existing errors, all in `src/codex/run_codex.py`,
unrelated to this task -- `src/graph/`, `scripts/run_pipeline.py`, and
`tests/test_graph.py` are all clean.

### Verified, not just written

- Confirmed the INSTALLED langgraph version (1.2.10) actually exposes
  `StateGraph`, `interrupt`, `Command`, `Send` (`langgraph.types`), and
  `InMemorySaver` (`langgraph.checkpoint.memory`) before writing against
  them, per the task's explicit instruction not to rely on a remembered
  API.
- Smoke-tested `interrupt()`/`Command(resume=...)` and `Send` fan-out with
  a dict-reducer state key in two standalone throwaway scripts first, to
  confirm the exact return shape (`result["__interrupt__"]` is a list of
  `Interrupt(value=..., id=...)`) before relying on it in `nodes.py` and
  `run_pipeline.py`.
- `uv run pytest` -- 246 passed.
- `uv run ruff check .` -- pre-existing error count unchanged.
- `uv run python scripts/run_pipeline.py --help` -- CLI wired correctly
  (`--text`, `--name`, `--description`, `--auto-confirm`, `--log/--no-log`
  all present).
- Did not run the graph against a real label image end to end -- that
  would call the Gemini API and the embedder for real, same as every other
  script in this project. `run_pipeline.py`'s output-writing and
  interrupt/resume loop are exercised structurally by the mocked test
  suite instead.

### Anything unexpected

- None of the seven existing scripts or `app.py` needed any change at all
  -- every node in `src/graph/nodes.py` calls the same pure function those
  scripts already call, so the "additional entry point, not a replacement"
  constraint held without a single adjustment to existing code.

## 2026-08-02 — Final report layer: verdict strip fix, narration, export, email

### What changed

**Verdict strip fix (measured problem).** After category confirmation,
`evaluate()` only ever returns a single candidate per item, so the verdict
strip could show only one block and never the divergence that made the
confirmation choice matter (e.g. Chipsmain's E551: permitted under the
confirmed 12.2.2, not permitted under 15.1). Fixed by computing a SECOND,
display-only `ProductVerdict` in `app.py`'s `_finalise()` -- the same pure
`evaluate()` call, run against the pre-confirmation candidates instead of
the confirmed ones -- and merging its `by_category` lists back onto the
authoritative verdict's items purely for rendering
(`_merge_preview_candidates`). `src/ui/components.py`'s `verdict_strip_html`
now renders every candidate, full colour and outlined for the confirmed
one (via a new `confirmed_fcs_code` key), dimmed for the rest, and still
collapses to one quiet block with no divergence line when every candidate's
verdict actually agrees. The compliance decision itself (`verdict`, the one
used for blocking/summary/everything real) is completely unchanged --
this is a display-only addition.

**Date label and citation fix.** `CategoryVerdict.retrieved_date` was
always eu_fip's `effective_date` (when a provision entered force), never a
real data-retrieval timestamp -- checked and confirmed no eu_fip row or any
other structure in this project tracks a genuine retrieval date. Relabelled
to "In force since `<date>`."; no second "data retrieved" line is shown,
since fabricating one would be worse than omitting it. Citation rendering
now always shows either a real link or "No source URL on this record."
whenever a candidate is present, driven by the candidate's own
`source_url` field directly rather than the item-level `uncitable_verdict`
flag (which could previously diverge from the specific candidate on
display in a multi-candidate item).

**`src/report/narrator.py`** -- `narrate(verdict, substitutes, horizon,
model_id) -> Narration`. The one place a model produces user-facing prose;
imports only the three finished result schemas, never a reference-data
loader. Retry/backoff duplicated from `src/extractors/gemini.py`'s
`_call_model` (not imported, by design -- this module answers to no other
stage). A deterministic faithfulness check runs after every generation:
every E-number, mg/kg figure, and dotted category code the model wrote is
checked as a substring against the JSON it was given; anything absent is
reported in `unfaithful_claims`, never dropped. Falls back to
`verdict.summary` with `model_id="unavailable"` on any failure.

**`src/report/export.py`** -- `to_json`, `to_csv`, `to_pdf`. The PDF
(reportlab, `uv add reportlab`) carries every caveat the results screen
carries: source URL and in-force date per verdict, an unconfirmed-category
note where it applies, the dosage caveat (inherited via
`verdict.summary`, rendered verbatim), the horizon lane's partial-coverage
warning (always rendered, signals or not), unverified-DOI flags, and the
narration's `model_id` plus any `unfaithful_claims`. `to_pdf` takes an
optional `product_name` kwarg (not in the task's literal 4-arg signature,
but additive/backward-compatible) so the header can show one when the
caller has it, without requiring a positional-signature change.

**`src/report/email.py`** -- `send_report(smtp_config, recipient, subject,
body, attachment)`. Opt-in only: no default recipient anywhere, five
optional `SMTP_*` settings added to `config.py` (absent -> feature
disabled via `smtp_config_from_settings() -> None`, `Settings()` still
constructs fine with none of them set).

**UI (`app.py`)** -- report screen now shows the narration summary/detail
prominently above the item sections (still followed by the unchanged,
verbatim `verdict.summary`), a "written by `<model_id>`... it adds no
facts" line, an `unfaithful_claims` warning when non-empty, three download
buttons (JSON/CSV/PDF), and a collapsed-by-default "Email this report"
expander that shows an explicit disclosure line before sending, or "email
is not configured" when SMTP settings are absent.

### Files touched

- `src/report/__init__.py`, `src/report/narrator.py`, `src/report/export.py`,
  `src/report/email.py` (new)
- `src/ui/components.py` (verdict strip rewrite, `_primary_candidate`,
  citation/date label fix)
- `src/ui/styles.py` (`data-dimmed` styling, `.eu-narration` /
  `.eu-narration-detail`)
- `app.py` (preview-verdict computation, `_merge_preview_candidates`,
  narration wiring, export/email section)
- `config.py` (optional `SMTP_*` settings)
- `.env.example` (documents the optional SMTP settings)
- `pyproject.toml` / `uv.lock` (added `reportlab`)

### Tests added

- `tests/test_narrator.py` (5) -- invented E-number and invented category
  code land in `unfaithful_claims`; faithful narration has none; model
  failure and malformed JSON both fall back to the deterministic summary.
- `tests/test_export.py` (4) -- JSON round-trips with narration intact; CSV
  has one row per item including unresolved ones; PDF produces valid
  `%PDF-` bytes with and without `product_name`.
- `tests/test_email.py` (4) -- `smtp_config_from_settings()` returns `None`
  when unset or partially set, builds a config when fully set;
  `send_report` sends to exactly the given recipient with the attachment
  intact (mocked `smtplib.SMTP`, no real connection).
- `tests/test_components.py` (8) -- the verdict-strip fix directly: single
  candidate, agreeing candidates collapse, divergent candidates show all
  blocks with exactly one confirmed/outlined and the rest dimmed, an
  unconfirmed multi-candidate strip dims nothing, duplicate fcs_codes
  dedupe, and `_primary_candidate`'s confirmed/fallback selection.

**Total: 176 tests passing** (155 before this task + 21 new).
`ruff check .` unchanged: 27 pre-existing errors, all in `src/codex/`,
unrelated to this task.

### Verified, not just written

Ran the full pipeline through `streamlit.testing.v1.AppTest` against real
`data/reference/eu_fip.json` data (E551 under a Chipsmain-shaped product):
confirmed the preview verdict genuinely diverges across 12.2.2/12.1.1/12.1
(not a synthetic fixture), inspected the rendered `verdict_strip_html`
output directly and confirmed the confirmed block is outlined/tagged and
the other two carry `data-dimmed="true"`, confirmed "In force since" now
appears in rendered markdown and "retrieved" does not, confirmed the three
download buttons and the collapsed email expander render with the correct
"not configured" message under this environment's `.env` (no SMTP
settings set), and confirmed zero exceptions through the entire
input -> category-confirm -> results flow with the narrator's model call
mocked (no real network call made during any of this verification).

### Anything unexpected

- `reportlab.lib.colors.Color.hexval()` returns `"0xRRGGBB"`, not
  `"#RRGGBB"` -- had to confirm empirically that reportlab's `Paragraph`
  `<font color=...>` markup actually accepts that format before relying on
  it (it does).
- eu_fip carries no field resembling a genuine data-retrieval date
  anywhere (checked all 18,987 rows' keys) -- confirms the "omit rather
  than substitute" instruction was the only honest option, not just the
  cautious one.

## 2026-08-02 — Review fixes: names, product identity, navigation, narration structure, result-screen detail, substitutes, horizon, configurable email

### What changed

**A. Names, everywhere at once.** One new function, `app.py`'s
`_enrich_additive_names`, backfills `ItemVerdict.additive_name` right after
`evaluate()` returns -- eu_fip name (unchanged) else the Codex INS name
(e.g. INS 143 -> "Fast Green FCF", absent from eu_fip, which is WHY it
blocks, but Codex still knows it) else the label's own wording
(name_as_declared/verbatim). Because `additive_name` was ALREADY the
first-choice display field everywhere (`src/ui/components.py`,
`src/report/export.py`'s `_display_name`, and `src/substitutes/advisor.py`'s
`blocked_name = item.additive_name`), enriching it ONCE, before
`find_substitutes()`/`narrate()` ever see the verdict, fixed every
downstream surface for free -- zero changes needed to advisor.py,
narrator.py, or export.py's own name-fallback logic. Verified live: an
INS-143-shaped item showed `additive_name: "Fast Green FCF"` on the
enriched verdict AND `blocked_name: "Fast Green FCF"` on its substitute
suggestion, both from one enrichment call.

**B. Product identity block.** New `ReportIdentity` dataclass
(`src/report/export.py`): product_name, source (filename or "pasted
text"), category (built from `ProductVerdict.category_used` +
`CategoryVerdict.category_name`, e.g. "Product: 14.1.4 (Flavoured_drinks)"),
timestamp. Built ONCE in `app.py`'s `_build_identity`, then shown on the
results screen, written as `# key: value` comment rows before the CSV
header, and printed in the PDF header -- one object, three surfaces, so
they cannot drift apart.

**C. Navigation.** A Back button on category confirmation returns to input
without touching extraction/resolution/category_results (a pure stage
change, not a reset). A Back button on the results screen returns to
category confirmation; re-confirming a DIFFERENT category recomputes the
verdict via the same pure `evaluate()` call (no extraction API call) --
verified live: switching a radio choice from 12.2.2 to 12.1.1 and
re-confirming changed `category_used` and the item's verdict correctly, with
`category_results` untouched throughout. "New screening" moved below the
title/identity block, in its own row with top margin, not flush against
the header.

**D. Narration structure.** Rewrote `src/report/narrator.py`'s
`_PROMPT_RULES`: a 2-3 sentence opening (can this ship, what stops it),
then labelled markdown paragraphs (**What is blocked** / **What is
permitted** / **What needs review** / **Possible substitutes** /
**Regulatory horizon**, each omitted when empty) with bullet lists where a
paragraph covers more than one item, substance names before codes, and
inline jargon glosses for "quantum satis", "Group I additive",
"carry-over", and food category codes. `app.py` now renders
`narration.summary`/`.detail` via plain `st.markdown()` (Streamlit's own
safe parser) instead of an escaped, HTML-wrapped `<p>` tag, so the
requested bold labels and bullets actually render instead of showing as
literal `**`/`-` characters. **The faithfulness check itself
(`_faithfulness_check`) is untouched**, per instruction -- only the prompt
text changed, and the existing narrator tests (which mock `_call_model`'s
return value directly, never inspecting the prompt) still pass unmodified.

**E. Result-screen detail.** `src/ui/components.py`: a count strip
(Blocked/Permitted/Review/Out of scope) at the top of the results screen.
Every blocking item now states WHY ("not authorised -- this additive does
not appear in the EU list..." vs "prohibited -- removed from the EU
permitted list", distinguished by the `prohibited` flag). Conditions text
now shows its first line inline, with the rest (if any) behind an expander
relabelled "Conditions of use" (previously "Conditions -- 14.1.4", which
said nothing about the contents). A new collapsed "Out of scope" section
lists flavourings/enzymes/food-ingredients WITH their reason
("flavouring -- regulated under Reg 1334/2008, not the additives
regulation", etc.) -- previously invisible, so "never assessed" was
indistinguishable from "assessed and cleared".

**F. Substitutes.** Internal flag names replaced with plain phrases at
display time only (`classes_from_subtypes` -> "function inferred from
sub-types", etc.) -- the raw flag is untouched in `to_json`. Each
candidate with conditions or a source now gets its own expander (screen and
PDF both) -- previously invisible entirely. The "excluded -- functional
class unknown" diagnostic lines, previously loose body text, now sit inside
a collapsed "N additives could not be assessed as substitutes" expander
with a one-sentence explanation.

**G. Regulatory horizon.** Added the explanatory sentence ("Additives can
be re-assessed by EFSA years before the law changes...") and moved the
partial-coverage warning to render BEFORE "no recent EFSA activity found"
(previously after, so the reassurance was read first).

**H. Configurable email.** The email expander now always shows host/
port/username/password(masked)/from-address fields, defaulting to
whatever `.env` provides (blank if nothing) -- previously, an unconfigured
installation showed a dead-end message with no form at all. Values live in
`st.session_state` only, seeded via `setdefault` before each widget
(the correct Streamlit pattern for a pre-filled, widget-owned value; using
both `value=` and `key=` on the same widget after first render raises).
Added `src/report/email.py`'s `test_connection()` -- opens and
authenticates an SMTP connection without sending anything -- wired to a
new "Send test email" button, disabled until all five fields are filled.

**I. CSV.** `component` was already reading `ItemVerdict.component_label`
correctly (verified against `data/outputs/verdict/Chipsmain.json`, which
has real "Seasoning"-component rows and rendered them correctly before this
change); the "empty on every row" report was Khusmain-specific, where the
product genuinely has no composite components -- every item maps to the
single product-scope query, so `component_label` is null for all of them
by construction, not a bug. Left as-is. Display names now come through
automatically via section A's enrichment. The product identity block is
written as leading `# key: value` comment rows (section B).

### J. Investigation: `conflicting_rows` on Khusmain

**Finding: genuine, structural, NOT a dedup-key artifact.** All three
permitted items on Khusmain (E330, E440, E224) carry `conflicting_rows`.
Traced to eu_fip: for E330 in category 14.1.4, there are two rows with the
SAME `(canonical_id, food_category_raw)` but genuinely DIFFERENT
`conditions` text and `max_level_basis` ("conditional" vs "gmp") -- one row
states "E 420, E 421, E 953, E 965, E 966 and E 967 may not be used...",
the other states "ML = quantum satis; except E 425 ML = 10000 mg/kg...".
These are two DIFFERENT, both-simultaneously-applicable restriction clauses
within the same "Group I, Additives" permission for that category, not
duplicate or near-duplicate records.

Checked whether this is per-additive noise or a structural export pattern:
of the 134 additives permitted via "Group I, Additives" in category 14.1.4,
131 (98%) carry EXACTLY this same two-clause pair, verbatim. This is EU
FIP's export format splitting one legal permission with multiple
restriction clauses into multiple rows, not a data conflict and not an
artifact of `_dedupe_rows`'s key being too narrow (whitespace/date
differences were the suspected cause; neither exists here -- the rows
differ in real, substantive legal text).

**Conclusion: `_dedupe_rows`'s key is correctly scoped and must NOT be
widened** -- doing so would silently merge two textually-different,
both-applicable legal conditions into one, which is a correctness
regression, not a fix. `conflicting_rows` is technically firing as
designed (more than one distinct row survives dedup), but its CHOSEN row
(`_select_row`'s "most restrictive" tie-break) is a full tie in this exact
case -- both rows have `max_level_mg_kg=None` and non-null conditions, so
`min()` picks whichever appears first in the source list, silently
dropping the OTHER clause's numeric detail from the displayed conditions
text. That is a real, separate finding -- `_select_row` picks a
REPRESENTATIVE row when it should arguably present or combine both -- but
it is a `src/rules/engine.py` behaviour change outside this task's scope,
not something to fix by touching `_dedupe_rows`'s key. Left unchanged, as
instructed.

### Tests

- `tests/test_export.py`: +7 (CSV component column, CSV identity comment
  rows with and without `ReportIdentity`, `_blocking_reason`,
  `_out_of_scope_reason`, `_substitute_flag_label`, updated PDF tests for
  the `identity` parameter).
- `tests/test_components.py`: +6 (`_display_name`'s enriched-name
  precedence and fallback chain, `_blocking_reason`, `_out_of_scope_reason`,
  `_substitute_flag_label`).
- `tests/test_email.py`: +2 (`test_connection` succeeds and authenticates
  without sending; propagates failure). Imported under an alias
  (`check_smtp_connection`) -- pytest would otherwise try to collect
  `email.py`'s `test_connection` itself as a test case, since its name
  matches the `test_*` discovery pattern.
- `tests/test_narrator.py`: unchanged -- the faithfulness check and its
  tests were explicitly not to be touched, and none of them depend on the
  prompt text (they mock `_call_model`'s return value directly).

**Total: 188 tests passing** (176 before this task + 12 new).
`ruff check .` unchanged: 27 pre-existing errors, all in `src/codex/`,
unrelated to this task.

### Verified, not just written

Ran the full pipeline through `streamlit.testing.v1.AppTest` against real
reference data (`data/reference/codex_ins.json`, `eu_fip.json`), with a
three-item product (an EU-unlisted INS-143-shaped colour, E551 with a
genuine multi-category divergence, and a flavouring) and the narrator's
model call mocked:
- Confirmed the enriched verdict shows `additive_name: "Fast Green FCF"`
  for the unlisted item, and the substitute suggestion's `blocked_name`
  matches -- both from the SAME single enrichment call.
- Confirmed Back-from-category-confirmation preserves `category_results`
  (`len` unchanged) and returns with zero exceptions.
- Confirmed Back-from-results -> pick a different radio option -> re-confirm
  actually recomputes: `category_used` changed from 12.2.2 to 12.1.1 and
  the item's `by_category` updated to match, with zero exceptions.
- Confirmed the count strip, blocking-reason text, "Conditions of use"
  expander, the "Out of scope (1)" collapsed section, and the "N additives
  could not be assessed as substitutes" collapsed section all rendered.
- Confirmed the horizon section's partial-coverage warning and the new
  "re-assessed by EFSA" explanatory sentence both render.
- Confirmed the email form shows all five SMTP fields plus recipient, with
  Port correctly defaulting to 587, and both "Send test email" and "Send"
  correctly disabled when the fields are incomplete.
- Confirmed the plain `st.markdown()` narration rendering (no
  `unsafe_allow_html`) means Streamlit's default sanitisation applies --
  the model's requested bold/bullet markdown renders, but raw HTML cannot.

### Anything unexpected

- Section I's reported CSV bug ("component empty on every row") did not
  reproduce against Chipsmain -- turned out to be correct behaviour for a
  product (Khusmain) with no composite components, not a defect. Verified
  before touching any code, per the same "investigate before fixing"
  discipline as item J.
- Importing `email.py`'s `test_connection` directly into
  `tests/test_email.py` under its real name would have made pytest try to
  collect and run it AS a test (name matches `test_*`), failing on a
  missing `smtp_config` argument -- caught before it became a CI surprise,
  fixed with an import alias.

## 2026-08-02 — Narration rendering, verdict-strip clarity, conditions context

### What changed

**A. Narration rendered as a raw Python dict — root cause and fix.**
`Narration.detail` was typed `str`, but the prompt (from the previous
turn) asked the model for topic-labelled content -- a shape the model
reasonably returned as a genuine JSON object anyway. `narrate()` did
`str(data["detail"])`, and `str()` on a dict produces exactly its Python
repr: `"{'What is blocked': [...], ...}"`, shown verbatim on screen. Fixed
at the root, not by re-prompting harder: `Narration.detail` is now typed
`dict[str, list[str]]` (topic -> short sentences), matching what the model
already wanted to produce. `app.py` and `src/report/export.py`'s `to_pdf`
both render each topic as its own bold sub-heading with its points as
bullets. A new `_coerce_detail` in `narrator.py` tolerates the harmless
drift of a single string instead of a list under one topic (wraps it), but
still raises -- triggering the existing model-unavailable fallback -- if
the TOP-level value isn't a JSON object at all, rather than silently
accepting the old flat-string shape and risking the same bug in reverse.

**B. Narration tone.** Rewrote `_PROMPT_RULES` for brevity and
orientation: short, one-idea sentences; lead with the answer ("This
product cannot ship..." / "No blocking issues found.") before the
reasoning; say what the reader should DO, not what the regulation says;
explicit instruction not to enumerate OTHER additives named only inside a
Group conditions clause (the source of the "certain polyols and glutamic
acid salts" noise); and an explicit rule against listing the same item
under both "What is permitted" and "What needs review" -- naming the exact
mechanism that caused it: `verdict.review_required` includes
`permitted_with_conditions` items by design (so a human remembers to read
their conditions), which the model was reasonably reading as "this item
needs its own write-up too." **The faithfulness check itself
(`_faithfulness_check`) is untouched**, per instruction -- only the prompt
text and the flattening of the now-structured `detail` before the check
runs changed.

**C. Codes missing in the blocking section.** Root cause: `ItemVerdict`
deliberately does not carry `canonical_ins` (see `src/substitutes/
advisor.py`'s docstring), so an item absent from eu_fip (like INS 143) had
no code anywhere to show -- `eu_canonical_id` is null precisely because it
was never crosswalked. Fixed at the same single enrichment point as the
earlier names fix: `app.py`'s `_enrich_additive_names`, when it falls back
to the Codex name, now folds the code in too -- `additive_name` becomes
"Fast Green FCF (INS 143)", not bare "Fast Green FCF". Because every
consumer (UI, CSV, PDF, the narrator's JSON) already reads `additive_name`
first, this fixes every surface with no new plumbing, exactly as the names
fix did. Items that DO have an `eu_canonical_id` are unaffected -- they
already get their E-number shown separately in the verdict strip head, so
folding it into the name too would have duplicated it.

**D. Verdict strip compared a category with its own ancestors.** eu_fip
records permissions at the LEAF category, so a retrieved PARENT code
(Khusmain's candidates were 14.1.4, 14.1, and 14 -- 14.1.4 is inside 14.1
is inside 14) has no row of its own and always rendered "not permitted in
this category": true, meaningless, and it falsely claimed a divergence
that does not exist. Fixed in `src/ui/components.py`: a new
`_is_ancestor_code` detects the relationship from the dotted codes alone
(string prefix + dot boundary, no tree structure needed, since eu_fip
codes are already flat "N.N.N..." strings). Candidates that are ancestors
of the confirmed (or, if unconfirmed, rank-1) candidate are split out
before any divergence logic runs, rendered as their own blocks labelled
"parent category" with no verdict colour, and never counted toward
"verdict depends on category" -- which now correctly disappears entirely
for Khusmain. The confirmed block also now shows the category NAME
alongside its code (e.g. "14.1.4 Flavoured drinks"), not just the code --
extended to every real candidate block, not only the confirmed one, since
the information is equally useful on the dimmed ones.

**E. Why conditions are identical across additives.** E330/E440 (Group I)
and all five E171 substitutes (Group II) legitimately share conditions
text, because the permission is granted to the whole GROUP, not the
individual additive -- correct, but reads as a bug. A new `_group_note` in
`src/ui/components.py` detects a conditions text opening with "Permitted
via Group <N>..." (tolerating the numbered-clause prefix the previous
turn's `multiple_provisions_apply` merge can produce) and derives the
group name from that text itself -- not hardcoded, and no eu_fip schema
change needed, since `via_group`/`group_code` live on the ROW and this
module only ever sees the already-resolved `CategoryVerdict`. Shown as one
line above the conditions block, in both the main verdict rows and each
substitute candidate's expander.

**F. Stale "Period of application" dates.** Checked eu_fip's schema across
every row (not a sample) for a structured expiry/end-date field: there is
none -- `effective_date` is the only date field, and it means when a
provision entered force, not when it lapses. Confirms the date lives only
inside the conditions prose. A new `_period_of_application_note` detects
the phrase and adds a note that eu_fip has no separate expiry field, the
date may be historical, and the source link is authoritative -- never
filtered or hidden, exactly as instructed, just labelled so a past date in
a live provision isn't mistaken for a current instruction.

### Tests

- `tests/test_narrator.py`: +3 (`detail` renders as structured sections,
  not a stringified dict -- the direct regression guard for the bug; a
  single string under one topic is wrapped, not dropped; the old flat-
  string shape now triggers the fallback rather than being silently
  accepted). 2 existing fixtures updated to the new `dict[str, list[str]]`
  shape.
- `tests/test_export.py`: fixture updated to the new `detail` shape; PDF
  tests unaffected otherwise.
- `tests/test_components.py`: +9 -- `_is_ancestor_code`'s directionality
  and non-reflexivity; THE REAL Khusmain case (14.1.4/14.1/14 collapses to
  one block, no divergence line, parents labelled); the confirmed block
  shows its category name; a regression guard that a GENUINE divergence
  between non-ancestor candidates (Chipsmain's E551) still renders;
  `_group_note` derives "Group I, Additives" / "Group II, Colours" from
  real conditions text including the merged/numbered shape, and is `None`
  for a non-Group permission; `_period_of_application_note`;
  `_conditions_notes` stacking both notes when both apply.

**Total: 205 tests passing** (193 before this task + 12 new).
`ruff check .` unchanged: 27 pre-existing errors, all in `src/codex/`,
unrelated to this task.

### Verified, not just written

Ran the full pipeline through `streamlit.testing.v1.AppTest` reproducing
the exact reported scenario -- Khusmain-shaped product (E330 confirmed at
14.1.4 against retrieved candidates 14.1.4/14.1/14, plus an INS-143-shaped
unlisted colour) with the narrator's model call mocked to return the new
structured shape:
- Confirmed the literal string `"{'What is blocked'"` does NOT appear
  anywhere in rendered markdown (the bug, directly), and that
  `"**What is blocked**"` and its bullet DO appear correctly instead.
- Confirmed the confirmed item's `additive_name` is exactly
  "Fast Green FCF (INS 143)" on the actual `ProductVerdict`, and that this
  same string is what the verdict strip, and the narration's own JSON
  input, all see.
- Confirmed "verdict depends on category" does NOT appear anywhere on
  Khusmain's rendering, and "parent category" appears exactly where
  expected.
- Confirmed the confirmed block shows "Flavoured drinks" (the category
  name).
- Confirmed the Group I explanatory note renders above E330's real,
  production conditions text.
- Confirmed `_period_of_application_note` fires correctly against E551's
  real eu_fip conditions text ("Period of application: from 1 February
  2014").
- Zero exceptions throughout; no real network call made (narrator mocked).

### Anything unexpected

- The bug in Section A was not a prompt-following failure -- the model was
  behaving reasonably given a topic-per-key instruction over a flat-string
  field. The fix that actually holds is a type fix (make the schema match
  what the model naturally produces), not a stronger prompt telling it not
  to do the sensible thing.
- Section D's ancestor detection needed no tree/hierarchy data structure
  at all -- eu_fip's food category codes are flat dotted strings, so a
  string-prefix check (with a dot boundary, to avoid "14.2" false-
  matching as a descendant of "14") is both correct and sufficient.

## 2026-08-02 — Narration fragmentation and count-strip double-counting

### What changed

**A. Narration fragmented into one-sentence bullets.** On Chipsmain,
"What is permitted" produced 24 bullets for 6 additives -- 4 per additive,
with "Conditions apply -- see below" and the retrieval date repeated on
every single one. The previous instruction ("short sentences, one idea
each") was being applied literally at the bullet level instead of within
a bullet's prose. `src/report/narrator.py`'s `_PROMPT_RULES` now says
explicitly: one bullet per additive or per GROUP of additives sharing the
same category/verdict/level, not one bullet per fact; group additives
that share a finding into a single sentence ("E470a, E627, E631, E330 and
E471 are all permitted in 12.2.2 ... at quantum satis as Group I
additives.") and call out only what differs in a separate bullet; state
shared boilerplate (conditions-apply, confirmed category, retrieval date)
ONCE at the end of the topic, never per bullet. `_faithfulness_check`, the
`Narration` schema, and every other function in the module are untouched.

**B. Count strip double-counted.** On Chipsmain, 0 blocked + 6 permitted +
7 review + 9 out of scope summed to 22 for 16 items. Root cause: the
"Review" cell read `len(verdict.review_required)` directly --
`ProductVerdict.review_required` is the ENGINE's own broader field
(`src/rules/engine.py`), which deliberately also includes
`permitted_with_conditions` items so a human remembers to check their
conditions -- correct for that field's purpose, wrong reused as a count,
since those same items were already counted under "Permitted". Fixed with
a new `src/ui/components.py::count_buckets(items: list[dict])`: a pure
partition by each item's `headline` alone (a single Literal value per
item, never more than one), so the four buckets -- Blocked, "Permitted,
conditions to check", Needs review (unresolved/category_unknown only,
never permitted_with_conditions), Out of scope -- are mutually exclusive
and sum to `len(items)` unconditionally, not just for the cases checked by
hand. `app.py`'s `render_results()` now builds one `item_dicts` list and
passes it to `components.count_buckets(...)` instead of hand-assembling
the strip dict from several partially-overlapping sources.

### Tests

- `tests/test_components.py`: +2 -- `count_buckets` sums to `len(items)`
  across one item per possible `headline` value (all four buckets get a
  nonzero count, asserted exactly, not just the sum); empty list sums to
  zero.
- `uv run ruff check .` on the five touched files: clean. Full-repo run
  still shows the same 27 pre-existing errors in `src/codex/`, unrelated
  to this task and unchanged by it.

**Total: 207 tests passing** (205 before this task + 2 new).

### Verified, not just written

- **Section A, live model call** (real Gemini call, `gemini-2.5-flash`,
  the app's configured `PRIMARY_MODEL` -- no mock): built the user's exact
  reported scenario, six additives permitted in 12.2.2 as Group I
  additives with one (E551) capped differently from the rest. Result: 3
  bullets total for "What is permitted", not 24 -- one bullet grouping
  E470a/E627/E631/E330/E471, one bullet for E551's differing cap, one
  closing line stating the confirmed category and retrieval date once.
  Zero unfaithful claims.
- **Section B, `streamlit.testing.v1.AppTest`** (full `app.main()` run,
  `stage="results"`, no mocking needed since this is deterministic
  render code, not a model call): reproduced the exact reported Chipsmain
  arithmetic -- 6 `permitted_with_conditions` items, 1 genuine
  `unresolved` item, 9 `out_of_scope` items, `verdict.review_required`
  built with 7 entries (the engine's real broad definition, confirming
  the OLD bug's "7 review" number was correct for that field). Parsed the
  rendered count-strip HTML directly: `0 Blocked + 6 Permitted, conditions
  to check + 1 Needs review + 9 Out of scope = 16`, matching the item
  total exactly -- the old code would have shown 22 for this identical
  scenario.
- No exceptions in either run.

### Anything unexpected

- The count-strip bug was NOT in `render_count_strip` (a generic, correct
  `dict[str, int] -> HTML` function needing no change) or in the locally-
  scoped `_REVIEW_HEADLINES` set already used correctly for the review-
  queue SECTION further down the page -- it was that the count strip's
  number source and the section's item list were two different
  computations that had drifted apart. Partitioning by `headline` alone,
  rather than by continuing to reconcile `blocking`/`category_conflict`/
  `review_required` list membership, removes the possibility of drifting
  apart again: every item has exactly one `headline`, so the four buckets
  can never overlap by construction.

## 2026-08-02 — Component/category routing collapsing on repeated additives

### What changed

**MEASURED BUG.** On Ice-creammain, maltitol is declared twice: item 2
(depth 1, under Inner Layer) and item 15 (depth 2, under Chocolate Paste,
itself under Outer Layer). Both are eu_canonical_id 965. Both landed on
the exact same (component, category) pair -- "Outer Layer" -- because
`scripts/verdict.py::_build_item_category_map` and its app.py duplicate
`_apply_category_confirmations` each built `by_additive_id: dict[str,
CategoryResult]`, keyed on eu_canonical_id (a substance id, not unique
per declaration): iterating every component query's `additive_ids` and
writing into that one dict meant whichever query was processed LAST
"won" for every item sharing that substance id, anywhere in the label.
Chocolatemain has the identical shape (E442 in both Milk Chocolate and
Centre).

**Root cause was entirely at the script/app join, not in category
retrieval.** `src/category/classifier.py::build_queries` already scopes
each item's additive to exactly one component query correctly (via
`_depth0_ancestor_id`, an existing per-item parent_item_id walk). The
per-component `CategoryQuery.additive_ids` lists were never wrong; the
bug was purely in how the boundary code matched an *item* back to its
*query* afterwards.

**Fix.** Added `src/category/classifier.py::item_component_labels(items,
resolved_by_id) -> dict[int, str | None]`: for every item, climbs
`parent_item_id` to its depth-0 ancestor (reusing `_depth0_ancestor_id`)
and returns that ancestor's label -- the SAME text `build_queries` used
to build the query in the first place -- or `None` (product scope) if the
item is depth-0 itself or its ancestor isn't classified `"compound"`.
Every item gets its own, independent walk; nothing is ever reused across
items. `_build_item_category_map` (scripts/verdict.py) and
`_apply_category_confirmations` (app.py) now take this `item_labels` map
as a parameter and join `by_component_label: dict[str, CategoryResult]`
(keyed on `CategoryResult.component_label`, unique per component) instead
of `by_additive_id` -- eu_canonical_id is no longer part of this join at
all. `scripts/verdict.py` now loads the extraction file's raw items
(previously only used for display names) to feed the walk;
`_load_extraction_names` was split out from a new `_load_extraction_items`
so both share one file read. app.py already had the extraction payload in
`st.session_state`, so no new I/O there -- `item_component_labels` is
computed once in `_finalise` and passed to both of its
`_apply_category_confirmations` calls (the authoritative verdict and the
preview verdict).

### Tests

- `tests/test_category.py`: +6 -- the real Ice-creammain shape (item 2
  under Inner Layer, item 15 at depth 2 under Chocolate Paste under Outer
  Layer, same eu_canonical_id) gets two distinct labels; a three-level
  walk (depth 2 -> depth 1 compound -> depth 0) returns the depth-0
  ancestor, never the intermediate; the real Chocolatemain shape (E442 in
  Milk Chocolate and Centre) gets two distinct labels; a non-nested label
  is unaffected (every item stays product-scope); a depth-0 ancestor that
  is not classified `"compound"` yields no component label, matching
  `build_queries`' own filter.
- `tests/test_verdict_cli.py`: 2 existing tests updated to the new
  `item_labels` parameter; +1 new -- the exact Ice-creammain-shaped
  eu_canonical_id collision, asserting `mapping[2].component_label ==
  "Inner Layer"`, `mapping[15].component_label == "Outer Layer"`, and
  `mapping[2] is not mapping[15]`.
- `uv run ruff check .` on the five touched files: clean. Full-repo run
  still shows the same 27 pre-existing errors in `src/codex/`.

**Total: 213 tests passing** (207 before this task + 6 new).

### Verified, not just written

- Ran `uv run python scripts/verdict.py data/outputs/resolution/Ice-creammain.json`
  against the real, on-disk resolution/category files (no mocking -- this
  is deterministic joining code, not a model call). Item 2:
  `component_label == "Inner Layer"`. Item 15: `component_label ==
  "Outer Layer"`. The two items no longer share a `CategoryResult` object
  (confirmed by inspecting the written `by_category` lists directly:
  Inner Layer's candidate order is `[3, 18.2, 5.1]`, Outer Layer's is
  `[3, 5.1, 2.3]` -- genuinely different retrieval results, not the same
  list reused).
- Ran the same command against `data/outputs/resolution/Chocolatemain.json`:
  E442 (item 5) is `component_label == "Milk Chocolate"` with candidates
  `[5.1, 5.2, 5]`; E442 (item 23) is `component_label == "Centre"` with
  candidates `[5.1, 5, 15]` -- distinct components, distinct rankings.

### Anything unexpected

- **Item 15's rank-1 category code does not match the task's stated
  expectation.** The routing fix makes item 2 land on `Inner Layer / 3`
  exactly as expected, but item 15 lands on `Outer Layer / 3`, not
  `Outer Layer / 5.1` -- because `Outer Layer`'s own persisted retrieval
  result (`data/outputs/category/Ice-creammain.json`, untouched by this
  fix) genuinely ranks "3 edible ices" ahead of "5.1 cocoa and chocolate"
  for that component's query text. This file was generated independently
  by `scripts/classify_category.py` and was never affected by the
  eu_canonical_id-collapse bug -- the component labels and per-query
  `additive_ids` in it were always correct; only the later join was
  wrong. Regenerating retrieval was out of scope for this fix (a network
  call, and a retrieval-ranking question -- recall@1 is separately
  measured at ~46% elsewhere in this project -- not a routing defect), so
  this is flagged rather than silently forced to match. The bug as
  described (both items colliding on the identical (component, category)
  pair) is fully fixed and verified either way: item 2 and item 15 now
  get two distinct components AND two distinct, correctly-sourced
  category rankings.

## 2026-08-02 — Corpus-construction experiments for the category stage

### What changed

**Gap.** Every prior corpus experiment (parent_inheritance, description
scope) added or removed text around a baseline that already included the
category's legal-prose description. The category NAME alone -- "3 edible
ices", "12.2.2 Seasonings and condiments" -- was never tried on its own,
despite reading like a food description already; the legal descriptions
share heavy boilerplate ("category" in 100/132, "this" in 98/132,
"covers" in 71/132) and were plausibly diluting the embedding signal
rather than sharpening it.

**`ExperimentConfig.corpus_mode`** (`src/category/experiment.py`), default
`"full"` (today's unchanged behaviour): `"name-only"` (code + name, no
description, no parent chain -- the null hypothesis), `"name-plus-
examples"` (name + the one sentence carrying an example clause -- "e.g.",
"for example", "Examples include", "such as" -- when the description has
one, else name only), `"strip-framing"` (same shape as "full", including
parent_inheritance, but every description used has sentence-initial stock
phrases removed first -- "This category covers/includes/comprises",
"Includes all other" -- while "Includes chocolate-coated wafers..." at a
sentence start is left alone, since it introduces real content), and
`"enriched"` (name + the distinct Codex functional classes of every
additive permitted in that category, ranked by how many permitted
additives share each class, capped at 10 -- data the legal text does not
carry at all). Plus `CONFIGS["name-only-with-component-name"]` and
`["enriched-with-component-name"]`, pairing the two most promising modes
with `include_component_name` (the best-measured flag so far, +0.17 on
both embedding and tfidf retrieval). All seven new `CONFIGS` entries are
additive; `CONFIGS["baseline"]` and every existing entry are untouched.

**`src/category/corpus.py`** grew the four new branches in
`build_documents`, plus: `_example_clause` / `_strip_framing` (both
built on one shared sentence-splitter that does not fragment "e.g. cow" --
splits only before a capital letter or "(", so an abbreviation's period
mid-example never counts as a sentence boundary), and the `"enriched"`
join -- `_functional_classes_by_id` (eu_fip's own `functional_classes`
field first, falling back to `codex_ins` via the same parent-code-widening
and CXG-36 "empty parent row, classes live on the sub-types"
sub-type-union fallback `src/substitutes/advisor.py` already uses) and
`_category_enriched_clause` (eu_fip's `status=="permitted"` rows for that
category code, ranked by class frequency). The eu_fip/codex_ins helpers
are duplicated from advisor.py rather than imported, matching this
project's established pattern of independent pure modules (advisor.py's
own DESIGN RULE comment gives the identical reasoning for not importing
the other way). `build_documents`/`build_corpus` both grew optional
`eu_fip`/`codex_ins` parameters, read only when `corpus_mode=="enriched"`
(a clear `ValueError` if that mode is requested without them, rather than
silently degrading).

**`scripts/classify_category.py`** now loads `codex_ins.json` and passes
both reference datasets through `_load_corpus` into `build_corpus` --
every other call site (`app.py`'s production `_CATEGORY_CONFIG =
CONFIGS["best"]`, which never sets `corpus_mode`) is unaffected, since
those parameters default to `None` and are only read for `"enriched"`.

**`scripts/diagnose_embeddings.py`** gained `--config` (default
`"baseline"`). It now actually calls `GeminiEmbedder` (previously it only
read the static cache file and warned about gaps) -- required for a new
`corpus_mode`'s text to have anything to diagnose at all; a repeat run of
an already-embedded config still costs zero API calls, since
`GeminiEmbedder.embed_documents` is itself cache-aware. Reports, per run:
mean/min/max pairwise similarity (with the delta against the measured
baseline, 0.7847, printed alongside), the 10 most similar pairs, document
length min/median/max, and the rank-1-to-rank-10 gap on Chipsmain's fixed
product-scope query (delta against baseline's 0.0335 printed alongside;
now embeds that query text fresh via `embedder.embed_query` instead of a
static cache lookup, so it works for a config that never ran before,
not only whichever config happened to embed it first). Prints cache
hit/miss counts before embedding. Appends one row per run to
`data/outputs/experiments.csv`, `stage="corpus_diagnostic"`, reusing
`score_category.py`'s existing 11-column header (`config`,
`retrieval_method`, `n`, `corpus_mean_similarity` populated;
`recall_at_*`/`mrr`/`index_build_seconds`/`truth_version` left blank, not
zero, since they do not apply to a corpus diagnostic) so both stages
accumulate into the one comparable table rather than a second file. The
richer per-run numbers (min/max similarity, rank gap, document lengths,
the pairs table) are console-reported only, not given their own CSV
columns -- extending the header would either rewrite 30 already-logged
historical rows or leave the file permanently ragged, and the task's own
ask for the CSV was the lightweight per-run accumulator, not a full
diagnostic export.

### Tests

- `tests/test_category.py`: +10 -- `name-only` has no description/parent
  text; `name-plus-examples` extracts the example sentence and correctly
  drops a non-example one, and falls back to name-only when no marker
  exists; `strip-framing` removes the stock leading phrase but keeps the
  informative remainder, does NOT touch "Includes chocolate-coated
  wafers..." (real content, not filler), strips "Includes all other" too,
  and still respects `parent_inheritance` (the "strip", not "drop
  parents", ablation); `enriched` lists only the functional classes of
  additives actually permitted in THAT category (excludes a class from a
  different category), falls back to `codex_ins`'s sub-type union for an
  eu_fip row with `functional_classes=[]`, caps at 10, and raises
  `ValueError` when requested without `eu_fip`/`codex_ins`.
- `uv run ruff check .` on the five touched files: clean. Full-repo run
  still shows the same 27 pre-existing errors in `src/codex/`.

**Total: 223 tests passing** (213 before this task + 10 new).

### Verified, not just written

Ran `uv run python scripts/diagnose_embeddings.py --config <name>` for
real (real Gemini embedding calls, real disk cache at
`data/reference/category_embeddings.json`), for every new config, twice
each:

- **Cache correctness.** First run of `name-only`: 25 hit / 130 miss (of
  155) -- NOT the ~100% miss a naive expectation would predict, so this
  was checked rather than assumed: 25 of `name-only`'s texts are
  byte-identical to texts already cached under `no-parent-inheritance`
  (confirmed directly -- `no-parent-inheritance` drops the parent chain
  but keeps the OWN description; for the 25 categories whose OWN
  description is empty, that reduces to exactly "code -- name", identical
  to `name-only`'s text for those same categories). This is the cache
  working exactly as designed -- content-addressed, so identical text
  legitimately reuses a vector regardless of which config name produced
  it first -- not a bug. Second run of every new config: 155/155 hit, 0
  miss. Re-running `baseline` itself printed delta 0.0000 against both
  hardcoded constants, self-confirming they were transcribed correctly.
- **Real measured numbers, config vs (mean similarity, rank-1-to-10
  gap)**, delta against baseline (0.7847, 0.0335) in parentheses:
  - `baseline`: 0.7847, 0.0335 (+0.0000, +0.0000)
  - `name-only`: 0.7612, 0.0449 (-0.0235, +0.0114)
  - `name-plus-examples`: 0.7552, 0.0413 (-0.0295, +0.0078)
  - `strip-framing`: 0.7816, 0.0393 (-0.0031, +0.0058)
  - `enriched`: 0.8107, 0.0460 (+0.0260, +0.0125)
  - `name-only-with-component-name` / `enriched-with-component-name`:
    confirmed to embed the SAME corpus text as their base mode (0 misses
    on first run, since `include_component_name` affects only query
    construction in `classifier.py`, never `corpus.py`'s documents) and
    to run without error.
- Ran `uv run python scripts/classify_category.py
  data/outputs/resolution/Chipsmain.json --config enriched` end-to-end
  (corpus -> embed -> retrieve -> filter -> write) with no exceptions,
  then re-ran with `--config baseline` to restore
  `data/outputs/category/Chipsmain.json` to its normal state (an
  untracked output file, but left as found).
- `data/outputs/experiments.csv` (untracked) now carries real
  `stage="corpus_diagnostic"` rows for every config above, verified by
  grep, sitting alongside the pre-existing `stage="category"` rows in one
  file with no column mismatch.

### Anything unexpected

- **The `name-plus-examples` marker count does not match the task's
  stated 33 of 132.** Measured directly against the real corpus with the
  literal four markers given ("e.g.", "for example", "Examples include",
  "such as", case-insensitive, union): 49 of 132 descriptions match, not
  33. Implemented and reported faithfully rather than narrowing the
  marker definition to force a match to an expectation that does not
  hold against the actual data.
- **`enriched` has the WORST mean pairwise similarity of every mode
  tested (0.8107, +0.026 vs baseline) -- worse than doing nothing.**
  Generic, EU-wide-common functional classes ("flavour enhancer",
  "preservative", "acidity regulator") appear as permitted somewhere in
  nearly every food category, so appending "typically permits: ..." adds
  shared vocabulary across categories rather than distinguishing text --
  the opposite of the intended effect. Its rank-1-to-10 gap is the best
  of the five (0.0460), so the two measures do not move together for
  this mode; this is exactly the kind of result recall@k scoring
  (`scripts/score_category.py`), not this diagnostic, would need to
  settle -- explicitly out of scope for this task.
- `name-only` and `name-plus-examples` both reduce mean similarity in the
  intended direction and both improve the rank gap, supporting the
  hypothesis that the description text has been diluting the signal --
  but the fixed sample query's rank-1 prediction under `name-only`
  changed to a plausibly-wrong category (2.3 "Vegetable oil pan spray"
  rather than 15.1 for a chips-shaped product), a reminder that a
  narrower similarity spread is not the same claim as more accurate
  retrieval. Scoring that properly needs `scripts/score_category.py`
  against `data/golden/category_truth.json`, per the task's explicit "do
  not touch retrieval methods... those come after we know whether the
  corpus is the bottleneck" -- left for a follow-up task.

## 2026-08-02 — Three retrieval strategies: MMR, multi-query fusion, hybrid RRF

### What changed

Per F-13 (docs/findings.md), the corpus is not the bottleneck and mean
pairwise similarity is not a valid retrieval-quality proxy -- this task
follows its own instruction to score strategy changes only against
`data/golden/category_truth.json`, never `diagnose_embeddings.py`.

**`ExperimentConfig`** (`src/category/experiment.py`) grew three new
fields, all default off, each a REFINEMENT of the same embedding vector
space rather than a new `retrieval_method`: `mmr_lambda: float | None =
None`, `multi_query: int = 0` (total query variants -- the original plus
`multi_query - 1` LLM-generated paraphrases), `hybrid: bool = False`.
`CONFIGS["mmr-0.7"]`, `["mmr-0.5"]`, `["mqr-3"]`, `["mqr-5"]`,
`["hybrid"]` all pair with `include_component_name=True` (the operating
config, per the task's instruction not to test against a weaker base).

**`src/category/classifier.py`** gained three pure functions: `top_k`
(shared truncation before fusion), `reciprocal_rank_fusion` (standard
RRF, k=60, used by both MQR and hybrid), and `mmr_scores` (standard MMR
-- Carbonell & Goldstein 1998 -- iterating the top-20 by raw cosine
similarity until 10 diverse candidates are selected; returns TRUE cosine
similarity for the selected set, not a synthetic MMR score, since
`classify()` re-ranks by permitted-status first regardless). `classify()`
itself gained one new PASSTHROUGH parameter, `query_variants: list[str]
| None`, stored verbatim onto `CategoryResult.query_variants`
(`src/category/schemas.py`) -- no new decision logic, so the task's "keep
classify() unchanged" instruction holds in spirit: it still only ever
consumes a `scores` dict, never knows which of the three strategies (or
none) produced it.

**`src/category/multiquery.py`** (new): `generate_paraphrases(query_text,
n, model_id)` -- one `generate_content` call asking for `n - 1`
alternative phrasings as a raw JSON array, same retry/backoff shape as
`src/extractors/gemini.py`'s `_call_model` (duplicated, not imported, per
this project's established per-module independence pattern -- see
`src/report/narrator.py`'s own copy of the identical block). Cached to
`data/reference/query_paraphrases.json`, keyed on
`sha256(query_text + model_id + n)` -- deliberately NOT keyed on the
prompt text (unlike `gemini.py`'s cache), since the prompt only asks for
a context-free paraphrase, not a project-specific behaviour that would
need invalidating on a wording tweak.

**`scripts/classify_category.py`**'s `_build_scorer` now composes: within
`retrieval_method == "embedding"`, checks `hybrid` -> `multi_query` ->
`mmr_lambda` (documented as the precedence if more than one is ever set,
though no `CONFIGS` entry does that -- untested combination). `hybrid`
fuses the embedding path's top-10 with `src/category/tfidf`'s top-10 over
the same documents via RRF. `multi_query` calls `generate_paraphrases`
per query text, embeds the original plus every paraphrase, and RRF-fuses
their top-10s. `ScoreQuery`'s return type grew from `dict[str, float]` to
`tuple[dict[str, float], list[str]]` (scores, the paraphrases actually
searched) so `_classify_file` can pass them into `classify()`'s new
parameter -- `[]` for every strategy except MQR. The output JSON gained a
top-level `"strategy"` field (`_strategy_label`: `"mmr-0.7"`, `"mqr-3"`,
`"hybrid-rrf"`, or `"none"`) alongside the existing `"config"`, so a
reader can tell how a ranking was produced without cross-referencing
`CONFIGS`.

### Tests

- `tests/test_category.py`: +17 -- `top_k` truncates/tolerates a small
  input; `reciprocal_rank_fusion` favours a document ranked well in BOTH
  input rankings over one ranked first in only one (hand-computed exact
  RRF scores), and keeps a document present in only one ranking; `mmr_scores`
  reproduces the measured Khusmain problem synthetically (three
  near-duplicate vectors dominating raw top-3) and confirms MMR picks at
  most one of them, confirms `lambda=1.0` degrades to plain top-N
  similarity (a formula boundary check), and confirms the selected set's
  scores are true cosine similarities, not synthetic MMR values;
  `classify()` passes `query_variants` through unchanged, defaulting to
  `[]`; `ExperimentConfig`'s three new fields default off; every new
  `CONFIGS` entry has the expected field values and none touch `baseline`.
- `tests/test_multiquery.py` (new): +7 -- `generate_paraphrases` returns
  exactly `n - 1` strings from a mocked model call; caches on
  `(text, model_id, n)` (a second identical call makes zero model calls);
  a different `n` is a different cache entry; strips markdown fences;
  truncates a model response that over-generates; raises `TypeError` on a
  non-list response; `n < 2` returns `[]` without calling the model at
  all (asserted via a mock that fails the test if invoked).
- `uv run ruff check .` on the nine touched/new files: clean. Full-repo
  run still shows the same 27 pre-existing errors in `src/codex/`.

**Total: 240 tests passing** (223 before this task + 17 new).

### Verified, not just written

Ran `scripts/classify_category.py --all --config X` then
`scripts/score_category.py --config X` for real, against the current
`data/golden/category_truth.json` (n=17, the same truth version F-13
used), comparing against `with-component-name` (0.53 / 0.82 / 0.66,
re-confirmed fresh this run):

- **`mmr-0.7` and `mmr-0.5`: both scored IDENTICALLY to the baseline**
  (0.53 / 0.82 / 0.66, exactly). Checked this was not MMR silently never
  running: compared `mmr-0.7`'s and the baseline's raw output JSON for
  Ice-creammain directly -- the `filtered_out` candidate lists genuinely
  differ (e.g. Inner Layer's baseline pool includes code 18.1, MMR's does
  not, replaced by a more diverse candidate), confirming MMR is real and
  active, changing which 10 candidates are considered -- it just did not
  happen to change which item wins the intersection-filtered top-3 for
  any of these 17 truth rows.
- **`hybrid`: 0.53 / 0.76 / 0.62** -- recall@1 unchanged, recall@3 down
  0.06, MRR down 0.04 from baseline. Per the task's own n=17 guidance, a
  difference under ~0.10 is one or two label-components and should be
  read as noise, not a real effect -- but it is a small decline, not a
  gain, in every direction it moved.
- **`mqr-3` and `mqr-5`: NOT completed.** `--all` (12 labels) hit the
  real Gemini free-tier daily quota partway through (20
  `generate_content` requests/day/model for gemini-2.5-flash, the model
  `generate_paraphrases` uses via `settings.PRIMARY_MODEL`) after 7 of 12
  labels succeeded. The mechanism itself is confirmed working, not just
  implemented: `data/reference/query_paraphrases.json` now holds 13 real
  cached paraphrase pairs (e.g. "Seasoning Potato Edible Vegetable Oil" ->
  "Edible Vegetable Oil Potato Seasoning" -- genuine word-order
  paraphrases, not garbage), and `query_variants` was populated correctly
  on those 7 labels' `CategoryResult`s. A full `mqr-3` run is now CHEAPER
  to finish later (those 7 labels' paraphrases are cached; only the
  remaining 5 need fresh calls), but `mqr-5` needs an entirely separate
  cache (different `n` -> different cache key) and was not attempted at
  all. Left for a follow-up run once the daily quota resets -- did not
  keep retrying against an exhausted DAILY quota, since short backoff
  cannot cross a day boundary.
- `data/outputs/category/*.json` (untracked) restored to
  `with-component-name` after every comparison run, including after the
  quota failure left 5 files mid-`mqr-3`/stale-`hybrid` -- verified by
  reading each file's own `"config"` field back to `"with-component-name"`
  before finishing.

### Anything unexpected

- **MMR changing the candidate pool did not change the final answer on
  this eval set.** This is a real result, not a wiring failure (verified
  above) -- Khusmain's own three-near-duplicates problem that motivated
  MMR may simply not be common enough across the 17 truth rows to move
  recall/MRR at this sample size, or the item that MMR's diversity
  intervention displaces from the pool is rarely the one the intersection
  filter would have promoted to rank-1 anyway. Not enough evidence either
  way at n=17 to say MMR doesn't help in general -- only that it made no
  measurable difference on THIS truth set.
- **A real API quota wall, not a code defect, is why `mqr-3`/`mqr-5`
  comparisons are incomplete.** Text-generation quota (`generate_content`)
  and embedding quota are tracked separately by the API -- `mmr`/`hybrid`
  (embedding-only) ran to completion without issue even after the
  text-generation quota for the day was already exhausted, which is what
  made it possible to still verify those two strategies live and restore
  `data/outputs/category/*.json` to a clean state afterward.
