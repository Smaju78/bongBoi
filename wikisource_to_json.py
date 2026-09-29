#!/usr/bin/env python3
"""
Fetch stories from Bengali Wikisource and save each one as a JSON file,
already split into short posts for the Bengali Short Reader.

Needs only Python 3.8+ (no extra packages).

Examples
  # Tagore's Galpaguchchha volumes that exist on Wikisource (default)
  python wikisource_to_json.py

  # Try just 3 stories first
  python wikisource_to_json.py --limit 3

  # One story by its Wikisource page title
  python wikisource_to_json.py --page "গল্পগুচ্ছ (প্রথম খণ্ড)/কাবুলিওয়ালা"

  # Any other collection: every subpage under a prefix
  python wikisource_to_json.py --prefix "গল্পগুচ্ছ (তৃতীয় খণ্ড)/" --author "রবীন্দ্রনাথ ঠাকুর"

Output (default folder: ./stories)
  stories/<id>.json   one file per story
  stories/catalog.json  list of all stories in the folder

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
DELAY = 1.0             # seconds between API calls, to be polite to Wikimedia


# ---------------------------------------------------------------- API

class Api:
    def __init__(self, user_agent, delay=DELAY):
        self.ua = user_agent
        self.delay = delay
        self.last = 0.0

    def get(self, **params):
        params.update(format="json", formatversion="2", maxlag="5")
        url = API + "?" + urllib.parse.urlencode(params)
        for attempt in range(4):
            wait = self.delay - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            req = urllib.request.Request(url, headers={"User-Agent": self.ua})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read().decode("utf-8"))
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == 3:
                    raise
                print(f"  network problem ({e}); retrying in {5 * (attempt + 1)}s", file=sys.stderr)
                time.sleep(5 * (attempt + 1))
                continue
            err = data.get("error")
            if err and err.get("code") == "maxlag":
                time.sleep(5 * (attempt + 1))
                continue
            if err:
                raise RuntimeError(f"Wikisource API error: {err.get('code')}: {err.get('info')}")
            return data
        raise RuntimeError("Wikisource API kept failing; try again later.")

    def list_subpages(self, prefix):
        titles, cont = [], {}
        while True:
            d = self.get(action="query", list="allpages", apprefix=prefix, apnamespace="0",
                         aplimit="500", **cont)
            titles += [p["title"] for p in d["query"]["allpages"]]
            if "continue" not in d:
                return titles
            cont = d["continue"]

    def parse(self, title):
        d = self.get(action="parse", page=title, prop="text|revid", redirects="1",
                     disablelimitreport="1", disableeditsection="1")
        p = d["parse"]
        return p["pageid"], p.get("revid"), p["title"], p["text"]


# ---------------------------------------------------------------- HTML -> paragraphs

SKIP_TAGS = {"style", "script", "sup", "table", "noscript"}
SKIP_CLASSES = {"ws-noexport", "noprint", "wikisource-header-template", "footertemplate",
                "pagenum", "ws-pagenum", "mw-references", "reference", "mw-editsection",
                "headertemplate", "__nop", "wst-nop", "mw-empty-elt"}
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
        skip = self.skipping() or tag in SKIP_TAGS or bool(classes & SKIP_CLASSES) \
            or a.get("id") in ("headertemplate", "footertemplate")
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
                if not was_skipping and tag in BLOCK_TAGS:
                    self.flush()
                return

    def handle_data(self, data):
        if not self.skipping():
            self.buf.append(data)

    def result(self):
        self.flush()
        return self.paras


BN_DIGITS = "০১২৩৪৫৬৭৮৯"
MONTHS = ("বৈশাখ", "জ্যৈষ্ঠ", "আষাঢ়", "আষাঢ", "শ্রাবণ", "ভাদ্র", "আশ্বিন", "কার্তিক", "কার্ত্তিক",
          "অগ্রহায়ণ", "অগ্রহায়ণ", "পৌষ", "মাঘ", "ফাল্গুন", "চৈত্র")


def clean(s):
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
    return False


def is_date_line(p):
    t = p.strip()
    return len(t) <= 30 and any(t.startswith(m) for m in MONTHS) and re.search(f"[{BN_DIGITS}]", t)


def tidy(paras, title):
    """Clean paragraphs, drop the title line, turn section markers into '---',
    and pull out a closing date line (e.g. 'অগ্রহায়ণ ১২৯৯')."""
    short_title = title.split("/")[-1].strip()
    out, dated = [], None
    for raw in paras:
        p = clean(raw)
        if p == "---" or is_break(p):
            if out and out[-1] != "---":
                out.append("---")
            continue
        if not out and p == short_title:
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

def story_record(pageid, revid, title, html, author):
    parser = StoryHTML()
    parser.feed(html)
    paras, dated = tidy(parser.result(), title)
    parts = title.split("/")
    items = to_posts(paras)
    return {
        "id": f"ws-{pageid}",
        "title": parts[-1].strip(),
        "author": author,
        "collection": parts[0].strip() if len(parts) > 1 else None,
        "dated": dated,
        "source": {
            "site": "bn.wikisource.org",
            "page": title,
            "url": SITE + urllib.parse.quote(title.replace(" ", "_")),
            "revision": revid,
            "license": "Public domain",
        },
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
    rows.sort(key=lambda r: ((r["collection"] or ""), r["title"]))
    (out / "catalog.json").write_text(json.dumps({"count": len(rows), "stories": rows},
                                                 ensure_ascii=False, indent=1), encoding="utf-8")
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description="Fetch Bengali Wikisource stories into reader-ready JSON.")
    ap.add_argument("--prefix", action="append",
                    help="Fetch every page whose title starts with this (repeatable). "
                         "Default: Tagore's Galpaguchchha volumes.")
    ap.add_argument("--page", action="append", help="Fetch one page by exact title (repeatable).")
    ap.add_argument("--author", default=DEFAULT_AUTHOR, help="Author name to store with each story.")
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
    if not titles:
        for pre in (args.prefix or DEFAULT_PREFIXES):
            found = [t for t in api.list_subpages(pre) if t != pre.rstrip("/")]
            print(f"{pre}  →  {len(found)} pages")
            titles += found
    if args.limit:
        titles = titles[:args.limit]

    # map of already-saved stories by Wikisource page title
    saved = {}
    for f in out.glob("ws-*.json"):
        try:
            saved[json.loads(f.read_text(encoding="utf-8"))["source"]["page"]] = f
        except (OSError, json.JSONDecodeError, KeyError):
            pass

    done = skipped = failed = 0
    warnings, failures = [], []
    for i, title in enumerate(titles, 1):
        label = title.split("/")[-1]
        if title in saved and not args.force:
            skipped += 1
            continue
        try:
            pageid, revid, real_title, html = api.parse(title)
            rec = story_record(pageid, revid, real_title, html, args.author)
        except Exception as e:  # keep going; report at the end
            failed += 1
            failures.append(f"{title}\t{type(e).__name__}: {e}")
            print(f"[{i}/{len(titles)}] FAILED {label}: {e}", file=sys.stderr)
            continue
        path = out / f"{rec['id']}.json"
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        done += 1
        st = rec["stats"]
        print(f"[{i}/{len(titles)}] {label}: {st['posts']} posts")
        if st["characters"] < 800:
            warnings.append(f"{label}: only {st['characters']} characters — page may be incomplete or unproofread")

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
