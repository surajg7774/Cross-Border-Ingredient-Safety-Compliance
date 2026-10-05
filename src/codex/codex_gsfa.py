"""
codex_gsfa.py — Adapter 1: Codex GSFA (General Standard for Food Additives)
===========================================================================

ROLE IN THE SYSTEM
------------------
Codex is the NORMALIZATION BACKBONE. Other sources key off a name (Canada), an
E-number (EU, UK) or a CAS number (US). Codex is where all of those resolve to a
single canonical INS number, so five markets can be lined up side by side.

Codex also carries reference max levels, so it doubles as the CODEX jurisdiction
source. It is reference ONLY — never a market verdict, and no Codex column in
the report matrix.

WHAT WE FETCH, AND WHY
----------------------
1. THE ADDITIVE INDEX  ->  the name -> INS dictionary
   https://www.fao.org/gsfaonline/additives/index.html?lang=en
   A flat list of links. The INS is the number in the LINK TEXT — "Citric acid
   (330)" — NOT the id in the URL, which is FAO's internal record number. Two
   different numbers on the same line; picking the wrong one poisons everything
   downstream, silently.

2. THE SAME PAGE + ?showSynonyms=1  ->  alternative names
   Synonyms carry a trailing '*' and point at the SAME id as their official
   name, so the id is the glue. A food label says "acacia gum", never
   "Gum arabic (Acacia gum)".

3. EACH ADDITIVE'S DETAIL PAGE  ->  max levels per food category
   https://www.fao.org/gsfaonline/additives/details.html?id=<id>
   One <table id="allowances">: Number | Food Category | Max Level | Notes.
   Only ADOPTED provisions appear online, so there is no year column to filter.

4. EACH GROUP PAGE  ->  members, and rules that exist nowhere else
   https://www.fao.org/gsfaonline/groups/details.html?id=<id>
   Two tables: <table id="additives"> (the members) and <table id="allowances">
   (the group's provisions, identical markup to an additive page).

THE FIVE BUGS THIS FILE FIXES
-----------------------------
Each was found by checking an additive whose answer was already known. None was
found by a crash. That is the project's core verification method.

  1. NOTE SENTENCES DISCARDED. The old parser flattened the page to text and
     collected only "Note 634". The sentence is not in the visible text at all —
     it lives in the `title` attribute of the note icon (the hover tooltip).
     Result: 2,929 rows held note numbers and ZERO note text.
     Concretely: adipic acid (355) was recorded as permitted at 1,500 mg/kg in
     ALL dairy-based desserts. Note 634 restricts it to products conforming to
     the Fermented Milks standard. A pudding is a dairy dessert; it is not a
     fermented milk product. A confident wrong answer, with no crash.

  2. A FALSE ASSUMPTION ABOUT THE PAGE. The old parser's own docstring claimed
     the page had "no clean per-row <tr>". It does: <table id="allowances">.
     Reading the table directly is simpler, safer, and picks up the notes.

  3. GROUP MEMBERS MISSING ENTIRELY. Members are NOT on the additive index.
     Verified against the saved backbone: benzoic acid (210), calcium/potassium/
     sodium benzoate (213/212/211), the tocopherols, the polysorbates, the
     cyclamates, the tartrates and the iron oxides were absent — not present
     with zero rules, simply missing. Some of the most widely used preservatives
     in the world could not be resolved at all. Same shape as EU bug #3.

  4. COMBINED LIMITS NOT CARRIED. Every group page states plainly: "The
     provisions that follow are defined at the additive group level, and thus
     apply to the TOTAL CONTENT of the additives participating in this group."
     A 1,500 mg/kg chewing-gum limit is a budget SHARED by four benzoates, not
     1,500 each. Copying the number without the sentence would authorise four
     times the legal amount. Every inherited row now carries the warning and the
     member list.

  5. DEAD CITATIONS. Every saved source_url had its path written twice
     (.../additives/gsfaonline/additives/details.html?id=434) and 404'd.
     source_url IS the citation — the whole argument for why this beats asking a
     language model. A link that looks right and goes nowhere is worse than no
     link. URLs are now always rebuilt from the id, and inherited rows cite the
     GROUP page, because that is where the rule is actually written.

COUNTS, MEASURED ON THE REAL INDEX PAGE
---------------------------------------
  318 index records | 33 GROUP headings | 285 crawlable additives
  63 ids containing brackets or letters (503(i), 160b(i), 307a)

  Earlier notes recorded 296 / 16 / 280. The index carries 33 group headings,
  not 16. The seventeen that were never expanded include SULFITES, NITRATES,
  NITRITES, SORBATES, PHOSPHATES, SACCHARINS, SORBIC-acid relatives and
  STEVIOL GLYCOSIDES. Their members are NOT listed on the index under their own
  names — checked directly: sulfur dioxide (220), sodium metabisulfite (223),
  sodium nitrite (250), sorbic acid (200) and potassium sorbate (202) are all
  absent. Sulfites must be declared on a label in most markets. Group
  expansion is what recovers them, and it is keyed off the URL path
  (groups/details.html), not off the name being in capitals, so it finds all 33
  without anyone having to list them.

VERIFIED AGAINST REAL SAVED PAGES
---------------------------------
  Adipic acid (id=174) ... 3 rows, 3 with note sentences
  Tartrazine  (id=86) .... 58 rows; vs the old crawl: same 58 categories, same
                           levels, same 63 note codes, none lost, none invented
                           — plus 31 rows of note text the old version dropped
  BENZOATES   (id=162) ... 4 members, 62 provisions, 248 inherited rows, every
                           row carrying the combined-limit warning
  TOCOPHEROLS (id=2) ..... 3 members (307a, 307b, 307c), 67 provisions — the
                           second group page ever inspected; identical markup
  Benzoic acid(id=231) ... the page EXISTS and carries its functional class,
                           but has NO provisions table. So a group member does
                           have a page of its own, and the group page is still
                           the only place its rules are written.

KNOWN GAPS (documented, not hidden)
-----------------------------------
  * functional_class arrives glued: "Flavour enhancer Sweetener". FAO prints
    multiple classes with no separator. FIXED — split_functional_classes() now
    emits a validated list in `functional_classes`, checked against the 27
    official Codex CXG 36-1989 class names. `functional_class` keeps the raw
    string for traceability. JOIN ON functional_classes, never on the raw field.
  * effective_date is always None. The online table lists adopted provisions
    with no adoption year, so there is no date to capture.
  * retrieved_date and content_hash are deliberately absent — they belong to the
    shared pipeline stage, written once for all five sources.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger("adapter.codex")

# =========================================================================== #
# CONSTANTS
# =========================================================================== #
BASE = "https://www.fao.org/gsfaonline/"
INDEX_URL = urljoin(BASE, "additives/index.html?lang=en")
SYNONYM_URL = urljoin(BASE, "additives/index.html?lang=en&showSynonyms=1")
# ?lang=en is MANDATORY on GSFA pages — they error without it.

# A descriptive User-Agent is basic good manners toward a public regulator.
HEADERS = {
    "User-Agent": (
        "CrossBorderComplianceBot/1.0 (PGCP-AI capstone; research use; "
        "contact: you@example.com)"
    )
}

REQUEST_DELAY_SECONDS = 1.0   # gap between requests
RETRY_ATTEMPTS = 4            # FAO returned 503 mid-project and recovered
RETRY_BASE_SECONDS = 2.0      # backoff grows: 2s, 4s, 6s

# The INS sits in the LAST bracket group of the link text and may carry
# sub-identifiers. canonical_id is TEXT — never int() it and never sort it
# numerically, or 503(i) and 160b(i) will crash or silently reorder.
#   "Citric acid (330)"                       -> "330"
#   "Ammonium carbonate (503(i))"             -> "503(i)"
#   "Annatto extracts, bixin-based (160b(i))" -> "160b(i)"
_INS_IN_NAME = re.compile(r"\(([0-9][0-9a-z()]*)\)\s*$", re.IGNORECASE)

# A food-category code: 01.7, 03.0, 14.2.7 — two digits, then optional .N groups.
_CAT_CODE = re.compile(r"\d{2}(?:\.\d+)*$")

# The tree icon beside each category also carries a title attribute. It is
# navigation furniture, not a note, and must not be mistaken for note text.
_NOT_NOTE_TITLES = {"food category hierarchy", "jecfa lookup"}

# The combined-limit warning printed at the top of every group page.
_GROUP_NOTICE = re.compile(
    r"(The provisions that follow are defined at the additive group level[^.]*\.)"
)

# The clean group name lives in a styled banner. Do NOT scrape it out of the
# flattened text: the phrase "Food Additive Group Details" appears three times on
# the page (browser title, banner, heading) and a text pattern grabs the wrong
# one. The first attempt produced a 90-character via_group value containing
# "48 th Session of the Codex Alimentarius Commission (2025)". Caught in testing.
_GROUP_TITLE_CLASS = "gsfaBigTitle"



# --------------------------------------------------------------------------- #
# FUNCTIONAL CLASSES
# --------------------------------------------------------------------------- #
# FAO prints multiple functional classes glued together with no separator:
#     "Flavour enhancer Sweetener"
#     "Emulsifier Stabilizer Thickener"
#     "Acidity regulator Antioxidant Colour retention agent Sequestrant"
# so the field cannot be joined on, filtered by, or counted as-is.
#
# HOW THE SPLIT WORKS: each class name begins with a capital and continues in
# lowercase ("Colour retention agent"), and FAO lists them alphabetically. So a
# new class starts at every capitalised word. Splitting on that boundary turned
# the 117 distinct glued strings in the live crawl into exactly these 27 tokens
# and nothing else — which is precisely the official list from Codex CXG 36-1989
# (Class Names and the International Numbering System for Food Additives).
# 117 messy strings decomposing into exactly the official 27, with zero
# leftovers, is the evidence that the split is right.
FUNCTIONAL_CLASSES = frozenset({
    "Acidity regulator", "Anticaking agent", "Antifoaming agent", "Antioxidant",
    "Bleaching agent", "Bulking agent", "Carbonating agent", "Carrier",
    "Colour", "Colour retention agent", "Emulsifier", "Emulsifying salt",
    "Firming agent", "Flavour enhancer", "Flour treatment agent",
    "Foaming agent", "Gelling agent", "Glazing agent", "Humectant",
    "Packaging gas", "Preservative", "Propellant", "Raising agent",
    "Sequestrant", "Stabilizer", "Sweetener", "Thickener",
})

# split at whitespace followed by a capital letter
_CLASS_BOUNDARY = re.compile(r"\s+(?=[A-Z])")

# The longest official class name is three words ("Colour retention agent",
# "Flour treatment agent"), so a longest-first walk never needs to look further.
_MAX_CLASS_WORDS = max(len(c.split()) for c in FUNCTIONAL_CLASSES)


def _consume_known_classes(words: list[str]) -> tuple[list[str], int]:
    """
    Walk a word list left to right, taking the LONGEST official class name that
    fits at each position. Returns (classes found, how many words were used).

    Longest-first matters: "Colour" and "Colour retention agent" are both real
    class names, so a shortest-first walk would stop at "Colour" and leave
    "retention agent" as rubbish.

    This is the second line of defence behind the capital-letter split, and it
    is also how extract_functional_class() knows where the class list ENDS on a
    page — it stops at the first word that cannot begin an official class.
    """
    found: list[str] = []
    i = 0
    while i < len(words):
        hit = None
        for n in range(min(_MAX_CLASS_WORDS, len(words) - i), 0, -1):
            candidate = " ".join(words[i:i + n]).strip(" ,;.")
            if candidate in FUNCTIONAL_CLASSES:
                hit = (candidate, n)
                break
        if hit is None:
            break
        found.append(hit[0])
        i += hit[1]
    return found, i


def split_functional_classes(raw: Optional[str]) -> list[str]:
    """
    'Flavour enhancer Sweetener' -> ['Flavour enhancer', 'Sweetener']

    VALIDATED, not guessed. Every piece is checked against the official Codex
    vocabulary above. If ANY piece is unrecognised the split is rejected and the
    original string is returned untouched, with a warning.

    That behaviour is deliberate. A wrong split would quietly relabel an
    additive's purpose — and the substitute advisor picks replacements by
    matching functional class, so a bad label there could recommend a thickener
    in place of a preservative. Better to keep an awkward string we know is
    faithful than a tidy list we cannot vouch for. An unrecognised piece means
    Codex added a class name; add it to FUNCTIONAL_CLASSES rather than loosening
    the check.
    """
    if not raw:
        return []
    raw = raw.strip()
    parts = [p.strip() for p in _CLASS_BOUNDARY.split(raw) if p.strip()]

    unknown = [p for p in parts if p not in FUNCTIONAL_CLASSES]
    if not unknown:
        return parts

    # SECOND ATTEMPT — the capital-letter rule failed. That happens when FAO
    # runs the classes together without a space ("PreservativeAntioxidant") or
    # separates them with a comma or slash instead. Try matching the official
    # vocabulary directly. This is still validation, not guessing: every piece
    # must be an official class name, and any leftover word rejects the whole
    # attempt exactly as before.
    words = re.split(r"[\s,;/]+", re.sub(r"(?<=[a-z])(?=[A-Z])", " ", raw))
    words = [w for w in words if w]
    found, used = _consume_known_classes(words)
    if found and used == len(words):
        return found

    log.warning(
        "unrecognised functional class %s in %r — keeping the raw string. "
        "If Codex has added a class, add it to FUNCTIONAL_CLASSES.",
        unknown, raw,
    )
    return [raw]


# =========================================================================== #
# THE SHARED RECORD SHAPE
# =========================================================================== #
@dataclass
class AdditiveRecord:
    """
    One record = ONE additive, in ONE food, in ONE market.

    NOT one additive. That is why ~285 additives become several thousand
    records:
    tartrazine alone has 58, because Codex sets a different ceiling in 58
    different foods.
    """
    # --- who is it? ---
    canonical_id: str                        # INS as TEXT, or "UNMAPPED:<name>"
    additive_name: str
    jurisdiction: str = "CODEX"
    synonyms: list[str] = field(default_factory=list)
    functional_class: Optional[str] = None   # RAW, exactly as FAO printed it
    functional_classes: list[str] = field(default_factory=list)  # split + validated
    is_group: bool = False                   # a heading, never a verdict key

    # --- where does the rule apply? ---
    food_category: Optional[str] = None      # "Chewing gum"
    food_category_raw: Optional[str] = None  # "05.3 Chewing gum" — the proof
    food_category_id: Optional[str] = None   # FAO's own id — the crosswalk key

    # --- what does it say? ---
    status: str = "permitted"                # online = adopted = permitted
    max_level_mg_kg: Optional[float] = None
    max_level_basis: Optional[str] = None    # mg/kg | mg/l | percent | gmp | conditional
    conditions: Optional[str] = None         # note SENTENCES + codes, joined
    note_codes: list[str] = field(default_factory=list)   # ['634', '1']

    # --- how do we prove it? ---
    source_name: str = "Codex GSFA"
    source_url: Optional[str] = None         # THE CITATION
    effective_date: Optional[str] = None     # always None for Codex — see GAPS

    # --- group provenance ---
    via_group: Optional[str] = None          # set when inherited from a group


# =========================================================================== #
# SMALL HELPERS
# =========================================================================== #
def _extract_id(href: str) -> Optional[str]:
    m = re.search(r"id=(\d+)", href or "")
    return m.group(1) if m else None


def detail_url(detail_id: str, is_group: bool = False) -> str:
    """
    Build a GSFA detail URL from a record id.

    ALWAYS reconstructed from the id, never appended to whatever the page wrote,
    so no path doubling can occur however the source writes its hrefs.
    """
    path = "groups/details.html" if is_group else "additives/details.html"
    return f"https://www.fao.org/gsfaonline/{path}?id={detail_id}"


def _split_name_and_ins(text: str) -> tuple[str, Optional[str]]:
    """'Citric acid (330)' -> ('Citric acid', '330'). Group names have no INS."""
    m = _INS_IN_NAME.search(text)
    if not m:
        return text.strip(), None
    return text[: m.start()].strip(), m.group(1)


def _parse_level(raw: str) -> tuple[Optional[float], Optional[str]]:
    """
    Turn a Max Level cell into (value, basis).

        'GMP'         -> (None, 'gmp')
        '1,500 mg/kg' -> (1500.0, 'mg/kg')
        '200 mg/l'    -> (200.0, 'mg/l')
        unparseable   -> (None, 'conditional')

    GMP MUST return None, never 0. Zero reads as "banned" downstream; None with
    basis 'gmp' reads as "permitted, no numeric ceiling — use only what is
    needed". Same idea as the EU's quantum satis (QS).
    """
    raw = (raw or "").strip()
    if not raw:
        return None, None
    if raw.upper() in {"GMP", "BPF"}:
        return None, "gmp"

    m = re.search(r"([\d,]+(?:\.\d+)?)", raw)
    if not m:
        return None, "conditional"
    value = float(m.group(1).replace(",", ""))

    low = raw.lower()
    if "mg/l" in low:
        return value, "mg/l"
    if "%" in raw or "percent" in low:
        return value, "percent"
    return value, "mg/kg"


def _flat_text(soup: BeautifulSoup) -> str:
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


_FCLASS_LABEL = re.compile(r"^functional\s+class(?:es)?\s*:?\s*$", re.IGNORECASE)
_FCLASS_INLINE = re.compile(r"Functional\s+Class(?:es)?\s*:?\s*", re.IGNORECASE)
_SYNONYM_LABEL = re.compile(r"^synonym\(?s?\)?\s*:?\s*$", re.IGNORECASE)


def _labelled_list_items(html_or_soup, label: re.Pattern) -> list[str]:
    """
    Find a bold label and return the items of the list that follows it.

    VERIFIED against the real saved pages. A GSFA detail page is built like this:

        <div class="bodycopybold">Functional Classes</div>
        <ul class="gsfaListTight">
          <li>Acidity regulator</li>
        </ul>

    The label must be matched first and the list taken from beside it. The same
    markup is used for a DIFFERENT field higher up the page:

        <div class="bodycopybold">Synonym(s)</div>
        <ul class="gsfaListTight">
          <li>CI (1975) No. 19140</li> <li>CI Food Yellow 4</li> <li>FD&C Yellow No. 5</li>
        </ul>

    On the real tartrazine page the synonym list comes FIRST. Taking "the
    gsfaListTight list" without checking its label would have recorded
    tartrazine's functional class as "FD&C Yellow No. 5". Anchor on the label.
    """
    soup = (html_or_soup if isinstance(html_or_soup, BeautifulSoup)
            else BeautifulSoup(html_or_soup, "lxml"))

    for tag in soup.find_all(["div", "p", "span", "b", "strong", "td", "th", "dt"]):
        if not label.match(tag.get_text(" ", strip=True)):
            continue
        lst = tag.find_next_sibling(["ul", "ol"])
        if lst:
            items = [li.get_text(" ", strip=True) for li in lst.find_all("li")]
            items = [re.sub(r"\s+", " ", i).strip(" ,;") for i in items if i.strip()]
            if items:
                return items
    return []


def extract_synonyms(html) -> list[str]:
    """
    Synonyms listed on the additive's OWN page.

    A bonus found while checking the real pages. The index-with-synonyms view
    gives starred alternative names; the detail page carries its own list, and
    for tartrazine it is richer — the Colour Index numbers and 'FD&C Yellow
    No. 5', which is what a US label actually prints. Harmless if empty.
    """
    return _labelled_list_items(html, _SYNONYM_LABEL)


def extract_functional_classes(html) -> tuple[Optional[str], list[str]]:
    """
    Return (raw string as FAO presented it, validated list of classes).

    Prefers the real <li> structure, which needs no splitting at all: FAO puts
    ONE class per list item. The "glued together" strings that fault 7 was about
    — 'Flavour enhancer Sweetener' — were produced by flattening the page to
    text, which welds the list items into one line. Read the list and the
    problem does not arise. split_functional_classes() is kept for the text
    fallback and for re-processing older data.

    Group pages carry no functional class at all, so ([], None) is a legitimate
    answer there, not a failure. run_codex.py STEP 4 backfills those from each
    member's own detail page.
    """
    items = _labelled_list_items(html, _FCLASS_LABEL)
    # VALIDATE BEFORE TRUSTING. The page footer carries a "Functional Classes
    # Glossary" link, and an earlier version of this function accepted whatever
    # sat beside the first thing matching the label — which on a group page
    # returned ['Glossary © FAO and WHO 2026']. A copyright line recorded as an
    # additive's job. Caught by running it against the real pages, which is the
    # only reason it is not in the data now.
    #
    # The official 27 are the guard: if not one item is a real Codex class, this
    # is not the functional class section and the answer is discarded.
    if items and any(i in FUNCTIONAL_CLASSES for i in items):
        unknown = [i for i in items if i not in FUNCTIONAL_CLASSES]
        if unknown:
            log.warning("functional class not in the official Codex 27: %s", unknown)
        return " ".join(items), items

    raw = extract_functional_class(html)
    return raw, split_functional_classes(raw)


def extract_functional_class(html: str) -> Optional[str]:
    """
    The RAW 'Functional Class(es)' value, as a single string.

    Kept because the record schema stores a raw string alongside the split list,
    and because it is the text fallback for extract_functional_classes(). Reads
    the list first, then falls back to flattened text bounded by the official
    vocabulary.

    WHY THE TEXT PATH IS BOUNDED BY VOCABULARY
    ------------------------------------------
    The first version took everything after the label up to whichever of a few
    hard-coded words came next — Click, GSFA, Food Category and so on. On the
    pages FAO serves today that works, because every detail page happens to end
    the section with "Click here to search the FAO JECFA database". It is
    working code, not broken code. But it is answered by a neighbouring
    sentence rather than by the field itself, and if that link ever moves the
    function returns None with no warning. Bounding the read by the official
    class list removes the dependency: the value ends where the class names
    stop.
    """
    items = _labelled_list_items(html, _FCLASS_LABEL)
    if items and any(i in FUNCTIONAL_CLASSES for i in items):
        return " ".join(items)

    soup = BeautifulSoup(html, "lxml")
    flat = _flat_text(soup)
    m = _FCLASS_INLINE.search(flat)
    if m:
        tail = flat[m.end():]
        words = [w for w in re.split(r"\s+", tail) if w]
        _, used = _consume_known_classes(words)
        if used:
            return " ".join(words[:used]).strip(" ,;:")
        # NOTHING RECOGNISED — return None, never a guess.
        #
        # An earlier version handed back the next six words so an unknown class
        # would still be visible. On a group page, where there is no functional
        # class section at all, the nearest match is the footer's "Functional
        # Classes Glossary" link, and those six words are "Glossary © FAO and
        # WHO 2026". That went into the data as an additive's job.
        #
        # A wrong functional class feeds the substitute advisor, which picks
        # replacements by matching this field. Empty sends an additive to manual
        # review; wrong recommends a thickener in place of a preservative. So
        # the field is left empty and the run reports how many rows are missing
        # one — see "rows with NO class at all" in the quality report.
        log.warning("no recognisable functional class after the label; nearest "
                    "text was %r — leaving the field empty",
                    " ".join(words[:6])[:60])
    return None


# =========================================================================== #
# PARSER 1 — THE ADDITIVE INDEX AND SYNONYMS
# =========================================================================== #
def parse_index(
    html: str,
    synonyms_by_id: Optional[dict[str, list[str]]] = None,
) -> list[AdditiveRecord]:
    """
    Turn the additive index into AdditiveRecords.

    We keep only anchors pointing at a detail page. That is far more stable than
    "the list is in the third table" — if FAO redesigns its menu tomorrow, this
    still works, because a real additive is defined by where it links to.
    """
    soup = BeautifulSoup(html, "lxml")
    records: list[AdditiveRecord] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "details.html?id=" not in href:
            continue                                   # nav, jump bar, print link

        detail_id = _extract_id(href)
        if detail_id is None:
            continue

        is_group = "groups/details.html" in href       # UPPERCASE entries
        key = f"{'g' if is_group else 'a'}:{detail_id}"   # separate id spaces
        if key in seen:
            continue
        seen.add(key)

        name, ins = _split_name_and_ins(a.get_text(" ", strip=True))
        records.append(
            AdditiveRecord(
                canonical_id=ins or f"UNMAPPED:{name}",
                additive_name=name,
                synonyms=(synonyms_by_id or {}).get(detail_id, []),
                source_url=detail_url(detail_id, is_group),
                is_group=is_group,
            )
        )

    log.info("index parsed: %d records (%d group headings)",
             len(records), sum(1 for r in records if r.is_group))
    return records


def parse_synonyms(html: str) -> dict[str, list[str]]:
    """
    Map detail-id -> [synonyms] from the ?showSynonyms=1 view.

    A synonym carries a trailing '*' and points at the SAME id as its official
    name, so the shared id tells us which additive it belongs to:
        <a href="details.html?id=63">Acacia Gum* (414)</a>              synonym
        <a href="details.html?id=63">Gum arabic (Acacia gum) (414)</a>  official
    """
    soup = BeautifulSoup(html, "lxml")
    result: dict[str, list[str]] = {}

    for a in soup.find_all("a", href=True):
        if "details.html?id=" not in a["href"]:
            continue
        did = _extract_id(a["href"])
        if not did:
            continue

        name, _ = _split_name_and_ins(a.get_text(" ", strip=True))
        name = name.rstrip()
        if not name.endswith("*"):
            continue                                   # official name, not a synonym

        synonym = name.rstrip("*").rstrip(", ").strip()
        if synonym:
            result.setdefault(did, [])
            if synonym not in result[did]:
                result[did].append(synonym)
    return result


# =========================================================================== #
# PARSER 2 — THE PROVISIONS TABLE (used by BOTH additive and group pages)
# =========================================================================== #
def parse_notes_cell(cell) -> tuple[list[str], list[str]]:
    """
    Read one Notes cell into two lists that line up position by position:

        codes = ['634', '1']
        texts = ['For use in products conforming to ... only.', 'As adipic acid.']

    Each note sits in its own <div>:
        <img title="...the sentence..."/>              <- the MEANING (tooltip)
        <a href=".../notes.html?id=634">Note 634</a>   <- the CITATION

    Both are kept. Entries are padded rather than filtered, so index i in one
    list always matches index i in the other.
    """
    codes: list[str] = []
    texts: list[str] = []

    blocks = cell.find_all("div") or [cell]

    for div in blocks:
        code = None
        for a in div.find_all("a", href=True):
            m = re.search(r"Note\s+([A-Za-z0-9]+)", a.get_text(" ", strip=True))
            if m:
                code = m.group(1)
                break
        if code is None:
            m = re.search(r"Note\s+([A-Za-z0-9]+)", div.get_text(" ", strip=True))
            code = m.group(1) if m else None

        text = None
        for img in div.find_all("img"):
            t = (img.get("title") or img.get("alt") or "").strip()
            if t and t.lower() not in _NOT_NOTE_TITLES:
                text = re.sub(r"\s+", " ", t)
                break

        if code or text:
            codes.append(code or "")
            texts.append(text or "")

    return codes, texts


def join_conditions(codes: list[str], texts: list[str]) -> Optional[str]:
    """
    Build the fine print stored in `conditions`:

        'For use in products conforming ... only. [Note 634] | As adipic acid. [Note 1]'

    Returns None (not "") when there is no fine print, so the future hash-diff
    stage sees one consistent "absent" value.
    """
    parts = []
    for code, text in zip(codes, texts):
        if text and code:
            parts.append(f"{text} [Note {code}]")
        elif text:
            parts.append(text)
        elif code:
            parts.append(f"[Note {code}]")
    return " | ".join(parts) if parts else None


def parse_provisions_table(html: str) -> list[dict]:
    """
    Read every provision row from <table id="allowances">.

    Row shape, verified on real pages:
        td[0] spacer | td[1] tree icon | td[2] 01.7 | td[3] category name
        td[4] class="allowanceMaxLevel" | td[5] class="allowanceComments" | td[6] spacer

    Returns plain dicts, so this also works on a GROUP page — identical markup.
    An empty list means "no provisions table", which is legitimate: some
    additives genuinely have no adopted provisions.
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="allowances")
    if table is None:
        return []

    body = table.find("tbody") or table
    rows: list[dict] = []

    for tr in body.find_all("tr", recursive=False):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 6:
            continue

        cat_num = tds[2].get_text(" ", strip=True)
        # GUARD: the number cell must look like 01.7. If FAO ever reorders the
        # columns, this fails loudly instead of silently writing category names
        # into the max-level field.
        if not _CAT_CODE.fullmatch(cat_num):
            continue

        cat_name = tds[3].get_text(" ", strip=True)

        # Prefer the NAMED cells over their positions. FAO tags the two cells
        # that matter (class="allowanceMaxLevel" and class="allowanceComments"),
        # and a name survives a column being added or reordered where td[4] and
        # td[5] would quietly start reading the wrong thing. Positions stay as
        # the fallback, so pages without the classes still parse.
        level_cell = tr.find("td", class_="allowanceMaxLevel") or tds[4]
        notes_cell = tr.find("td", class_="allowanceComments") or tds[5]

        value, basis = _parse_level(level_cell.get_text(" ", strip=True))
        codes, texts = parse_notes_cell(notes_cell)

        link = tds[2].find("a", href=True) or tds[3].find("a", href=True)
        cat_id = _extract_id(link["href"]) if link else None

        rows.append({
            "food_category": cat_name,
            "food_category_raw": f"{cat_num} {cat_name}",
            "food_category_id": cat_id,
            "max_level_mg_kg": value,
            "max_level_basis": basis,
            "conditions": join_conditions(codes, texts),
            "note_codes": [c for c in codes if c],
        })

    return rows


# =========================================================================== #
# PARSER 2b — GSFA TABLE 3 (a SECOND, separate permission list)
# =========================================================================== #
# Found on the real Curdlan page. A detail page can carry TWO provision tables:
#
#   <table id="allowances">  Tables 1 and 2 — "this additive, in this food, at
#                            this maximum level". Curdlan has ONE row here.
#   <table id="categories">  Table 3 — "this additive may be used under GMP in
#                            all of these foods". Curdlan has SIXTY-ONE rows.
#
# Table 3 of the GSFA lists additives accepted for use under Good Manufacturing
# Practice with no numerical limit. For an additive in Table 3, the `allowances`
# table is the small part of the picture and `categories` is the large part.
#
# Reading only `allowances` therefore reports curdlan as having one permitted
# food. It has sixty-two. The other sixty-one would come back as "not allowed",
# which under the project's own decision rules is a verdict, not a gap — a
# confident wrong answer of exactly the kind this pipeline exists to prevent.
#
# The two tables are NOT the same shape. `categories` has no max-level cell and
# no notes cell, so parse_provisions_table() cannot read it and would not want
# to: the level is not missing, it is GMP by definition.
_TABLE3_HEADING = re.compile(r"GSFA\s+Table\s+3\s+Provisions", re.IGNORECASE)
_TABLE3_SENTENCE = re.compile(
    r"GSFA\s+Table\s+3\s+Provisions\s+(.*?excluded accordingly\.)",
    re.IGNORECASE | re.DOTALL,
)
_TABLE3_EXTRA = re.compile(r"(Although not listed below.*?\.)",
                           re.IGNORECASE | re.DOTALL)
# FAO template variables that failed to render, e.g. "(additive.commodityStandardsA)".
# Seen on the live Curdlan page. Where these appear, the sentence they belong to
# is missing its content on FAO's side — worth reporting, never worth storing.
_UNRENDERED = re.compile(r"\(additive\.[A-Za-z]+\)")


def is_table3_page(html: str) -> bool:
    """
    True only if the page declares the 'GSFA Table 3 Provisions' section.

    The declaration, not the table id, is what authorises writing a GMP
    permission into the data.
    """
    return bool(_TABLE3_HEADING.search(_flat_text(BeautifulSoup(html, "lxml"))))


def parse_table3_categories(html: str) -> list[dict]:
    """
    Read the Table 3 food list from <table id="categories">.

    Row shape, verified on the real Curdlan page:
        td[0] spacer | td[1] tree icon | td[2] 01.1.4 | td[3] category name

    Same guard as the provisions table: the number cell must look like a food
    category code, so a reordered table produces nothing rather than nonsense.
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="categories")
    if table is None:
        return []

    body = table.find("tbody") or table
    out: list[dict] = []

    for tr in body.find_all("tr", recursive=False):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 4:
            continue
        code = tds[2].get_text(" ", strip=True)
        if not _CAT_CODE.fullmatch(code):
            continue
        name = tds[3].get_text(" ", strip=True)
        link = tds[2].find("a", href=True) or tds[3].find("a", href=True)
        out.append({
            "food_category": name,
            "food_category_raw": f"{code} {name}",
            "food_category_id": _extract_id(link["href"]) if link else None,
        })
    return out


def parse_table3_notice(html: str) -> Optional[str]:
    """
    The paragraph that explains the Table 3 permission, verbatim.

    Carried onto every Table 3 row for the same reason the combined-limit
    sentence is carried onto every inherited group row: the permission is not
    the list on its own. The paragraph states that the Annex to Table 3 has
    already been subtracted, and — on the Curdlan page — names two extra foods
    that are permitted but do NOT appear in the table:

        "Although not listed below, Curdlan could also be used in heat-treated
         butter milk of food category 01.1.1 and spices of food category 12.2.1."

    Those two are deliberately NOT turned into rows. The permission is for
    heat-treated butter milk, which is one item inside category 01.1.1, not for
    the whole of 01.1.1. Emitting a row for 01.1.1 would over-permit exactly the
    way copying a group limit without its "total content" sentence over-permits.
    The sentence travels with the data and the run reports which additives have
    one, so a human decides.
    """
    soup = BeautifulSoup(html, "lxml")
    flat = _flat_text(soup)
    if not _TABLE3_HEADING.search(flat):
        return None
    m = _TABLE3_SENTENCE.search(flat)
    if not m:
        return None
    return re.sub(r"\s+([,.])", r"\1", m.group(1).strip())


def parse_table3_extra_foods(html: str) -> Optional[str]:
    """The 'Although not listed below...' sentence on its own, or None."""
    m = _TABLE3_EXTRA.search(_flat_text(BeautifulSoup(html, "lxml")))
    return re.sub(r"\s+([,.])", r"\1", m.group(1).strip()) if m else None


def unrendered_placeholders(html: str) -> list[str]:
    """FAO template variables left unfilled on the page — a fault at the source."""
    return sorted(set(_UNRENDERED.findall(_flat_text(BeautifulSoup(html, "lxml")))))


def parse_detail(html: str, base: AdditiveRecord) -> list[AdditiveRecord]:
    """
    Expand one additive detail page into one record per provision.

    Falls back to a single identity record when the page has no provisions table,
    so the additive stays in the data rather than disappearing.
    """
    fclass, classes = extract_functional_classes(html)
    page_synonyms = extract_synonyms(html)
    rows = parse_provisions_table(html)

    def _merge_synonyms(rec: AdditiveRecord) -> None:
        """Add the detail page's own synonyms without losing the index's."""
        for s in page_synonyms:
            if s and s not in rec.synonyms and s != rec.additive_name:
                rec.synonyms.append(s)

    if not rows:
        rec = AdditiveRecord(**asdict(base))
        rec.functional_class = fclass or rec.functional_class
        rec.functional_classes = classes or rec.functional_classes
        _merge_synonyms(rec)
        # An additive with no allowances but WITH Table 3 rows is not empty —
        # carrageenan-shaped. Skip the identity placeholder in that case.
        out = [] if (is_table3_page(html) and parse_table3_categories(html)) else [rec]
    else:
        out = []
        for row in rows:
            rec = AdditiveRecord(**asdict(base))
            rec.functional_class = fclass or rec.functional_class
            rec.functional_classes = classes or rec.functional_classes
            _merge_synonyms(rec)
            rec.food_category = row["food_category"]
            rec.food_category_raw = row["food_category_raw"]
            rec.food_category_id = row["food_category_id"]
            rec.max_level_mg_kg = row["max_level_mg_kg"]
            rec.max_level_basis = row["max_level_basis"]
            rec.conditions = row["conditions"]
            rec.note_codes = row["note_codes"]
            rec.effective_date = None
            out.append(rec)

    # ---- TABLE 3: the second permission list ---- #
    # GATED ON THE HEADING. parse_table3_categories() will read any
    # <table id="categories">, but a GMP permission is only emitted when the
    # page actually says "GSFA Table 3 Provisions". If FAO ever reuses that
    # table id for something else — a related-additives list, a navigation
    # panel — this refuses to turn it into permissions. Inventing a permission
    # is the worst error this file can make, so the declaration must be present.
    t3_rows = parse_table3_categories(html) if is_table3_page(html) else []
    if t3_rows:
        notice = parse_table3_notice(html) or (
            "Included in GSFA Table 3: permitted under Good Manufacturing "
            "Practice in the listed foods."
        )
        for row in t3_rows:
            rec = AdditiveRecord(**asdict(base))
            rec.functional_class = fclass or rec.functional_class
            rec.functional_classes = classes or rec.functional_classes
            _merge_synonyms(rec)
            rec.food_category = row["food_category"]
            rec.food_category_raw = row["food_category_raw"]
            rec.food_category_id = row["food_category_id"]
            # GMP by definition — null, never 0. Table 3 sets no numeric ceiling.
            rec.max_level_mg_kg = None
            rec.max_level_basis = "gmp"
            rec.conditions = notice
            rec.note_codes = []
            rec.effective_date = None
            # Tagged in source_name rather than a new field, so the shared
            # 19-field record shape is unchanged. Same pattern as "[via GROUP]".
            rec.source_name = "Codex GSFA [Table 3]"
            out.append(rec)

        missing = unrendered_placeholders(html)
        if missing:
            log.warning("FAO left template variables unrendered on this page: %s "
                        "— the sentence they belong to has no content at source",
                        missing)
    return out


# =========================================================================== #
# PARSER 3 — GROUP PAGES
# =========================================================================== #
def parse_group_members(html: str) -> list[dict]:
    """
    Read the member list from <table id="additives">.

        td[0] JECFA icon | td[1] 210 (the member's INS) | td[2] <a>Benzoic acid</a>

    Returns [{'canonical_id': '210', 'additive_name': 'Benzoic acid',
              'detail_id': '231'}, ...]
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="additives")
    if table is None:
        return []

    body = table.find("tbody") or table
    out: list[dict] = []

    for tr in body.find_all("tr", recursive=False):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 3:
            continue
        ins = tds[1].get_text(" ", strip=True)
        link = tds[2].find("a", href=True)
        if not ins or not link:
            continue
        out.append({
            "canonical_id": ins,
            "additive_name": link.get_text(" ", strip=True),
            "detail_id": _extract_id(link["href"]),
        })
    return out


def parse_group_name(html: str) -> Optional[str]:
    """
    Pull the clean group name, e.g. 'BENZOATES', from its styled banner.

    The class is looked for on ANY tag, not just <table>. The original searched
    for a <table> carrying it; if FAO puts the class on a <div> or <td> on any
    of the fifteen group pages nobody has opened yet, that search returns None
    and the name silently degrades to whatever the index link said.

    Banner furniture is stripped and the result length-checked, because the very
    first attempt at this returned ninety characters of page banner including
    "48 th Session of the Codex Alimentarius Commission (2025)". A group name is
    a short word. Anything long is the banner, not the name.
    """
    soup = BeautifulSoup(html, "lxml")

    for banner in soup.find_all(class_=_GROUP_TITLE_CLASS):
        name = re.sub(r"\s+", " ", banner.get_text(" ", strip=True))
        name = re.sub(r"Food Additive Group Details", "", name, flags=re.IGNORECASE)
        name = re.sub(r"GSFA Online.*?$", "", name, flags=re.IGNORECASE)
        name = name.strip(" -–—:")
        if name and len(name) <= 60:
            return name

    if soup.title:
        m = re.search(r"Group Details for\s+(.+)$", soup.title.get_text(strip=True))
        if m:
            return m.group(1).strip()
    return None


def parse_group_notice(html: str) -> Optional[str]:
    """Pull the combined-limit warning sentence, verbatim, from the page."""
    m = _GROUP_NOTICE.search(_flat_text(BeautifulSoup(html, "lxml")))
    return m.group(1).strip() if m else None


def parse_group(html: str, group_record: AdditiveRecord) -> list[AdditiveRecord]:
    """
    Expand one group page into usable records.

    Emits one record per (member x group provision), keyed on the MEMBER's own
    INS — because a verdict must be answerable for "sodium benzoate", never for
    "BENZOATES", which is a heading and not a substance. Never returns a record
    keyed on the group itself.

    Every emitted row carries:
        via_group    'BENZOATES'
        source_name  'Codex GSFA [via BENZOATES]'
        source_url   the GROUP page — the rule is written there, and members
                     generally have no detail page of their own
        conditions   the row's own notes + the combined-limit warning + the full
                     member list, so the shared budget travels with the data
    """
    members = parse_group_members(html)
    if not members:
        return []

    gname = parse_group_name(html) or group_record.additive_name
    notice = parse_group_notice(html)
    fclass, fclasses = extract_functional_classes(html)
    rows = parse_provisions_table(html)
    group_url = group_record.source_url

    ins_list = ", ".join(m["canonical_id"] for m in members)
    shared = (
        f"{notice} Group '{gname}' members: {ins_list}."
        if notice
        else f"Inherited from group '{gname}'. Members: {ins_list}."
    )

    out: list[AdditiveRecord] = []
    for m in members:
        base = AdditiveRecord(
            canonical_id=m["canonical_id"],
            additive_name=m["additive_name"],
            functional_class=fclass,
            functional_classes=fclasses,
            source_name=f"Codex GSFA [via {gname}]",
            source_url=group_url,
            is_group=False,
            via_group=gname,
        )

        if not rows:                        # group has no provisions of its own
            base.conditions = shared
            out.append(base)
            continue

        for row in rows:
            rec = AdditiveRecord(**asdict(base))
            rec.food_category = row["food_category"]
            rec.food_category_raw = row["food_category_raw"]
            rec.food_category_id = row["food_category_id"]
            rec.max_level_mg_kg = row["max_level_mg_kg"]
            rec.max_level_basis = row["max_level_basis"]
            rec.note_codes = row["note_codes"]
            rec.conditions = (
                f"{row['conditions']} | {shared}" if row["conditions"] else shared
            )
            out.append(rec)
    return out


# =========================================================================== #
# PROBES — always look at a real page before trusting a crawl
# =========================================================================== #
def probe_detail_html(html: str) -> str:
    lines = ["--- DETAIL PAGE PROBE ---",
             f"functional class : {extract_functional_class(html)!r}",
             f"synonyms on page : {extract_synonyms(html)}"]

    t3 = parse_table3_categories(html)
    if t3:
        lines += ["",
                  f"GSFA TABLE 3    : YES — {len(t3)} food categories under GMP",
                  f"  notice        : {(parse_table3_notice(html) or '')[:150]}",
                  f"  extra foods   : {parse_table3_extra_foods(html)}",
                  f"  unrendered    : {unrendered_placeholders(html)}",
                  "  first 3       : " + "; ".join(r['food_category_raw'][:40] for r in t3[:3]),
                  ""]

    rows = parse_provisions_table(html)
    if not rows:
        lines.append("NO provisions table (<table id='allowances'> missing).")
        lines.append("Legitimate for additives with no adopted provisions.")
        return "\n".join(lines)

    lines.append(f"provisions       : {len(rows)}")
    lines.append(f"  with note codes: {sum(1 for r in rows if r['note_codes'])}")
    lines.append(f"  with note TEXT : {sum(1 for r in rows if r['conditions'])}")
    for r in rows[:5]:
        lines += ["",
                  f"  {r['food_category_raw'][:68]}",
                  f"     level : {r['max_level_mg_kg']} {r['max_level_basis']}",
                  f"     cat id: {r['food_category_id']}",
                  f"     notes : {r['note_codes']}",
                  f"     text  : {r['conditions']}"]
    if len(rows) > 5:
        lines.append(f"\n  ... (+{len(rows) - 5} more)")
    return "\n".join(lines)


def probe_group_html(html: str) -> str:
    members = parse_group_members(html)
    lines = ["--- GROUP PAGE PROBE ---",
             f"group name : {parse_group_name(html)!r}",
             f"members    : {len(members)}"]
    for m in members:
        lines.append(f"   INS {m['canonical_id']:<10} id={str(m['detail_id']):<6} "
                     f"{m['additive_name']}")
    lines += ["", f"combined-limit notice: {parse_group_notice(html)!r}", "",
              probe_detail_html(html)]
    if not members:
        lines += ["", "WARNING: no members parsed. <table id='additives'> may be",
                  "laid out differently on this group. Save the page and inspect it."]
    return "\n".join(lines)


# =========================================================================== #
# NETWORK LAYER
# =========================================================================== #
def fetch(client: httpx.Client, url: str) -> str:
    """
    GET one page, politely.

    Descriptive User-Agent, a 1-second gap between requests, and retry with
    growing backoff on 429 (too many requests) and 503 (server busy). FAO
    returned 503 mid-project and recovered, so this is not theoretical.
    """
    last: Optional[Exception] = None

    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = client.get(url, headers=HEADERS, timeout=30.0,
                              follow_redirects=True)
            if resp.status_code in (429, 503) and attempt < RETRY_ATTEMPTS:
                wait = RETRY_BASE_SECONDS * attempt
                log.warning("%s on %s — waiting %.0fs (attempt %d/%d)",
                            resp.status_code, url, wait, attempt, RETRY_ATTEMPTS)
                time.sleep(wait)
                continue
            if resp.status_code >= 400:
                # STEP 4 asks for up to ~60 member pages that may not exist. The
                # original returned those 404s with no pause, firing them off as
                # fast as the network allowed — the one place the crawl stopped
                # being polite. Wait here too.
                time.sleep(REQUEST_DELAY_SECONDS)
            resp.raise_for_status()
            time.sleep(REQUEST_DELAY_SECONDS)
            return resp.text
        except httpx.TransportError as e:
            last = e
            if attempt >= RETRY_ATTEMPTS:
                break
            wait = RETRY_BASE_SECONDS * attempt
            log.warning("network error on %s (%s) — waiting %.0fs", url, e, wait)
            time.sleep(wait)

    raise httpx.HTTPError(f"gave up after {RETRY_ATTEMPTS} attempts: {url} ({last})")


def fetch_backbone(client: httpx.Client,
                   expand_groups: bool = True) -> list[AdditiveRecord]:
    """
    Build the name -> INS dictionary.

    expand_groups=True also opens each group page and adds its members. This
    DEFAULTS ON because leaving it off silently drops benzoic acid, the
    tocopherols, the polysorbates and more. Turning it off is a debugging
    convenience, not a normal mode.

    The group HEADING row is kept (is_group=True) as a genuine reference entry,
    but must never be used as a verdict key.
    """
    records = parse_index(
        fetch(client, INDEX_URL),
        synonyms_by_id=parse_synonyms(fetch(client, SYNONYM_URL)),
    )
    if not expand_groups:
        return records

    known = {r.canonical_id for r in records}
    added: list[AdditiveRecord] = []

    for grp in [r for r in records if r.is_group and r.source_url]:
        try:
            ghtml = fetch(client, grp.source_url)
        except httpx.HTTPError as e:
            log.warning("group fetch failed: %s (%s)", grp.additive_name, e)
            continue

        gname = parse_group_name(ghtml) or grp.additive_name
        members = parse_group_members(ghtml)
        log.info("  group %-46s %d members", gname, len(members))

        for m in members:
            if m["canonical_id"] in known:
                continue                        # already listed under its own name
            known.add(m["canonical_id"])
            added.append(
                AdditiveRecord(
                    canonical_id=m["canonical_id"],
                    additive_name=m["additive_name"],
                    source_name=f"Codex GSFA [via {gname}]",
                    source_url=grp.source_url,  # cite where the rule is written
                    is_group=False,
                    via_group=gname,
                )
            )

    log.info("group expansion recovered %d additives missing from the index",
             len(added))
    return records + added