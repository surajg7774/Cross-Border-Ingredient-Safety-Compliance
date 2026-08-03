# Draft verdict ground truth — 5 labels

**Status: AI-DRAFTED from the label images, independent of pipeline output.
NOT verified against Annex II row-by-row. Every line must be cross-checked
against eu_fip before this becomes ground truth.**

Confidence key:
- **HIGH** — settled fact about EU additive law, safe to treat as truth
- **MED** — believed correct, verify against the eu_fip row
- **UNSET** — cannot determine without reading the row; leave blank

---

## 1. Khusmain (khus syrup)

Pinned category: **14.1.4 — flavoured drinks**

| Declared | Resolved | Expected headline | Conf |
|---|---|---|---|
| Sugar | food_ingredient | out_of_scope | HIGH |
| Water | food_ingredient | out_of_scope | HIGH |
| Citric Acid (INS 330) | E330 | permitted | HIGH |
| Stabilising Agent (INS 440) | E440 pectins | permitted | MED |
| Natural Khus Flavour | flavouring | out_of_scope | HIGH |
| Added Khus Flavour (nature identical) | flavouring | out_of_scope | HIGH |
| CLASS II PRESERVATIVE (INS 224) | E224 sodium metabisulphite | UNSET | UNSET |
| Permitted Synthetic Food Colour (INS 143) | Fast Green FCF | **not_authorised_eu** | **HIGH** |

**The load-bearing item is INS 143.** Fast Green FCF is not on the EU
permitted colours list at all — not a category question, a flat absence.
This is the item that must never come back clear.

E224 needs the row read: sulphites are permitted in some drink categories
with limits and carry a labelling obligation, but I won't assert 14.1.4
from memory.

---

## 2. Parle-Gmain (biscuit)

Pinned category: **7.2 — fine bakery wares**
(Component: invert sugar syrup — see note below)

| Declared | Resolved | Expected headline | Conf |
|---|---|---|---|
| Refined wheat flour (maida) | food_ingredient | out_of_scope | HIGH |
| Sugar | food_ingredient | out_of_scope | HIGH |
| Refined palm oil | food_ingredient | out_of_scope | HIGH |
| Invert sugar syrup | compound | out_of_scope (container) | MED |
| — citric acid (within syrup) | E330 | permitted | HIGH |
| Iodised salt | food_ingredient | out_of_scope | HIGH |
| Raising agent 503(ii) | E503(ii) ammonium hydrogen carbonate | permitted | MED |
| Raising agent 500(ii) | E500(ii) sodium hydrogen carbonate | permitted (via E500 parent) | MED |
| Milk solids | food_ingredient | out_of_scope | HIGH |
| Flour treatment agent 1101(ii) | papain, enzyme | **out_of_scope** (Reg 1332/2008) | HIGH |
| Emulsifier 472e | E472e | permitted | MED |
| Added flavour (vanilla) | flavouring | out_of_scope | HIGH |

**Expected outcome under 7.2: nothing blocked.** E500, E503, E472e and
E330 are all Group I additives and Group I is permitted at quantum satis
in fine bakery wares.

Note the demo contrast: under **11.1** (sugars and syrups) Group I is not
broadly permitted, which is what produces the 3 NOT PERMITTED. If you want
that branch scored too, add a second pinned-category variant rather than
changing this one.

---

## 3. Chipsmain (potato crisps)

Pinned categories: **product = 15.1 potato snacks**,
**component "Seasoning" = 12.2.2 seasonings and condiments**

Every additive sits inside the seasoning bracket, so all are judged
against 12.2.2.

| Declared | Resolved | Expected headline | Conf |
|---|---|---|---|
| Potato | food_ingredient | out_of_scope | HIGH |
| Edible vegetable oil (rice bran) | food_ingredient | out_of_scope | HIGH |
| Spices and condiments (chilli) | food_ingredient | out_of_scope | HIGH |
| Iodised salt | food_ingredient | out_of_scope | HIGH |
| Sugar | food_ingredient | out_of_scope | HIGH |
| Maltodextrin | food_ingredient | out_of_scope | HIGH |
| Natural / nature identical flavouring | flavouring | out_of_scope | HIGH |
| Anticaking agent INS 470(i) | E470a (crosswalk by name) | UNSET | UNSET |
| Anticaking agent INS 551 | E551 silicon dioxide | permitted in 12.2.2 | HIGH |
| Starch | food_ingredient | out_of_scope | HIGH |
| Flavour enhancer INS 627 | E627 disodium guanylate | UNSET | UNSET |
| Flavour enhancer INS 631 | E631 disodium inosinate | UNSET | UNSET |
| Acidity regulator INS 330 | E330 | permitted | HIGH |
| Emulsifying/stabilizing INS 471 | E471 | permitted | MED |

**E551 is the case that motivated component-level classification**:
permitted in 12.2.2, NOT permitted in 15.1. If the pinned category is
applied correctly this comes back permitted. If it comes back blocked,
the component routing has regressed.

INS 470(i) has no direct E-number; it reaches E470a by name comparison at
capped confidence. Whether that widening is correct is a judgement call —
leave UNSET and read the row.

---

## 4. EU_productmain (dehydrated potato snack)

Pinned categories: **product = 15.1**,
**component "Seasoning" = 12.2.2**

| Declared | Resolved | Expected headline | Conf |
|---|---|---|---|
| Dehydrated potatoes | food_ingredient | out_of_scope | HIGH |
| Modified cornstarch (×2) | ambiguous — 17 candidates | **unresolved / review** | HIGH |
| Corn maltodextrin | food_ingredient | out_of_scope | HIGH |
| Salt | food_ingredient | out_of_scope | HIGH |
| Citric acid | E330 | permitted | HIGH |
| Sodium acetate | E262 | permitted | MED |
| Acetic acid | E260 | permitted | MED |
| Dextrose | food_ingredient | out_of_scope | HIGH |
| High oleic sunflower oil | food_ingredient | out_of_scope | HIGH |
| Vinegar solids | food_ingredient | out_of_scope | HIGH |
| Malic acid | E296 | UNSET | UNSET |
| Vegetable oil | food_ingredient | out_of_scope | HIGH |
| Sugar | food_ingredient | out_of_scope | HIGH |
| Calcium phosphate | ambiguous — E341(i)/(ii)/(iii) | **unresolved / review** | HIGH |
| Soy lecithin | E322 lecithins | permitted | HIGH |
| Sodium bicarbonate | E500(ii) | permitted (via E500 parent) | MED |

**This label's value is the resolution path, not the verdict.** Seven
additives declared by name with no codes. Two items (modified cornstarch,
calcium phosphate) must land in the review queue — declining is the
correct answer, not a failure.

E260/E262 are the Group I pair your grouped-permitted rendering was built
around; expect them to share one merged conditions block.

---

## 5. Ice-creammain (choc hazelnut ice cream bar)

Pinned categories: **inner layer = 3 edible ices**,
**outer layer = 5.1 cocoa and chocolate products**

| Declared | Layer | Resolved | Expected headline | Conf |
|---|---|---|---|---|
| Milk and milk solids | inner | food_ingredient | out_of_scope | HIGH |
| Maltitol | inner | E965 | UNSET | UNSET |
| Fructooligosaccharides | inner | food_ingredient | out_of_scope | MED |
| Cocoa solids | inner | food_ingredient | out_of_scope | HIGH |
| Erythritol | inner | E968 | UNSET | UNSET |
| Whey protein concentrate | inner | food_ingredient | out_of_scope | HIGH |
| Emulsifier E471 | inner | E471 | permitted | MED |
| Stabiliser E412 | inner | E412 guar gum | permitted | MED |
| Stabiliser E407 | inner | E407 carrageenan | permitted | MED |
| Stabiliser E410 | inner | E410 locust bean gum | permitted | MED |
| Stevia | inner | ambiguous — steviol glycosides E960(x) | **unresolved / review** | HIGH |
| Nature identical flavouring (hazelnut) | inner | flavouring | out_of_scope | HIGH |
| Maltitol | outer | E965 | UNSET | UNSET |
| Refined coconut oil | outer | food_ingredient | out_of_scope | HIGH |
| Cocoa solids | outer | food_ingredient | out_of_scope | HIGH |
| Soya lecithin | outer | E322 | permitted | HIGH |
| Cocoa butter | outer | food_ingredient | out_of_scope | HIGH |
| Hazelnut pieces / paste | outer | food_ingredient | out_of_scope | HIGH |

**Maltitol appears in both layers** — this is the item_id keying case.
Each occurrence must carry its own layer and its own category. If both
come back labelled "Outer Layer", the parent-chain walk has regressed.

**"CONTAINS PERMITTED NATURAL COLOUR(S)"** declares a colour without
naming it. The correct behaviour is to surface this as undeterminable,
not to ignore it. Worth checking whether the extractor captures it at all
— it sits outside the ingredients list proper.

E965 and E968 are polyols; sweetener permissions in category 3 are
level-bound and I won't assert them from memory. UNSET.

---

## What to do with this

1. Cross-check every row against the actual eu_fip rows.
2. Anywhere this draft and eu_fip disagree — that is the interesting
   case. Read the law, decide, record the reasoning.
3. Anywhere marked UNSET — read the row, fill it in.
4. Only then compare against pipeline output.

Do NOT promote this file to ground truth as-is. It is one independent
reading, useful as a cross-check, not as an authority on EU law.
