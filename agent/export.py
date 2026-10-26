"""Export full article text as Markdown (+ index.csv with SEO stats). Public content only."""
import csv
import html
import re
from urllib.parse import unquote, urlparse

import yaml

from .common import ensure
from .config import ROOT, load_config
from .htmlmd import html_to_md
from .site import all_terms, fetch_all
from .wp_client import WPClient

OUT = ROOT / "knowledge" / "articles"
FILE_RE = re.compile(r"^\d+(-[a-z0-9-]+)?\.md$")


def _stats(raw_html, host):
    imgs = re.findall(r"<img\b[^>]*>", raw_html or "", flags=re.I)
    no_alt = [i for i in imgs if not re.search(r'\balt="[^"]+"', i, flags=re.I)]
    hrefs = [h for h in re.findall(r'<a\b[^>]*\bhref="([^"]+)"', raw_html or "", flags=re.I)
             if not h.startswith(("#", "mailto:", "tel:", "javascript:"))]
    internal = [h for h in hrefs if h.startswith("/") or host in h]
    words = len(re.sub(r"<[^>]+>", " ", raw_html or "").split())
    return {"words": words, "h2": len(re.findall(r"<h2\b", raw_html or "", flags=re.I)),
            "images": len(imgs), "images_no_alt": len(no_alt),
            "links_internal": len(internal), "links_external": len(hrefs) - len(internal)}


def export_articles(include_all=False):
    cfg = load_config()
    host = urlparse(cfg.site_url).netloc
    c = WPClient(cfg)
    c.detect_mode()
    print(f"Exporting {'ALL statuses' if include_all else 'published'} articles from {cfg.site_url} (read-only)...")
    posts = fetch_all(c, "wp/v2/posts", "wp",
                      {"status": "any" if include_all else "publish", "context": "edit",
                       "_fields": "id,slug,link,status,title,date,modified,content,categories,tags,meta"},
                      per_page=25)
    cats = {t["id"]: html.unescape(t["name"]) for t in all_terms(c, "category")}
    tags = {t["id"]: html.unescape(t["name"]) for t in all_terms(c, "post_tag")}
    OUT.mkdir(parents=True, exist_ok=True)
    rows, keep, total_words = [], set(), 0
    for p in posts:
        meta = p.get("meta") if isinstance(p.get("meta"), dict) else {}
        body_html = (p.get("content") or {}).get("rendered") or (p.get("content") or {}).get("raw") or ""
        st = _stats(body_html, host)
        slug = unquote(p.get("slug", ""))
        key = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")[:60]
        fname = f"{p['id']}-{key}.md" if key else f"{p['id']}.md"
        title = html.unescape((p.get("title") or {}).get("rendered", ""))
        fm = {"title": title, "slug": slug, "url": p.get("link", ""), "status": p.get("status", ""),
              "date": p.get("date", ""), "modified": p.get("modified", ""),
              "categories": [cats.get(i, str(i)) for i in p.get("categories", [])],
              "tags": [tags.get(i, str(i)) for i in p.get("tags", [])],
              "focus_keyword": meta.get("_yoast_wpseo_focuskw", ""),
              "seo_title": meta.get("_yoast_wpseo_title", ""),
              "meta_description": meta.get("_yoast_wpseo_metadesc", ""), **st}
        head = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, width=10000)
        (OUT / fname).write_text(f"---\n{head}---\n\n{html_to_md(body_html)}", encoding="utf-8")
        keep.add(fname)
        total_words += st["words"]
        rows.append([p["id"], fm["status"], title, slug, fm["url"], fm["date"], fm["modified"], st["words"], st["h2"],
                     st["images"], st["images_no_alt"], st["links_internal"], st["links_external"],
                     "; ".join(fm["categories"]), "; ".join(fm["tags"]), fm["focus_keyword"],
                     len(fm["seo_title"] or title), len(fm["meta_description"]), fname])
    for f in OUT.glob("*.md"):  # keep the folder an exact mirror
        if FILE_RE.match(f.name) and f.name not in keep:
            f.unlink()
    with open(OUT / "index.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "status", "title", "slug", "url", "date", "modified", "words", "h2", "images", "images_no_alt",
                    "links_internal", "links_external", "categories", "tags", "focus_keyword", "title_len",
                    "meta_len", "file"])
        w.writerows(rows)
    size = sum(f.stat().st_size for f in OUT.glob("*.md")) // 1024
    print(f"\n{len(rows)} articles, ~{total_words} words, {size} KB of Markdown -> {OUT}")
    print("Also written: index.csv (SEO stats per article).")
    miss = sum(1 for r in rows if not r[15])
    print(f"Articles without a focus keyword: {miss}; without meta description: {sum(1 for r in rows if r[17] == 0)}")
