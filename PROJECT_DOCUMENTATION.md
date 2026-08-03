# EU Food Additive Compliance Screening
## Project documentation

An Indian food manufacturer photographs a product label. The system reads it and
answers one question: **can this be sold in the EU?**

This document explains how it works, what shape the data takes at every step, every
experiment that was run, and everything that went wrong along the way.

---

# Part 1 — What problem this solves

## The question

EU law lists every food additive that is allowed, and in which kinds of food. That
list is **Annex II of Regulation (EC) No 1333/2008**. Our copy of it has **18,987
rows**.

The important thing about that list: an additive allowed in one kind of food may not
be allowed in another. So the answer always depends on two things:

1. **Which additive is it, exactly?**
2. **What kind of food is it in?**

Most of this system exists to answer those two questions reliably.

## A real example that shows why it matters

From one of our test labels, a packet of potato crisps:

```
E551 silicon dioxide in SEASONING (category 12.2.2)   →  permitted
E551 silicon dioxide in POTATO SNACKS (category 15.1) →  NOT permitted
```

Same additive. Same packet. Opposite answers. Silicon dioxide is an anticaking agent
for powder — EU law permits it in the seasoning blend, and does not permit it in the
finished crisp.

Get the food category wrong and the verdict is confidently wrong.

## Why it is hard

**Different numbering systems.** Indian labels use INS numbers (`INS 471`). EU law
uses E numbers (`E471`). Usually they match. Sometimes they do not — `INS 470(i)` has
no direct EU equivalent at all.

**Many additives are declared by name with no number.** `soy lecithin` is E322, but EU
law calls it *"Lecithins"*. `sodium bicarbonate` is E500(ii), but EU law calls it
*"sodium hydrogen carbonate"* — no shared word between them.

**Some ingredients look like additives but are not.** Turmeric is a spice *and* a
colour (E100), depending on how it is declared on the label.

**A photo is a photo.** It may be blurry, curved, badly lit, or not a label at all.

---

# Part 2 — The architecture

## Overall shape: a modular monolith

This is **not** microservices. It is one Python application, one process, one
deployment. But it is divided into six stages that are deliberately kept independent.

```
src/
  extractors/   stage 1  — read the label (a photo, via a vision model)
  extract/                — read a PASTED ingredients declaration instead
                            (text only — no photo, no model call; a second
                            entry point into the same extracted-item shape)
  resolve/      stage 2  — work out what each item is
  category/     stage 3  — what kind of food is this
  rules/        stage 4  — look up the law
  substitutes/  stage 5a — suggest replacements
  horizon/      stage 5b — what might change
  report/       stage 6  — write it up and export
  agent/                 — resolve leftovers on demand
  graph/                 — orchestration
  eval/                  — scoring
```

`extract/` and `extractors/` are two different directories, easy to
misread as a typo of each other — they are not.

## The rule that keeps them separate

**Stages communicate through JSON files on disk. They never call each other.**

```
data/outputs/extraction/Parle-Gmain.json   ← stage 1 writes
data/outputs/resolution/Parle-Gmain.json   ← stage 2 reads the above, writes this
data/outputs/category/Parle-Gmain.json     ← stage 3 reads the above, writes this
data/outputs/verdict/Parle-Gmain.json      ← stage 4 reads, writes
```

Plus one import rule: **a stage may import its own schemas and the previous stage's
schemas. Nothing else.** `src/resolve/` never imports from `src/extractors/`.

And one purity rule: **core functions take data in and return data out.** No file
reads, no printing, no settings access inside `src/`. The scripts do all input and
output.

## Why this mattered in practice

It sounds like bureaucracy. It paid for itself three times:

**Cheap experiments.** We measured 20+ retrieval configurations for stage 3 while
stage 1 ran once. Re-running resolution costs nothing — no API calls, no waiting.

**Free orchestration.** When LangGraph was added, every node was a one-line wrapper
around an existing pure function. Zero logic moved.

**Independent scoring.** Each stage has its own answer key and its own score, so you
can tell which stage broke.

## Why not microservices

Considered and rejected. One user, one deployment, one developer, all Python. None of
the four reasons to distribute applies. What it would have cost: six Dockerfiles, six
health checks, network calls that can fail between every stage, a cluster that must be
running to test one label, and a demo that breaks if one service is down.

The file boundary gives looser coupling than HTTP anyway — a service call couples you
to uptime and latency; a file does not.

---

# Part 3 — The data shape at every step

This is the part worth reading carefully. Each stage adds a **new** record keyed on
`item_id`. **Nothing is ever copied forward.** Adding a field upstream cannot break
anything downstream.

We will follow one real ingredient the whole way: `1101 ( ii )` from a Parle-G biscuit
label.

## Step 0 — the input

```python
image_bytes = Path("data/labels/Parle-Gmain.jpeg").read_bytes()   # ~200 KB
```

No schema. Just bytes. Not resized, not cropped.

## Step 1 output — `ExtractedItem`

**What the model is asked:** *what is printed here?* Nothing more.

```json
{
  "item_id": 10,
  "position": 7,
  "nesting_depth": 0,
  "parent_item_id": null,
  "verbatim": "1101 ( ii )",
  "name_as_declared": null,
  "declared_role": "FLOUR TREATMENT AGENT",
  "role_source": "group_heading",
  "declared_code": "1101 ( ii )",
  "code_system": "bare",
  "percentage": null,
  "emphasis": false,
  "footnote_marker": null,
  "confidence": 1.0
}
```

Field by field, in plain terms:

| Field | Means |
|---|---|
| `item_id` | a number so later stages can point at this item |
| `position` | where it appears in the list; shared by items from one bracket |
| `nesting_depth` | 0 = top level, 1 = inside a bracket, 2 = inside that |
| `parent_item_id` | what it sits inside, if anything |
| `verbatim` | exactly as printed — note the spaces inside the brackets |
| `name_as_declared` | null, because only a code was printed |
| `declared_role` | the functional class that introduced it |
| `role_source` | how it was attached: a colon, a bracket, or neither |
| `declared_code` | the code as printed, untouched |
| `code_system` | `E`, `INS`, `bare` or `none` — which system was printed |
| `emphasis` | is it styled differently from surrounding text |
| `footnote_marker` | a `+` or `#` tying it to a footnote |

### The rule that shapes everything

There is deliberately **no `is_additive` field** and **no canonical ID**.

> The extractor reports **observations only**. The model records what is *printed*;
> a later deterministic step decides what it *means*.

**Why this earned its place:** a Chips label prints `INS 470(i)`. If the model had been
allowed to convert it, it would plausibly have written `E470` — which looks right and
is wrong. Because it copied verbatim, we later *discovered* that `INS 470(i)` has no
E-number equivalent at all, and getting from one to the other needs a name comparison.

**A field the model cannot fill is a mistake it cannot make.**

### The container

```json
{
  "declaration_verbatim": "INGREDIENTS : REFINED WHEAT FLOUR (MAIDA)...",
  "language": "en",
  "items": [ ...12 items... ],
  "unparsed_fragments": [],
  "warnings": ["declaration is uniformly styled; emphasis not determinable"],
  "allergen_statements": ["CONTAINS : WHEAT, MILK"],
  "footnotes": {"#": "(D - GLUCOSE, LEVULOSE )"},
  "declaration_statements": ["CONTAINS ADDED FLAVOUR (...)"]
}
```

Four separate buckets so nothing is silently dropped. `unparsed_fragments` means *the
parser failed*; everything else has a home. Each bucket was added because text was
disappearing.

## Step 2 output — `ResolvedItem`

**What this stage asks:** *what is this thing?* No AI at all.

```json
{
  "item_id": 10,
  "canonical_ins": "1101(ii)",
  "eu_canonical_id": null,
  "classification": "enzyme",
  "normalised_role": "Flour treatment agent",
  "codex_functional_classes": ["Flavour enhancer"],
  "resolution_method": "code_exact",
  "resolution_confidence": 0.98,
  "flags": ["role_class_mismatch"],
  "candidates": [],
  "matched_on": "1101(ii)"
}
```

Notice:

- `item_id` is the **only** link back. Nothing is copied.
- `canonical_ins` is normalised — the spaces are gone.
- `eu_canonical_id` is **null**: this is papain, an enzyme, and enzymes are governed by
  a different EU regulation.
- `role_class_mismatch` is flagged: the label says *flour treatment agent*, the
  international standard says papain is a *flavour enhancer*. Recorded, not resolved.

**This stage reads only 5 of the 14 fields above** — `item_id`, `declared_code`,
`name_as_declared`, `declared_role`, `verbatim`.

## Step 3 output — category candidates

**What this stage asks:** *what kind of food is this?*

```json
{
  "component_label": null,
  "query": {
    "text": "REFINED WHEAT FLOUR SUGAR REFINED PALM OIL IODISED SALT MILK SOLIDS",
    "fields_used": ["ingredient_names"],
    "additive_ids": ["330", "503", "500(ii)", "472e"]
  },
  "top3": [
    {"code": "11.1", "name": "Sugars and syrups...", "similarity": 0.66, "permitted": false},
    {"code": "7.2",  "name": "Fine bakery wares",     "similarity": 0.65, "permitted": false},
    {"code": "11.2", "name": "Other sugars and syrups","similarity": 0.65, "permitted": false}
  ],
  "empty_intersection": true
}
```

Note the correct answer — `7.2 Fine bakery wares` for a biscuit — is **rank 2**. That
is the central weakness, and the next step is the response to it.

## Step 4 output — `ItemVerdict`

**What this stage asks:** *what does the law say?*

```json
{
  "item_id": 10,
  "eu_canonical_id": null,
  "additive_name": "Papain",
  "component_label": null,
  "headline": "out_of_scope",
  "verdict_certainty": "certain",
  "category_sensitive": false,
  "by_category": [],
  "flags": ["enzyme_reg_1332_2008"]
}
```

For an additive that *is* assessed, `by_category` holds one entry per candidate
category:

```json
{
  "fcs_code": "7.2",
  "category_name": "Fine bakery wares",
  "rank": 1,
  "verdict": "permitted_with_conditions",
  "max_level_mg_kg": null,
  "max_level_basis": "gmp",
  "conditions": "Permitted via Group I, Additives; ML = quantum satis; except...",
  "note_codes": [],
  "source_url": "https://ec.europa.eu/food/food-feed-portal/...",
  "retrieved_date": "2011-12-01"
}
```

## The funnel

```
14 fields written by extraction
 →  5 read by the resolver
 →  3 read by the category stage
```

Each stage consumes **less** than the last. That is why the boundaries hold.

## The worked example, end to end

```
label prints        "FLOUR TREATMENT AGENT [1101 ( ii )]"
extraction records  declared_code "1101 ( ii )", code_system "bare"
resolver normalises "1101(ii)", finds PAPAIN in the standard
                    label says flour treatment agent, standard says flavour
                    enhancer → flag role_class_mismatch
                    absent from EU additive law → classification "enzyme"
rule engine         out_of_scope — Reg 1332/2008 governs enzymes, not Annex II
```

**Why this case matters.** A naive closed-list check would reason: *"not in the Union
list, therefore not permitted"* — and produce a false export failure on a perfectly
legal biscuit.

---

# Part 4 — Each stage in detail

## Stage 1 — Reading the label

Two calls to a vision model.

**Call 1, the gate.** *Is this a food label? Is there an ingredients list? What
languages? Where is the panel?*

Asked separately so that a nutrition table with no ingredients list stops here, rather
than wasting a second call and returning an empty result.

**Call 2, the extract.** *Break the declaration into items.*

### Four defences around every model call

Each exists because it actually went wrong.

**1 — Fence stripping.** The model wraps its answer in ```` ```json ```` despite the
prompt saying not to. Direct evidence that a prompt is a *request*, not a guarantee.

**2 — Empty response.** Sometimes the model returns no text at all — blocked or
truncated. The library hands back `None` rather than raising, and our code crashed on
it. Now it counts as a failed attempt and retries.

**3 — Backoff on 429 / 500 / 503.** Started as 429 only. A 503 on image 3 of 10 killed
a whole batch run and images 4–10 never executed.

**4 — Caching on four things:** image + model ID + which call + **the prompt text**.

- Without the model ID, comparing two models returns the first one's cached answer and
  they look identical.
- Without the prompt text, editing a prompt returns the *old* result and you conclude
  the fix did not work.

### Three places it stops

| Condition | Result |
|---|---|
| not a food label | stop, with the reason |
| a label but no ingredients list | stop — *"please photograph the ingredients panel"* |
| zero items extracted | **stop — this is a failure, not a clean result** |

The third matters most. A screening that returns nothing and reports success reads
downstream as *"no problems found"* — the most dangerous output this system could
produce.

## Stage 2 — Working out what each item is

No AI. A fixed sequence of lookups.

### What it looks things up in

| File | Contents |
|---|---|
| `codex_ins.json` | 668 substances from the international standard: number, official name, other names, function |
| `label_aliases.json` | 66 everyday names the standard omits, 12 genuinely ambiguous names, and a food-ingredient list |
| `eu_fip.json` | 18,987 rows of EU law |

### Two routes in

Across the test labels: **48 items had codes, 140 had only names.**

**The code path:**

```
1  tidy it up      "INS 470(i)", "503 ( ii )", "E471"  →  470(i), 503(ii), 471
2  look it up      works for 24 of 29 distinct codes
3  try the family  451(i) not listed, but 451 is — use it and flag the widening
4  give up         mark unresolved. Never guess a near neighbour.
```

**The name path — the order is critical:**

```
1  exact match against the official name
2  match against listed alternative names
3  match against our hand-written alias list
4  ── IS IT A FOOD? ──  if yes, STOP. Not an additive.
5  is it one of the 12 known-ambiguous names? → send to a person
6  fuzzy match, allowing small spelling differences
7  give up → unresolved
```

**Step 4 must come before step 6.** If fuzzy matching runs first, `turmeric powder`
matches E100 curcumin — which *is* a real additive made from turmeric. The spice in a
masala would be reported as a food colour.

### The family guard

Some additives have names differing by one letter and are entirely different
substances in law:

```
E472a  E472b  E472c  E472d  E472e  E472f
E160a  E160b  E160c  E160d  E160e  E160f
E306   E307   E308   E309
```

A fuzzy match scoring 0.94 against E472c is not evidence — it is noise that happens to
score well. So a match landing in one of these families **without** the distinguishing
letter in the original text is forced to manual review, whatever the score.

### Classification

| Class | Meaning |
|---|---|
| `additive` | in the standard and in EU law |
| `food_ingredient` | flour, sugar, milk — not regulated as an additive |
| `flavouring` | a different regulation (1334/2008) |
| `enzyme` | a different regulation again (1332/2008) |
| `compound` | a container like *Seasoning* holding other things |
| `ambiguous` | we know the family, not the member |
| `unknown` | could not identify |

**One hard rule:** if it appears in EU additive law, it **is** an additive, whatever the
label's wording suggests. That rule caught a real bug where *"Flavour Enhancers"* was
being read as a flavouring and two genuine additives were skipped entirely.

### Confidence comes from method, never from a model

```
0.98  exact code or exact name
0.95  a listed alternative name
0.92  our alias list
0.90  the family parent
0.80  fuzzy match
0.75  matched by comparing names across systems
0.00  ambiguous or unresolved  →  a person must decide
```

**Anything below 0.80 never reaches the law lookup.** A wrong verdict is worse than no
verdict.

### Measured result on the hardest label

`EU_productmain` declares **7 additives by name only**, no codes at all.

```
against EU law alone                 3 of 7 identified
with the standard + our alias list   6 identified, 2 correctly sent to review
```

`sodium bicarbonate` is the clearest case: EU law calls it *"sodium hydrogen
carbonate"*. No amount of clever matching bridges that. It needs a human-written alias.

## Stage 3 — What kind of food is this

### How the search works, in plain terms

Each of the 155 EU food categories has its description turned into a list of **768
numbers**. That list captures *meaning*, not words. The same is done to a description
of the product. Then we measure which category's numbers are closest.

This is what "embeddings" means. It finds *fine bakery wares* for a biscuit even
though the label never uses those words.

### One category is not enough

A packet of crisps is not one food:

```
the crisp itself      →  15.1 potato snacks
the seasoning powder  →  12.2.2 seasonings and condiments
```

All six additives on that label sit **inside** the seasoning bracket, so they must be
judged against the seasoning's category.

**We found this because E551 kept returning "not permitted" and we could not work out
why.** It is an anticaking agent for powder — EU law permits it in seasoning and not in
the finished crisp. The law was right; we were asking the wrong question.

So: one search for the product, plus one for every component that contains additives.

### How well it works

```
right answer ranked first       0.53
right answer in the top three   0.82
```

Not good enough to trust alone. Good enough to offer three options to a person —
**but the person is not limited to those three.** The confirmation screen shows the
top three as radio options, and underneath, inside a "Choose another category"
expander, a searchable dropdown lists **all 155 EU food categories** (`app.py:938-941`).

**This override is not a nicety, it is load-bearing.** recall@3 = 0.82 means for
18% of components the right category is not in the top three at all — it does not
exist to be picked. Without the full list, those components could never be
confirmed correctly, no matter what the reviewer chose.

## Stage 4 — Looking up the law

No AI. A table lookup and a decision tree.

### The order of checks

```
1  out of scope?          flavouring, enzyme, or food → stop, no lookup
2  failed to identify?    unresolved — NOT "banned", we just do not know
3  prohibited outright?   six substances are banned EU-wide (titanium dioxide
                          among them). Independent of category, so checked early.
4  in the law at all?     if not: not authorised. Also category-independent.
5  look up the row        no row but exists elsewhere → not permitted in THIS category
                          a maximum level               → permitted up to that level
                          conditions text               → permitted, read the conditions
                          nothing else                  → permitted, no numeric limit
```

### The distinction that matters most

**`not_permitted_in_category`** — legal in the EU, just not in this kind of food. The
manufacturer can move it or reformulate. **Fixable.**

**`not_authorised_eu`** — not on the permitted list anywhere. No reformulation fixes it.
**A dead end.**

A naive lookup reports both as "not found". Telling someone their product is
unsalvageable when they just need to move an ingredient is a serious error.

### All three categories are evaluated, not just one

Because category matching is only 53% right first time, the engine computes the verdict
under **every** candidate and compares:

```
all three agree  →  verdict is certain
they disagree    →  the verdict DEPENDS ON THE CATEGORY
```

A category-dependent block is **not** reported as blocked. It is reported as:

> *"not permitted under 11.1 sugars, permitted under 7.2 bakery wares — confirm the
> category before treating this as a compliance failure"*

**Why this was added:** Parle-G originally reported *3 items NOT PERMITTED*. We had
already worked out by hand that all three **are** permitted. The category was wrong and
the engine reported the wrong answer with full confidence.

### The family fallback

EU law often records a permission for a whole family, not each member:

```
500      "sodium carbonates"           104 rows, including bakery wares
500(ii)  "sodium hydrogen carbonate"     2 rows, none in bakery wares
```

`500(ii)` is baking soda. A biscuit obviously contains it. Looking up `500(ii)` alone
finds nothing and would report *"not permitted"* — telling a manufacturer their biscuit
fails on bicarbonate of soda.

So before declaring a failure, the parent is tried: `500(ii)` → `500`. And the widening
is flagged so a reviewer can see it happened.

### The conditions — 96% of rows carry them

```
"only tuna"
"only fat-containing cereal-based foods including biscuits and rusks"
"Note 18: E 300, E 301 and E 302 are authorised individually or in combination"
```

**We do not interpret these.** They are shown word for word.

**Why not parse them:** this is prose written by lawyers, with exceptions inside
exceptions. A parser that gets it 90% right is worse than no parser, because the 10% is
invisible. Showing the text is honest.

### What it never says

> *"This product is cleared for export."*

A label tells you an additive is **present**. It does not tell you **how much** was
used. Half the permissions carry numeric limits a label cannot verify. The system says
so, every time.

## Stage 5a — Substitutes

For anything genuinely blocked, find alternatives that:

- do the same **job** (a colour must be replaced by a colour)
- are permitted in the **same** food category
- are permitted at the same level or higher
- do not bring a new warning label with them

**Real result:** Fast Green FCF is blocked in a khus syrup. The system proposes
chlorophylls (E140) — a green colour, permitted in flavoured drinks.

**The honest caveat, stated every time:** doing the same job is not the same as being
interchangeable. Titanium dioxide's opacity is famously hard to replace, which is why
the industry struggled after the 2022 ban. These are **candidates for a formulator to
test**, not recommendations.

## Stage 5b — Regulatory horizon

An additive can be legal today and banned in three years. Titanium dioxide is the known
example:

```
2016  EFSA begins re-examining it
2021  EFSA publishes: no longer considered safe
2022  banned
```

For those six years the correct verdict was *permitted*. But a manufacturer building a
product line on it would have wanted to know.

**The rule this lane obeys:** it can raise a flag. It can **never** change a verdict.
An EFSA opinion is not law. There is a test asserting `affects_verdict is False`.

It also stops the substitute advisor recommending a replacement that is itself under
review.

## Stage 6 — Narration and export

The one place an AI model writes for a person. It receives the **finished** assessment
and rewrites it in plain English. It looks nothing up. It adds no facts. It decides
nothing.

It is instructed to:

- never write *"banned"* — write *"not authorised as a food additive"*
- never claim the product is cleared for export
- explain jargon on first use
- say if the category was not confirmed by a person

**Then it is checked.** Every E-number, level and category code in the prose must appear
in the data handed to it. Anything that does not is recorded in `unfaithful_claims`.

If the model is unavailable, a fixed deterministic summary is shown instead.

## The agent — for items nobody could identify

About 20 items across the test labels end up needing a person. Each needs a **different**
approach:

```
"modified cornstarch"  17 possible matches  →  needs narrowing
"INS 924"              in no reference data →  needs a web search
"Hazelnut Pieces"      food or additive?    →  needs a lookup
```

**That is why this one is an agent:** it chooses which tool to use per item. A fixed
pipeline would do the same thing to all of them.

Six tools, all wrapping existing functions: `lookup_codex`, `lookup_eu_fip`,
`check_food_lexicon`, `list_family_members`, `get_product_context`, `search_web`.
A LangGraph loop with a **step cap of 5**.

**It proposes. A person decides.** The agent never writes to a verdict.

---

# Part 5 — Orchestration

## Why LangGraph

Category recall@1 is 0.53, and 20+ configurations were measured without improving it.
So **human confirmation is a design requirement, not a fallback.**

That confirmation was previously hand-rolled **twice**: as a `--category` CLI flag
applied *after* a wrong verdict was computed, and in the Streamlit app as manual
pause/resume through session state with a stage marker and a full script rerun.

LangGraph provides `interrupt()` and `Command(resume=...)` with durable checkpointing
as a primitive. It replaces hand-written machinery with the thing it was imitating.

**What gets confirmed is not limited to the retrieved candidates.** `confirm_node`'s
`interrupt()` payload carries the top three per component (see Stage 3), but the
resume value it accepts is any valid EU category code — app.py's confirmation screen
resumes it with a full searchable list of all 155 categories when the reviewer opens
"Choose another category", not just one of the three offered.

## The graph

```
extract → resolve → [Send: one classify per component] → confirm (interrupt)
        → verdict → [substitutes ‖ horizon] → narrate → END
```

Three primitives doing real work:

**`Send`** — one `classify_node` per component, running concurrently. An ice cream with
an inner and outer layer classifies both at once.

**`interrupt()`** — execution stops, state is checkpointed, the caller resumes with
`Command(resume={component: code})`. On a composite label it fires **per component** —
two independent, separately resumable pauses.

**Parallel branches** — substitutes and horizon both read the verdict and do not depend
on each other.

**Every node is a thin wrapper.** `resolve_items`, `classify`, `evaluate`,
`find_substitutes`, `find_horizon_signals`, `narrate` are all called unmodified and
remain callable from the standalone scripts.

## An API limitation found by testing

`Command(resume=...)` **cannot re-pause the same checkpoint with a different value.**
Resuming the same checkpoint twice returns the first resume's result both times.

So the UI's "Back" button starts a **fresh thread** rather than replaying. This
contradicts what the "time travel" framing in the documentation implies, and was found
with a throwaway script before anything relied on it.

---

# Part 6 — How it is measured

## The mechanism

Two folders with the same filenames:

```
data/golden/    the RIGHT answer, written by hand, never regenerated
data/outputs/   what the system produced this time
```

A script compares them and prints a score. **If the score drops, the script exits with
an error** — so a change that breaks something fails loudly instead of silently.

## The scores

| Stage | Metric | Result |
|---|---|---|
| Reading the label | additive code recall | **57 / 57 = 1.00** |
| Identifying items | canonical INS / EU code / classification | **202 items, 1.00 / 1.00 / 1.00** |
| Food category | recall@1 · recall@3 · MRR | **0.53 · 0.82 · 0.66** |
| The agent | correct · **wrong** · correctly declined · wrongly declined | **15 · 0 · 4 · 3** |
| End to end | verdict accuracy | **not measured — see limitations** |

## Why the agent's scores are never combined

```
correct              15
wrong                 0   ← the number that matters
correctly declined    4
wrongly declined      3
```

A **wrong** proposal is dangerous because a human reviewer may accept it. A **decline**
only leaves work undone. Combining them into one "86% accuracy" figure would hide the
distinction that matters.

**Declining is a success, not a failure.** All four genuinely ambiguous items —
modified cornstarch (×2), calcium phosphate, Stevia — were declined rather than guessed,
because the label genuinely does not determine which of 17 / 3 / 4 candidates is meant.

**The three wrongly-declined items are a different kind of miss.** The repo
evaluation (`docs/findings.md` F-16) traces all three to infrastructure limits, not
reasoning failures: one from `search_web` quota exhaustion, two from hitting the
5-step cap before reaching a final answer. The agent did not misjudge these cases —
it ran out of budget before it could.

## What is deliberately not scored

Free-text fields — `declared_role`, `verbatim`, model confidence — are excluded from
every scorer. They vary between runs (we observed *"Emulsifiers"* becoming
*"Emulsifiors"* on the same image). **No verdict depends on them, so no metric should.**

---

# Part 7 — Every experiment

All scored against hand-checked answers, all logged to `data/outputs/experiments.csv`
with a `truth_version` hash.

## Retrieval methods

| Config | recall@1 | recall@3 | MRR | Outcome |
|---|---|---|---|---|
| embedding baseline | 0.41 | 0.65 | 0.53 | |
| **embedding + component name** | **0.53** | **0.82** | **0.66** | **adopted** |
| TF-IDF (keyword) | — | 0.24 | — | rejected |
| TF-IDF + component name | — | 0.41 | — | rejected |
| ChromaDB (vector store) | 0.41 | 0.65 | 0.53 | identical, not adopted |
| ChromaDB + component name | 0.53 | 0.82 | 0.66 | identical |
| MMR λ=0.7 | 0.53 | 0.82 | 0.66 | no change |
| MMR λ=0.5 | 0.53 | 0.82 | 0.66 | no change |
| Hybrid RRF (dense + sparse) | 0.53 | 0.76 | 0.62 | declined |
| Multi-query (MQR) | — | — | — | incomplete, quota |

## Corpus constructions

| Config | recall@1 | recall@3 | MRR |
|---|---|---|---|
| **full text + component name** | **0.53** | **0.82** | **0.66** |
| strip-framing | 0.47 | 0.55 | — |
| baseline (full, no component name) | 0.41 | 0.53 | — |
| name-only + component name | 0.41 | 0.71 | 0.55 |
| name-only | 0.35 | 0.71 | 0.51 |
| name-plus-examples | 0.35 | 0.59 | 0.47 |
| enriched (with functional classes) | — | ≈baseline | — |

## Query composition

| Config | Effect |
|---|---|
| **+ component name** | **+0.17 on BOTH retrieval methods — the single biggest lever** |
| + product description, all queries | no net gain: fixed product misses, broke component queries |
| + product description, product only | best MRR (0.63) but lower recall@3 |

## Other measured decisions

| Component | Measured | Outcome |
|---|---|---|
| Crop-and-upscale | identical extraction where it worked; **0 items instead of 25** on one label | **removed** |
| LLM self-reported confidence | returned 0.0 alongside a confident yes; every item exactly 1.0 | **replaced with derivation** |
| LangChain | would rewrite 3 tested call sites for identical behaviour | **not adopted** |
| Microservices | one user, one process, all Python | **not adopted** |

---

# Part 8 — Problems and how they were fixed

Every one of these was found by running the system on real labels, not by reasoning
beforehand.

## The website blocked us

**Problem.** The international additive database returned **403 Forbidden**, and its
`robots.txt` forbids automated access to every page pattern we needed.

**Fix.** The same organisation publishes the whole standard as a PDF, at a URL the
block does not cover. We parsed 140 pages instead.

**Result.** 668 records, zero validation failures — and a *better* source: one dated,
citable document instead of 400 scraped pages.

## The model's confidence was meaningless

**Problem.** It reported `0.0` while confidently identifying a food label with clear
evidence. Every extracted item came back at exactly `1.0`.

**Fix.** Stopped asking. Confidence is computed from countable evidence.

**Result.** A number that can be explained. *"2 of 5 markers found"* is defensible;
*"0.87 because the model said so"* is not.

## A feature we added made things worse

**Problem.** We cropped the photo to the ingredients panel and enlarged it, on the
reasonable theory that small print needs more pixels.

**Measured.** Across 10 labels it changed nothing where it worked — and on one label it
cropped the wrong part of the pack and returned **zero items instead of 25**.

**Fix.** Turned off by default, code kept behind a flag.

**Why it matters.** A component removed because it was measured, not because it was
guessed at.

## Word-matching rules kept failing — three times

```
"ising → izing" for UK spelling      turned "raising agent" into "raizing agent"
a check for "flavour enhancer"       missed the plural printed on the label
a check for the word "flavour"       matched "flavour enhancer" and marked two
                                     REAL additives as out of scope
```

**Fix, each time.** Replace the clever pattern with an explicit list, or with a lookup
against authoritative data.

**Lesson.** Regulatory vocabulary defeats general text rules.

## The system said a product was fine when it was not

**Problem.** A khus syrup contains Fast Green FCF, not authorised in the EU at all. The
system reported: *"No item was found not permitted."*

**Cause.** The resolver correctly distinguished *"identified, and absent from EU law"*
from *"could not identify"*. The verdict stage collapsed both into `unresolved` with a
single `OR` condition.

**Why it matters.** Each stage passed its **own** tests. Only running a real label with
a genuinely unauthorised additive exposed it.

**Fix.** Split the branch. Added a test asserting the summary can never report a clean
result when a blocking item is present.

**This is the strongest argument in the project for end-to-end testing.**

## One additive, two places, one answer

**Problem.** An ice cream declares maltitol twice — once in the ice cream, once in the
chocolate coating. Both came back labelled *"Outer Layer"* with the same category.

**Cause.** The lookup was keyed on the **additive**, not on the **item**. Two entries
for maltitol collapsed onto one routing decision.

**Fix.** Key everything on `item_id` and walk each item's own parent chain.

## The measurement we were using was wrong

**Problem.** A diagnostic measured how "spread out" the 155 category descriptions were
in embedding space. We treated a low spread (0.7847 mean pairwise similarity) as the
ceiling on achievable accuracy.

**Then we tested it properly.** One corpus version had the **best** spread (0.7552) and
the **worst** accuracy (0.59 vs 0.82). The proxy moved in the wrong direction.

**Fix.** Stopped using it to judge anything. Everything scored against hand-checked
answers instead.

**Lesson.** A proxy metric not validated against ground truth can be actively
misleading — and we had reasoned from it for several days.

## A dataset we could not use

**Problem.** EFSA publishes all its assessments as a downloadable file. We wanted it for
the horizon feature.

**What we found.** 18 linked sheets joined by internal UUIDs, keyed on **CAS registry
numbers**. **No E-numbers anywhere.** The entire system is built on E-numbers, and
nothing available links the two. A search for "sucralose" returned 82 rows, all CAS
substring collisions.

**Fix.** Abandoned automated ingestion. Built a small hand-checked list instead, and
documented that it is partial — on every screen, unconditionally.

**Lesson.** *"The data exists"* and *"the data is usable"* are different claims.

## Two runs, two answer keys

**Problem.** A ChromaDB run appeared to beat the logged baseline (0.41/0.65 against
0.35/0.59). A fresh baseline run then matched ChromaDB exactly.

**Cause.** The earlier row predated an edit to the answer key. The two rows were scored
against **different ground truth**.

**Fix.** Added a `truth_version` hash to every row in `experiments.csv`, and a warning
when the current version differs from the last logged one.

---

# Part 9 — What we are not claiming

**The test set is 12 labels.** That is small. Differences under about 0.10 on the
category metrics are one or two label-components and should be treated as noise.

**The correct food categories were AI-drafted and not verified by a regulatory
expert.** So the category numbers are useful for comparing configurations against each
other, but they are **not a validated accuracy claim**.

**All the labels are Indian.** The multilingual handling built for European packs was
never exercised — every label returned `["en"]`.

**End-to-end accuracy is unmeasured.** Stage-local metrics are strong, but
`extraction = 1.00` and `resolution = 1.00` do not imply the verdict is right, because
category retrieval sits between them at 0.53 and is outcome-determining.

**The system reads what a label declares.** It cannot detect undeclared additives,
adulterants or contamination. RASFF alerts on Indian food exports have largely involved
unauthorised dyes — substances illegal in both jurisdictions and therefore never
declared. Laboratory analysis is the control for those, not label reading.

**Conditions text is surfaced, not interpreted.** A permission reading *"only tuna"*
narrows below its category, and the system does not evaluate that.

**Carry-over (Article 18) is not modelled.** An additive may be lawfully present in a
compound food if permitted in one of its ingredients. The engine does not know this.

**Only depth-0 components are scoped.** A biscuit nested inside a centre inside a
chocolate bar is attributed to the centre, not given its own category.

---

# Part 10 — The whole thing in one paragraph

A model reads the photo. Fixed lookups work out what each ingredient is. A search
suggests what kind of food it is, and a person confirms that — because the search is
right first time only about half the time, and the answer depends on it. A database
lookup produces the verdict, with a link to the law and the conditions in full. Extras
suggest replacements and flag what might change. A model writes it up, and a checker
confirms it invented nothing.

**Models at the edges. A lookup in the middle. A person at the point where the
measurement said one was needed.**
