"""Tests for the deterministic resolver (src/resolve/). Plain pytest, small
hand-built dicts standing in for the reference datasets -- no fixture files.
"""

from src.resolve.resolver import References, resolve_items, routing_band
from src.resolve.roles import normalise_role


def _codex(ins, name, functional_classes=None, purposes=None, synonyms=None, parent_ins=None):
    return {
        "ins": ins,
        "parent_ins": parent_ins,
        "name": name,
        "synonyms": synonyms or [],
        "functional_classes": functional_classes or [],
        "purposes": purposes or [],
        "is_parent_row": not functional_classes,
    }


def _item(
    item_id,
    declared_code=None,
    name_as_declared=None,
    verbatim="",
    declared_role=None,
    parent_item_id=None,
):
    return {
        "item_id": item_id,
        "declared_code": declared_code,
        "name_as_declared": name_as_declared,
        "verbatim": verbatim,
        "declared_role": declared_role,
        "parent_item_id": parent_item_id,
    }


FUNCTIONAL_CLASSES = [
    {"class": "Emulsifier", "definition": "...", "purposes": ["emulsifier"]},
    {"class": "Stabilizer", "definition": "...", "purposes": ["stabilizer"]},
    {"class": "Raising agent", "definition": "...", "purposes": ["raising agent"]},
    {"class": "Humectant", "definition": "...", "purposes": ["moisture-retention agent"]},
]

LABEL_ALIASES = {
    "aliases": [
        {"alias": "soy lecithin", "ins": "322(i)", "ins_name": "Lecithin", "confidence": "high"},
    ],
    "ambiguous": [
        {
            "alias": "modified starch",
            "candidates": ["1400", "1401", "1402"],
            "action": "manual_review",
        },
    ],
    "never_additives": {
        "terms": ["dehydrated potatoes", "turmeric powder"],
    },
}


def _refs(codex_ins, eu_fip=None):
    return References(
        codex_ins=codex_ins,
        functional_classes=FUNCTIONAL_CLASSES,
        label_aliases=LABEL_ALIASES,
        eu_fip=eu_fip or [],
        index_version="test",
    )


def test_code_with_prefix_and_spaces_normalises_and_resolves():
    codex_ins = [_codex("470(i)", "Salts of myristic, palmitic and stearic acids")]
    items = [_item(0, declared_code="INS 470(i)")]
    result = resolve_items(items, _refs(codex_ins))
    resolved = result.items[0]
    assert resolved.canonical_ins == "470(i)"
    assert resolved.resolution_method == "code_exact"


def test_missing_sub_notation_falls_through_to_parent():
    # 451(i) is deliberately absent -- only its parent "451" exists, so the
    # code path must widen rather than fail outright.
    codex_ins = [_codex("451", "Triphosphates")]
    items = [_item(0, declared_code="451(i)")]
    result = resolve_items(items, _refs(codex_ins))
    resolved = result.items[0]
    assert resolved.canonical_ins == "451"
    assert resolved.resolution_method == "code_parent"
    assert "widened_to_parent" in resolved.flags


def test_alias_resolves_soy_lecithin_to_322i():
    codex_ins = [_codex("322(i)", "Lecithin")]
    items = [_item(0, name_as_declared="soy lecithin")]
    result = resolve_items(items, _refs(codex_ins))
    resolved = result.items[0]
    assert resolved.canonical_ins == "322(i)"
    assert resolved.resolution_method == "alias"


def test_food_lexicon_exits_as_food_ingredient():
    codex_ins = []
    items = [_item(0, name_as_declared="dehydrated potatoes")]
    result = resolve_items(items, _refs(codex_ins))
    resolved = result.items[0]
    assert resolved.classification == "food_ingredient"
    assert resolved.resolution_method == "food_lexicon"


def test_food_lexicon_beats_fuzzy_for_turmeric_powder():
    # "Turmeric" is a real additive name -- without the food lexicon running
    # BEFORE fuzzy matching, "turmeric powder" would fuzzy-match it.
    codex_ins = [_codex("100(ii)", "Turmeric", functional_classes=["Colour"])]
    items = [_item(0, name_as_declared="turmeric powder")]
    result = resolve_items(items, _refs(codex_ins))
    resolved = result.items[0]
    assert resolved.classification == "food_ingredient"
    assert resolved.resolution_method == "food_lexicon"
    assert resolved.canonical_ins is None


def test_unresolved_flavouring_classifies_as_flavouring_not_unknown():
    # Codex explicitly excludes flavourings ("The INS does not include
    # flavourings"), so this can never resolve via codex/EU lookup -- it must
    # not fall into "unknown" just because canonical_ins stays None.
    items = [_item(0, name_as_declared="Natural Flavouring Substance (Hazelnut)")]
    result = resolve_items(items, _refs([]))
    resolved = result.items[0]
    assert resolved.classification == "flavouring"
    assert resolved.canonical_ins is None


def test_flavouring_is_certain_not_manual_review():
    # Certain it IS a flavouring, just not which one -- and flavourings fall
    # under Reg 1334/2008, not Annex II, so there is no Annex II verdict to
    # reach. Must not sit in the same review queue as genuine unknowns.
    items = [_item(0, name_as_declared="Natural Flavouring Substance (Hazelnut)")]
    result = resolve_items(items, _refs([]))
    resolved = result.items[0]
    assert resolved.resolution_confidence == 0.95
    assert routing_band(resolved.resolution_confidence) == "straight_through"
    assert "out_of_annex_ii_scope" in resolved.flags


def test_flavour_enhancer_role_is_still_additive_not_flavouring():
    # "Flavour enhancer" is a genuine Codex additive class (e.g. MSG), not a
    # Reg 1334/2008 flavouring -- the word "flavour" alone must not trigger it.
    codex_ins = [_codex("621", "Monosodium L-glutamate", functional_classes=["Flavour enhancer"])]
    items = [_item(0, declared_code="621", declared_role="Flavour enhancer")]
    result = resolve_items(items, _refs(codex_ins))
    resolved = result.items[0]
    assert resolved.classification == "additive"


def test_flavour_enhancers_plural_role_is_additive_627():
    # Real Chipsmain bug: the label declares INS 627 under the group heading
    # "Flavour Enhancers" (PLURAL). The exclusion check originally only knew
    # the singular "flavour enhancer", so this fell through to the generic
    # "flavour" substring check and was wrongly classified "flavouring".
    codex_ins = [_codex("627", "Disodium 5'-guanylate", functional_classes=["Flavour enhancer"])]
    eu_fip = [{"canonical_id": "627", "additive_name": "Disodium 5'-guanylate", "functional_classes": []}]
    items = [_item(0, declared_code="627", declared_role="Flavour Enhancers")]
    result = resolve_items(items, _refs(codex_ins, eu_fip))
    resolved = result.items[0]
    assert resolved.classification == "additive"
    assert "out_of_annex_ii_scope" not in resolved.flags


def test_flavour_enhancers_plural_role_is_additive_631():
    codex_ins = [_codex("631", "Disodium 5'-inosinate", functional_classes=["Flavour enhancer"])]
    eu_fip = [{"canonical_id": "631", "additive_name": "Disodium 5'-inosinate", "functional_classes": []}]
    items = [_item(0, declared_code="631", declared_role="Flavour Enhancers")]
    result = resolve_items(items, _refs(codex_ins, eu_fip))
    resolved = result.items[0]
    assert resolved.classification == "additive"
    assert "out_of_annex_ii_scope" not in resolved.flags


def test_unresolved_natural_flavouring_still_flavouring_no_eu_id():
    # The genuine flavouring case: no INS number to resolve, because Codex
    # excludes flavourings entirely -- must stay "flavouring" with the scope
    # flag, and must not pick up an eu_canonical_id from anywhere.
    items = [_item(0, name_as_declared="Natural Flavouring Substance (Hazelnut)")]
    result = resolve_items(items, _refs([], []))
    resolved = result.items[0]
    assert resolved.classification == "flavouring"
    assert resolved.eu_canonical_id is None
    assert "out_of_annex_ii_scope" in resolved.flags


def test_eu_canonical_id_invariant_overrules_enzyme_classification():
    # Direct invariant test: an item with a non-null eu_canonical_id can
    # never be "flavouring" or "enzyme". Fix 2 already keeps a resolved-in-
    # Codex item out of "flavouring", so the only way to exercise this
    # invariant directly is via "enzyme", which Fix 2 does not touch: 1101(ii)
    # resolves to "enzyme" via the code path (enzyme INS prefix). Placing its
    # code in eu_fip too (artificial for the test -- real enzymes fall under
    # Reg 1332, not Annex II) must force the invariant to overrule it.
    codex_ins = [_codex("1101(ii)", "Papain")]
    eu_fip = [{"canonical_id": "1101(ii)", "additive_name": "Papain", "functional_classes": []}]
    items = [_item(0, declared_code="1101(ii)", declared_role="Flour treatment agent")]
    result = resolve_items(items, _refs(codex_ins, eu_fip))
    resolved = result.items[0]
    assert resolved.eu_canonical_id is not None
    assert resolved.classification == "additive"
    assert "flavouring_check_overruled" in resolved.flags


def test_ambiguous_returns_candidate_list():
    codex_ins = []
    items = [_item(0, name_as_declared="modified starch")]
    result = resolve_items(items, _refs(codex_ins))
    resolved = result.items[0]
    assert resolved.classification == "ambiguous"
    assert resolved.candidates == ["1400", "1401", "1402"]
    assert resolved.flags == ["ambiguous_candidates"]


def test_food_lexicon_confidence_is_high_not_zero():
    # A food-lexicon hit is a CERTAIN classification, not an uncertain one --
    # it must not route to manual review.
    items = [_item(0, name_as_declared="dehydrated potatoes")]
    result = resolve_items(items, _refs([]))
    resolved = result.items[0]
    assert resolved.resolution_confidence == 0.95
    assert routing_band(resolved.resolution_confidence) == "straight_through"


def test_enzyme_classification_has_no_eu_id():
    codex_ins = [_codex("1101(ii)", "Papain")]
    items = [_item(0, declared_code="1101(ii)", declared_role="Flour treatment agent")]
    result = resolve_items(items, _refs(codex_ins))
    resolved = result.items[0]
    assert resolved.classification == "enzyme"
    assert resolved.eu_canonical_id is None


def test_role_class_mismatch_flagged_when_role_and_codex_disagree():
    # 1101(ii) is Papain, a Flavour enhancer per Codex -- but the label calls
    # it a Flour treatment agent, a different sub-type of the 1101 family.
    # This is a signal, not a rejection: classification stays "enzyme".
    functional_classes = [
        *FUNCTIONAL_CLASSES,
        {"class": "Flavour enhancer", "definition": "...", "purposes": ["flavour enhancer"]},
        {"class": "Flour treatment agent", "definition": "...", "purposes": ["flour treatment agent"]},
    ]
    codex_ins = [_codex("1101(ii)", "Papain", functional_classes=["Flavour enhancer"])]
    refs = References(
        codex_ins=codex_ins,
        functional_classes=functional_classes,
        label_aliases=LABEL_ALIASES,
        index_version="test",
    )
    items = [_item(0, declared_code="1101(ii)", declared_role="Flour treatment agent")]
    result = resolve_items(items, refs)
    resolved = result.items[0]
    assert "role_class_mismatch" in resolved.flags
    assert resolved.classification == "enzyme"


def test_compound_parent_classified_as_compound_not_unknown():
    # A container item -- something else's parent_item_id points at it --
    # that doesn't itself resolve to an additive is a structural node, not
    # an unresolved substance.
    items = [
        _item(0, name_as_declared="Seasoning"),
        _item(1, name_as_declared="Salt", parent_item_id=0),
        _item(2, name_as_declared="Citric Acid", parent_item_id=0),
    ]
    result = resolve_items(items, _refs([]))
    parent = result.items[0]
    assert parent.classification == "compound"
    assert parent.resolution_method == "structural"
    assert parent.resolution_confidence == 0.95


def test_compound_override_does_not_clobber_a_real_additive():
    # An item that IS a parent but also resolves as an additive keeps
    # "additive" -- that combination is possible in principle.
    codex_ins = [_codex("330", "Citric acid")]
    items = [
        _item(0, declared_code="330"),
        _item(1, name_as_declared="something", parent_item_id=0),
    ]
    result = resolve_items(items, _refs(codex_ins))
    parent = result.items[0]
    assert parent.classification == "additive"


def test_fuzzy_match_into_guarded_family_forced_ambiguous():
    codex_ins = [
        _codex("472a", "Acetic and fatty acid esters of glycerol"),
        _codex("472c", "Citric and fatty acid esters of glycerol"),
    ]
    items = [_item(0, name_as_declared="citric fatty acid ester of glycerol")]
    result = resolve_items(items, _refs(codex_ins))
    resolved = result.items[0]
    assert resolved.resolution_method == "fuzzy"
    assert resolved.canonical_ins == "472c"
    assert resolved.classification == "ambiguous"
    assert resolved.resolution_confidence == 0.5
    assert "family_guard" in resolved.flags


def test_role_raising_and_stabilising_pinned_against_each_other():
    # Both end in "ising", but only one is a UK spelling variant. A substring
    # rule ("ising" -> "izing") would turn "raising" into the nonsense
    # "raizing" -- pinning these together catches that regression.
    assert normalise_role("RAISING AGENTS", FUNCTIONAL_CLASSES) == ["Raising agent"]
    assert normalise_role("stabilising agents", FUNCTIONAL_CLASSES) == ["Stabilizer"]


def test_role_strips_trailing_of_qualifier():
    assert normalise_role("EMULSIFIER OF VEGETABLE ORIGIN", FUNCTIONAL_CLASSES) == ["Emulsifier"]


def test_role_splits_compound_phrase():
    assert normalise_role("Emulsifying and Stabilizing Agent", FUNCTIONAL_CLASSES) == [
        "Emulsifier",
        "Stabilizer",
    ]


def test_role_strips_permitted_prefix():
    assert normalise_role("Permitted Emulsifying", FUNCTIONAL_CLASSES) == ["Emulsifier"]


def test_role_extras_map_covers_leavening_agent():
    assert normalise_role("Leavening Agents", FUNCTIONAL_CLASSES) == ["Raising agent"]
