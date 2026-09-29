#!/usr/bin/env python3
"""
Fetch stories from Bengali Wikisource and save each one as a JSON file,
already split into short posts for the Bengali Short Reader.

Needs only Python 3.8+ (no extra packages).

Examples
  # Every short story on Bengali Wikisource (category ছোট গল্প), all authors.
  # First a dry run: lists what would be fetched, downloads no text.
  python wikisource_to_json.py --category "ছোট গল্প" --dry-run
  python wikisource_to_json.py --category "ছোট গল্প"

  # Tagore's Galpaguchchha volumes (the default when no source is given)
  python wikisource_to_json.py

  # Try just 3 stories first
  python wikisource_to_json.py --category "ছোট গল্প" --limit 3

  # One story by its Wikisource page title
  python wikisource_to_json.py --page "গল্পগুচ্ছ (প্রথম খণ্ড)/কাবুলিওয়ালা"

  # Every subpage under a prefix
  python wikisource_to_json.py --prefix "গল্পগুচ্ছ (তৃতীয় খণ্ড)/"

The author is read from each page's Wikisource header. --author only fills in
when a page has no author in its header.

Output (default folder: ./stories)
  stories/<id>.json      one file per story
  stories/catalog.json   list of all stories in the folder
  stories/report.txt     failures and things worth checking
  stories/inventory.tsv  (dry run) what would be fetched

Re-running skips stories already saved. Use --force to fetch them again.
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

API = "https://bn.wikisource.org/w/api.php"
SITE = "https://bn.wikisource.org/wiki/"

# Tagore's Galpaguchchha volumes that have per-story pages on Wikisource.
DEFAULT_PREFIXES = ["গল্পগুচ্ছ (প্রথম খণ্ড)/", "গল্পগুচ্ছ (তৃতীয় খণ্ড)/"]
DEFAULT_AUTHOR = "রবীন্দ্রনাথ ঠাকুর"

MAX_POST = 210          # target characters per post
DELAY = 2.0             # seconds between API calls, to be polite to Wikimedia


# ---------------------------------------------------------------- API

class Api:
    def __init__(self, user_agent, delay=DELAY):
        self.ua = user_agent
        self.delay = delay          # grows if Wikimedia asks us to slow down
        self.last = 0.0
        self.calls = 0
        self.slowdowns = 0

    def get(self, **params):
        """One API call, strictly one at a time, with at least `delay` seconds between calls.
        If the server says 'too many requests' (429), 'busy' (503) or 'replication lag',
        wait as long as it asks (Retry-After) or back off 10s, 20s, 40s…, and make every
        later call slower too. Gives up after 6 tries; saved stories are kept either way."""
        params.update(format="json", formatversion="2", maxlag="5")
        url = API + "?" + urllib.parse.urlencode(params)
        tries = 6
        for attempt in range(tries):
            wait = self.delay - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            self.calls += 1
            req = urllib.request.Request(url, headers={"User-Agent": self.ua, "Accept-Encoding": "identity"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code in (429, 503) and attempt < tries - 1:
                    self._back_off(attempt, e.headers.get("Retry-After"), f"server said {e.code}")
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt == tries - 1:
                    raise
                pause = 5 * (attempt + 1)
                print(f"  network problem ({e}); retrying in {pause}s", file=sys.stderr)
                time.sleep(pause)
                continue
            err = data.get("error")
            if err and err.get("code") in ("maxlag", "ratelimited") and attempt < tries - 1:
                self._back_off(attempt, None, err.get("code"))
                continue
            if err:
                raise RuntimeError(f"Wikisource API error: {err.get('code')}: {err.get('info')}")
            return data
        raise RuntimeError("Wikisource API kept refusing; wait a while and run again (saved stories are kept).")

    def _back_off(self, attempt, retry_after, why):
        try:
            pause = float(retry_after)
        except (TypeError, ValueError):
            pause = 30 * (2 ** attempt)
        pause = min(max(pause, 15), 300)
        self.slowdowns += 1
        self.delay = min(self.delay * 1.5, 10)   # stay slower for the rest of the run
        print(f"  Wikisource asked us to slow down ({why}); waiting {pause:.0f}s, "
              f"then 1 request every {self.delay:.1f}s", file=sys.stderr)
        time.sleep(pause)

    def list_subpages(self, prefix):
        titles, cont = [], {}
        while True:
            d = self.get(action="query", list="allpages", apprefix=prefix, apnamespace="0",
                         aplimit="500", **cont)
            titles += [p["title"] for p in d["query"]["allpages"]]
            if "continue" not in d:
                return titles
            cont = d["continue"]

    def list_category(self, name):
        cat = name if name.startswith("বিষয়শ্রেণী:") else "বিষয়শ্রেণী:" + name
        titles, cont = [], {}
        while True:
            d = self.get(action="query", list="categorymembers", cmtitle=cat, cmnamespace="0",
                         cmlimit="500", **cont)
            titles += [p["title"] for p in d["query"]["categorymembers"]]
            if "continue" not in d:
                return titles
            cont = d["continue"]

    def parse(self, title):
        d = self.get(action="parse", page=title, prop="text|revid", redirects="1",
                     disablelimitreport="1", disableeditsection="1")
        p = d["parse"]
        return p["pageid"], p.get("revid"), p["title"], p["text"]


# ---------------------------------------------------------------- HTML -> paragraphs

SKIP_TAGS = {"style", "script", "sup", "table", "noscript", "figure", "figcaption"}
SKIP_CLASSES = {"ws-noexport", "noprint", "wikisource-header-template", "footertemplate",
                "pagenum", "ws-pagenum", "mw-references", "reference", "mw-editsection",
                "headertemplate", "__nop", "wst-nop", "mw-empty-elt",
                # pictures and their captions (e.g. '[ পৃঃ ৯৬' page references under illustrations)
                "img-center", "img-left", "img-right", "img-float", "pageimage", "thumb", "thumbcaption",
                "wst-img-float", "wst-figure", "ws-figure", "gallery"}
BLOCK_TAGS = {"p", "div", "li", "dd", "dt", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6",
              "center", "tr"}
VOID_TAGS = {"br", "hr", "img", "wbr", "input", "meta", "link", "source", "area", "col"}


class StoryHTML(HTMLParser):
    """Collects the readable paragraphs of a Wikisource page, dropping headers,
    footers, page numbers and footnote markers. A <hr> becomes a section break."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []          # (tag, skipping?)
        self.buf = []
        self.paras = []
        self.meta_depth = None   # inside the header's ws-author / ws-title span
        self.meta_key = None
        self.meta = {"author": "", "work": ""}
        self.links = []          # titles of wiki pages linked from the text, in order

    def skipping(self):
        return bool(self.stack) and self.stack[-1][1]

    def flush(self):
        text = "".join(self.buf)
        self.buf = []
        lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.split("\n")]
        text = "\n".join(ln for ln in lines if ln)
        if text:
            self.paras.append(text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = set((a.get("class") or "").split())
        if self.meta_depth is None and tag not in VOID_TAGS and classes & {"ws-author", "ws-title"}:
            key = "author" if "ws-author" in classes else "work"
            if not self.meta[key]:
                self.meta_key, self.meta_depth = key, len(self.stack)
        skip = self.skipping() or tag in SKIP_TAGS or bool(classes & SKIP_CLASSES) \
            or a.get("id") in ("headertemplate", "footertemplate")
        if not skip and tag == "a" and a.get("title"):
            self.links.append(a["title"])
        if not skip:
            if tag == "br":
                self.buf.append("\n")
            elif tag == "hr":
                self.flush()
                self.paras.append("---")
            elif tag in BLOCK_TAGS:
                self.flush()
        if tag not in VOID_TAGS:
            self.stack.append((tag, skip))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS and self.stack and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag in VOID_TAGS:
            return
        # pop back to the matching open tag (tolerates sloppy HTML)
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                was_skipping = self.stack[i][1]
                del self.stack[i:]
                if self.meta_depth is not None and len(self.stack) <= self.meta_depth:
                    self.meta_depth = None
                if not was_skipping and tag in BLOCK_TAGS:
                    self.flush()
                return

    def handle_data(self, data):
        if self.meta_depth is not None:
            self.meta[self.meta_key] = re.sub(r"\s+", " ", self.meta[self.meta_key] + data)
        if not self.skipping():
            self.buf.append(data)

    def result(self):
        self.flush()
        return self.paras


BN_DIGITS = "০১২৩৪৫৬৭৮৯"
NUMBER_WORDS = {"এক", "দুই", "তিন", "চার", "পাঁচ", "ছয়", "ছয়", "সাত", "আট", "নয়", "নয়", "দশ", "এগার", "এগারো",
                "বার", "বারো", "তের", "তেরো", "চোদ্দ", "চৌদ্দ", "পনের", "পনেরো", "ষোল", "ষোলো", "সতের", "সতেরো",
                "আঠার", "আঠারো", "উনিশ", "কুড়ি", "বিশ"}
MONTHS = ("বৈশাখ", "জ্যৈষ্ঠ", "আষাঢ়", "আষাঢ", "শ্রাবণ", "ভাদ্র", "আশ্বিন", "কার্তিক", "কার্ত্তিক",
          "অগ্রহায়ণ", "অগ্রহায়ণ", "পৌষ", "মাঘ", "ফাল্গুন", "চৈত্র")


PAGE_REF = re.compile(r"\[\s*পৃঃ?\s*[০-৯0-9]+\s*\]?")


def clean(s):
    s = PAGE_REF.sub("", s)
    s = s.replace("​", "").replace("‌‌", "‌").replace("﻿", "")
    s = re.sub(r"“\s+", "“", s)
    s = re.sub(r"\s+”", "”", s)
    s = re.sub(r"‘\s+", "‘", s)
    s = re.sub(r"\s+’", "’", s)
    s = re.sub(r"\s+([;,।?!])", r"\1", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


def is_break(p):
    """Lines like '* * *', '১', '২', 'II' mark a new section."""
    t = p.replace(" ", "")
    if not t:
        return True
    if re.fullmatch(r"[*＊✱•·।॥\-–—~]+", t):
        return True
    if re.fullmatch(f"[{BN_DIGITS}0-9]{{1,2}}[.।]?", t):
        return True
    if re.fullmatch(r"[IVXLC]{1,5}\.?", t):
        return True
    if t.rstrip(".।") in NUMBER_WORDS:   # chapter headings like "এক", "দুই"
        return True
    return False


def is_date_line(p):
    t = p.strip()
    return len(t) <= 30 and any(t.startswith(m) for m in MONTHS) and re.search(f"[{BN_DIGITS}]", t)


def tidy(paras, title):
    """Clean paragraphs, drop the title line, turn section markers into '---',
    and pull out a closing date line (e.g. 'অগ্রহায়ণ ১২৯৯')."""
    short_title = title.split("/")[-1].strip()
    work = re.sub(r"\s*\(.*?\)\s*", "", title.split("/")[0]).strip()
    out, dated = [], None
    for raw in paras:
        p = clean(raw)
        if p == "---" or is_break(p):
            if out and out[-1] != "---":
                out.append("---")
            continue
        if not out and p.rstrip("।") in (short_title, work):   # title lines repeated in the text
            continue
        out.append(p)
    while out and out[0] == "---":
        out.pop(0)
    while out and out[-1] == "---":
        out.pop()
    if out and is_date_line(out[-1]):
        dated = out.pop()
    return out, dated


# ---------------------------------------------------------------- paragraphs -> posts

SENT = re.compile(r"[^।?!]+[।?!]+(?:\s*[”’])?\s*")


def sentences(p):
    out = [m.group(0) for m in SENT.finditer(p)]
    rest = p[len("".join(out)):].strip()
    if rest:
        if out and re.match(r"^[”’]", rest):
            out[-1] += rest
        else:
            out.append(rest)
    return [s.strip() for s in out if s.strip()]


def split_long(s):
    if len(s) <= MAX_POST + 60:
        return [s]
    parts = re.split(r"(?<=[,;—])\s+", s)
    if max(len(x) for x in parts) > MAX_POST + 60:   # no commas to break on: fall back to spaces
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
    """Returns a list of items: {"t": text} for a post, {"brk": true} for a section break.
    Posts never cross a paragraph; poems keep their line breaks."""
    items = []
    for p in paras:
        if p == "---":
            items.append({"brk": True})
            continue
        if "\n" in p:
            lines = p.split("\n")
            if sum(len(x) for x in lines) / len(lines) > 60:   # prose with stray line breaks
                p = " ".join(lines)
        if "\n" in p:   # verse: group lines, don't re-flow
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


# ---------------------------------------------------------------- main

def read_page(html, title):
    parser = StoryHTML()
    parser.feed(html)
    paras, dated = tidy(parser.result(), title)
    chapters = list(dict.fromkeys(t for t in parser.links if t.startswith(title + "/")))
    return paras, dated, parser.meta["author"].strip(), chapters


def looks_like_contents(paras, chapters):
    """A story's main page that is only a list of its chapters (the text is on subpages)."""
    return len(chapters) >= 2 and sum(len(p) for p in paras if p != "---") < 3000


def fetch_story(api, title):
    """Fetch a page; if it is only a table of contents, fetch its chapters in order and join them."""
    pageid, revid, real_title, html = api.parse(title)
    paras, dated, page_author, chapters = read_page(html, real_title)
    n_chapters = 0
    if looks_like_contents(paras, chapters):
        joined = []
        for ch in chapters:
            _, _, _, ch_html = api.parse(ch)
            ch_paras, ch_dated, ch_author, _ = read_page(ch_html, ch)
            if joined:
                joined.append("---")
            joined += ch_paras
            dated = ch_dated or dated
            page_author = page_author or ch_author
            n_chapters += 1
        paras = joined
    return story_record(pageid, revid, real_title, paras, dated, page_author, n_chapters)


def story_record(pageid, revid, title, paras, dated, page_author, n_chapters=0, author=None):
    parts = title.split("/")
    items = to_posts(paras)
    return {
        "id": f"ws-{pageid}",
        "title": parts[-1].strip(),
        "author": page_author or author or "অজ্ঞাত",
        "collection": parts[0].strip() if len(parts) > 1 else None,
        "dated": dated,
        "source": {
            "site": "bn.wikisource.org",
            "page": title,
            "url": SITE + urllib.parse.quote(title.replace(" ", "_")),
            "revision": revid,
            "license": "Public domain",
        },
        "chapters": n_chapters or None,
        "stats": {"paragraphs": sum(1 for p in paras if p != "---"),
                  "posts": sum(1 for i in items if "t" in i),
                  "characters": sum(len(i.get("t", "")) for i in items)},
        "items": items,
    }


def write_catalog(out):
    rows = []
    for f in sorted(out.glob("ws-*.json")):
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows.append({"id": s["id"], "title": s["title"], "author": s["author"],
                     "collection": s.get("collection"), "posts": s["stats"]["posts"],
                     "file": f.name})
    rows.sort(key=lambda r: (r["author"], (r["collection"] or ""), r["title"]))
    (out / "catalog.json").write_text(json.dumps({"count": len(rows), "stories": rows},
                                                 ensure_ascii=False, indent=1), encoding="utf-8")
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description="Fetch Bengali Wikisource stories into reader-ready JSON.")
    ap.add_argument("--prefix", action="append",
                    help="Fetch every page whose title starts with this (repeatable). "
                         "Default: Tagore's Galpaguchchha volumes.")
    ap.add_argument("--category", action="append",
                    help="Fetch every page in this Wikisource category, e.g. \"ছোট গল্প\" (repeatable).")
    ap.add_argument("--page", action="append", help="Fetch one page by exact title (repeatable).")
    ap.add_argument("--pages-file", help="Fetch the page titles listed in this file, one per line "
                                         "(lines may be 'title<TAB>anything', like report.txt's FAILED list).")
    ap.add_argument("--author", default=None,
                    help="Author to use only when a page's header names none.")
    ap.add_argument("--dry-run", action="store_true",
                    help="List what would be fetched (writes inventory.tsv); download no text.")
    ap.add_argument("--out", default="stories", help="Output folder (default: stories).")
    ap.add_argument("--limit", type=int, default=0, help="Stop after this many stories (for testing).")
    ap.add_argument("--force", action="store_true", help="Fetch again even if already saved.")
    ap.add_argument("--contact", default="",
                    help="Your email or URL, added to the User-Agent as Wikimedia asks. Optional.")
    ap.add_argument("--delay", type=float, default=DELAY, help="Seconds between requests (default 1).")
    args = ap.parse_args()

    ua = "BengaliShortReader/0.1 (personal reading project" + (f"; {args.contact}" if args.contact else "") + ") python-urllib"
    api = Api(ua, args.delay)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    titles = list(args.page or [])
    if args.pages_file:
        for line in Path(args.pages_file).read_text(encoding="utf-8").splitlines():
            t = line.split("\t")[0].strip()
            if t and t not in ("FAILED", "CHECK", "none", "page") and not t.startswith(("collection", "check")):
                titles.append(t)
    for cat in (args.category or []):
        found = api.list_category(cat)
        print(f"Category {cat}  →  {len(found)} pages")
        titles += found
    if not titles:
        for pre in (args.prefix or DEFAULT_PREFIXES):
            found = [t for t in api.list_subpages(pre) if t != pre.rstrip("/")]
            print(f"{pre}  →  {len(found)} pages")
            titles += found
    titles = list(dict.fromkeys(titles))   # drop repeats, keep order
    if args.limit:
        titles = titles[:args.limit]

    # map of already-saved stories by Wikisource page title
    saved = {}
    for f in out.glob("ws-*.json"):
        try:
            saved[json.loads(f.read_text(encoding="utf-8"))["source"]["page"]] = f
        except (OSError, json.JSONDecodeError, KeyError):
            pass

    if args.dry_run:
        groups = {}
        for t in titles:
            groups.setdefault(t.split("/")[0] if "/" in t else "(single pages)", []).append(t)
        rows = ["collection\tstories\talready saved"]
        for g, ts in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            rows.append(f"{g}\t{len(ts)}\t{sum(1 for t in ts if t in saved)}")
        (out / "inventory.tsv").write_text("\n".join(rows) + "\n\npage\n" + "\n".join(titles) + "\n", encoding="utf-8")
        new = sum(1 for t in titles if t not in saved)
        print(f"\n{len(titles)} pages in {len(groups)} collections; {new} not saved yet "
              f"(about {max(1, round(new * args.delay * 1.3 / 60))} min to fetch).")
        print(f"Top collections:")
        for r in rows[1:16]:
            print("  " + r.replace("\t", "  ·  "))
        print(f"Full list written to {out / 'inventory.tsv'}")
        return

    done = skipped = failed = 0
    warnings, failures = [], []
    todo = sum(1 for t in titles if args.force or t not in saved)
    started = time.time()
    fails_in_a_row = 0
    for i, title in enumerate(titles, 1):
        label = title.split("/")[-1]
        if title in saved and not args.force:
            skipped += 1
            continue
        try:
            rec = fetch_story(api, title)
            if rec["author"] == "অজ্ঞাত" and args.author:
                rec["author"] = args.author
            fails_in_a_row = 0
        except KeyboardInterrupt:
            print("\nStopped. Everything saved so far is kept; run the same command to continue.")
            break
        except Exception as e:  # keep going; report at the end
            failed += 1
            fails_in_a_row += 1
            failures.append(f"{title}\t{type(e).__name__}: {e}")
            print(f"[{i}/{len(titles)}] FAILED {label}: {e}", file=sys.stderr)
            if "429" in str(e) and fails_in_a_row < 3:
                print("  Pausing 2 minutes to let Wikisource's limit reset…", file=sys.stderr)
                time.sleep(120)
            if fails_in_a_row >= 3:
                print("Three failures in a row: stopping so we don't keep hitting Wikisource. "
                      "Wait 10–15 minutes and run the same command; it continues where it stopped.", file=sys.stderr)
                break
            continue
        path = out / f"{rec['id']}.json"
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        done += 1
        st = rec["stats"]
        spent = time.time() - started
        eta = spent / (done + failed) * (todo - done - failed)
        print(f"[{i}/{len(titles)}] {label} ({rec['author']}): {st['posts']} posts   "
              f"{fmt_time(spent)} elapsed, ~{fmt_time(eta)} left")
        if st["characters"] < 800:
            warnings.append(f"{label}: only {st['characters']} characters — page may be incomplete or unproofread")

    print(f"\nTook {fmt_time(time.time() - started)} for {api.calls} requests"
          + (f"; Wikisource asked us to slow down {api.slowdowns} time(s)." if api.slowdowns else "; no slow-down requests."))

    n = write_catalog(out)
    warnings += find_duplicates(out)
    report = out / "report.txt"
    report.write_text("FAILED\n" + ("\n".join(failures) or "none") + "\n\nCHECK\n" + ("\n".join(warnings) or "none") + "\n",
                      encoding="utf-8")
    print(f"\nSaved {done}, skipped {skipped} already saved, failed {failed}. Catalog now lists {n} stories in {out}/")
    for w in warnings:
        print("  check:", w)
    if failures or warnings:
        print(f"Details written to {report}")


def fmt_time(sec):
    sec = int(sec)
    return f"{sec // 60}m {sec % 60:02d}s" if sec >= 60 else f"{sec}s"


def find_duplicates(out):
    """Warn when two story files share most of their text (a Wikisource page showing the wrong story)."""
    texts = {}
    for f in out.glob("ws-*.json"):
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        texts[s["title"]] = {i["t"] for i in s["items"] if "t" in i}
    names, warns = sorted(texts), []
    for a_i, a in enumerate(names):
        for b in names[a_i + 1:]:
            A, B = texts[a], texts[b]
            if A and B and len(A & B) / min(len(A), len(B)) > 0.5:
                warns.append(f"{a} and {b}: mostly the same text — one Wikisource page likely shows the wrong story")
    return warns


if __name__ == "__main__":
    main()
