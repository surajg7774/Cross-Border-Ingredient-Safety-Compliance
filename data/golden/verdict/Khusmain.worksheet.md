# Verdict golden worksheet -- Khusmain

Pinned categories (PROVISIONAL -- rank-1 retrieved, review before trusting):
  - (product): '14.1.4' (Flavoured_drinks)

## item_id=1
- verbatim: 'Water'
- classification: food_ingredient
- resolved eu_canonical_id: None  canonical_ins: None
- component: None  pinned category: '(product)' -> {'code': '14.1.4', 'name': 'Flavoured_drinks'}
- OUT OF SCOPE -- not an additive. Not an Annex II lookup.

## item_id=2
- verbatim: 'Citric Acid (INS 330)'
- classification: additive
- resolved eu_canonical_id: '330'  canonical_ins: '330'
- component: None  pinned category: '(product)' -> {'code': '14.1.4', 'name': 'Flavoured_drinks'}
- eu_fip evidence:
  Exact match: eu_fip rows for ('330', '14.1.4'):
    - status=permitted, level=no numeric cap
      conditions: Permitted via Group I, Additives; E 420, E 421, E 953, E 965, E 966 and E 967 may not be used  E 968 may not be used except where specifically provided for in this food category
    - status=permitted, level=no numeric cap
      conditions: Permitted via Group I, Additives; ML = quantum satis; except E 425 ML = 10000 mg/kg; E 620 to E 625, ML = 10000 mg/kg individually or in combination, expressed as glutamic acid; E 626 to E 635, ML = 500 mg/kg individually or in combination, expressed as guanylic acid.

## item_id=3
- verbatim: 'INS 440'
- classification: additive
- resolved eu_canonical_id: '440'  canonical_ins: '440'
- component: None  pinned category: '(product)' -> {'code': '14.1.4', 'name': 'Flavoured_drinks'}
- eu_fip evidence:
  Exact match: eu_fip rows for ('440', '14.1.4'):
    - status=permitted, level=no numeric cap
      conditions: Permitted via Group I, Additives; E 420, E 421, E 953, E 965, E 966 and E 967 may not be used  E 968 may not be used except where specifically provided for in this food category
    - status=permitted, level=no numeric cap
      conditions: Permitted via Group I, Additives; ML = quantum satis; except E 425 ML = 10000 mg/kg; E 620 to E 625, ML = 10000 mg/kg individually or in combination, expressed as glutamic acid; E 626 to E 635, ML = 500 mg/kg individually or in combination, expressed as guanylic acid.

## item_id=4
- verbatim: 'Natural Khus Flavour'
- classification: flavouring
- resolved eu_canonical_id: None  canonical_ins: None
- component: None  pinned category: '(product)' -> {'code': '14.1.4', 'name': 'Flavoured_drinks'}
- OUT OF SCOPE -- Reg 1334/2008. Not an Annex II lookup.

## item_id=5
- verbatim: 'Added Khus Flavour (Natural-Nature Identical Flavouring Substances)'
- classification: compound
- resolved eu_canonical_id: None  canonical_ins: None
- component: None  pinned category: '(product)' -> {'code': '14.1.4', 'name': 'Flavoured_drinks'}
- OUT OF SCOPE -- not an additive (a container/group node, not a substance). Not an Annex II lookup.

## item_id=6
- verbatim: 'Natural-Nature Identical Flavouring Substances'
- classification: flavouring
- resolved eu_canonical_id: None  canonical_ins: None
- component: 'Added Khus Flavour'  pinned category: 'Added Khus Flavour' -> None
- OUT OF SCOPE -- Reg 1334/2008. Not an Annex II lookup.

## item_id=7
- verbatim: 'INS 224'
- classification: additive
- resolved eu_canonical_id: '224'  canonical_ins: '224'
- component: None  pinned category: '(product)' -> {'code': '14.1.4', 'name': 'Flavoured_drinks'}
- eu_fip evidence:
  Exact match: eu_fip rows for ('224', '14.1.4'):
    - status=permitted, level=20.0 mg/kg
      conditions: Permitted via Sulphur dioxide - sulphites; only carry over from concentrates in non-alcoholic flavoured drinks containing fruit juice
    - status=permitted, level=50.0 mg/kg
      conditions: Permitted via Sulphur dioxide - sulphites; only non-alcoholic flavoured drinks containing at least 235 g/l glucose syrup
    - status=permitted, level=350.0 mg/kg
      conditions: Permitted via Sulphur dioxide - sulphites; only concentrates based on fruit juice and containing not less than 2,5 % barley (barley water)
    - status=permitted, level=250.0 mg/kg
      conditions: Permitted via Sulphur dioxide - sulphites; only other concentrates based on fruit juice or comminuted fruit; capilé, groselha

## item_id=8
- verbatim: 'INS 143'
- classification: additive
- resolved eu_canonical_id: None  canonical_ins: '143'
- component: None  pinned category: '(product)' -> {'code': '14.1.4', 'name': 'Flavoured_drinks'}
- ABSENT FROM eu_fip (Codex identified INS 143, no EU crosswalk entry) -- category-independent, not_authorised_eu by absence, per src/rules/engine.py's _evaluate_item step 2.
