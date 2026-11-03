"""Stage 3 - keyword bank, keyword -> target page map, cannibalization, content gaps. Code only (no AI), read-only.
Inputs (all already produced by earlier stages): site snapshot (sync), exported articles (export), Search Console import (gsc).
Matching is done by token coverage with IDF weights, so a rare word like «نگین» matters more than «زعفران» that is on every page."""
import csv
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime

from .common import DIGITS, norm
from .config import ROOT
from .corpus import h2_list, read_articles
from .gsc import brand_terms, load as gsc_load, saved as gsc_saved
from .site import load_snapshot

KW = ROOT / "knowledge" / "keywords"
MANUAL = KW / "manual.txt"
ANALYSIS = KW / "analysis.json"
REP = ROOT / "reports"

STOP = set("""و در به از که را با برای این آن یک تا یا های ها هم است بود شد می بر هر چه چی کدام کدوم بی تر ترین
the a an of in for and to with""".split())
# words that describe what the searcher wants, not the topic; they count less when matching pages
COMMERCIAL = {"خرید", "قیمت", "فروش", "عمده", "ارزان", "سفارش", "فروشگاه", "اینترنتی", "بهترین", "مرغوب", "اصل", "تخفیف", "کیلویی", "گرمی", "مثقال", "مثقالی"}
INFO = {"خواص", "فواید", "طرز", "تهیه", "روش", "نحوه", "چیست", "چگونه", "چطور", "آموزش", "مصرف", "تشخیص", "نگهداری", "مضرات", "عوارض",
        "کاربرد", "انواع", "تفاوت", "مقایسه", "دمنوش", "پخت", "درست", "راهنما", "اصالت", "تقلبی", "چیه"}
MODIFIERS = COMMERCIAL | INFO
KIND_FA = {"article": "مقاله", "product": "محصول", "product_cat": "دستهٔ محصول", "category": "دستهٔ مقاله", "page": "صفحه"}
INTENT_FA = {"commercial": "خرید", "info": "اطلاعاتی", "brand": "برند", "unknown": "نامشخص"}
STRONG, WEAK = 0.8, 0.5


# ------------------------------------------------------------------ text
def tokens(text):
    s = norm(text).translate(DIGITS)
    s = re.sub(r"[\u064B-\u065F\u0640]", "", s).replace("ة", "ه").replace("أ", "ا").replace("إ", "ا").replace("ٱ", "ا")
    out = []
    for t in re.findall(r"[\w]+", s):
        if t.endswith("ها") and len(t) > 4:  # زعفرانها -> زعفران  (with ZWNJ the word is already split)
            t = t[:-2]
        if t in STOP or len(t) < 2 or t.isdigit():
            continue
        out.append(t)
    return out


def tset(text):
    return set(tokens(text))


# synonyms collapse to one token so «قیمت X» and «خرید X», «خواص X» and «فواید X» are seen as the same topic;
# different sub-intents («خواص X» vs «تشخیص X») stay different, which is what keeps unrelated pages from being called rivals
CANON = {**{w: "§خرید" for w in COMMERCIAL}, "فواید": "خواص", "مزایا": "خواص", "چیه": "چیست", "چطور": "چگونه", "نحوه": "روش", "طرز": "روش"}


def topic_set(text):
    return {CANON.get(t, t) for t in tokens(text)}


def intent_of(q, brand=False):
    if brand:
        return "brand"
    t = tset(q)
    c, i = len(t & COMMERCIAL), len(t & INFO)
    return "commercial" if c > i else "info" if i > c else ("commercial" if c else "unknown")


# ------------------------------------------------------------------ documents (pages that can be a keyword's target)
def in_stock(p):
    """True only when the product is known to be available. Zero stock, out-of-stock status or an old snapshot without stock data (re-sync!) -> False."""
    st = p.get("stock_status")
    if st not in ("instock", "onbackorder"):
        return False
    q = p.get("stock_quantity")
    return not (p.get("manage_stock") and q is not None and q <= 0 and st != "onbackorder")


def build_docs(arts=None, snap=None):
    """Every published page we know: articles (title/focus/H2), products, product categories, article categories, pages."""
    snap = snap if snap is not None else (load_snapshot() or {})
    arts = arts if arts is not None else read_articles()
    docs, seen_ids = [], set()
    for a in arts:
        if str(a.get("status", "publish")) != "publish":
            continue
        seen_ids.add(a.get("id"))
        prim = tset(" ".join([str(a.get("title", "")), str(a.get("focus_keyword") or ""), str(a.get("seo_title") or "")]))
        sec = tset(" ".join(h2_list(a.get("body") or "") + [str(c) for c in (a.get("categories") or [])] + [str(t) for t in (a.get("tags") or [])])) - prim
        docs.append({"kind": "article", "id": a.get("id"), "title": a.get("title", ""), "url": a.get("url", ""), "focus": a.get("focus_keyword") or "",
                     "prim": prim, "sec": sec, "topic": topic_set(a.get("focus_keyword") or a.get("title", "")), "modified": str(a.get("modified", ""))[:10]})
    for p in snap.get("posts", []):  # published articles that were not exported yet: title only
        if p.get("status") == "publish" and p["id"] not in seen_ids:
            ts = tset(p.get("title", ""))
            docs.append({"kind": "article", "id": p["id"], "title": p.get("title", ""), "url": p.get("url", ""), "focus": "", "prim": ts,
                         "sec": tset(" ".join(p.get("categories", []))) - ts, "topic": topic_set(p.get("title", "")), "modified": str(p.get("modified", ""))[:10]})
    for p in snap.get("products", []):
        if p.get("status") == "publish":
            ts = tset(p.get("name", ""))
            docs.append({"kind": "product", "id": p["id"], "title": p.get("name", ""), "url": p.get("url", ""), "focus": "", "prim": ts,
                         "sec": tset(" ".join(p.get("categories", []))) - ts, "topic": topic_set(p.get("name", "")), "modified": "", "oos": not in_stock(p)})
    for tax, kind in (("product_cat", "product_cat"), ("category", "category")):
        for t in snap.get(tax, []):
            if t.get("count", 0) > 0 and t.get("name"):
                ts = tset(t["name"])
                docs.append({"kind": kind, "id": t["id"], "title": t["name"], "url": t.get("link", ""), "focus": "", "prim": ts, "sec": set(),
                             "topic": topic_set(t["name"]), "modified": ""})
    for p in snap.get("pages", []):
        if p.get("status") == "publish":
            ts = tset(p.get("title", ""))
            docs.append({"kind": "page", "id": p["id"], "title": p.get("title", ""), "url": p.get("url", ""), "focus": "", "prim": ts, "sec": set(),
                         "topic": topic_set(p.get("title", "")), "modified": ""})
    return [d for d in docs if d["prim"]]


def idf_table(docs):
    n, df = len(docs), Counter()
    for d in docs:
        df.update(d["prim"] | d["sec"])
    return {t: 0.3 + math.log((n + 1) / (c + 1)) for t, c in df.items()}, n


def weight(t, idf, n):
    w = idf.get(t, 0.3 + math.log(n + 1)) * (0.4 if t in MODIFIERS else 1.0)  # unseen word = very specific
    return w


def cover(qt, doc, idf, n):
    """0..1 : how much of the (weighted) query is covered by the page. Headline words count fully, H2/category words 60%."""
    if not qt:
        return 0.0
    tot = got = 0.0
    for t in qt:
        w = weight(t, idf, n)
        tot += w
        got += w if t in doc["prim"] else 0.6 * w if t in doc["sec"] else 0.0
    return got / tot if tot else 0.0


def best_targets(qt, docs, idf, n, intent="unknown", k=3):
    res = []
    for d in docs:
        s = cover(qt, d, idf, n)
        if s > 0:
            # on equal coverage: buyers land best on a category, then a product; readers on an article
            fit = {"commercial": {"product_cat": 0.04, "product": 0.03}, "info": {"article": 0.03}}.get(intent, {}).get(d["kind"], 0.0)
            res.append((s + fit, s, d))
    res.sort(key=lambda x: -x[0])
    return [(s, d) for _, s, d in res[:k]]


def jacc(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


# ------------------------------------------------------------------ build
def read_manual():
    if not MANUAL.exists():
        return []
    return [ln.strip() for ln in MANUAL.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.strip().startswith("#")]


def _is_brand(q, brands):
    ql = q.lower()
    return any(b in ql for b in brands)


def rising(cur, prev, min_impr=10):
    """Trend-lite: queries whose impressions grew a lot between two same-length Search Console exports."""
    if not prev:
        return {"note": "برای دیدن عبارت‌های در حال رشد، دست‌کم دو خروجی Search Console (هم‌بازه) لازم است.", "rows": []}
    a, b = cur.get("days"), prev.get("days")
    if not a or not b or abs(a - b) > 0.25 * max(a, b):
        return {"note": f"بازهٔ دو خروجی هم‌اندازه نیست ({b} روز در برابر {a} روز)؛ مقایسه گمراه‌کننده است.", "rows": []}
    pv = {r["key"]: r for r in prev.get("queries", [])}
    rows = []
    for r in cur.get("queries", []):
        o = pv.get(r["key"])
        before = o["impr"] if o else 0
        gain = r["impr"] - before
        if r["impr"] >= max(min_impr, 10) and gain >= 10 and (before == 0 or r["impr"] >= 1.5 * before):
            rows.append({"key": r["key"], "impr": r["impr"], "before": before, "gain": gain, "clicks": r["clicks"], "pos": r["pos"], "new": o is None})
    return {"note": "", "rows": sorted(rows, key=lambda r: -r["gain"])[:40]}


def _union_groups(items, same):
    parent = list(range(len(items)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if same(items[i], items[j]):
                parent[find(i)] = find(j)
    g = defaultdict(list)
    for i in range(len(items)):
        g[find(i)].append(items[i])
    return [v for v in g.values() if len(v) > 1]


def cannibal(docs, queries, min_impr, brands):
    # A) articles whose focus keyword is the same or nearly the same
    arts = [d for d in docs if d["kind"] == "article" and d["focus"]]
    groups = []
    for g in _union_groups(arts, lambda x, y: norm(x["focus"]) == norm(y["focus"]) or (len(x["topic"]) >= 2 and len(y["topic"]) >= 2 and jacc(x["topic"], y["topic"]) >= 0.66)):
        exact = len({norm(d["focus"]) for d in g}) == 1
        groups.append({"sev": "H" if exact else "M", "label": g[0]["focus"], "exact": exact,
                       "pages": [{"kind": d["kind"], "id": d["id"], "title": d["title"], "url": d["url"], "focus": d["focus"]} for d in g]})
    # B) a search phrase that two or more pages are "about"
    comp = []
    for q in queries:
        if q["impr"] < min_impr or _is_brand(q["key"], brands):
            continue
        qt = topic_set(q["key"])
        hits = [d for d in docs if jacc(qt, d["topic"]) >= 0.6]
        # a category + its own products sharing a name is normal shop structure; competition needs at least one ARTICLE involved
        if len(hits) >= 2 and any(d["kind"] == "article" for d in hits) and (len(qt) >= 2 or len(hits) <= 3):
            comp.append({"query": q["key"], "impr": q["impr"], "clicks": q["clicks"], "pos": q["pos"],
                         "pages": [{"kind": d["kind"], "id": d["id"], "title": d["title"], "url": d["url"]} for d in hits[:6]]})
    comp.sort(key=lambda r: -r["impr"])
    return groups, comp[:60]


def cluster_gaps(gaps, idf_generic):
    """Greedy grouping of uncovered queries that share their specific words (code only; AI can regroup semantically later)."""
    clusters = []
    for g in sorted(gaps, key=lambda x: -x["impr"]):
        core = (tset(g["key"]) - MODIFIERS - idf_generic) or tset(g["key"])
        for c in clusters:  # joins the cluster whose SEED (its highest-impression query) shares a specific word; seeds do not grow, so no snowballing
            if core & c["core"]:
                c["queries"].append(g)
                break
        else:
            clusters.append({"core": set(core), "queries": [g]})
    out = []
    for i, c in enumerate(clusters, 1):
        qs = c["queries"]
        ints = Counter(q["intent"] for q in qs if q["intent"] != "unknown")
        top = ints.most_common(1)[0][0] if ints else "unknown"
        impr = sum(q["impr"] for q in qs)
        out.append({"id": i, "label": qs[0]["key"], "impr": impr, "clicks": sum(q["clicks"] for q in qs),
                    "pos": round(sum(q["pos"] * q["impr"] for q in qs) / impr, 1) if impr else 0.0, "intent": top,
                    "suggest": {"commercial": "دستهٔ محصول یا صفحهٔ محصول", "info": "مقالهٔ جدید"}.get(top, "مقالهٔ جدید (یا دستهٔ محصول)"),
                    "queries": [{"key": q["key"], "impr": q["impr"], "clicks": q["clicks"], "pos": q["pos"]} for q in qs[:12]], "n": len(qs)})
    return sorted(out, key=lambda c: -c["impr"])


def build(min_impr=10, log=print):
    """Builds knowledge/keywords/analysis.json + reports/keywords_*.csv. Reads local files only; changes nothing on the site."""
    snap = load_snapshot()
    if not snap:
        raise ValueError("اول «همگام‌سازی سایت» (گام ۲ در خانه) را اجرا کنید.")
    arts = read_articles()
    docs = build_docs(arts, snap)
    if not docs:
        raise ValueError("هیچ صفحهٔ منتشرشده‌ای پیدا نشد (همگام‌سازی و خروجی مقاله‌ها را بررسی کنید).")
    idf, n = idf_table(docs)
    brands = brand_terms()
    files = gsc_saved()
    cur = gsc_load(files[-1]) if files else None
    prev = gsc_load(files[-2]) if len(files) > 1 else None
    queries = (cur or {}).get("queries", [])
    manual = read_manual()
    # generic words = on many pages (e.g. «زعفران» on a saffron shop); they must not decide clustering
    df = Counter()
    for d in docs:
        df.update(d["prim"])
    generic = {t for t, c in df.items() if c / n >= 0.35}

    bank = {}

    def put(text, source, **m):
        ts = tuple(sorted(set(tokens(text))))
        if not ts:
            return
        e = bank.setdefault(ts, {"key": text.strip(), "sources": [], "clicks": 0, "impr": 0, "pos": 0.0, "ctr": 0.0, "owner": None})
        if source not in e["sources"]:
            e["sources"].append(source)
        if source == "gsc" and m.get("impr", 0) >= e["impr"]:
            e.update(key=text.strip(), clicks=m["clicks"], impr=m["impr"], pos=m["pos"], ctr=m["ctr"])
        if m.get("owner"):
            e["owner"] = m["owner"]

    for q in queries:
        put(q["key"], "gsc", clicks=q["clicks"], impr=q["impr"], pos=q["pos"], ctr=q["ctr"])
    for d in docs:
        if d["kind"] == "article" and d["focus"]:
            put(d["focus"], "focus", owner=d)
    for tax, src in (("product_cat", "term"), ("category", "term"), ("product_tag", "term"), ("post_tag", "term")):
        for t in snap.get(tax, []):
            if t.get("count", 0) > 0 and t.get("name"):
                put(t["name"], src)
    for m_ in manual:
        put(m_, "manual")

    rows = []
    for ts, e in bank.items():
        qt = set(ts)
        brand = _is_brand(e["key"], brands)
        it = intent_of(e["key"], brand)
        tg = best_targets(qt, docs, idf, n, it)
        if e["owner"] is not None and (not tg or tg[0][1]["id"] != e["owner"]["id"]):
            tg = [(1.0, e["owner"])] + tg  # the article that declares this focus keyword is its target
        score, tdoc = (tg[0] if tg else (0.0, None))
        status = "brand" if brand else "covered" if score >= STRONG else "weak" if score >= WEAK else "gap"
        mismatch = bool(tdoc) and ((it == "commercial" and tdoc["kind"] == "article") or (it == "info" and tdoc["kind"] in ("product", "product_cat")))
        rows.append({"key": e["key"], "sources": e["sources"], "clicks": e["clicks"], "impr": e["impr"], "pos": e["pos"], "ctr": e["ctr"], "intent": it,
                     "score": round(score, 2), "status": status, "mismatch": mismatch, "tokens": list(ts),
                     "target": {k: tdoc[k] for k in ("kind", "id", "title", "url")} if tdoc else None})
    rows.sort(key=lambda r: (-r["impr"], -len(r["sources"]), r["key"]))

    groups, comp = cannibal(docs, queries, min_impr, brands)
    gap_q = [r for r in rows if r["status"] == "gap" and "gsc" in r["sources"] and r["impr"] >= min_impr]
    gap_m = [r for r in rows if "manual" in r["sources"] and r["status"] in ("gap", "weak")]
    clusters = cluster_gaps(gap_q, generic)
    # product categories with no supporting article at all
    art_docs = [d for d in docs if d["kind"] == "article"]
    nocat = []
    for t in snap.get("product_cat", []):
        if t.get("count", 0) > 0 and t.get("name"):
            qt = topic_set(t["name"])
            if art_docs and max(cover(qt, d, idf, n) for d in art_docs) < STRONG:
                nocat.append({"id": t["id"], "name": t["name"], "count": t["count"], "url": t.get("link", "")})
    res = {"built": datetime.now().isoformat(timespec="seconds"), "min_impr": min_impr, "gsc_file": (cur or {}).get("_file", ""),
           "gsc_days": (cur or {}).get("days"), "n_docs": n, "n_articles": len(art_docs), "has_gsc": bool(cur), "has_articles": bool(arts),
           "bank": rows, "cannibal_focus": groups, "cannibal_queries": comp, "gap_clusters": clusters, "gap_manual": gap_m[:40],
           "cats_without_article": nocat, "rising": rising(cur, prev, min_impr) if cur else {"note": "خروجی Search Console وارد نشده است.", "rows": []}}
    KW.mkdir(parents=True, exist_ok=True)
    ANALYSIS.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    _write_csv(res)
    c = Counter(r["status"] for r in rows)
    log(f"بانک کلمات: {len(rows)} عبارت (پوشش‌داده‌شده {c['covered']}، ضعیف {c['weak']}، شکاف {c['gap']}، برند {c['brand']})")
    log(f"هم‌نوع‌خواری: {len(groups)} گروه کلمه کلیدی مشابه، {len(comp)} عبارت جست‌وجو با بیش از یک صفحهٔ رقیب")
    log(f"شکاف محتوا: {len(clusters)} خوشه از عبارت‌های بدون صفحه؛ دستهٔ محصول بدون مقاله: {len(nocat)}")
    if not cur:
        log("توجه: خروجی Search Console وارد نشده؛ بانک فقط از مقاله‌ها و دسته‌ها ساخته شد و شکاف مبتنی بر جست‌وجو خالی است.")
    if not arts:
        log("توجه: «خروجی مقاله‌ها» اجرا نشده؛ تطبیق فقط با عنوان مقاله‌ها انجام شد (کلمه کلیدی و تیترها بررسی نشد).")
    return res


def _write_csv(res):
    REP.mkdir(exist_ok=True)

    def wr(name, header, rows):
        with open(REP / name, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
    ST = {"covered": "پوشش کامل", "weak": "پوشش ضعیف", "gap": "شکاف", "brand": "برند"}
    wr("keywords_bank.csv", ["keyword", "sources", "impressions", "clicks", "ctr_percent", "position", "intent", "status", "match", "intent_mismatch",
                              "target_kind", "target_title", "target_url"],
       [[r["key"], "+".join(r["sources"]), r["impr"], r["clicks"], round(r["ctr"] * 100, 2), r["pos"], INTENT_FA[r["intent"]], ST[r["status"]], r["score"],
         "yes" if r["mismatch"] else "", KIND_FA.get(r["target"]["kind"], "") if r["target"] else "", r["target"]["title"] if r["target"] else "",
         r["target"]["url"] if r["target"] else ""] for r in res["bank"]])
    wr("content_gaps.csv", ["cluster", "label", "total_impressions", "clicks", "avg_position", "queries", "suggested_type", "sample_queries"],
       [[c["id"], c["label"], c["impr"], c["clicks"], c["pos"], c["n"], c["suggest"], " | ".join(q["key"] for q in c["queries"][:8])] for c in res["gap_clusters"]])
    rows = []
    for g in res["cannibal_focus"]:
        rows.append(["focus_keyword", "exact" if g["exact"] else "similar", g["label"], "", " | ".join(p["url"] for p in g["pages"])])
    for c in res["cannibal_queries"]:
        rows.append(["search_query", "", c["query"], c["impr"], " | ".join(p["url"] for p in c["pages"])])
    wr("keywords_cannibal.csv", ["type", "kind", "keyword", "impressions", "pages"], rows)


def load_analysis():
    if not ANALYSIS.exists():
        return None
    try:
        return json.loads(ANALYSIS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_manual(text):
    KW.mkdir(parents=True, exist_ok=True)
    MANUAL.write_text(text.strip() + "\n" if text.strip() else "", encoding="utf-8")


def summary_lines(res):
    """Short lines for the weekly routine report."""
    c = Counter(r["status"] for r in res["bank"])
    L = [f"- بانک: {len(res['bank'])} عبارت؛ شکاف: {c['gap']}، پوشش ضعیف: {c['weak']}"]
    L.append(f"- هم‌نوع‌خواری: {len(res['cannibal_focus'])} گروه کلمه کلیدی مشابه، {len(res['cannibal_queries'])} عبارت با چند صفحهٔ رقیب")
    L.append(f"- خوشه‌های شکاف محتوا: {len(res['gap_clusters'])}" + (f" (بزرگ‌ترین: «{res['gap_clusters'][0]['label']}» با {res['gap_clusters'][0]['impr']} نمایش)" if res["gap_clusters"] else ""))
    return L
