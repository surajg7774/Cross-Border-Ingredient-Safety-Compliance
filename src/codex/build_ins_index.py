"""Build the Codex INS backbone from the official CXG 36-1989 PDF.

Run once:   uv run python src/codex/build_ins_index.py

Reads data/reference/CXG_036e.pdf and writes:
  data/reference/functional_classes.json  (Section 2 -- the 27 functional classes)
  data/reference/codex_ins.json           (Section 3 -- the INS list, numerical order)

WHY A PDF AND NOT THE LIVE SITE
--------------------------------
src/codex/run_codex.py scrapes gsfaonline.fao.org. That site returns 403, and
its robots.txt disallows the URL pattern the crawler needs (`Disallow: /*?id=*`).
This script reads the official published PDF instead -- no network access at
all. run_codex.py, codex_gsfa.py and codex_selftest.py are untouched; this is
a separate, standalone build script.

WHY SECTION BOUNDARIES ARE FOUND BY HEADING TEXT, NOT PAGE NUMBERS
--------------------------------------------------------------------
The document is amended periodically (this copy is the 2025 amendment) and
page numbers shift between versions. Every boundary below is located by
searching for the literal heading/header text that marks it, so a future PDF
with different pagination still parses correctly.

WHY WORD POSITIONS, NOT pdfplumber's TABLE EXTRACTION
--------------------------------------------------------
The source tables have no ruling lines between columns, so pdfplumber's table
detection does not find them. Worse, columns are NOT vertically aligned with
each other: a wrapped ingredient name can start a line ABOVE its own INS
number, and a functional class's purposes can appear both above and below the
class name's own line (confirmed by inspecting real word coordinates for INS
470/470(i)/470(ii) and 341(iii)). So this script treats each column as an
INDEPENDENT stream of words, and assigns every non-anchor fragment (a wrapped
name line, a purpose line, a wrapped class-name fragment) to whichever anchor
(an INS number, or a numbered functional-class heading) is NEAREST on the
page -- not to whichever anchor precedes it. See `nearest_index`.
"""

import bisect
import json
import re
from collections import Counter
from pathlib import Path

import pdfplumber

PDF_PATH = Path("data/reference/CXG_036e.pdf")
FUNCTIONAL_CLASSES_PATH = Path("data/reference/functional_classes.json")
CODEX_INS_PATH = Path("data/reference/codex_ins.json")

PAGE_SPAN = 2000.0  # > any real page height; makes (page, top) sort as one monotonic number
TOP_MIN, TOP_MAX = 145, 780  # excludes the running page banner/header and the footer page number

INS_RE = re.compile(r"^\d{2,5}[a-z]?(\([ivxlc]+\))?$", re.IGNORECASE)
CLASS_ANCHOR_RE = re.compile(r"^(\d+)\.\s*(.+)$")

# Column x0-boundaries, read from the header row's word positions (see module docstring
# for why these can't just be pdfplumber table columns). Same for every page checked.
COLS_FUNCTIONAL_CLASSES = {"class": (0, 205), "definition": (205, 353), "purpose": (353, 560)}
COLS_INS_LIST = {"ins": (0, 180), "name": (180, 372), "class": (372, 458), "purpose": (458, 560)}


# =========================================================================== #
# low-level: words, lines, section boundaries
# =========================================================================== #
def _gtop(page_index, top):
    """A single number combining page + vertical position, increasing through the doc."""
    return page_index * PAGE_SPAN + top


_footnote_top_cache = {}


def _footnote_top(pdf, page_index):
    """Top of a page-body footnote paragraph (starts with a standalone '*'), if any.

    INS 183 (Jagua blue) carries a footnote explaining its parentheses are not a
    synonym marker. It's printed in the body text area, not a true page footer, so
    TOP_MAX alone doesn't exclude it -- and left in, its prose leaks into whichever
    column and INS record happens to sit nearest on the page.
    """
    if page_index not in _footnote_top_cache:
        top = None
        for w in pdf.pages[page_index].extract_words():
            if w["text"] == "*" and w["top"] > TOP_MIN:
                top = w["top"] if top is None else min(top, w["top"])
        _footnote_top_cache[page_index] = top
    return _footnote_top_cache[page_index]


def _zone_words(pdf, g_start, g_end, x_range):
    """Words in one column's x-range, within (g_start, g_end), across however many pages that spans."""
    x_min, x_max = x_range
    page_start = max(0, int(g_start // PAGE_SPAN))
    page_end = min(len(pdf.pages), int(g_end // PAGE_SPAN) + 1)
    words = []
    for page_index in range(page_start, page_end):
        page_top_max = TOP_MAX
        footnote_top = _footnote_top(pdf, page_index)
        if footnote_top is not None:
            page_top_max = min(page_top_max, footnote_top - 1)
        for w in pdf.pages[page_index].extract_words():
            if not (TOP_MIN <= w["top"] <= page_top_max):
                continue
            if not (x_min <= w["x0"] < x_max):
                continue
            g = _gtop(page_index, w["top"])
            if not (g_start < g < g_end):
                continue
            w = dict(w)
            w["gtop"] = g
            words.append(w)
    words.sort(key=lambda w: (w["gtop"], w["x0"]))
    return words


def _cluster_lines(words, tolerance=2.5):
    """Group same-column words into physical lines by proximity in vertical position."""
    lines = []
    for w in words:
        if lines and abs(w["gtop"] - lines[-1][-1]["gtop"]) <= tolerance:
            lines[-1].append(w)
        else:
            lines.append([w])
    for line in lines:
        line.sort(key=lambda w: w["x0"])
    return lines


def _line_text(line):
    return " ".join(w["text"] for w in line)


def _nearest_index(sorted_positions, g):
    """Index of the anchor position in `sorted_positions` closest to `g` (either direction)."""
    i = bisect.bisect_left(sorted_positions, g)
    candidates = [j for j in (i - 1, i) if 0 <= j < len(sorted_positions)]
    return min(candidates, key=lambda j: abs(sorted_positions[j] - g))


def _find_heading(pdf, needle_words, after_gtop=0.0):
    """gtop of the first place `needle_words` appear consecutively, in reading order, after `after_gtop`."""
    for page_index, page in enumerate(pdf.pages):
        words = sorted(page.extract_words(), key=lambda w: (w["top"], w["x0"]))
        for i in range(len(words) - len(needle_words) + 1):
            g = _gtop(page_index, words[i]["top"])
            if g <= after_gtop:
                continue
            if all(words[i + j]["text"] == needle_words[j] for j in range(len(needle_words))):
                return g
    raise ValueError(f"heading not found after {after_gtop}: {needle_words!r}")


def find_section_boundaries(pdf):
    """Locate every section/sub-list boundary by heading text (never a hardcoded page number)."""
    section2_start = _find_heading(pdf, ["Functional", "classes"])
    section3_start = _find_heading(pdf, ["INS", "No."], after_gtop=section2_start)
    section3_supp_heading = _find_heading(pdf, ["SUPPLEMENTARY", "LIST"], after_gtop=section3_start)
    section3_supp_start = _find_heading(pdf, ["INS", "No."], after_gtop=section3_supp_heading)
    section4_heading = _find_heading(
        pdf, ["List", "in", "alphabetical", "order"], after_gtop=section3_supp_start
    )
    section4_start = _find_heading(pdf, ["INS", "No."], after_gtop=section4_heading)
    section4_supp_heading = _find_heading(pdf, ["SUPPLEMENTARY", "LIST"], after_gtop=section4_start)
    section4_supp_start = _find_heading(pdf, ["INS", "No."], after_gtop=section4_supp_heading)
    end = _find_heading(pdf, ["Referenced", "texts"], after_gtop=section4_supp_start)
    return {
        "section2": (section2_start, section3_start),
        "section3_main": (section3_start, section3_supp_heading),
        "section3_supplementary": (section3_supp_start, section4_heading),
        "section4_main": (section4_start, section4_supp_heading),
        "section4_supplementary": (section4_supp_start, end),
    }


# =========================================================================== #
# Section 2 -- functional classes, definitions, purposes
# =========================================================================== #
def parse_functional_classes(pdf, g_start, g_end):
    cols = COLS_FUNCTIONAL_CLASSES
    class_lines = _cluster_lines(_zone_words(pdf, g_start, g_end, cols["class"]))
    definition_lines = _cluster_lines(_zone_words(pdf, g_start, g_end, cols["definition"]))
    purpose_lines = _cluster_lines(_zone_words(pdf, g_start, g_end, cols["purpose"]))

    anchors, names = [], []
    for line in class_lines:
        m = CLASS_ANCHOR_RE.match(_line_text(line))
        if m:
            anchors.append(line[0]["gtop"])
            names.append(m.group(2).strip())

    definitions = [[] for _ in anchors]
    for line in definition_lines:
        definitions[_nearest_index(anchors, line[0]["gtop"])].append((line[0]["gtop"], _line_text(line)))
    purposes_raw = [[] for _ in anchors]
    for line in purpose_lines:
        purposes_raw[_nearest_index(anchors, line[0]["gtop"])].append((line[0]["gtop"], _line_text(line)))

    records = []
    for i, class_name in enumerate(names):
        definitions[i].sort()
        purposes_raw[i].sort()
        definition = " ".join(t for _, t in definitions[i])
        purpose_text = " ".join(t for _, t in purposes_raw[i])
        purposes = [p.strip() for p in purpose_text.split(",") if p.strip()]
        records.append({"class": class_name, "definition": definition, "purposes": purposes})
    return records


# =========================================================================== #
# Section 3 / 4 -- the INS list itself
# =========================================================================== #
def _merge_class_fragments(class_fragments, known_classes):
    """Merge a wrapped class name (e.g. 'Flour treatment' + 'agent') using the known-27
    vocabulary -- these columns have no reliable one-line-per-class boundary. Must run
    on the FULL, section-wide, top-ordered fragment stream, not per INS record: a
    fragment pair can straddle a record boundary, and bucketing by record first would
    split them into two different (invalid) records.
    """
    entries = []  # (gtop, class_name)
    buffer_top, buffer_text = None, ""
    for g, text in class_fragments:
        candidate = f"{buffer_text} {text}".strip() if buffer_text else text
        if candidate in known_classes:
            entries.append((buffer_top if buffer_top is not None else g, candidate))
            buffer_top, buffer_text = None, ""
        elif any(k.startswith(candidate) for k in known_classes):
            buffer_top = g if buffer_top is None else buffer_top
            buffer_text = candidate
        else:
            entries.append((g, f"{text} [UNRECOGNISED]"))
            buffer_top, buffer_text = None, ""
    if buffer_text:
        entries.append((buffer_top, f"{buffer_text} [INCOMPLETE]"))
    return entries


def parse_ins_list(pdf, g_start, g_end, known_classes):
    """Parse one numerical- or alphabetical-order INS block into raw (ins, name, classes, purposes) dicts."""
    cols = COLS_INS_LIST
    ins_lines = _cluster_lines(_zone_words(pdf, g_start, g_end, cols["ins"]))
    name_lines = _cluster_lines(_zone_words(pdf, g_start, g_end, cols["name"]))
    class_lines = _cluster_lines(_zone_words(pdf, g_start, g_end, cols["class"]))
    purpose_lines = _cluster_lines(_zone_words(pdf, g_start, g_end, cols["purpose"]))

    anchors, ins_numbers, warnings = [], [], []
    for line in ins_lines:
        text = _line_text(line)
        if INS_RE.match(text):
            anchors.append(line[0]["gtop"])
            ins_numbers.append(text)
        else:
            warnings.append(f"unrecognised text in INS column: {text!r}")

    names = [[] for _ in anchors]
    for line in name_lines:
        names[_nearest_index(anchors, line[0]["gtop"])].append((line[0]["gtop"], _line_text(line)))

    class_fragments = sorted((line[0]["gtop"], _line_text(line)) for line in class_lines)
    merged_classes = _merge_class_fragments(class_fragments, known_classes)
    classes_per_anchor = [[] for _ in anchors]
    for g, class_name in merged_classes:
        classes_per_anchor[_nearest_index(anchors, g)].append((g, class_name))

    purposes_per_anchor = [[] for _ in anchors]
    for line in purpose_lines:
        purposes_per_anchor[_nearest_index(anchors, line[0]["gtop"])].append(
            (line[0]["gtop"], _line_text(line))
        )

    records = []
    for i, ins in enumerate(ins_numbers):
        names[i].sort()
        name = " ".join(t for _, t in names[i])

        classes_per_anchor[i].sort()
        class_entries = classes_per_anchor[i]
        class_tops = [g for g, _ in class_entries]

        purposes_per_anchor[i].sort()
        # A purpose's class is whichever class-name line is NEAREST it -- purposes can
        # appear above OR below their own class's line (confirmed on 341(iii): the
        # first purpose of "Acidity regulator" prints one line ABOVE the class name).
        classes, purposes = [], []
        for g, purpose_text in purposes_per_anchor[i]:
            if class_tops:
                cls = class_entries[_nearest_index(class_tops, g)][1]
                if cls not in classes:
                    classes.append(cls)
            else:
                warnings.append(f"{ins}: purpose {purpose_text!r} has no functional class nearby")
            purposes.append(purpose_text)

        records.append({"ins": ins, "name": name, "functional_classes": classes, "purposes": purposes})
    return records, warnings


# =========================================================================== #
# post-processing
# =========================================================================== #
_TRAILING_SYNONYM_RE = re.compile(r"^(.*\S)\s*\(([^()]+)\)\s*$")
_PARENT_PAREN_RE = re.compile(r"^(.+)\(([ivxlc]+)\)$", re.IGNORECASE)
_PARENT_LETTER_RE = re.compile(r"^(\d+)([a-z])$", re.IGNORECASE)


def split_synonym(raw_name):
    """Split a trailing parenthetical into (name, synonyms).

    Only the FINAL trailing paren group at the very end of the string counts as a
    synonym; any earlier parens stay part of the name. This is exactly the rule
    stated in the standard's own footnote for INS 183 "Jagua (genipin-glycine) blue
    (Jagua blue)": the inner paren is part of the name, the outer one is the synonym.
    A comma-introduced description ("Zeaxanthin, synthetic") is untouched -- it isn't
    parenthesized, so the regex below never matches it.
    """
    stripped = raw_name.rstrip("*").strip()
    m = _TRAILING_SYNONYM_RE.match(stripped)
    if m:
        return m.group(1).strip(), [m.group(2).strip()]
    return stripped, []


def derive_parent_ins(ins):
    """The next INS level up, or None if `ins` is already a bare top-level number.

    Strips exactly one level: "160a(i)" -> "160a" (not "160" -- "160a" is itself a
    real sub-type), "470(i)" -> "470", "150a" -> "150".
    """
    m = _PARENT_PAREN_RE.match(ins)
    if m:
        return m.group(1)
    m = _PARENT_LETTER_RE.match(ins)
    if m:
        return m.group(1)
    return None


def finalise_ins_record(raw):
    name, synonyms = split_synonym(raw["name"])
    return {
        "ins": raw["ins"],
        "parent_ins": derive_parent_ins(raw["ins"]),
        "name": name,
        "synonyms": synonyms,
        "functional_classes": raw["functional_classes"],
        "purposes": raw["purposes"],
        "is_parent_row": len(raw["functional_classes"]) == 0,
    }


# =========================================================================== #
# Section 3 vs Section 4 cross-check
# =========================================================================== #
def cross_check(section3_records, section4_records):
    """Section 4 is the same data in alphabetical order. Agreement between the two
    independent parses is the detector for column-misalignment and bad row grouping
    -- if a record were built from the wrong physical lines, it would very likely
    disagree with the other section's independent parse of the same additive.
    """
    by_ins_3 = Counter(r["ins"] for r in section3_records)
    by_ins_4 = Counter(r["ins"] for r in section4_records)
    names_3 = {r["ins"]: r["name"] for r in section3_records}
    names_4 = {r["ins"]: r["name"] for r in section4_records}

    only_in_3 = sorted(set(by_ins_3) - set(by_ins_4))
    only_in_4 = sorted(set(by_ins_4) - set(by_ins_3))
    duplicates_3 = sorted(ins for ins, n in by_ins_3.items() if n > 1)
    duplicates_4 = sorted(ins for ins, n in by_ins_4.items() if n > 1)
    name_mismatches = sorted(
        (ins, names_3[ins], names_4[ins])
        for ins in set(names_3) & set(names_4)
        if names_3[ins].strip() != names_4[ins].strip()
    )
    return {
        "section3_count": len(section3_records),
        "section4_count": len(section4_records),
        "only_in_section3": only_in_3,
        "only_in_section4": only_in_4,
        "duplicate_ins_in_section3": duplicates_3,
        "duplicate_ins_in_section4": duplicates_4,
        "name_mismatches": name_mismatches,
    }


# =========================================================================== #
# main
# =========================================================================== #
def main() -> None:
    with pdfplumber.open(PDF_PATH) as pdf:
        bounds = find_section_boundaries(pdf)

        functional_classes = parse_functional_classes(pdf, *bounds["section2"])
        known_classes = {c["class"] for c in functional_classes}

        section3_main, warnings_3main = parse_ins_list(pdf, *bounds["section3_main"], known_classes)
        section3_supp, warnings_3supp = parse_ins_list(
            pdf, *bounds["section3_supplementary"], known_classes
        )
        section3_raw = section3_main + section3_supp

        # Section 4 is used only for the cross-check below; its own parse warnings
        # aren't separately reported (Section 3 is the actual output source).
        section4_main, _warnings_4main = parse_ins_list(pdf, *bounds["section4_main"], known_classes)
        section4_supp, _warnings_4supp = parse_ins_list(
            pdf, *bounds["section4_supplementary"], known_classes
        )
        section4_raw = section4_main + section4_supp

    section3_records = [finalise_ins_record(r) for r in section3_raw]

    validation_failures = [
        (r["ins"], c)
        for r in section3_records
        for c in r["functional_classes"]
        if c not in known_classes
    ]

    check = cross_check(section3_raw, section4_raw)

    FUNCTIONAL_CLASSES_PATH.parent.mkdir(parents=True, exist_ok=True)
    FUNCTIONAL_CLASSES_PATH.write_text(
        json.dumps(functional_classes, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    CODEX_INS_PATH.write_text(
        json.dumps(section3_records, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    with_synonyms = sum(1 for r in section3_records if r["synonyms"])
    multi_class = sum(1 for r in section3_records if len(r["functional_classes"]) > 1)
    parent_rows = sum(1 for r in section3_records if r["is_parent_row"])
    supplementary_count = len(section3_supp)

    print("=" * 66)
    print("STEP 1 -- functional classes (Section 2)")
    print("=" * 66)
    print(f"  classes parsed .............. {len(functional_classes)}   <- must be 27")
    print(f"  saved -> {FUNCTIONAL_CLASSES_PATH.resolve()}")

    print("\n" + "=" * 66)
    print("STEP 2 -- INS list (Section 3, numerical order)")
    print("=" * 66)
    print(f"  total records ................ {len(section3_records)}")
    print(f"  parent rows (no own class) ... {parent_rows}")
    print(f"  records with synonyms ........ {with_synonyms}")
    print(f"  records with >1 func. class .. {multi_class}")
    print(f"  supplementary-list records ... {supplementary_count}")
    print(f"  parse warnings ................ {len(warnings_3main) + len(warnings_3supp)}")
    for w in warnings_3main + warnings_3supp:
        print(f"     {w}")
    print(f"  validation failures (class not in the 27) .. {len(validation_failures)}   <- must be 0")
    for ins, bad_class in validation_failures:
        print(f"     {ins}: {bad_class!r}")
    print(f"  saved -> {CODEX_INS_PATH.resolve()}")

    print("\n" + "=" * 66)
    print("STEP 3 -- cross-check against Section 4 (alphabetical order)")
    print("=" * 66)
    print(f"  Section 3 records ............ {check['section3_count']}")
    print(f"  Section 4 records ............ {check['section4_count']}   "
          f"<- should match Section 3 (mismatch = bad row grouping)")
    print(f"  only in Section 3 ............ {len(check['only_in_section3'])}: {check['only_in_section3']}")
    print(f"  only in Section 4 ............ {len(check['only_in_section4'])}: {check['only_in_section4']}")
    print(f"  duplicate INS in Section 3 .... {len(check['duplicate_ins_in_section3'])}: "
          f"{check['duplicate_ins_in_section3']}")
    print(f"  duplicate INS in Section 4 .... {len(check['duplicate_ins_in_section4'])}: "
          f"{check['duplicate_ins_in_section4']}")
    print(f"  name mismatches ............... {len(check['name_mismatches'])}")
    print("     (many of these are the source document itself using Title Case for a")
    print("      synonym in Section 3 but lower case in Section 4 -- not a parse bug.")
    print("      Read each one; codex_ins.json is built from Section 3 only.)")
    for ins, n3, n4 in check["name_mismatches"]:
        print(f"     {ins}: {n3!r}  vs  {n4!r}")


if __name__ == "__main__":
    main()
