# Build log

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
