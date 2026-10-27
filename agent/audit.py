"""Free local SEO audit of the exported articles. Writes reports/seo_audit_*.csv + .md"""
import csv
from collections import Counter, defaultdict
from datetime import datetime
from urllib.parse import unquote, urlparse

import re

from .common import norm
from .config import ROOT
from .corpus import h2_list, paragraphs, read_articles, strip_md

W = {"H": 3, "M": 2, "L": 1}


def _path(u):
    return unquote(urlparse(u).path).rstrip("/")


def audit():
    arts = read_articles()
    if not arts:
        print("No exported articles. Run export_articles.bat first.")
        return
    def bare(h):
        return h.lower().removeprefix("www.")
    host = bare(urlparse(arts[0].get("url", "")).netloc)
    by_path = {_path(a.get("url", "")): a for a in arts if a.get("url")}
    inbound = Counter()
    for a in arts:
        for u in re.findall(r"\]\((https?://[^)\s]+|/[^)\s]*)\)", a["body"]):
            pu = urlparse(u)
            if bare(pu.netloc) in ("", host):
                t = by_path.get(_path(u))
                if t and t is not a:
                    inbound[t["file"]] += 1
    kw_groups, meta_groups, title_groups = defaultdict(list), defaultdict(list), defaultdict(list)
    for a in arts:
        if a.get("focus_keyword"):
            kw_groups[norm(a["focus_keyword"])].append(a)
        if a.get("meta_description"):
            meta_groups[norm(a["meta_description"])].append(a)
        title_groups[norm(a.get("title", ""))].append(a)
    rows, codes = [], Counter()
    for a in arts:
        iss = []

        def add(sev, code, text):
            iss.append((sev, code, text))
            codes[code] += 1
        title, meta, fk = a.get("title", ""), a.get("meta_description") or "", a.get("focus_keyword") or ""
        seo_title = a.get("seo_title") or title
        words = a.get("words", 0)
        h2 = h2_list(a["body"])
        if not fk:
            add("H", "no_focus_keyword", "no focus keyword")
        if not meta:
            add("H", "no_meta", "no meta description")
        elif not 110 <= len(meta) <= 160:
            add("L", "meta_length", f"meta length {len(meta)}")
        if len(seo_title) > 60:
            add("L", "title_long", f"title {len(seo_title)} chars")
        if words < 300:
            add("H", "very_short", f"only {words} words")
        elif words < 600:
            add("M", "short", f"{words} words")
        if not h2:
            add("M", "no_h2", "no H2 headings")
        if a.get("images_no_alt", 0):
            add("M", "img_no_alt", f"{a['images_no_alt']} image(s) without alt")
        if a.get("links_internal", 0) < 2:
            add("M", "few_internal_links", f"{a.get('links_internal', 0)} internal link(s)")
        if not inbound[a["file"]]:
            add("M", "no_inbound_links", "no other article links to it")
        if fk:
            first = " ".join(strip_md(" ".join(paragraphs(a["body"])[:2])).split()[:120])
            if norm(fk) not in norm(seo_title) and norm(fk) not in norm(title):
                add("M", "kw_not_in_title", "focus keyword not in title")
            if meta and norm(fk) not in norm(meta):
                add("M", "kw_not_in_meta", "focus keyword not in meta")
            if norm(fk) not in norm(first):
                add("M", "kw_not_in_intro", "focus keyword not in intro")
            if h2 and not any(norm(fk) in norm(h) for h in h2):
                add("L", "kw_not_in_h2", "focus keyword in no H2")
            if len(kw_groups[norm(fk)]) > 1:
                add("H", "kw_cannibalization", "same focus keyword as ids " +
                    ",".join(str(x["id"]) for x in kw_groups[norm(fk)] if x is not a))
        if meta and len(meta_groups[norm(meta)]) > 1:
            add("M", "dup_meta", "meta duplicated")
        if len(title_groups[norm(title)]) > 1:
            add("H", "dup_title", "title duplicated")
        if re.search(r"[^\x00-\x7f]", a.get("slug", "")):
            add("L", "non_latin_slug", "slug is not Latin")
        score = sum(W[s] for s, _, _ in iss)
        rows.append((score, a, iss))
    rows.sort(key=lambda r: -r[0])
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    with open(out / f"seo_audit_{ts}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["score", "id", "title", "url", "words", "issues", "file"])
        for sc, a, iss in rows:
            w.writerow([sc, a.get("id"), a.get("title"), a.get("url"), a.get("words"),
                        " | ".join(f"[{s}] {t}" for s, _, t in iss), a["file"]])
    L = [f"# SEO audit {ts} - {len(arts)} articles", "", "## Issue counts"] + \
        [f"- {c}: {n}" for c, n in codes.most_common()] + ["", "## Worst 15 (fix first)"]
    for sc, a, iss in rows[:15]:
        L.append(f"- ({sc}) {a.get('title')} - id {a.get('id')} - " + "; ".join(t for _, _, t in iss[:6]))
    (out / f"seo_audit_{ts}.md").write_text("\n".join(L), encoding="utf-8")
    print(f"{len(arts)} articles audited. Issue counts:")
    for c, n in codes.most_common():
        print(f"  {c}: {n}")
    print("\nWorst 5:")
    for sc, a, iss in rows[:5]:
        print(f"  ({sc}) {a.get('title')} [id {a.get('id')}]")
    print(f"\nFull list: reports\\seo_audit_{ts}.csv (open in Excel) and .md")
    return {"n": len(arts), "codes": dict(codes), "worst": [(a.get("id"), a.get("title"), sc) for sc, a, _ in rows[:5]]}
