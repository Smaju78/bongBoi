#!/usr/bin/env python3
"""
Fetch English short stories from Project Gutenberg and save each one as a JSON file,
already split into short posts, in the same shape as wikisource_to_json.py writes.

Needs only Python 3.8+ (no extra packages).

Examples
  # See which stories of every book can be found, download nothing new if cached
  python gutenberg_to_json.py --dry-run

  # Try one book first
  python gutenberg_to_json.py --only 2814 --limit 3

  # Everything in the CATALOGUE below
  python gutenberg_to_json.py

Each CATALOGUE entry is one Gutenberg ebook that holds several stories:
  all   = every story heading in the book, in order (used to find where a story ends)
  pick  = the stories to keep, in the same spelling as in `all`
Headings are matched ignoring case and punctuation, and a leading "I." / "XII." is ignored.
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

MAX_POST = 210          # target characters per post
DELAY = 2.0
UA = "bongBoi-reader/1.0 (personal reading app; polite crawler)"
URL = "https://www.gutenberg.org/cache/epub/{id}/pg{id}.txt"
EBOOK = "https://www.gutenberg.org/ebooks/{id}"

CATALOGUE = [
    {"id": 2148, "author": "Edgar Allan Poe", "book": "Tales, Vol. 2", "year": 1845,
     "all": ["The Purloined Letter", "The Thousand-and-Second Tale of Scheherazade", "A Descent into the Maelstrom",
             "Von Kempelen and His Discovery", "Mesmeric Revelation", "The Facts in the Case of M. Valdemar",
             "The Black Cat", "The Fall of the House of Usher", "Silence—A Fable", "The Masque of the Red Death",
             "The Cask of Amontillado", "The Imp of the Perverse", "The Island of the Fay", "The Assignation",
             "The Pit and the Pendulum", "The Premature Burial", "The Domain of Arnheim", "Landor’s Cottage",
             "William Wilson", "The Tell-Tale Heart", "Berenice", "Eleonora"],
     "pick": ["The Purloined Letter", "A Descent into the Maelstrom", "The Facts in the Case of M. Valdemar",
              "The Black Cat", "The Fall of the House of Usher", "The Masque of the Red Death",
              "The Cask of Amontillado", "The Pit and the Pendulum", "The Premature Burial", "William Wilson",
              "The Tell-Tale Heart", "Berenice", "Eleonora"]},
    {"id": 2147, "author": "Edgar Allan Poe", "book": "Tales, Vol. 1", "year": 1843,
     "all": ["The Gold-Bug", "Four Beasts in One—The Homo-Cameleopard", "The Murders in the Rue Morgue",
             "A Sequel to “The Murders in the Rue Morgue.”", "The Balloon-Hoax", "MS. Found in a Bottle",
             "The Oval Portrait", "The Mystery of Marie Roget"],
     "pick": ["The Gold-Bug", "The Murders in the Rue Morgue", "MS. Found in a Bottle"]},
    {"id": 1732, "author": "Anton Chekhov", "book": "The Schoolmistress and Other Stories", "year": 1887,
     "translator": "Constance Garnett",
     "all": ["The Schoolmistress", "A Nervous Breakdown", "Misery", "Champagne", "After the Theatre",
             "A Lady’s Story", "In Exile", "The Cattle-Dealers", "Sorrow", "On Official Duty",
             "The First-Class Passenger", "A Tragic Actor", "A Transgression", "Small Fry", "The Requiem",
             "In the Coach-House", "Panic Fears", "The Bet", "The Head-Gardener’s Story", "The Beauties",
             "The Shoemaker and the Devil"],
     "pick": ["The Schoolmistress", "Misery", "In Exile", "Sorrow", "The Bet"]},
    {"id": 13415, "author": "Anton Chekhov", "book": "The Lady with the Dog and Other Stories", "year": 1899,
     "translator": "Constance Garnett",
     "all": ["The Lady with the Dog", "A Doctor's Visit", "An Upheaval", "Ionitch", "The Head of the Family",
             "The Black Monk", "Volodya", "An Anonymous Story", "The Husband"],
     "pick": ["The Lady with the Dog", "A Doctor's Visit", "Ionitch", "The Black Monk", "Volodya"]},
    {"id": 3077, "author": "Guy de Maupassant", "book": "Original Short Stories, Vol. 1", "year": 1880,
     "translator": "A. E. Henderson and others",
     "all": ["Boule de Suif", "Two Friends", "The Lancer's Wife", "The Prisoners", "Two Little Soldiers",
             "Father Milon", "A Coup d'Etat", "Lieutenant Lare's Marriage", "The Horrible", "Madame Parisse",
             "Mademoiselle Fifi", "A Duel"],
     "pick": ["Boule de Suif", "Two Friends", "Two Little Soldiers", "Father Milon", "Mademoiselle Fifi"]},
    {"id": 2776, "author": "O. Henry", "book": "The Four Million", "year": 1906,
     "all": ["Tobin’s Palm", "The Gift of the Magi", "Between Rounds", "The Skylight Room", "A Service of Love",
             "The Coming-Out of Maggie", "Man About Town", "The Cop and the Anthem", "An Adjustment of Nature",
             "Memoirs of a Yellow Dog", "The Love-Philtre of Ikey Schoenstein", "Mammon and the Archer",
             "The Green Door", "From the Cabby’s Seat", "An Unfinished Story", "The Caliph, Cupid and the Clock",
             "Sisters of the Golden Circle", "The Romance of a Busy Broker", "After Twenty Years",
             "Lost on Dress Parade", "By Courier", "The Furnished Room", "A Cosmopolite in a Café",
             "Springtime à la Carte", "The Brief Début of Tildy"],
     "pick": ["The Gift of the Magi", "The Cop and the Anthem", "After Twenty Years", "The Furnished Room",
              "The Skylight Room", "Mammon and the Archer", "The Green Door", "A Service of Love",
              "The Romance of a Busy Broker", "Memoirs of a Yellow Dog"]},
    {"id": 902, "author": "Oscar Wilde", "book": "The Happy Prince and Other Tales", "year": 1888,
     "all": ["The Happy Prince", "The Nightingale and the Rose", "The Selfish Giant", "The Devoted Friend",
             "The Remarkable Rocket"],
     "pick": ["The Happy Prince", "The Nightingale and the Rose", "The Selfish Giant", "The Devoted Friend",
              "The Remarkable Rocket"]},
    {"id": 1661, "author": "Arthur Conan Doyle", "book": "The Adventures of Sherlock Holmes", "year": 1892,
     "all": ["A Scandal in Bohemia", "The Red-Headed League", "A Case of Identity", "The Boscombe Valley Mystery",
             "The Five Orange Pips", "The Man with the Twisted Lip", "The Adventure of the Blue Carbuncle",
             "The Adventure of the Speckled Band", "The Adventure of the Engineer’s Thumb",
             "The Adventure of the Noble Bachelor", "The Adventure of the Beryl Coronet",
             "The Adventure of the Copper Beeches"],
     "pick": ["A Scandal in Bohemia", "The Red-Headed League", "A Case of Identity", "The Five Orange Pips",
              "The Man with the Twisted Lip", "The Adventure of the Blue Carbuncle",
              "The Adventure of the Speckled Band", "The Adventure of the Copper Beeches"]},
    {"id": 1858, "author": "Rudyard Kipling", "book": "Plain Tales from the Hills", "year": 1888,
     "all": ["Lispeth", "Three and--an Extra", "Thrown Away", "Miss Youghal's Sais", "Yoked with an Unbeliever",
             "False Dawn", "The Rescue of Pluffles", "Cupid's Arrows", "His Chance in Life", "Watches of the Night",
             "The Other Man", "Consequences", "A Germ Destroyer", "Kidnapped", "The Arrest of Lieutenant Golightly",
             "The House of Suddhoo", "His Wedded Wife", "The Broken Link Handicapped", "Beyond the Pale", "In Error",
             "A Bank Fraud", "Tod's Amendment", "In the Pride of His Youth", "Pig", "The Rout of the White Hussars",
             "The Bronckhorst Divorce-Case", "Venus Annodomini", "The Bisara of Pooree",
             "The Gate of a Hundred Sorrows", "The Story of Muhammad Din", "On the Strength of a Likeness",
             "Wressley of the Foreign Office", "By Word of Mouth", "To Be Held for Reference", "The Conversion of Aurelian McGoggin"],
     "pick": ["Lispeth", "Thrown Away", "His Chance in Life", "Beyond the Pale", "The Rout of the White Hussars",
              "The Story of Muhammad Din"]},
    {"id": 236, "author": "Rudyard Kipling", "book": "The Jungle Book", "year": 1894,
     "all": ["Mowgli's Brothers", "Hunting-Song of the Seeonee Pack", "Kaa's Hunting", "Road-Song of the Bandar-Log",
             "Tiger! Tiger!", "Mowgli's Song", "The White Seal", "Lukannon", "Rikki-Tikki-Tavi", "Darzee's Chant",
             "Toomai of the Elephants", "Shiv and the Grasshopper", "Her Majesty's Servants",
             "Parade Song of the Camp Animals"],
     "pick": ["Mowgli's Brothers", "Kaa's Hunting", "Tiger! Tiger!", "The White Seal", "Rikki-Tikki-Tavi",
              "Toomai of the Elephants"]},
    {"id": 512, "author": "Nathaniel Hawthorne", "book": "Mosses from an Old Manse", "year": 1846,
     "all": ["The Old Manse", "The Birthmark", "A Select Party", "Young Goodman Brown", "Rappaccini’s Daughter",
             "Mrs. Bullfrog", "Fire Worship", "Buds and Bird Voices", "Monsieur du Miroir", "The Hall of Fantasy",
             "The Celestial Railroad", "The Procession of Life", "Feathertop: A Moralized Legend",
             "The New Adam and Eve", "Egotism; or, the Bosom Serpent", "The Christmas Banquet",
             "Drowne’s Wooden Image", "The Intelligence Office", "Roger Malvin’s Burial", "P.’s Correspondence",
             "Earth’s Holocaust", "Passages from a Relinquished Work", "At Home", "Sketches from Memory",
             "The Notch of the White Mountains", "The Old Apple Dealer", "The Artist of the Beautiful",
             "A Virtuoso’s Collection"],
     "pick": ["The Birthmark", "Young Goodman Brown", "Rappaccini’s Daughter", "The Celestial Railroad",
              "Feathertop: A Moralized Legend", "Drowne’s Wooden Image", "Roger Malvin’s Burial",
              "The Artist of the Beautiful"]},
    {"id": 1429, "author": "Katherine Mansfield", "book": "The Garden Party and Other Stories", "year": 1922,
     "all": ["At the Bay", "The Garden-Party", "The Daughters of the Late Colonel", "Mr. and Mrs. Dove",
             "The Young Girl", "Life of Ma Parker", "Marriage à la Mode", "The Voyage", "Miss Brill",
             "Her First Ball", "The Singing Lesson", "The Stranger", "Bank Holiday", "An Ideal Family",
             "The Lady’s Maid"],
     "pick": ["At the Bay", "The Garden-Party", "Mr. and Mrs. Dove", "Life of Ma Parker", "The Voyage",
              "Miss Brill", "Her First Ball"]},
    {"id": 2814, "author": "James Joyce", "book": "Dubliners", "year": 1914,
     "all": ["The Sisters", "An Encounter", "Araby", "Eveline", "After the Race", "Two Gallants",
             "The Boarding House", "A Little Cloud", "Counterparts", "Clay", "A Painful Case",
             "Ivy Day in the Committee Room", "A Mother", "Grace", "The Dead"],
     "pick": ["The Sisters", "An Encounter", "Araby", "Eveline", "After the Race", "Two Gallants",
              "The Boarding House", "A Little Cloud", "Counterparts", "Clay", "A Painful Case",
              "Ivy Day in the Committee Room", "A Mother", "Grace", "The Dead"]},
    {"id": 3688, "author": "Saki", "book": "The Chronicles of Clovis", "year": 1911,
     "all": ["The Match-Maker", "Tobermory", "Mrs. Packletide's Tiger", "The Stampeding of Lady Bastable",
             "The Background", "Hermann the Irascible--A Story of the Great Weep", "The Unrest-Cure",
             "The Jesting of Arlington Stringham", "Sredni Vashtar", "Adrian", "The Chaplet", "The Quest",
             "Wratislav", "The Easter Egg", "Filboid Studge, the Story of a Mouse That Helped",
             "The Music on the Hill", "The Story of St. Vespaluus", "The Way to the Dairy", "The Peace Offering",
             "The Peace of Mowsle Barton", "The Talking-Out of Tarrington", "The Hounds of Fate",
             "The Recessional", "A Matter of Sentiment", "The Secret Sin of Septimus Brope",
             "The Remoulding of Groby Lington", "Ministers of Grace"],
     "pick": ["Tobermory", "Mrs. Packletide's Tiger", "The Background", "The Unrest-Cure", "Sredni Vashtar",
              "Filboid Studge, the Story of a Mouse That Helped", "The Music on the Hill"]},
    {"id": 689, "author": "Leo Tolstoy", "book": "The Kreutzer Sonata and Other Stories", "year": 1886,
     "translator": "Benjamin R. Tucker",
     "all": ["The Kreutzer Sonata", "Lesson of “The Kreutzer Sonata.”", "Ivan the Fool", "A Lost Opportunity",
             "The Candle", "Polikushka"],
     "pick": ["Ivan the Fool", "A Lost Opportunity", "The Candle"]},
    {"id": 243, "author": "Leo Tolstoy", "book": "The Forged Coupon and Other Stories", "year": 1911,
     "translator": "public-domain translators",
     "all": ["The Forged Coupon", "After the Dance", "Alyosha the Pot", "My Dream", "There Are No Guilty People",
             "The Young Tsar"],
     "pick": ["After the Dance", "Alyosha the Pot", "My Dream", "There Are No Guilty People"]},
    {"id": 36238, "author": "Nikolai Gogol", "book": "The Mantle and Other Stories", "year": 1842,
     "translator": "Claud Field",
     "all": ["The Mantle", "The Nose", "Memoirs of a Madman", "A May Night", "The Viy"],
     "pick": ["The Mantle", "The Nose", "Memoirs of a Madman", "The Viy"]},
    {"id": 3056, "author": "Thomas Hardy", "book": "Wessex Tales", "year": 1888,
     "all": ["Preface", "An Imaginative Woman", "The Three Strangers", "The Withered Arm", "Fellow-Townsmen",
             "Interlopers at the Knap", "The Distracted Preacher"],
     "pick": ["An Imaginative Woman", "The Three Strangers", "The Withered Arm", "The Distracted Preacher"]},
    {"id": 710, "author": "Jack London", "book": "Love of Life and Other Stories", "year": 1907,
     "all": ["Love of Life", "A Day's Lodging", "The White Man's Way", "The Story of Keesh", "The Unexpected",
             "Brown Wolf", "The Sun-Dog Trail", "Negore, the Coward"],
     "pick": ["Love of Life", "The Story of Keesh", "Brown Wolf", "Negore, the Coward"]},
    {"id": 4366, "author": "Ambrose Bierce", "book": "Can Such Things Be?", "year": 1893,
     "all": ["The Death of Halpin Frayser", "The Secret of Macarger’s Gulch", "One Summer Night",
             "The Moonlit Road", "A Diagnosis of Death", "Moxon’s Master", "A Tough Tussle", "One of Twins",
             "A Letter Found Among the Papers of the Late Mortimer Barr", "The Haunted Valley", "A Jug of Sirup",
             "Staley Fleming’s Hallucination", "A Resumed Identity", "A Baby Tramp",
             "The Night-Doings at “Deadman’s”", "A Story that is Untrue", "Beyond the Wall",
             "A Psychological Shipwreck", "The Middle Toe of the Right Foot", "The Realm of the Unreal",
             "John Bartine’s Watch", "A Story by a Physician", "The Damned Thing", "An Inhabitant of Carcosa",
             "The Stranger", "John Mortonson’s Funeral"],
     "pick": ["The Death of Halpin Frayser", "The Moonlit Road", "Moxon’s Master",
              "A Resumed Identity", "Beyond the Wall", "An Inhabitant of Carcosa", "The Damned Thing",
              "The Stranger"]},
]

END_LINE = re.compile(r"^(FOOTNOTES?|NOTES? TO THE .*|THE END\.?|NOTES\.?)$", re.I)


# ---------------------------------------------------------------- download

def fetch(book_id, cache, delay):
    f = cache / f"pg{book_id}.txt"
    if not f.exists():
        cache.mkdir(parents=True, exist_ok=True)
        time.sleep(delay)
        req = urllib.request.Request(URL.format(id=book_id), headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            f.write_bytes(r.read())
    text = f.read_text(encoding="utf-8-sig")
    lines = text.replace("\r\n", "\n").split("\n")
    start = next(i for i, l in enumerate(lines) if "*** START OF" in l) + 1
    end = next(i for i, l in enumerate(lines) if "*** END OF" in l)
    return lines[start:end]


# ---------------------------------------------------------------- finding stories

def norm(s):
    s = re.sub(r"^\s*[IVXLC]+\.\s+", "", s.strip())          # "XII. THE ..." -> "THE ..."
    return re.sub(r"[^a-z0-9]", "", s.lower().replace("à", "a").replace("é", "e"))


def heading_positions(lines):
    pos = {}
    for i, l in enumerate(lines):
        n = norm(l)
        if n and len(l.strip()) < 90:
            pos.setdefault(n, []).append(i)
    return pos


def story_range(lines, pos, entry, title):
    """Story text is between its heading (the last full-line match: earlier ones are the contents) and
    the next heading from `all`."""
    n = norm(title)
    if n not in pos:
        return None
    start = pos[n][-1]
    ends = [len(lines)]
    for other in entry["all"]:
        for p in pos.get(norm(other), []):
            if p > start:
                ends.append(p)
    for i in range(start + 1, len(lines)):
        if END_LINE.match(lines[i].strip()):
            ends.append(i)
            break
    return start + 1, min(ends)


# ---------------------------------------------------------------- cleaning

CAPS_HEADING = re.compile(r"^[A-Z0-9 .,'’“”:;!?\-—()&]+$")
SECTION = re.compile(r"^(?:CHAPTER\s+)?[IVXLC]+[.—]?(?:\s*[A-Z].*)?$|^\d{1,3}\.?$|^\*(?:\s*\*)+$|^-{3,}$")
DROP = re.compile(r"^\[(?:Picture|Illustration|Footnote|Sidenote)\b", re.I)


def smart_quotes(p):
    out = []
    for i, ch in enumerate(p):
        prev = p[i - 1] if i else " "
        if ch == '"':
            out.append("“" if prev in " \n([—-“‘" else "”")
        elif ch == "'":
            out.append("‘" if prev in " \n([—-“" and i + 1 < len(p) and p[i + 1].isalnum() else "’")
        else:
            out.append(ch)
    return "".join(out)


def clean_paragraph(lines):
    lines = [l.strip() for l in lines if l.strip()]
    if not lines:
        return None
    first = " ".join(lines)
    if DROP.match(first) or re.fullmatch(r"\[\d+\]", first):
        return None
    if SECTION.match(first) or (CAPS_HEADING.match(first) and len(first) <= 70 and re.search(r"[A-Z]{2}", first)
                                and not first.endswith(",") and not first[0] in "“‘\""):
        return "---"
    verse = len(lines) >= 3 and all(len(l) < 60 for l in lines[:-1])
    text = ("\n" if verse else " ").join(lines)
    text = re.sub(r"\[\d+\]", "", text)                      # footnote markers
    text = re.sub(r"\[Picture:[^\]]*\]|\[Illustration:?[^\]]*\]", "", text)
    text = re.sub(r"(?<![\w])_([^_\n]+)_(?![\w])", r"\1", text)   # _italics_
    text = text.replace("_", "")
    text = smart_quotes(text.replace("--", "—"))
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    return text or None


def story_paragraphs(chunk, title, notes=None):
    """`notes` collects ALL-CAPS lines that were turned into section breaks (a missing story heading shows up here)."""
    paras, cur = [], []
    for l in chunk + [""]:
        if l.strip():
            cur.append(l)
        else:
            if cur:
                head = " ".join(x.strip() for x in cur)
                if notes is not None and CAPS_HEADING.match(head) and len(head) > 4 and not SECTION.match(head):
                    notes.append(head)
                p = clean_paragraph(cur)
                if p:
                    paras.append(p)
            cur = []
    out = []
    for p in paras:
        if p == "---":
            if out and out[-1] != "---":
                out.append(p)
            continue
        if not out and norm(p) == norm(title):
            continue
        out.append(p)
    while out and out[0] == "---":
        out.pop(0)
    while out and out[-1] == "---":
        out.pop()
    return out


# ---------------------------------------------------------------- paragraphs -> posts

ABBREV = {"mr", "mrs", "ms", "dr", "st", "messrs", "col", "capt", "mme", "mlle", "m", "sr", "jr", "prof", "no",
          "vs", "gen", "lt", "sgt", "rev", "hon", "sir", "mt", "ft", "etc", "i.e", "e.g", "p", "a.m", "p.m"}
BOUNDARY = re.compile(r"([.?!]+[”’\")\]]*)(\s+)(?=[“‘\"(]?[A-Z0-9])")


def sentences(p):
    out, last = [], 0
    for m in BOUNDARY.finditer(p):
        before = p[last:m.start()].split(" ")[-1].lstrip("“‘\"(").rstrip(".").lower()
        if m.group(1) == "." and (before in ABBREV or (len(before) == 1 and before.isalpha())):
            continue
        out.append(p[last:m.end(1)])
        last = m.end()
    rest = p[last:].strip()
    if rest:
        out.append(rest)
    return [s.strip() for s in out if s.strip()]


def split_long(s):
    if len(s) <= MAX_POST + 60:
        return [s]
    parts = re.split(r"(?<=[,;:—])\s+", s)
    if max(len(x) for x in parts) > MAX_POST + 60:
        parts = [w for x in parts for w in (x.split(" ") if len(x) > MAX_POST + 60 else [x])]
    res, cur = [], ""
    for x in parts:
        if cur and len(cur) + 1 + len(x) > MAX_POST:
            res.append(cur)
            cur = x
        else:
            cur = f"{cur} {x}" if cur else x
    if cur:
        res.append(cur)
    return res


def to_posts(paras):
    """Items: {"t": text} for a post, {"brk": true} for a section break. Posts never cross a paragraph."""
    items = []
    for p in paras:
        if p == "---":
            items.append({"brk": True})
            continue
        if "\n" in p:                                          # verse: group lines, don't re-flow
            cur = ""
            for line in p.split("\n"):
                if cur and len(cur) + 1 + len(line) > MAX_POST:
                    items.append({"t": cur})
                    cur = line
                else:
                    cur = f"{cur}\n{line}" if cur else line
            if cur:
                items.append({"t": cur})
            continue
        cur = ""
        for s in (x for sent in sentences(p) for x in split_long(sent)):
            if cur and len(cur) + 1 + len(s) > MAX_POST:
                items.append({"t": cur})
                cur = s
            else:
                cur = f"{cur} {s}" if cur else s
        if cur:
            items.append({"t": cur})
    return items


# ---------------------------------------------------------------- output

def story_record(entry, idx, title, paras):
    items = to_posts(paras)
    rec = {
        "id": f"en-{entry['id']}-{idx + 1}",
        "title": title,
        "author": entry["author"],
        "collection": entry["book"],
        "dated": str(entry["year"]) if entry.get("year") else None,
        "lang": "en",
        "source": {"site": "gutenberg.org", "page": entry["book"], "url": EBOOK.format(id=entry["id"]),
                   "revision": None, "license": "Public domain (Project Gutenberg)"},
        "chapters": None,
        "stats": {"paragraphs": sum(1 for p in paras if p != "---"),
                  "posts": sum(1 for i in items if "t" in i),
                  "characters": sum(len(i.get("t", "")) for i in items)},
        "items": items,
    }
    if entry.get("translator"):
        rec["translator"] = entry["translator"]
    return rec


def write_catalog(out):
    rows = []
    for f in sorted(out.glob("en-*.json")):
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        book, n = f.stem.split("-")[1:3]
        rows.append({"id": s["id"], "title": s["title"], "author": s["author"], "collection": s.get("collection"),
                     "posts": s["stats"]["posts"], "file": f.name, "lang": "en",
                     "translator": s.get("translator"), "_k": (s["author"], s.get("collection") or "", int(book), int(n))})
    rows.sort(key=lambda r: r["_k"])
    for r in rows:
        del r["_k"]
        if r["translator"] is None:
            del r["translator"]
    (out / "catalog.json").write_text(json.dumps({"count": len(rows), "stories": rows},
                                                 ensure_ascii=False, indent=1), encoding="utf-8")
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description="Fetch English short stories from Project Gutenberg into reader-ready JSON.")
    ap.add_argument("--only", type=int, action="append", help="Only this Gutenberg ebook id (repeatable).")
    ap.add_argument("--out", default="stories/en", help="Output folder (default: stories/en).")
    ap.add_argument("--cache", default="gutenberg_cache", help="Where downloaded books are kept.")
    ap.add_argument("--limit", type=int, default=0, help="Stop after this many stories (for testing).")
    ap.add_argument("--dry-run", action="store_true", help="Report which stories are found; write nothing.")
    ap.add_argument("--delay", type=float, default=DELAY, help="Seconds between downloads (default 2).")
    args = ap.parse_args()

    out, cache = Path(args.out), Path(args.cache)
    if not args.dry_run:
        out.mkdir(parents=True, exist_ok=True)
    made = missing = 0
    for entry in CATALOGUE:
        if args.only and entry["id"] not in args.only:
            continue
        lines = fetch(entry["id"], cache, args.delay)
        pos = heading_positions(lines)
        print(f"\n{entry['author']} - {entry['book']} (#{entry['id']})")
        for title in entry["pick"]:
            idx = entry["all"].index(title)
            rng = story_range(lines, pos, entry, title)
            if not rng:
                print(f"  NOT FOUND   {title}")
                missing += 1
                continue
            notes = []
            paras = story_paragraphs(lines[rng[0]:rng[1]], title, notes)
            rec = story_record(entry, idx, title, paras)
            flag = "" if 15 <= rec["stats"]["posts"] <= 600 else "   <-- check this one"
            print(f"  {rec['stats']['posts']:4d} posts  {title}{flag}")
            if notes:
                print(f"        caps lines inside: {' | '.join(dict.fromkeys(notes))[:200]}")
            if not args.dry_run:
                (out / f"{rec['id']}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
            made += 1
            if args.limit and made >= args.limit:
                break
        if args.limit and made >= args.limit:
            break
    if not args.dry_run:
        print(f"\ncatalog.json: {write_catalog(out)} stories")
    print(f"done: {made} stories, {missing} not found")


if __name__ == "__main__":
    sys.exit(main())
