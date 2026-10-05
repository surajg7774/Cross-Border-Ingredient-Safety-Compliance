"""Prompt templates used to ask extraction models about label images."""

GATE_PROMPT = """You are looking at a photograph of a product package.

Decide whether this image shows a FOOD product label with a visible
ingredients declaration. Look for this evidence and report which of it
you actually found:
  - "ingredients_keyword": a word meaning "ingredients" (or a translation of
    it) introducing a list
  - "nutrition_table": a nutrition facts / nutritional information table
  - "net_quantity": a net weight or volume (e.g. "250 g", "1 L")
  - "e_number_pattern": any additive code in the ingredients list — an
    E-number (E100, E250), an INS number ("INS 471"), or a bare additive
    number in brackets after a functional class, e.g. "Colour (150d)",
    "Raising Agents [503(ii), 500(ii)]", "Emulsifiers (442, 476)"
  - "best_before": a best-before / use-by date or lot code

Also:
  - Read off the product name and a short product descriptor (e.g. flavour,
    variant) if visible.
  - Detect EVERY language you can see printed anywhere on the pack.
  - Select exactly ONE language to extract from: prefer English if present,
    otherwise the language with the most complete, clearly legible
    ingredients declaration.
  - Give a normalized bounding box [x_min, y_min, x_max, y_max] covering the
    ingredients declaration in the SELECTED language, PLUS anything directly
    attached to it that appears immediately after: footnote lines explaining
    a marker such as "+", "#" or "*" (e.g. "+Used as natural flavouring
    agent"), and allergen statements ("Contains: ...", "May contain ...",
    "Allergen advice: ..."). Extend the box downward to include these.
    Do NOT extend it to cover the nutrition table, storage instructions,
    marketing text, or barcodes. Each value is between 0 and 1 relative to
    image width/height. Set this to null if you cannot locate the
    declaration.
  - Separately from is_food_label, decide has_ingredients_declaration: is an
    actual ingredients list (not just a nutrition facts table) visible
    anywhere in the image? A photo showing only a nutrition facts panel, with
    no ingredients list, is still is_food_label=true but
    has_ingredients_declaration=false.

If this is not a food label (e.g. it's a non-food product, a blank surface,
a person, or text unrelated to a food package), set is_food_label to false
and explain why in reject_reason.

Respond with RAW JSON ONLY. No markdown code fences, no commentary before or
after. Match this exact shape:

{
  "is_food_label": true,
  "has_ingredients_declaration": true,
  "evidence_found": ["ingredients_keyword", "e_number_pattern"],
  "reject_reason": null,
  "product_name": "string or null",
  "product_descriptor": "string or null",
  "languages_detected": ["en", "fr"],
  "language_selected": "en",
  "ingredients_panel_bbox": [0.0, 0.0, 1.0, 1.0]
}
"""

EXTRACT_PROMPT = """You are transcribing the ingredients declaration from a
food label image, in the language: {language}.

Parse it into an ORDERED list of items, one per ingredient or sub-ingredient,
following these rules exactly:

1. NESTING: when an ingredient is followed by a bracketed breakdown of its
   own sub-ingredients (e.g. "chocolate (sugar, cocoa mass, emulsifier:
   soya lecithin)"), the parent gets nesting_depth 0, and each sub-ingredient
   inside the brackets gets nesting_depth 1 (or deeper if brackets nest
   further) and parent_item_id set to the parent's item_id. Top-level items
   have parent_item_id = null and nesting_depth = 0.

   A component may also be introduced by a NAME FOLLOWED BY A COLON, where
   the name describes a part of the product rather than a functional class
   — e.g. "Inner Layer (Ice Cream 68%): ...", "Outer Layer (32%): ...",
   "Filling: ...", "Coating: ...", "Seasoning: ...". Treat this exactly
   like a bracketed breakdown:
     - create an item for the component itself at nesting_depth 0, with
       name_as_declared set to the component name and its percentage
       captured if printed
     - every ingredient belonging to that component gets nesting_depth 1
       (or deeper) with parent_item_id pointing at the component
     - role_source stays "none" — a component is not a functional class

   Do NOT confuse this with rule 2. "Preservative: Potassium Sorbate" is a
   FUNCTIONAL CLASS before a colon (role_source "explicit_prefix").
   "Outer Layer (32%): Chocolate Paste, Cocoa Butter" is a COMPONENT before
   a colon (nesting, role_source "none"). The test is the same as rule 3:
   is the text before the punctuation a functional class, or not?

   A bracket holding a SINGLE term that is an alternative name for the
   ingredient it follows (e.g. "Refined Wheat Flour (Maida)") is not a
   breakdown — do not create a child item. Keep the whole thing as one
   item, with name_as_declared set to the primary name and the alternative
   left in verbatim. If you are unsure whether a single bracketed term is
   an alternative name or a genuine sub-ingredient, create the child item
   — a spare item is safer than a missing one.

2. FUNCTIONAL-CLASS PREFIXES: when an item is introduced by a functional
   class name before a colon (e.g. "emulsifier: soya lecithin"), set
   declared_role to that class ("emulsifier") and role_source to
   "explicit_prefix". name_as_declared is just the ingredient name after the
   colon ("soya lecithin").

3. GROUP HEADINGS: when a functional class introduces a list of codes or
   names in one group, create ONE item per code/name in that group. ALL of
   them get the SAME position (their shared position in the declaration),
   declared_role set to that class, and role_source "group_heading". This
   applies for EITHER bracket style — round "( )" or square "[ ]" — for
   example both "colours (E160a, E102)" and "RAISING AGENTS [503(ii),
   500(ii)]". Items within a group may be separated by a comma, "&", or the
   word "and" — treat all three the same way.

   Brackets alone do NOT decide this. What decides it is whether the text
   BEFORE the bracket names a FUNCTIONAL CLASS (e.g. raising agents,
   emulsifier, colour, preservative, thickener, anticaking agent, acidity
   regulator, flavour enhancer, stabiliser, humectant, leavening agent,
   flour treatment agent, moisture retaining agent, sequestrant,
   antioxidant, firming agent, glazing agent, propellant, and "permitted
   <class>" forms such as "Permitted Emulsifying", "Permitted Natural
   Colour" — this list is illustrative, not exhaustive: any term naming a
   TECHNOLOGICAL FUNCTION rather than a substance is a functional class) or
   an INGREDIENT.
     - A functional class followed by a bracketed list -> one item per
       entry, all "group_heading", all sharing the class's position.
     - An INGREDIENT followed by a bracketed breakdown -> that is NESTING
       under rule 1: parent at nesting_depth 0, contents at nesting_depth 1
       with parent_item_id set, and role_source "none".
     - A bracketed group containing only ONE code is still a group — use
       "group_heading", not "explicit_prefix". A colon means
       "explicit_prefix"; a bracket after a functional class means
       "group_heading".

   A functional class followed by a bracket is ALWAYS "group_heading",
   whether the bracket holds one code or several, and whether the class is
   singular or plural. "Emulsifier (322)", "Humectant (451(i))" and
   "Thickeners (508 & 412)" are all group headings. In every such case:
     - create one item per code
     - declared_role = the functional class
     - role_source = "group_heading"
     - name_as_declared = null
     - verbatim = just the code
   Never set role_source to "none" for an item introduced by a functional
   class — the class is evidence about the item and must not be discarded.
   "explicit_prefix" is ONLY for a colon, e.g. "Preservative: Potassium
   Sorbate".

4. declared_code: set this field to the code EXACTLY as printed, if any code
   is present for that item. Codes can appear in several forms — all of
   these are valid and must be copied verbatim, with no reformatting:
     - "E322", "E 322" (E-number, with or without a space)
     - "322" (a bare number, no letter)
     - "INS 322", "INS 471" (explicit INS prefix)
     - with a roman-numeral sub-notation: "470(i)", "500(ii)", "503(ii)",
       "501(i)", "451(i)", "160b(i)"
     - with a lowercase letter suffix: "472e", "150d", "160a"
     - combining both: "1101(ii)"
   CRITICAL: copy the code EXACTLY as printed on the label.
     - Do NOT normalize "470(i)" to "470i" — keep the parentheses.
     - Do NOT add an "E" prefix to a bare number — "322" stays "322".
     - Do NOT convert "INS 322" to "E322" or vice versa.
     - Never infer, guess, or backfill a code from the ingredient name. If
       no code is printed for that item, set declared_code to null.
   The resolver (a separate, later stage) is responsible for normalizing and
   cross-referencing codes — your only job is faithful transcription.

   ONE EXCEPTION to copying exactly: if a WORD is split across two printed
   lines by a hyphen (e.g. "Emulsifi-" at the end of one line and "er" at
   the start of the next), rejoin it into the single word "Emulsifier".
   This is repairing a typesetting artefact, not normalizing. It applies
   ONLY to words broken by line wrapping. It does NOT apply to codes, to
   genuine hyphens inside a name (e.g. "Mono- and diglycerides",
   "D - Glucose"), or to anything else — those are copied exactly as
   printed. Apply the same repair in declaration_verbatim, so the
   transcribed text and the items agree.

5. code_system: record which numbering system the code was printed in,
   exactly as it appears — this is an observation about the label, not an
   inference about the ingredient:
     - "E322"           -> code_system "E"
     - "INS 322"        -> code_system "INS"
     - "322" (bare)     -> code_system "bare"
     - no code at all   -> code_system "none"
   Do NOT assume a bare number is INS-system just because that is common on
   some labels — record "bare" and let the resolver make that call.

6. emphasis: set true ONLY if the item is styled DIFFERENTLY from the rest
   of the declaration (e.g. bold or capitals where surrounding text is not).
   Emphasis is a CONTRAST. If the ENTIRE declaration is uniformly IN ALL
   CAPITALS or uniformly BOLD, no item is emphasised — set emphasis=false for
   ALL items and add "declaration is uniformly styled; emphasis not
   determinable" to warnings. Do NOT add this warning just because the
   declaration is consistently title-case or sentence-case — that is normal
   styling, not a loss of emphasis information, so no warning is needed.

7. footnote_marker: if the item has a footnote symbol attached to it in the
   declaration (e.g. "+Spices and Condiments", "INVERT SUGAR SYRUP#"),
   record just the marker character(s) ("+", "#", "*") in footnote_marker.
   This links the item to its footnote for a later stage — you are not
   expected to resolve what the footnote says. Set to null when no marker
   is attached.

8. FOOTNOTE TEXT: a declaration may include lines explaining a marker, e.g.
   "+Used as natural flavouring agent" or "#(D - GLUCOSE, LEVULOSE)". These
   are NOT ingredients — do not create items for them. Record each in the
   top-level footnotes object, keyed by its marker character:
     {"+": "Used as natural flavouring agent"}
   Strip the leading marker from the value. If a footnote's content is
   itself a list of ingredients (e.g. "#(D - GLUCOSE, LEVULOSE)"), still
   record it here as text — do not create items for its contents.

9. REGULATORY STATEMENTS: a sentence printed with the declaration that
   states something ABOUT the product rather than listing an ingredient —
   "Contains permitted natural colour(s)", "Contains added flavour",
   "Polyols may have laxative effect", "Contains class II preservative" —
   is NOT an ingredient. Do not create an item for it. Copy each verbatim
   into the top-level declaration_statements list. Allergen advisories
   ("Contains: Milk", "May contain nuts") still go to allergen_statements,
   not here.

10. ALLERGEN STATEMENTS: an allergen advisory statement — "Contains: Wheat,
    Milk", "May contain traces of...", "Allergen advice: contains peanuts" —
    is NOT an ingredient. Do not create an item for it and do not put it in
    unparsed_fragments. Instead, copy each such statement verbatim into the
    top-level allergen_statements list.

11. If a fragment of the declaration cannot be confidently parsed into an
    item, do not drop it and do not force it into a guessed item — instead
    append the raw text to unparsed_fragments.

12. item_id must be a unique integer per item, assigned in reading order.
    position is the ordinal position of the item (or item group) within the
    declaration, starting at 0.

WORKED EXAMPLE

Declaration text: "Refined Wheat Flour, Sugar, Invert Sugar Syrup# [Sugar,
Citric Acid], Emulsifier of Vegetable Origin [472e], Raising Agents
[503(ii), 500(ii)], Thickeners (508 & 412), Iodised Salt, Emulsifier (322),
Seasoning (Spices, Salt, Anticaking Agent (INS 551)). #(D - GLUCOSE,
LEVULOSE). Contains: Wheat, Milk."

Note the two bracketed constructions right next to each other in meaning
but OPPOSITE in structure:
  - "Invert Sugar Syrup# [Sugar, Citric Acid]" — the text before the
    bracket ("Invert Sugar Syrup") is an INGREDIENT, not a functional
    class, so this is NESTING (rule 1): role_source="none". The trailing
    "#" is a footnote marker (rule 7): footnote_marker="#".
  - "Raising Agents [503(ii), 500(ii)]" — the text before the bracket
    ("Raising Agents") IS a functional class, so this is a GROUP HEADING
    (rule 3): role_source="group_heading".
  - "Emulsifier (322)" is ALSO a group heading (rule 3), even though the
    class is singular ("Emulsifier", not "Emulsifiers") and the bracket
    holds only one code — role_source="group_heading", NEVER "none".
Also note:
  - "#(D - GLUCOSE, LEVULOSE)" is the footnote TEXT for that "#" marker
    (rule 8) — it produces NO items at all, even though it looks like a
    list of ingredients; it goes into footnotes, keyed by "#".
  - "Contains: Wheat, Milk." at the end is an allergen statement (rule 10)
    — it also produces NO items; it goes into allergen_statements.

Expected items:
  - item_id=0, position=0, nesting_depth=0, parent_item_id=null,
    verbatim="Refined Wheat Flour", name_as_declared="Refined Wheat Flour",
    declared_role=null, role_source="none", declared_code=null,
    code_system="none", footnote_marker=null
  - item_id=1, position=1, nesting_depth=0, parent_item_id=null,
    verbatim="Sugar", name_as_declared="Sugar", declared_role=null,
    role_source="none", declared_code=null, code_system="none",
    footnote_marker=null
  - item_id=2, position=2, nesting_depth=0, parent_item_id=null,
    verbatim="Invert Sugar Syrup# [Sugar, Citric Acid]",
    name_as_declared="Invert Sugar Syrup", declared_role=null,
    role_source="none", declared_code=null, code_system="none",
    footnote_marker="#"
  - item_id=3, position=2, nesting_depth=1, parent_item_id=2,
    verbatim="Sugar", name_as_declared="Sugar", declared_role=null,
    role_source="none", declared_code=null, code_system="none",
    footnote_marker=null
  - item_id=4, position=2, nesting_depth=1, parent_item_id=2,
    verbatim="Citric Acid", name_as_declared="Citric Acid",
    declared_role=null, role_source="none", declared_code=null,
    code_system="none", footnote_marker=null
  - item_id=5, position=3, nesting_depth=0, parent_item_id=null,
    verbatim="472e", name_as_declared=null,
    declared_role="Emulsifier of Vegetable Origin", role_source="group_heading",
    declared_code="472e", code_system="bare", footnote_marker=null
  - item_id=6, position=4, nesting_depth=0, parent_item_id=null,
    verbatim="503(ii)", name_as_declared=null, declared_role="Raising Agents",
    role_source="group_heading", declared_code="503(ii)", code_system="bare",
    footnote_marker=null
  - item_id=7, position=4, nesting_depth=0, parent_item_id=null,
    verbatim="500(ii)", name_as_declared=null, declared_role="Raising Agents",
    role_source="group_heading", declared_code="500(ii)", code_system="bare",
    footnote_marker=null
  - item_id=8, position=5, nesting_depth=0, parent_item_id=null,
    verbatim="508", name_as_declared=null, declared_role="Thickeners",
    role_source="group_heading", declared_code="508", code_system="bare",
    footnote_marker=null
  - item_id=9, position=5, nesting_depth=0, parent_item_id=null,
    verbatim="412", name_as_declared=null, declared_role="Thickeners",
    role_source="group_heading", declared_code="412", code_system="bare",
    footnote_marker=null
  - item_id=10, position=6, nesting_depth=0, parent_item_id=null,
    verbatim="Iodised Salt", name_as_declared="Iodised Salt",
    declared_role=null, role_source="none", declared_code=null,
    code_system="none", footnote_marker=null
  - item_id=11, position=7, nesting_depth=0, parent_item_id=null,
    verbatim="322", name_as_declared=null, declared_role="Emulsifier",
    role_source="group_heading", declared_code="322", code_system="bare",
    footnote_marker=null
  - item_id=12, position=8, nesting_depth=0, parent_item_id=null,
    verbatim="Seasoning (Spices, Salt, Anticaking Agent (INS 551))",
    name_as_declared="Seasoning", declared_role=null, role_source="none",
    declared_code=null, code_system="none", footnote_marker=null
  - item_id=13, position=8, nesting_depth=1, parent_item_id=12,
    verbatim="Spices", name_as_declared="Spices", declared_role=null,
    role_source="none", declared_code=null, code_system="none",
    footnote_marker=null
  - item_id=14, position=8, nesting_depth=1, parent_item_id=12,
    verbatim="Salt", name_as_declared="Salt", declared_role=null,
    role_source="none", declared_code=null, code_system="none",
    footnote_marker=null
  - item_id=15, position=8, nesting_depth=1, parent_item_id=12,
    verbatim="INS 551", name_as_declared=null,
    declared_role="Anticaking Agent", role_source="group_heading",
    declared_code="INS 551", code_system="INS", footnote_marker=null

Expected footnotes: {"#": "(D - GLUCOSE, LEVULOSE)."}

Expected declaration_statements: []

Expected allergen_statements: ["Contains: Wheat, Milk."]

SECOND WORKED EXAMPLE — components and regulatory statements

Declaration text: "Inner Layer (Ice Cream 68%): Milk Solids, Sugar,
Emulsifier (E471). Outer Layer (32%): Cocoa Solids, Cocoa Butter, Soya
Lecithin. Contains permitted natural colour(s). Contains: Milk, Soya."

"Inner Layer" and "Outer Layer" are COMPONENTS before a colon, not
functional classes (rule 1) — each becomes a parent item at nesting_depth 0
with its percentage captured, and everything after its colon nests at
nesting_depth 1 underneath it, role_source="none". This composite structure
matters downstream: each component can fall in a different EU food
category, so an additive must stay attached to the RIGHT component.
"Contains permitted natural colour(s)" is a REGULATORY STATEMENT (rule 9)
— it produces NO item and is NOT an allergen advisory, so it goes into
declaration_statements, separately from "Contains: Milk, Soya." which goes
into allergen_statements (rule 10) as before.

Expected items:
  - item_id=0, position=0, nesting_depth=0, parent_item_id=null,
    verbatim="Inner Layer (Ice Cream 68%)", name_as_declared="Inner Layer",
    declared_role=null, role_source="none", declared_code=null,
    code_system="none", percentage=68.0, footnote_marker=null
  - item_id=1, position=0, nesting_depth=1, parent_item_id=0,
    verbatim="Milk Solids", name_as_declared="Milk Solids",
    declared_role=null, role_source="none", declared_code=null,
    code_system="none", footnote_marker=null
  - item_id=2, position=0, nesting_depth=1, parent_item_id=0,
    verbatim="Sugar", name_as_declared="Sugar", declared_role=null,
    role_source="none", declared_code=null, code_system="none",
    footnote_marker=null
  - item_id=3, position=0, nesting_depth=1, parent_item_id=0,
    verbatim="E471", name_as_declared=null, declared_role="Emulsifier",
    role_source="group_heading", declared_code="E471", code_system="E",
    footnote_marker=null
  - item_id=4, position=1, nesting_depth=0, parent_item_id=null,
    verbatim="Outer Layer (32%)", name_as_declared="Outer Layer",
    declared_role=null, role_source="none", declared_code=null,
    code_system="none", percentage=32.0, footnote_marker=null
  - item_id=5, position=1, nesting_depth=1, parent_item_id=4,
    verbatim="Cocoa Solids", name_as_declared="Cocoa Solids",
    declared_role=null, role_source="none", declared_code=null,
    code_system="none", footnote_marker=null
  - item_id=6, position=1, nesting_depth=1, parent_item_id=4,
    verbatim="Cocoa Butter", name_as_declared="Cocoa Butter",
    declared_role=null, role_source="none", declared_code=null,
    code_system="none", footnote_marker=null
  - item_id=7, position=1, nesting_depth=1, parent_item_id=4,
    verbatim="Soya Lecithin", name_as_declared="Soya Lecithin",
    declared_role=null, role_source="none", declared_code=null,
    code_system="none", footnote_marker=null

Expected declaration_statements: ["Contains permitted natural colour(s)."]

Expected allergen_statements: ["Contains: Milk, Soya."]

Now transcribe the actual declaration in the image. Respond with RAW JSON
ONLY. No markdown code fences, no commentary before or after. Match this
exact shape:

{
  "declaration_verbatim": "full text of the declaration as printed",
  "language": "{language}",
  "items": [
    {
      "item_id": 0,
      "position": 0,
      "nesting_depth": 0,
      "parent_item_id": null,
      "verbatim": "string",
      "name_as_declared": "string or null",
      "declared_role": "string or null",
      "role_source": "explicit_prefix",
      "declared_code": "string or null",
      "code_system": "none",
      "percentage": null,
      "emphasis": false,
      "footnote_marker": null,
      "confidence": 0.0
    }
  ],
  "unparsed_fragments": [],
  "warnings": [],
  "allergen_statements": [],
  "footnotes": {},
  "declaration_statements": []
}
"""
