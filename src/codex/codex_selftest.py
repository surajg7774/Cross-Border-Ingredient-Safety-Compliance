"""
codex_selftest.py — prove the parsers work without touching the internet.

    python run_codex.py --selftest

WHY THIS FILE EXISTS
--------------------
The single biggest open item on this source is that it has never been run live.
The FAO site was down when it was written, so "the code is finished" and "Codex
is finished" have been two different sentences for a while.

This file closes half that gap. It carries small HTML pages built to the exact
shape of the real ones — the same table ids, the same cell order, the same note
icons with the sentence in the `title` attribute — and asserts the behaviour
that each of the seven faults was about. It needs no network, so it works when
FAO is down, on a train, or on a machine that has never had the project's data.

It does NOT prove the downloading works. Nothing except the live site can prove
that. It proves that IF a page arrives, it is read correctly.

WHAT EACH TEST DEFENDS
----------------------
Every assertion below is tied to a fault that was actually found, so a failure
here means a fault has come back, not that a style rule was broken.
"""
from __future__ import annotations

import logging

import codex_gsfa as C

log = logging.getLogger("codex.selftest")


# =========================================================================== #
# FIXTURES — built to the documented shape of the real pages
# =========================================================================== #
INDEX = """<html><head><title>GSFA Online Additive Index</title></head><body>
<table>
<tr><td><a href="additives/details.html?id=173">Citric acid (330)</a></td></tr>
<tr><td><a href="additives/details.html?id=86">Tartrazine (102)</a></td></tr>
<tr><td><a href="additives/details.html?id=174">Adipic acid (355)</a></td></tr>
<tr><td><a href="additives/details.html?id=63">Gum arabic (Acacia gum) (414)</a></td></tr>
<tr><td><a href="additives/details.html?id=901">Ammonium carbonate (503(i))</a></td></tr>
<tr><td><a href="additives/details.html?id=902">Annatto extracts, bixin-based (160b(i))</a></td></tr>
<tr><td><a href="groups/details.html?id=162">BENZOATES</a></td></tr>
<tr><td><a href="index.html?lang=en&amp;showSynonyms=1">Show synonyms</a></td></tr>
</table></body></html>"""

SYNONYMS = """<html><body><table>
<tr><td><a href="additives/details.html?id=63">Gum arabic (Acacia gum) (414)</a></td></tr>
<tr><td><a href="additives/details.html?id=63">Acacia Gum* (414)</a></td></tr>
</table></body></html>"""


def _row(code, name, level, notes, cat_id):
    return (f'<tr><td>&nbsp;</td>'
            f'<td><img src="tree.gif" title="Food Category Hierarchy"/></td>'
            f'<td><a href="foodcat.html?id={cat_id}">{code}</a></td>'
            f'<td>{name}</td>'
            f'<td class="allowanceMaxLevel">{level}</td>'
            f'<td class="allowanceComments">{notes}</td>'
            f'<td>&nbsp;</td></tr>')


_N634 = ('<div><img src="notesm.gif" title="For use in products conforming to the '
         'Standard for Fermented Milks (CXS243-2003) only."/>'
         '<a href="notes.html?id=634">Note 634</a></div>')
_N1 = ('<div><img src="notesm.gif" title="As adipic acid."/>'
       '<a href="notes.html?id=1">Note 1</a></div>')

_FOOTER = ('<div class="footer"><a href="glossary.html">Functional Classes</a> '
           'Glossary © FAO and WHO 2026</div>')

# Real detail-page structure, copied from the saved pages: a bold label, then a
# <ul>. NOTE the synonym list comes FIRST and uses the SAME css class — on the
# real tartrazine page it does exactly this. Anything that grabs "the
# gsfaListTight list" without checking the label records the synonyms as the
# functional class.
ADIPIC = f"""<html><head><title>Additive Details for Adipic acid</title></head><body>
<p class="gsfaBigTitle">Adipic acid (355)</p>
<div class="bodycopybold">Synonym(s)</div>
<ul class="gsfaListTight"><li>Hexanedioic acid</li></ul>
<div class="bodycopybold">Functional Classes</div>
<ul class="gsfaListTight">
<li>Acidity regulator</li><li>Raising agent</li><li>Sequestrant</li></ul>
<p>Click here to search the FAO JECFA database for INS No. 355</p>
<table id="allowances"><tbody>
{_row("01.7", "Dairy-based desserts (e.g. pudding, fruit or flavoured yoghurt)",
      "1,500 mg/kg", _N634 + _N1, "55")}
{_row("05.3", "Chewing gum", "GMP", "", "77")}
</tbody></table>{_FOOTER}</body></html>"""

# A GROUP page. Verified against the real BENZOATES and TOCOPHEROLS pages:
# there is NO functional class section at all. The only thing on the page that
# looks like one is the footer link. The correct answer here is EMPTY.
BENZOATES = f"""<html><head><title>Food Additive Group Details for BENZOATES</title></head>
<body><p class="gsfaBigTitle">BENZOATES</p>
<p>The provisions that follow are defined at the additive group level, and thus
apply to the total content of the additives participating in this group.</p>
<table id="additives"><tbody>
<tr><td><img src="j.gif"/></td><td>210</td><td><a href="additives/details.html?id=231">Benzoic acid</a></td></tr>
<tr><td><img src="j.gif"/></td><td>211</td><td><a href="additives/details.html?id=232">Sodium benzoate</a></td></tr>
<tr><td><img src="j.gif"/></td><td>212</td><td><a href="additives/details.html?id=233">Potassium benzoate</a></td></tr>
<tr><td><img src="j.gif"/></td><td>213</td><td><a href="additives/details.html?id=234">Calcium benzoate</a></td></tr>
</tbody></table>
<table id="allowances"><tbody>
{_row("05.3", "Chewing gum", "1,500 mg/kg", "", "77")}
</tbody></table>{_FOOTER}</body></html>"""

# A group MEMBER's own page: it exists, it names the functional class, and it
# has no provisions of its own. Verified against the real benzoic acid page.
BENZOIC = f"""<html><head><title>Additive Details for Benzoic acid</title></head><body>
<p class="gsfaBigTitle">Benzoic acid (210)</p>
<div class="bodycopybold">Functional Classes</div>
<ul class="gsfaListTight"><li>Preservative</li></ul>
<p>Click here to search the FAO JECFA database for INS No. 210</p>
{_FOOTER}</body></html>"""

# A TABLE 3 additive. Verified against the real Curdlan page: ONE row in the
# allowances table, SIXTY-ONE in a second table with id="categories". Reading
# only the first reports the additive as permitted in one food out of sixty-two.
TABLE3 = f"""<html><head><title>Additive Details for Curdlan</title></head><body>
<p class="gsfaBigTitle">Curdlan (424)</p>
<div class="bodycopybold">Functional Classes</div>
<ul class="gsfaListTight"><li>Gelling agent</li><li>Stabilizer</li><li>Thickener</li></ul>
<table id="allowances"><tbody>
{_row("06.4.1", "Fresh pastas and noodles and like products", "GMP", _N1, "106")}
</tbody></table>
<td>GSFA Table 3 Provisions Curdlan is a food additive that is included in
Table 3 , and as such may be used in the following foods under the conditions of
good manufacturing practices (GMP) as outlined in the Preamble of the Codex GSFA.
Although not listed below, Curdlan could also be used in heat-treated butter milk
of food category 01.1.1 and spices of food category 12.2.1. Note that food
categories listed in the Annex to Table 3 were excluded accordingly.
Curdlan is acceptable in foods conforming to: (additive.commodityStandardsA).</td>
<table id="categories"><tbody>
<tr><td>&nbsp;</td><td><img src="tree.gif"/></td><td><a href="foods/details.html?id=6">01.1.4</a></td><td><a href="foods/details.html?id=6">Flavoured fluid milk drinks</a></td></tr>
<tr><td>&nbsp;</td><td><img src="tree.gif"/></td><td><a href="foods/details.html?id=9">01.3</a></td><td><a href="foods/details.html?id=9">Condensed milk and analogues</a></td></tr>
<tr><td>&nbsp;</td><td><img src="tree.gif"/></td><td><a href="foods/details.html?id=99">16.0</a></td><td><a href="foods/details.html?id=99">Prepared foods</a></td></tr>
</tbody></table>{_FOOTER}</body></html>"""

NO_PROVISIONS = f"""<html><head><title>Additive Details for Nothing</title></head><body>
<p class="gsfaBigTitle">Nothing (999)</p>
<div class="bodycopybold">Functional Classes</div>
<ul class="gsfaListTight"><li>Thickener</li></ul>{_FOOTER}</body></html>"""


# =========================================================================== #
# THE TESTS
# =========================================================================== #
def run() -> bool:
    """Run every check. Returns True if all passed."""
    passed: list[str] = []
    failed: list[str] = []

    def ok(label, got, want):
        (passed if got == want else failed).append(label)
        mark = "ok  " if got == want else "FAIL"
        print(f"  {mark} {label}")
        if got != want:
            print(f"         got  {got!r}")
            print(f"         want {want!r}")

    print("\n" + "=" * 66)
    print("SELF-TEST — parsers only, no internet")
    print("=" * 66)

    # ---- fault 6: dead citations ---- #
    print("\nfault 6 — citations must be rebuilt, never appended")
    recs = C.parse_index(INDEX, synonyms_by_id=C.parse_synonyms(SYNONYMS))
    by = {r.additive_name: r for r in recs}
    ok("citric acid url is not doubled", by["Citric acid"].source_url,
       "https://www.fao.org/gsfaonline/additives/details.html?id=173")
    ok("group url uses the groups path", by["BENZOATES"].source_url,
       "https://www.fao.org/gsfaonline/groups/details.html?id=162")
    ok("no url contains gsfaonline twice",
       sum(1 for r in recs if (r.source_url or "").count("gsfaonline") > 1), 0)

    # ---- the INS trap: link text, not url id ---- #
    print("\nthe INS trap — the number is in the link TEXT, not the url")
    ok("citric acid is 330, not 173", by["Citric acid"].canonical_id, "330")
    ok("503(i) survives as text", by["Ammonium carbonate"].canonical_id, "503(i)")
    ok("160b(i) survives as text",
       by["Annatto extracts, bixin-based"].canonical_id, "160b(i)")
    ok("canonical_id is always a string",
       all(isinstance(r.canonical_id, str) for r in recs), True)

    # ---- synonyms ---- #
    print("\nsynonyms — the shared id is the glue")
    ok("acacia gum attached to gum arabic",
       by["Gum arabic (Acacia gum)"].synonyms, ["Acacia Gum"])

    # ---- fault 1 and 3: note sentences and note codes ---- #
    print("\nfaults 1 and 3 — note SENTENCES kept, codes as a list")
    rows = C.parse_provisions_table(ADIPIC)
    ok("two provisions read", len(rows), 2)
    ok("note codes are a list, not one glued string",
       rows[0]["note_codes"], ["634", "1"])
    ok("the fermented-milks sentence is present",
       "Fermented Milks" in (rows[0]["conditions"] or ""), True)
    ok("the second note sentence is present too",
       "As adipic acid." in (rows[0]["conditions"] or ""), True)
    ok("the tree icon's tooltip is NOT mistaken for a note",
       rows[1]["conditions"], None)

    # ---- the GMP rule ---- #
    print("\nthe GMP rule — null, never zero")
    ok("GMP level is None", rows[1]["max_level_mg_kg"], None)
    ok("GMP basis is gmp", rows[1]["max_level_basis"], "gmp")
    ok("GMP is not stored as 0", rows[1]["max_level_mg_kg"] == 0, False)
    ok("1,500 with a comma parses", rows[0]["max_level_mg_kg"], 1500.0)

    # ---- the food category id bonus ---- #
    print("\nfood category id — the crosswalk key")
    ok("category id captured", rows[0]["food_category_id"], "55")
    ok("raw category kept as proof", rows[0]["food_category_raw"].startswith("01.7"), True)

    # ---- fault 7: functional classes ---- #
    print("\nfault 7 — functional classes split and validated")
    ok("glued pair splits", C.split_functional_classes("Flavour enhancer Sweetener"),
       ["Flavour enhancer", "Sweetener"])
    ok("triple splits", C.split_functional_classes("Acidity regulator Raising agent Sequestrant"),
       ["Acidity regulator", "Raising agent", "Sequestrant"])
    ok("longest name wins over short one",
       C.split_functional_classes("Colour retention agent"), ["Colour retention agent"])
    ok("invented class is REFUSED and kept raw",
       C.split_functional_classes("Colour Wizardry agent"), ["Colour Wizardry agent"])
    ok("official list is the Codex 27", len(C.FUNCTIONAL_CLASSES), 27)

    print("\nfunctional class must be READ from the list, and VALIDATED")
    raw, lst = C.extract_functional_classes(ADIPIC)
    ok("three classes read as three list items", lst,
       ["Acidity regulator", "Raising agent", "Sequestrant"])
    ok("raw string kept alongside", raw, "Acidity regulator Raising agent Sequestrant")
    ok("the synonym list above it is NOT mistaken for classes",
       "Hexanedioic acid" in lst, False)
    ok("the member's own page gives its class",
       C.extract_functional_classes(BENZOIC)[1], ["Preservative"])
    ok("a page with no provisions still gives its class",
       C.extract_functional_classes(NO_PROVISIONS)[1], ["Thickener"])
    ok("a GROUP page has no functional class, and none is invented",
       C.extract_functional_classes(BENZOATES)[1], [])
    ok("the footer 'Functional Classes Glossary' link is not read as a class",
       C.extract_functional_classes(BENZOATES)[0], None)

    print("\nsynonyms on the additive's own page")
    ok("detail-page synonyms read", C.extract_synonyms(ADIPIC), ["Hexanedioic acid"])
    ok("no synonym section gives an empty list", C.extract_synonyms(BENZOIC), [])

    # ---- faults 4 and 5: groups ---- #
    print("\nfaults 4 and 5 — group members recovered, shared limit carried")
    ok("group name is clean, not the banner", C.parse_group_name(BENZOATES), "BENZOATES")
    members = C.parse_group_members(BENZOATES)
    ok("four benzoates found", [m["canonical_id"] for m in members],
       ["210", "211", "212", "213"])
    grows = C.parse_group(BENZOATES, by["BENZOATES"])
    ok("one row per member per provision", len(grows), 4)
    ok("rows are keyed on the MEMBER, never the group",
       sorted({r.canonical_id for r in grows}), ["210", "211", "212", "213"])
    ok("every row carries the combined-limit sentence",
       all("total content" in (r.conditions or "") for r in grows), True)
    ok("every row names the other members",
       all("210, 211, 212, 213" in (r.conditions or "") for r in grows), True)
    ok("every row cites the GROUP page, where the rule is written",
       {r.source_url for r in grows},
       {"https://www.fao.org/gsfaonline/groups/details.html?id=162"})
    ok("via_group recorded", {r.via_group for r in grows}, {"BENZOATES"})
    ok("group rows carry NO invented class (backfilled in STEP 4 instead)",
       grows[0].functional_classes, [])

    # ---- the schema ---- #
    print("\nthe shared record shape")
    detail = C.parse_detail(ADIPIC, by["Adipic acid"])
    ok("one record per provision", len(detail), 2)
    ok("19 fields", len(vars(detail[0])), 19)
    ok("jurisdiction is CODEX", detail[0].jurisdiction, "CODEX")
    ok("effective_date is None by design", detail[0].effective_date, None)
    ok("an additive with no provisions still survives as one record",
       len(C.parse_detail(NO_PROVISIONS, by["Citric acid"])), 1)
    ok("...and that record still carries its functional class, for backfill",
       C.parse_detail(BENZOIC, by["Citric acid"])[0].functional_classes, ["Preservative"])

    # ---- fault 2: read structure, not words ---- #
    print("\nGSFA Table 3 — the SECOND permission list")
    t3 = C.parse_table3_categories(TABLE3)
    ok("Table 3 categories read", len(t3), 3)
    ok("category code and name kept", t3[0]["food_category_raw"],
       "01.1.4 Flavoured fluid milk drinks")
    ok("category id kept for the crosswalk", t3[0]["food_category_id"], "6")
    ok("Table 3 not confused with allowances",
       len(C.parse_provisions_table(TABLE3)), 1)
    ok("a page with no Table 3 gives an empty list",
       C.parse_table3_categories(ADIPIC), [])
    notice = C.parse_table3_notice(TABLE3)
    ok("the paragraph is captured with the additive name",
       (notice or "").startswith("Curdlan is a food additive"), True)
    ok("the exclusion sentence travels with it",
       "Annex to Table 3" in (notice or ""), True)
    ok("the extra-foods sentence is captured",
       "heat-treated butter milk" in (C.parse_table3_extra_foods(TABLE3) or ""), True)
    ok("FAO's unrendered template variables are reported",
       C.unrendered_placeholders(TABLE3), ["(additive.commodityStandardsA)"])

    t3recs = C.parse_detail(TABLE3, by["Citric acid"])
    ok("one record per allowance PLUS one per Table 3 food", len(t3recs), 4)
    ok("Table 3 rows are GMP with a null level",
       all(r.max_level_mg_kg is None and r.max_level_basis == "gmp"
           for r in t3recs if "Table 3" in r.source_name), True)
    ok("Table 3 rows never store 0",
       any(r.max_level_mg_kg == 0 for r in t3recs), False)
    ok("Table 3 rows are tagged in source_name, schema still 19 fields",
       len(vars(t3recs[-1])), 19)
    ok("every Table 3 row carries the GMP paragraph",
       all("under the conditions of good manufacturing" in (r.conditions or "")
           for r in t3recs if "Table 3" in r.source_name), True)
    ok("a categories table WITHOUT the Table 3 declaration is ignored",
       len([r for r in C.parse_detail(TABLE3.replace("GSFA Table 3 Provisions",
                                                     "Related Additives"),
                                      by["Citric acid"])
            if "Table 3" in r.source_name]), 0)
    ok("an additive with NO allowances but WITH Table 3 still yields rows",
       len([r for r in C.parse_detail(
                TABLE3.replace('<table id="allowances">', '<table id="none">'),
                by["Citric acid"]) if "Table 3" in r.source_name]), 3)
    ok("no row was invented for the extra foods (01.1.1 / 12.2.1)",
       any((r.food_category_raw or "").startswith(("01.1.1", "12.2.1")) for r in t3recs),
       False)

    print("\nfault 2 — structure survives cosmetic change")
    ok("a table with no <tbody> still parses",
       len(C.parse_provisions_table(ADIPIC.replace("<tbody>", "").replace("</tbody>", ""))), 2)
    shifted = ADIPIC.replace('<tr><td>&nbsp;</td><td><img src="tree.gif"',
                             '<tr><td>&nbsp;</td><td>&nbsp;</td><td><img src="tree.gif"')
    ok("a shifted column is REFUSED, not misread",
       len(C.parse_provisions_table(shifted)), 0)

    print("\n" + "=" * 66)
    if failed:
        print(f"SELF-TEST FAILED — {len(failed)} of {len(passed) + len(failed)} checks")
        for f in failed:
            print(f"   {f}")
        print("\nDo NOT run the crawl until these pass.")
    else:
        print(f"SELF-TEST PASSED — all {len(passed)} checks")
        print("\nThe parsers are sound. This says nothing about the downloading,")
        print("which only the live site can prove. Next: --probe-group 162")
    print("=" * 66)
    return not failed