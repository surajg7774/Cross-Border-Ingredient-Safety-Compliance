# Verdict golden worksheet -- Chipsmain

Pinned categories (PROVISIONAL -- rank-1 retrieved, review before trusting):
  - Seasoning: '12.2.2' (Seasonings and condiments)

## item_id=0
- verbatim: 'Potato (57%)'
- classification: food_ingredient
- resolved eu_canonical_id: None  canonical_ins: None
- component: None  pinned category: '(product)' -> None
- OUT OF SCOPE -- not an additive. Not an Annex II lookup.

## item_id=1
- verbatim: 'Edible Vegetable Oil (Rice Bran Oil)'
- classification: compound
- resolved eu_canonical_id: None  canonical_ins: None
- component: None  pinned category: '(product)' -> None
- OUT OF SCOPE -- not an additive (a container/group node, not a substance). Not an Annex II lookup.

## item_id=2
- verbatim: 'Rice Bran Oil'
- classification: food_ingredient
- resolved eu_canonical_id: None  canonical_ins: None
- component: 'Edible Vegetable Oil'  pinned category: 'Edible Vegetable Oil' -> None
- OUT OF SCOPE -- not an additive. Not an Annex II lookup.

## item_id=3
- verbatim: 'Seasoning [+Spices and Condiments (Contains Chilli), Iodised Salt, Sugar, Maltodextrin, Natural and Nature Identical Flavouring Substances, Anticaking Agent (INS 470(i), INS 551)), Starch, Flavour Enhancers (INS 627, INS 631), Acidity Regulator (INS 330), Emulsifying and Stabilizing Agent (INS 471)]'
- classification: compound
- resolved eu_canonical_id: None  canonical_ins: None
- component: None  pinned category: '(product)' -> None
- OUT OF SCOPE -- not an additive (a container/group node, not a substance). Not an Annex II lookup.

## item_id=4
- verbatim: '+Spices and Condiments (Contains Chilli)'
- classification: unknown
- resolved eu_canonical_id: None  canonical_ins: None
- component: 'Seasoning'  pinned category: 'Seasoning' -> {'code': '12.2.2', 'name': 'Seasonings and condiments'}
- UNRESOLVED -- the resolver did not identify a substance; category-independent.

## item_id=5
- verbatim: 'Iodised Salt'
- classification: food_ingredient
- resolved eu_canonical_id: None  canonical_ins: None
- component: 'Seasoning'  pinned category: 'Seasoning' -> {'code': '12.2.2', 'name': 'Seasonings and condiments'}
- OUT OF SCOPE -- not an additive. Not an Annex II lookup.

## item_id=6
- verbatim: 'Sugar'
- classification: food_ingredient
- resolved eu_canonical_id: None  canonical_ins: None
- component: 'Seasoning'  pinned category: 'Seasoning' -> {'code': '12.2.2', 'name': 'Seasonings and condiments'}
- OUT OF SCOPE -- not an additive. Not an Annex II lookup.

## item_id=7
- verbatim: 'Maltodextrin'
- classification: food_ingredient
- resolved eu_canonical_id: None  canonical_ins: None
- component: 'Seasoning'  pinned category: 'Seasoning' -> {'code': '12.2.2', 'name': 'Seasonings and condiments'}
- OUT OF SCOPE -- not an additive. Not an Annex II lookup.

## item_id=8
- verbatim: 'Natural and Nature Identical Flavouring Substances'
- classification: flavouring
- resolved eu_canonical_id: None  canonical_ins: None
- component: 'Seasoning'  pinned category: 'Seasoning' -> {'code': '12.2.2', 'name': 'Seasonings and condiments'}
- OUT OF SCOPE -- Reg 1334/2008. Not an Annex II lookup.

## item_id=9
- verbatim: 'INS 470(i)'
- classification: additive
- resolved eu_canonical_id: '470a'  canonical_ins: '470(i)'
- component: 'Seasoning'  pinned category: 'Seasoning' -> {'code': '12.2.2', 'name': 'Seasonings and condiments'}
- eu_fip evidence:
  Exact match: eu_fip rows for ('470a', '12.2.2'):
    - status=permitted, level=no numeric cap
      conditions: Permitted via Group I, Additives; ML = quantum satis; except E 425 ML = 10000 mg/kg; E 620 to E 625, ML = 10000 mg/kg individually or in combination, expressed as glutamic acid; E 626 to E 635, ML = 500 mg/kg individually or in combination, expressed as guanylic acid.

## item_id=10
- verbatim: 'INS 551'
- classification: additive
- resolved eu_canonical_id: '551'  canonical_ins: '551'
- component: 'Seasoning'  pinned category: 'Seasoning' -> {'code': '12.2.2', 'name': 'Seasonings and condiments'}
- eu_fip evidence:
  Exact match: eu_fip rows for ('551', '12.2.2'):
    - status=permitted, level=30000.0 mg/kg
      conditions: Permitted via Silicon dioxide - silicates; only seasoning  Period of application:  from 1 February 2014; Note 1: The additives may be added individually or in combination
    - status=permitted, level=30000.0 mg/kg
      conditions: Permitted via Silicon dioxide - silicates; only seasoning  Period of application:  until 31 January 2014

## item_id=11
- verbatim: 'Starch'
- classification: food_ingredient
- resolved eu_canonical_id: None  canonical_ins: None
- component: 'Seasoning'  pinned category: 'Seasoning' -> {'code': '12.2.2', 'name': 'Seasonings and condiments'}
- OUT OF SCOPE -- not an additive. Not an Annex II lookup.

## item_id=12
- verbatim: 'INS 627'
- classification: additive
- resolved eu_canonical_id: '627'  canonical_ins: '627'
- component: 'Seasoning'  pinned category: 'Seasoning' -> {'code': '12.2.2', 'name': 'Seasonings and condiments'}
- eu_fip evidence:
  Exact match: eu_fip rows for ('627', '12.2.2'):
    - status=permitted, level=no numeric cap
      conditions: Permitted via Group I, Additives; ML = quantum satis; except E 425 ML = 10000 mg/kg; E 620 to E 625, ML = 10000 mg/kg individually or in combination, expressed as glutamic acid; E 626 to E 635, ML = 500 mg/kg individually or in combination, expressed as guanylic acid.
    - status=permitted, level=no numeric cap
      conditions: Permitted via Ribonucleotides

## item_id=13
- verbatim: 'INS 631'
- classification: additive
- resolved eu_canonical_id: '631'  canonical_ins: '631'
- component: 'Seasoning'  pinned category: 'Seasoning' -> {'code': '12.2.2', 'name': 'Seasonings and condiments'}
- eu_fip evidence:
  Exact match: eu_fip rows for ('631', '12.2.2'):
    - status=permitted, level=no numeric cap
      conditions: Permitted via Group I, Additives; ML = quantum satis; except E 425 ML = 10000 mg/kg; E 620 to E 625, ML = 10000 mg/kg individually or in combination, expressed as glutamic acid; E 626 to E 635, ML = 500 mg/kg individually or in combination, expressed as guanylic acid.
    - status=permitted, level=no numeric cap
      conditions: Permitted via Ribonucleotides

## item_id=14
- verbatim: 'INS 330'
- classification: additive
- resolved eu_canonical_id: '330'  canonical_ins: '330'
- component: 'Seasoning'  pinned category: 'Seasoning' -> {'code': '12.2.2', 'name': 'Seasonings and condiments'}
- eu_fip evidence:
  Exact match: eu_fip rows for ('330', '12.2.2'):
    - status=permitted, level=no numeric cap
      conditions: Permitted via Group I, Additives; ML = quantum satis; except E 425 ML = 10000 mg/kg; E 620 to E 625, ML = 10000 mg/kg individually or in combination, expressed as glutamic acid; E 626 to E 635, ML = 500 mg/kg individually or in combination, expressed as guanylic acid.

## item_id=15
- verbatim: 'INS 471'
- classification: additive
- resolved eu_canonical_id: '471'  canonical_ins: '471'
- component: 'Seasoning'  pinned category: 'Seasoning' -> {'code': '12.2.2', 'name': 'Seasonings and condiments'}
- eu_fip evidence:
  Exact match: eu_fip rows for ('471', '12.2.2'):
    - status=permitted, level=no numeric cap
      conditions: Permitted via Group I, Additives; ML = quantum satis; except E 425 ML = 10000 mg/kg; E 620 to E 625, ML = 10000 mg/kg individually or in combination, expressed as glutamic acid; E 626 to E 635, ML = 500 mg/kg individually or in combination, expressed as guanylic acid.
