"""
run_codex.py — ONE command to fetch everything from Codex GSFA.

    python run_codex.py

That does the whole job:

    STEP 1  the dictionary   index + synonyms + all 33 group pages
                             -> data/codex_backbone.json
    STEP 2  group rules      each group's provisions copied down to every member,
                             carrying via_group and the combined-limit warning
    STEP 3  additive rules   one row per (additive x food category)
    STEP 4  member pages     group members may or may not have a page of their
                             own. We try. No page is not an error.
    STEP 5  quality report   the numbers that tell you it worked
                             -> data/codex_provisions.json

Safe to stop with Ctrl+C at any time. Everything is checkpointed; running it
again resumes where it left off.

OTHER COMMANDS
--------------
    python run_codex.py --selftest            check the parsers offline, no internet
    python run_codex.py --probe-group 162     look at ONE group page, save nothing
    python run_codex.py --probe 86            look at ONE additive page, save nothing
    python run_codex.py --offline page.html   parse a SAVED page, no internet
    python run_codex.py --limit 10            small trial crawl
    python run_codex.py --backbone-only       just the dictionary, no rules
    python run_codex.py --fresh               delete old data and start clean
    python run_codex.py --check Tartrazine    print every stored rule for one additive
    python run_codex.py --data DIR            write somewhere else

WHY THE PROBES EXIST
--------------------
The project rule is: never write or trust a parser before looking at one real
page. Run --probe-group 162 and --probe 86 first. They take ten seconds, save
nothing, and would catch most of what could go wrong.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict

import httpx

from codex_gsfa import (
    FUNCTIONAL_CLASSES, AdditiveRecord, detail_url, fetch, fetch_backbone,
    parse_detail, parse_group, parse_group_members,
    probe_detail_html, probe_group_html,
)

# =========================================================================== #
# WHERE THE DATA GOES
# =========================================================================== #
# This project has TWO folders called "data":
#
#     PROJECT/data/        <- CODEX output belongs here
#     PROJECT/src/data/    <- the EU output lives here
#
# "one level up from this file" is src/data — the EU folder. Writing there
# would quietly overwrite another source's work. So instead we walk UP from
# this file until we find the project root (the folder holding pyproject.toml)
# and take the data folder sitting beside it. main() prints the folder it
# chose before anything is read, written or deleted.
def _find_data_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    folder = here
    while True:
        if os.path.exists(os.path.join(folder, "pyproject.toml")):
            return os.path.join(folder, "data")
        parent = os.path.dirname(folder)
        if parent == folder:                      # reached the drive root
            fallback = os.path.abspath(os.path.join(here, "..", "..", "data"))
            print("WARNING: no pyproject.toml found above")
            print(f"         {here}")
            print(f"         Guessing {fallback}")
            print("         If that is wrong, pass --data C:\\path\\to\\PROJECT\\data")
            return fallback
        folder = parent


DATA = BACKBONE = PROVISIONS = DONE = ""


def _set_data_dir(path: str) -> None:
    """Point every output file at `path`. Called once, before anything runs."""
    global DATA, BACKBONE, PROVISIONS, DONE
    DATA = os.path.abspath(path)
    BACKBONE = os.path.join(DATA, "codex_backbone.json")
    PROVISIONS = os.path.join(DATA, "codex_provisions.json")
    DONE = os.path.join(DATA, "_provisions_done.json")


_set_data_dir(_find_data_dir())

log = logging.getLogger("run_codex")


# =========================================================================== #
# disk
# =========================================================================== #
def _read(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def _write(path, obj):
    os.makedirs(DATA, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _load_backbone() -> list[AdditiveRecord]:
    raw = _read(BACKBONE, None)
    if raw is None:
        print(f"MISSING: {os.path.abspath(BACKBONE)}")
        print("Run: python run_codex.py")
        sys.exit(1)
    keep = set(AdditiveRecord.__dataclass_fields__)   # tolerate older files
    return [AdditiveRecord(**{k: v for k, v in d.items() if k in keep}) for d in raw]


# =========================================================================== #
# STEP 1 — the dictionary
# =========================================================================== #
def build_backbone(client) -> list[AdditiveRecord]:
    print("\n" + "=" * 66)
    print("STEP 1 — building the name -> INS dictionary")
    print("=" * 66)

    records = fetch_backbone(client, expand_groups=True)
    _write(BACKBONE, [asdict(r) for r in records])

    total = len(records)
    groups = [r for r in records if r.is_group]
    members = [r for r in records if r.via_group]
    unmapped_nongroup = [r for r in records
                         if r.canonical_id.startswith("UNMAPPED:") and not r.is_group]
    with_syn = [r for r in records if r.synonyms]
    doubled = [r for r in records
               if r.source_url and r.source_url.count("gsfaonline") > 1]
    bracketed = [r for r in records
                 if "(" in r.canonical_id and not r.canonical_id.startswith("UNMAPPED:")]

    print(f"\n  total records ............... {total}")
    print(f"  real INS assigned ........... {total - len(groups)}")
    print(f"  group headings .............. {len(groups)}   -> expected UNMAPPED")
    print(f"  members recovered by groups . {len(members)}   <- these were MISSING before")
    print(f"  records with a synonym ...... {len(with_syn)}")
    print(f"  sub-type ids like 503(i) .... {len(bracketed)}   -> canonical_id is TEXT")
    print(f"  UNMAPPED but NOT a group .... {len(unmapped_nongroup)}   <- must be 0")
    print(f"  DOUBLED urls (broken) ....... {len(doubled)}   <- must be 0")

    if members:
        print("\n  sample recovered members:")
        for r in members[:6]:
            print(f"     {r.canonical_id:>10}  {r.additive_name[:36]:<36} via {r.via_group}")
    else:
        print("\n  WARNING: no group members recovered. Check one group page:")
        print("     python run_codex.py --probe-group 162")

    if unmapped_nongroup:
        print("\n  WARNING: real additives with no INS (first 5):")
        for r in unmapped_nongroup[:5]:
            print(f"     {r.additive_name}")

    print(f"\n  saved -> {os.path.abspath(BACKBONE)}")
    return records


# =========================================================================== #
# STEPS 2-4 — the rules
# =========================================================================== #
def crawl(client, records, limit=None) -> list[dict]:
    provisions = _read(PROVISIONS, [])
    done = set(_read(DONE, []))

    groups = [r for r in records if r.is_group and r.source_url]
    # members were added by group expansion; they are handled in STEP 4
    additives = [r for r in records
                 if not r.is_group and r.source_url and not r.via_group]
    if limit:
        groups = groups[: max(1, limit // 10)]
        additives = additives[:limit]

    def save():
        _write(PROVISIONS, provisions)
        _write(DONE, sorted(done))

    member_queue: list[dict] = []

    # ---------------- STEP 2 — groups ---------------- #
    print("\n" + "=" * 66)
    print(f"STEP 2 — group rules ({len(groups)} groups)")
    print("=" * 66)

    for i, grp in enumerate(groups, 1):
        try:
            html = fetch(client, grp.source_url)
        except httpx.HTTPError as e:
            print(f"  [{i}/{len(groups)}] FAILED {grp.additive_name}: {e}")
            continue

        members = parse_group_members(html)
        member_queue.extend(members)

        if grp.source_url in done:
            print(f"  [{i}/{len(groups)}] {grp.additive_name[:40]:<40} (already done)")
            continue

        rows = parse_group(html, grp)
        provisions.extend(asdict(r) for r in rows)
        done.add(grp.source_url)
        flag = "" if members else "   <-- NO MEMBERS PARSED, CHECK THIS PAGE"
        print(f"  [{i}/{len(groups)}] {grp.additive_name[:40]:<40} "
              f"{len(members)} members  +{len(rows)} rows{flag}")
        save()

    # ---------------- STEP 3 — additives ---------------- #
    print("\n" + "=" * 66)
    print(f"STEP 3 — additive rules ({len(additives)} additives)")
    print("=" * 66)

    for i, rec in enumerate(additives, 1):
        # rebuild a clean URL from the id, immune to doubling in an older file
        did = rec.source_url.split("id=")[-1] if rec.source_url else None
        if not did or not did.isdigit():
            print(f"  [{i}/{len(additives)}] SKIP {rec.canonical_id}: no usable id")
            continue
        url = detail_url(did, is_group=False)
        if url in done:
            continue

        try:
            rows = parse_detail(fetch(client, url), rec)
        except httpx.HTTPError as e:
            print(f"  [{i}/{len(additives)}] FAILED {rec.canonical_id}: {e}")
            continue

        provisions.extend(asdict(r) for r in rows)
        done.add(url)
        print(f"  [{i}/{len(additives)}] {rec.canonical_id:>10} "
              f"{rec.additive_name[:38]:<38} +{len(rows)} rows")
        if i % 20 == 0:
            save()

    save()

    # ---------------- STEP 4 — do members have their own pages? ------------- #
    if member_queue:
        print("\n" + "=" * 66)
        print(f"STEP 4 — checking {len(member_queue)} group members for own pages")
        print("=" * 66)

        own_rules = empty_page = no_page = 0
        # A group page carries NO functional class of its own — verified on the
        # real BENZOATES and TOCOPHEROLS pages, neither of which has the label.
        # So every row inherited from a group starts with an empty class, and
        # the EU join (which keys on exactly that field) would get nothing for
        # benzoic acid, the tocopherols, the sulfites and the rest.
        #
        # The member's OWN page does carry it. Benzoic acid's page has zero
        # provisions but does say "Preservative". The original kept only rows
        # with a food category and dropped that page entirely, fetching the
        # label and then throwing it away. Collected here and written back.
        backfill: dict[str, dict] = {}
        for i, m in enumerate(member_queue, 1):
            if not m["detail_id"]:
                no_page += 1
                continue
            url = detail_url(m["detail_id"], is_group=False)
            if url in done:
                continue

            base = AdditiveRecord(
                canonical_id=m["canonical_id"],
                additive_name=m["additive_name"],
                source_url=url,
                is_group=False,
                # deliberately NO via_group: these would be the member's OWN rules
            )
            try:
                rows = parse_detail(fetch(client, url), base)
            except httpx.HTTPError:
                no_page += 1
                done.add(url)
                print(f"  [{i}] {m['canonical_id']:>10} {m['additive_name'][:32]:<32} "
                      f"no own page")
                continue

            # Harvest identity even when the page has no rules of its own.
            if rows:
                got = {}
                if rows[0].functional_classes:
                    got["functional_class"] = rows[0].functional_class
                    got["functional_classes"] = rows[0].functional_classes
                if rows[0].synonyms:
                    got["synonyms"] = rows[0].synonyms
                if got:
                    backfill[m["canonical_id"]] = got

            # parse_detail returns one identity row when a page has no table.
            # That is not real data — the member is already in the backbone.
            real = [r for r in rows if r.food_category_raw]
            if real:
                provisions.extend(asdict(r) for r in real)
                own_rules += 1
                print(f"  [{i}] {m['canonical_id']:>10} {m['additive_name'][:32]:<32} "
                      f"+{len(real)} OWN rows")
            else:
                empty_page += 1
                tag = ""
                if m["canonical_id"] in backfill:
                    tag = f"  -> class {backfill[m['canonical_id']].get('functional_classes')}"
                print(f"  [{i}] {m['canonical_id']:>10} {m['additive_name'][:32]:<32} "
                      f"page exists, no rules{tag}")
            done.add(url)
            if i % 20 == 0:
                save()

        # ---- write the harvested identity back onto the inherited rows ---- #
        filled = 0
        for p in provisions:
            got = backfill.get(p.get("canonical_id"))
            if not got:
                continue
            if got.get("functional_classes") and not p.get("functional_classes"):
                p["functional_class"] = got["functional_class"]
                p["functional_classes"] = got["functional_classes"]
                filled += 1
            for s in got.get("synonyms", []):
                if s not in (p.get("synonyms") or []):
                    p.setdefault("synonyms", []).append(s)

        save()
        print(f"\n  with own rules ..... {own_rules}")
        print(f"  page but no rules .. {empty_page}")
        print(f"  no page at all ..... {no_page}")
        print(f"  rows given a class back from the member's page: {filled}")
        print("  -> if 'with own rules' is 0, the group page is the ONLY source")
        print("     of rules for those additives, which is what we expect.")

    return provisions


# =========================================================================== #
# STEP 5 — the report
# =========================================================================== #
def _has_sentence(conditions) -> bool:
    """True if any part of the fine print is words, not just a bare [Note n]."""
    if not conditions:
        return False
    return any(not part.strip().startswith("[Note")
               for part in conditions.split(" | ") if part.strip())


def report(provisions: list[dict]) -> None:
    total = len(provisions)
    ids = {p["canonical_id"] for p in provisions}
    inherited = [p for p in provisions if p.get("via_group")]
    with_cond = [p for p in provisions if p.get("conditions")]
    # A row "carries a sentence" if ANY of its notes has words, not just the
    # first one. "[Note 161] | As adipic acid. [Note 1]" does carry a sentence,
    # even though it opens with a bare code.
    with_text = [p for p in provisions if _has_sentence(p.get("conditions"))]
    gmp = [p for p in provisions if p.get("max_level_basis") == "gmp"]
    zero_gmp = [p for p in gmp if p.get("max_level_mg_kg") == 0]
    dead = [p for p in provisions
            if (p.get("source_url") or "").count("gsfaonline") > 1]
    cat_ids = [p for p in provisions if p.get("food_category_id")]
    # A group's limit is a budget SHARED by its members. That sentence is
    # scraped from the group page, so if one group words it differently the
    # regex misses it and the constraint vanishes silently. Count it.
    shared_ok = [p for p in inherited
                 if "total content" in (p.get("conditions") or "").lower()]
    lost_notice = sorted({p.get("via_group") for p in inherited
                          if p not in shared_ok})
    # a class that failed validation comes back as the raw string in a 1-item
    # list, so it still contains a space-joined multi-class value
    unsplit = [p for p in provisions
               if len(p.get("functional_classes") or []) == 1
               and p["functional_classes"][0] not in FUNCTIONAL_CLASSES]
    all_classes = sorted({c for p in provisions
                          for c in (p.get("functional_classes") or [])})
    # A row with NO functional class at all. This number exists because the
    # first version of extract_functional_class() could return None silently on
    # a whole page and nothing in this report would have shown it. The EU's
    # 15,199 rows are meant to be filled in by joining on this field, so an
    # empty one here quietly empties that join. Counted, and named per additive
    # so you can go and look at the page.
    no_class = [p for p in provisions if not p.get("functional_classes")]
    no_class_names = sorted({p["additive_name"] for p in no_class})
    # GSFA Table 3 — the second permission list, read from <table id="categories">.
    # For an additive in Table 3 this is the bulk of its permissions; the
    # allowances table may hold only one or two rows. Curdlan: 1 allowance row,
    # 61 Table 3 rows.
    t3 = [p for p in provisions if "Table 3" in (p.get("source_name") or "")]
    t3_names = sorted({p["additive_name"] for p in t3})
    # Rows whose Table 3 paragraph names extra foods that are NOT in the list.
    # Deliberately not turned into rows — the permission is for an item inside
    # the category, not the whole category. Flagged for a human instead.
    t3_extra = sorted({p["additive_name"] for p in t3
                       if "Although not listed below" in (p.get("conditions") or "")})

    print("\n" + "=" * 66)
    print("STEP 5 — quality report")
    print("=" * 66)
    print(f"  provision rows ............. {total}")
    print(f"  distinct additives ......... {len(ids)}")
    print(f"  inherited from a group ..... {len(inherited)}")
    print(f"  ... carrying shared-limit .. {len(shared_ok)}   "
          f"<- must equal {len(inherited)}")
    print(f"  rows with conditions ....... {len(with_cond)}")
    print(f"  ... carrying real SENTENCES  {len(with_text)}   <- was 0 before the fix")
    print(f"  rows with a food category id {len(cat_ids)}")
    print(f"  GMP rows ................... {len(gmp)}")
    print(f"  GMP stored as 0 ............ {len(zero_gmp)}   <- must be 0 (0 = banned)")
    print(f"  GSFA Table 3 rows .......... {len(t3)}   ({len(t3_names)} additives)")
    if t3_extra:
        print(f"  ... naming EXTRA foods not in the list: {len(t3_extra)}   <- READ THESE")
        for n in t3_extra[:5]:
            print(f"       {n}")
        print("     The paragraph permits a specific item inside a category, not")
        print("     the whole category, so no row was invented. Decide by hand.")
    print(f"  distinct functional classes  {len(all_classes)}   <- expect 27 or fewer")
    print(f"  rows with NO class at all .. {len(no_class)}   <- blocks the EU join")
    print(f"  classes that failed to split {len(unsplit)}   <- must be 0")
    if no_class_names:
        print(f"     {len(no_class_names)} additives have no functional class. First 5:")
        for n in no_class_names[:5]:
            print(f"       {n}")
        print("     Open one page and check the 'Functional Class' line is there.")
    if unsplit:
        print("     Codex may have added a class name. Add it to")
        print("     FUNCTIONAL_CLASSES in codex_gsfa.py. Examples:")
        for p in unsplit[:3]:
            print(f"       {p['functional_classes'][0]!r}")
    print(f"  BROKEN citations ........... {len(dead)}   <- must be 0")
    if lost_notice:
        print("\n  WARNING: the combined-limit sentence was NOT found on these")
        print("  groups, so their members' rows do not carry the shared budget.")
        print("  Save one of those pages and check how it words the notice:")
        for g in lost_notice[:5]:
            print(f"       {g}")
    print(f"\n  saved -> {os.path.abspath(PROVISIONS)}")

    print("\n  CHECK THESE THREE NOW:")
    print('     python run_codex.py --check "Adipic acid"')
    print("     python run_codex.py --check Tartrazine")
    print('     python run_codex.py --check "Sodium benzoate"')


# =========================================================================== #
# --check
# =========================================================================== #
def check(needle: str, full: bool = False) -> None:
    def hit(row):
        n = needle.lower().strip()
        return (row.get("canonical_id", "").lower() == n
                or n in (row.get("additive_name") or "").lower()
                or any(n in (s or "").lower() for s in row.get("synonyms") or []))

    rows = [r for r in _read(PROVISIONS, []) if hit(r)]
    if not rows:
        back = [r for r in _read(BACKBONE, []) if hit(r)]
        print(f"\nNo rules stored for {needle!r}.")
        if back:
            print("It IS in the dictionary, so it is known but has no stored rules:")
            for r in back[:5]:
                tag = "  (GROUP HEADING)" if r["is_group"] else ""
                print(f"   {r['canonical_id']:>10}  {r['additive_name']}{tag}")
        else:
            print("It is not in the dictionary either — the resolver would return")
            print("UNMAPPED and send it to manual review, never 'banned'.")
        return

    inherited = [r for r in rows if r.get("via_group")]
    print(f"\n=== {needle} ===")
    print(f"  canonical id ....... {', '.join(sorted({r['canonical_id'] for r in rows}))}")
    print(f"  name ............... {rows[0]['additive_name']}")
    print(f"  functional class ... {rows[0].get('functional_class')}   (raw, as FAO printed it)")
    print(f"  ... split into ..... {rows[0].get('functional_classes')}")
    print(f"  synonyms ........... {rows[0].get('synonyms')}")
    print(f"  total rules ........ {len(rows)}")
    print(f"     own ............. {len(rows) - len(inherited)}")
    print(f"     via a group ..... {len(inherited)}"
          + (f"  ({', '.join(sorted({r['via_group'] for r in inherited}))})"
             if inherited else ""))
    print(f"  rules with notes ... {sum(1 for r in rows if r.get('conditions'))}")
    print(f"  broken citations ... "
          f"{sum(1 for r in rows if (r.get('source_url') or '').count('gsfaonline') > 1)}"
          "   <- must be 0")

    show = rows if full else rows[:12]
    print(f"\n--- rules ({len(show)} of {len(rows)}) ---")
    for r in show:
        lvl = ("GMP" if r["max_level_basis"] == "gmp"
               else f"{r['max_level_mg_kg']} {r['max_level_basis']}")
        print(f"\n  {r['food_category_raw']}")
        print(f"     level : {lvl}")
        if r.get("via_group"):
            print(f"     via   : group {r['via_group']}")
        if r.get("conditions"):
            print(f"     notes : {r['conditions']}")
    if not full and len(rows) > 12:
        print(f"\n  ... {len(rows) - 12} more. Add --full to see them all.")

    print(f"\n  cited page: {rows[0].get('source_url')}")
    print("  Open it and compare. If they disagree, the DATA is wrong.")


# =========================================================================== #
# main
# =========================================================================== #
def main() -> None:
    ap = argparse.ArgumentParser(
        description="Fetch everything from Codex GSFA.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Start with:  python run_codex.py --probe-group 162",
    )
    ap.add_argument("--selftest", action="store_true",
                    help="check the parsers offline, no internet needed")
    ap.add_argument("--probe", metavar="ID", help="look at one additive page")
    ap.add_argument("--probe-group", metavar="ID", help="look at one group page")
    ap.add_argument("--offline", metavar="FILE", help="parse a saved .html file")
    ap.add_argument("--check", metavar="NAME", help="print stored rules for one additive")
    ap.add_argument("--full", action="store_true", help="with --check, show every rule")
    ap.add_argument("--limit", type=int, help="small trial crawl")
    ap.add_argument("--backbone-only", action="store_true", help="dictionary only")
    ap.add_argument("--fresh", action="store_true", help="delete old data first")
    ap.add_argument("--data", metavar="DIR", help="write somewhere else")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # Say which folder we are about to use BEFORE touching anything. This
    # project has two folders named "data"; printing the resolved one is how
    # you catch it writing to the wrong source's directory.
    if args.data:
        _set_data_dir(args.data)
    print(f"data folder: {DATA}")
    if not os.path.isdir(DATA):
        print("             (does not exist yet — it will be created on first write)")

    # ---- selftest: no network, no data folder needed ---- #
    if args.selftest:
        import codex_selftest
        sys.exit(0 if codex_selftest.run() else 1)

    # ---- offline / check: no network ---- #
    if args.offline:
        html = open(args.offline, encoding="utf-8", errors="replace").read()
        # Decide group-vs-additive by PARSING for the members table, not by
        # searching the raw text for id="additives". A browser "Save as" can
        # rewrite attributes with single quotes or reorder them, and the raw
        # string match would then send a group page down the additive path.
        from bs4 import BeautifulSoup
        is_group_page = BeautifulSoup(html, "lxml").find("table", id="additives") is not None
        print(probe_group_html(html) if is_group_page else probe_detail_html(html))
        return

    if args.check:
        check(args.check, full=args.full)
        return

    # ---- probes: one request, nothing saved ---- #
    if args.probe or args.probe_group:
        is_group = bool(args.probe_group)
        url = detail_url(args.probe_group or args.probe, is_group=is_group)
        with httpx.Client() as client:
            html = fetch(client, url)
        print(f"\nprobed: {url}\n")
        print(probe_group_html(html) if is_group else probe_detail_html(html))
        print("\nNothing was saved. Compare this with the page in your browser.")
        return

    # ---- the full run ---- #
    if args.fresh:
        for p in (BACKBONE, PROVISIONS, DONE):
            if os.path.exists(p):
                os.remove(p)
                print(f"deleted {os.path.abspath(p)}")

    provisions: list[dict] = []
    with httpx.Client() as client:
        try:
            records = build_backbone(client)
            if args.backbone_only:
                print("\nBackbone only — stopping here.")
                return
            provisions = crawl(client, records, limit=args.limit)
        except KeyboardInterrupt:
            print("\n\nStopped by you. Everything so far is saved.")
            print("Run the same command again to resume.")
            provisions = _read(PROVISIONS, [])
        finally:
            if provisions:
                report(provisions)


if __name__ == "__main__":
    main()