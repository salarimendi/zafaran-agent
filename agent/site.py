"""Site knowledge: read-only sync of terms/posts/products, strict term resolution, slug checks, new-term creation."""
import csv
import html
import json
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher, get_close_matches
from urllib.parse import unquote

import requests
import yaml

from .common import StepError, confirm, ensure, log_action, norm
from .config import ROOT, load_config
from .wp_client import WPClient

KNOW = ROOT / "knowledge"
SITE = KNOW / "site"
NEW_TERMS = KNOW / "new_terms.yaml"

# tax -> (REST route, auth, label)
TAX = {
    "category": ("wp/v2/categories", "wp", "post category"),
    "post_tag": ("wp/v2/tags", "wp", "post tag"),
    "product_cat": ("wc/v3/products/categories", "wc", "product category"),
    "product_tag": ("wc/v3/products/tags", "wc", "product tag"),
}


def fetch_all(c, route, auth="wp", params=None, per_page=100):
    out, page = [], 1
    while True:
        p = dict(params or {})
        p.update(per_page=per_page, page=page)
        r = c.get(route, auth=auth, params=p)
        out += ensure(r, f"list {route}")
        if page >= int(r.headers.get("X-WP-TotalPages", "1")):
            return out
        page += 1


def _t(s):
    return html.unescape(s or "")


# ---------- terms ----------
def all_terms(c, tax):
    cache = c.__dict__.setdefault("_terms", {})
    if tax not in cache:
        route, auth, _ = TAX[tax]
        cache[tax] = fetch_all(c, route, auth)
    return cache[tax]


def resolve_term(c, tax, value):
    """Return the id of an EXISTING term (matched by name or slug). Never creates anything."""
    _, _, label = TAX[tax]
    terms = all_terms(c, tax)
    v = norm(value)
    hits = [t for t in terms if norm(t.get("name", "")) == v or norm(unquote(t.get("slug", ""))) == v]
    if len(hits) == 1:
        return hits[0]["id"]
    if len(hits) > 1:
        raise StepError(f"{label} '{value}' is ambiguous (ids {[h['id'] for h in hits]}); write its slug instead")
    cands = {}
    for t in terms:
        cands[_t(t.get("name"))] = t
        cands[unquote(t.get("slug", ""))] = t
    close = get_close_matches(str(value), list(cands), n=3, cutoff=0.5)
    sug = sorted({f"{_t(cands[k].get('name'))} [{unquote(cands[k].get('slug', ''))}]" for k in close})
    names = ", ".join(_t(t.get("name")) for t in terms[:15]) + (" ..." if len(terms) > 15 else "")
    msg = f"{label} '{value}' does NOT exist on the site."
    if sug:
        msg += f" Did you mean: {'; '.join(sug)}?"
    msg += f" Existing: {names or '(none yet)'}. To add a new one: write it in knowledge/new_terms.yaml and run new_terms.bat."
    raise StepError(msg)


# ---------- slugs / urls ----------
def slug_in_use(c, kind, slug):
    if kind == "product":
        items = ensure(c.get("wc/v3/products", auth="wc", params={"slug": slug, "status": "any"}), "check product slug")
        return [(i["id"], i.get("status"), i.get("permalink")) for i in items]
    out = []
    for route in ("wp/v2/posts", "wp/v2/pages"):
        for i in ensure(c.get(route, auth="wp", params={"slug": slug, "status": "any"}), "check slug"):
            out.append((i["id"], i.get("status"), i.get("link")))
    return out


def load_snapshot():
    f = SITE / "snapshot.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def expected_url(kind, slug):
    """Infer the permalink pattern from already-published items in the last sync."""
    snap = load_snapshot()
    if not snap:
        return None
    items = snap["products"] if kind == "product" else snap["posts"]
    pref, total = Counter(), 0
    for it in items:
        s, link = it.get("slug", ""), unquote(it.get("url", "") or "").rstrip("/")
        if it.get("status") == "publish" and s and link.endswith("/" + unquote(s)):
            pref[link[: -len(unquote(s)) - 1]] += 1
            total += 1
    if not total:
        return None
    best, n = pref.most_common(1)[0]
    return f"{best}/{slug}/" if n / total >= 0.8 else None


def similar_names(kind, text, self_id=None, cutoff=0.85):
    """Names/titles in the last sync that look like `text` (warning only)."""
    snap = load_snapshot()
    if not snap:
        return []
    items, key = (snap["products"], "name") if kind == "product" else (snap["posts"], "title")
    out = []
    for it in items:
        if self_id and it["id"] == self_id:
            continue
        r = SequenceMatcher(None, norm(text), norm(it[key])).ratio()
        if r >= cutoff:
            out.append((it["id"], it[key], round(r, 2)))
    return out


# ---------- sync ----------
def _write_csv(name, header, rows):
    with open(SITE / name, "w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel shows Persian
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _paths(terms):
    by = {t["id"]: t for t in terms}

    def path(t):
        names, seen = [t["name"]], set()
        while t.get("parent") and t["parent"] in by and t["parent"] not in seen:
            seen.add(t["parent"])
            t = by[t["parent"]]
            names.append(t["name"])
        return " > ".join(reversed(names))
    return {t["id"]: path(t) for t in terms}


def sync():
    cfg = load_config()
    c = WPClient(cfg)
    c.detect_mode()
    SITE.mkdir(parents=True, exist_ok=True)
    print(f"Syncing {cfg.site_url} (read-only)...")
    snap = {"fetched_at": datetime.now().isoformat(timespec="seconds"), "site": cfg.site_url}
    # terms
    links = {}
    for tax, wproute in (("product_cat", "wp/v2/product_cat"), ("product_tag", "wp/v2/product_tag")):
        try:  # optional: public term links
            links[tax] = {i["id"]: i.get("link", "") for i in fetch_all(c, wproute, "wp", {"_fields": "id,link"})}
        except (StepError, requests.RequestException):
            links[tax] = {}
    term_rows = []
    for tax in TAX:
        terms = [{"id": t["id"], "name": _t(t.get("name")), "slug": unquote(t.get("slug", "")),
                  "parent": t.get("parent", 0), "count": t.get("count", 0),
                  "link": t.get("link") or links.get(tax, {}).get(t["id"], "")} for t in all_terms(c, tax)]
        snap[tax] = terms
        pth = _paths(terms)
        term_rows += [[tax, t["id"], t["name"], t["slug"], pth[t["id"]], t["count"], t["link"]] for t in terms]
        print(f"  {TAX[tax][2]}: {len(terms)}")
    _write_csv("terms.csv", ["type", "id", "name", "slug", "path", "count", "url"], term_rows)
    cat_names = {t["id"]: t["name"] for t in snap["category"]}
    # posts + pages
    posts = fetch_all(c, "wp/v2/posts", "wp", {"status": "any", "_fields": "id,slug,link,status,title,categories,date,modified"})
    snap["posts"] = [{"id": p["id"], "status": p["status"], "title": _t(p["title"]["rendered"]), "slug": unquote(p.get("slug", "")),
                      "url": p.get("link", ""), "categories": [cat_names.get(i, str(i)) for i in p.get("categories", [])],
                      "date": p.get("date", ""), "modified": p.get("modified", "")} for p in posts]
    pages = fetch_all(c, "wp/v2/pages", "wp", {"status": "any", "_fields": "id,slug,link,status,title,modified"})
    snap["pages"] = [{"id": p["id"], "status": p["status"], "title": _t(p["title"]["rendered"]),
                      "slug": unquote(p.get("slug", "")), "url": p.get("link", "")} for p in pages]
    # products
    prods = fetch_all(c, "wc/v3/products", "wc", {"status": "any"})
    snap["products"] = [{"id": p["id"], "status": p["status"], "name": _t(p["name"]), "sku": p.get("sku", ""),
                         "slug": unquote(p.get("slug", "")), "url": p.get("permalink", ""), "price": p.get("price", ""),
                         "categories": [_t(x["name"]) for x in p.get("categories", [])]} for p in prods]
    print(f"  posts: {len(posts)}  pages: {len(pages)}  products: {len(prods)}")
    _write_csv("posts.csv", ["id", "status", "title", "slug", "url", "categories", "date", "modified"],
               [[p["id"], p["status"], p["title"], p["slug"], p["url"], "; ".join(p["categories"]), p["date"], p["modified"]]
                for p in snap["posts"]])
    _write_csv("pages.csv", ["id", "status", "title", "slug", "url"],
               [[p["id"], p["status"], p["title"], p["slug"], p["url"]] for p in snap["pages"]])
    _write_csv("products.csv", ["id", "status", "sku", "name", "slug", "url", "price", "categories"],
               [[p["id"], p["status"], p["sku"], p["name"], p["slug"], p["url"], p["price"], "; ".join(p["categories"])]
                for p in snap["products"]])
    (SITE / "snapshot.json").write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    # compact index for internal linking / pasting into any chat model
    L = [f"# Site index ({snap['fetched_at']}) - {cfg.site_url}", "", "## Product categories"]
    L += [f"- {t['name']} | {t['slug']} | {t['link']}" for t in snap["product_cat"]]
    L += ["", "## Post categories"] + [f"- {t['name']} | {t['slug']} | {t['link']}" for t in snap["category"]]
    L += ["", "## Published articles (title | url | categories)"]
    L += [f"- {p['title']} | {p['url']} | {', '.join(p['categories'])}" for p in snap["posts"] if p["status"] == "publish"]
    L += ["", "## Published products (name | url | sku)"]
    L += [f"- {p['name']} | {p['url']} | {p['sku']}" for p in snap["products"] if p["status"] == "publish"]
    L += ["", "## Pages (title | url)"] + [f"- {p['title']} | {p['url']}" for p in snap["pages"] if p["status"] == "publish"]
    (SITE / "linking_index.md").write_text("\n".join(L), encoding="utf-8")
    print(f"\nSaved to {SITE}\\ : terms.csv, posts.csv, pages.csv, products.csv, linking_index.md, snapshot.json")


# ---------- new terms (explicit, confirmed) ----------
TEMPLATE = """# هر دسته/برچسب جدید را اینجا اضافه کنید، بعد new_terms.bat را اجرا کنید.
# tax: category (دستهٔ مقاله) | post_tag (برچسب مقاله) | product_cat (دستهٔ محصول) | product_tag (برچسب محصول)
# slug: لاتین و کوچک. parent: اختیاری، slug دستهٔ والد (که از قبل وجود دارد).
# مثال (علامت # را بردارید):
# - tax: product_cat
#   name: زعفران پوشال
#   slug: poushal-saffron
#   parent: saffron
[]
"""


def run_newterms(go):
    if not NEW_TERMS.exists():
        KNOW.mkdir(exist_ok=True)
        NEW_TERMS.write_text(TEMPLATE, encoding="utf-8")
        print(f"Created {NEW_TERMS}. Fill it in, then run again.")
        return
    items = yaml.safe_load(NEW_TERMS.read_text(encoding="utf-8")) or []
    if not items:
        print("knowledge\\new_terms.yaml has no entries.")
        return
    from .common import slug_error
    cfg = load_config()
    c = WPClient(cfg)
    c.detect_mode()
    plan = []
    for it in items:
        tax, name, slug = it.get("tax"), str(it.get("name", "")).strip(), str(it.get("slug", "")).strip()
        try:
            if tax not in TAX:
                raise StepError(f"tax must be one of {list(TAX)}")
            if not name:
                raise StepError("name is required")
            if slug_error(slug):
                raise StepError(slug_error(slug))
            terms = all_terms(c, tax)
            if any(unquote(t.get("slug", "")) == slug for t in terms) or any(norm(t["name"]) == norm(name) for t in terms):
                print(f"= {tax} '{name}' [{slug}]: already exists - skipped")
                continue
            parent = 0
            if it.get("parent"):
                p = [t for t in terms if unquote(t.get("slug", "")) == str(it["parent"])]
                if not p:
                    raise StepError(f"parent slug '{it['parent']}' not found")
                parent = p[0]["id"]
            print(f"+ {tax} '{name}' [{slug}]" + (f" under '{it['parent']}'" if parent else "") + "  -> will be CREATED")
            plan.append((tax, name, slug, parent))
        except StepError as e:
            print(f"! {tax} '{name}': {e}")
    if not plan:
        print("Nothing to create.")
        return
    if not go:
        print("\nDry run. Run new_terms.bat to create.")
        return
    if not confirm(len(plan), "new term(s)"):
        print("Cancelled.")
        return
    for tax, name, slug, parent in plan:
        body = {"name": name, "slug": slug}
        if parent:
            body["parent"] = parent
        try:
            d = ensure(c.request("POST", TAX[tax][0], auth=TAX[tax][1], json=body), f"create {tax}")
            print(f"  created {tax} '{name}' id={d['id']}")
            log_action("term_created", {"tax": tax, "name": name, "slug": slug, "id": d["id"]})
        except StepError as e:
            print(f"  FAILED {name}: {e}")
    print("\nRefreshing site snapshot...")
    sync()
