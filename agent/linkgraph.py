"""Stage 5 - link graph and topic clusters. READ-ONLY and code only (no AI, no cost).
Nodes: articles, products, product categories, article categories, pages (from the last sync + exported articles).
Edges: internal links found in the article texts. Result: orphans, dead ends, topic clusters (a parent «pillar» article + supporting
articles), missing links inside each cluster, article <-> product / category links, links to out-of-stock products.
Suggestions are written in the SAME format as the link plan (proposals/links_*.csv), so the existing tick -> preview -> apply (with backup) flow is reused.
Honest limits: only article texts are read (product/category pages are not), so «product -> article» links are unknown; clusters are guessed from
titles, keywords and headings (not from search data), so review them."""
import csv
import json
import re
from collections import defaultdict
from datetime import datetime
from urllib.parse import unquote, urlparse

from . import keywords as kw
from . import proposals as pr
from .common import StepError
from .config import ROOT
from .corpus import read_articles
from .site import load_snapshot

GRAPH = ROOT / "knowledge" / "graph"
REP = ROOT / "reports"
SIM_T = 0.22                 # similarity (0..1) from which two articles count as the same topic
LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(\s*([^)\s]+)")
KIND_FA = {"article": "مقاله", "product": "محصول", "product_cat": "دستهٔ محصول", "category": "دستهٔ مقاله", "page": "صفحه"}


def _key(url):
    return unquote(urlparse(str(url)).path).rstrip("/")


def _nid(d):
    return f"{d['kind']}:{d['id']}"


def _vec(d, idf, n):
    v = {}
    for t in d["prim"] | d["sec"]:
        v[t] = kw.weight(t, idf, n) * (1.0 if t in d["prim"] else 0.6)
    return v


def _sim(a, b):
    common = set(a) & set(b)
    if not common:
        return 0.0
    num = sum(min(a[t], b[t]) for t in common)
    den = sum(max(a.get(t, 0), b.get(t, 0)) for t in set(a) | set(b))
    return num / den if den else 0.0


def build(log=print, save=True):
    snap = load_snapshot()
    arts = [a for a in read_articles() if str(a.get("status", "publish")) == "publish" and a.get("url")]
    if not snap or len(arts) < 5:
        raise StepError("اول «همگام‌سازی سایت» و «خروجی مقاله‌ها» (صفحهٔ خانه) را اجرا کنید؛ دست‌کم ۵ مقالهٔ منتشرشده لازم است.")
    docs = kw.build_docs(arts, snap)
    by_key = {}
    for d in docs:
        if d.get("url"):
            by_key.setdefault(_key(d["url"]), d)
    host = pr.bare(urlparse(snap.get("site", "")).netloc or urlparse(arts[0]["url"]).netloc)
    art_doc = {d["id"]: d for d in docs if d["kind"] == "article"}
    out, inb, by_type, oos_from = defaultdict(set), defaultdict(set), defaultdict(int), defaultdict(set)
    for a in arts:
        src = art_doc.get(a.get("id"))
        if not src:
            continue
        for u in LINK_RE.findall(a["body"]):
            if u.startswith(("#", "mailto:", "tel:")):
                continue
            if u.startswith("http") and pr.bare(urlparse(u).netloc) != host:
                continue
            t = by_key.get(_key(u))
            if not t or _nid(t) == _nid(src):
                continue
            if _nid(t) in out[_nid(src)]:
                continue
            out[_nid(src)].add(_nid(t))
            inb[_nid(t)].add(_nid(src))
            by_type[f"{src['kind']}->{t['kind']}"] += 1
            if t["kind"] == "product" and t.get("oos"):
                oos_from[_nid(t)].add(src["title"])
    A = [d for d in docs if d["kind"] == "article"]
    idf, n = kw.idf_table(docs)
    vec = {_nid(d): _vec(d, idf, n) for d in A}
    # ---- clusters: the broadest article (most similar others, then most inbound links) becomes the pillar of the articles that are about the same topic
    sims = {(_nid(a), _nid(b)): _sim(vec[_nid(a)], vec[_nid(b)]) for i, a in enumerate(A) for b in A[i + 1:]}
    sim = lambda a, b: sims.get((_nid(a), _nid(b))) or sims.get((_nid(b), _nid(a))) or 0.0
    breadth = {_nid(a): sum(1 for b in A if b is not a and sim(a, b) >= SIM_T) for a in A}
    order = sorted(A, key=lambda d: (-breadth[_nid(d)], -len(inb[_nid(d)]), d["title"]))
    taken, clusters = set(), []
    for p in order:
        if _nid(p) in taken:
            continue
        sup = sorted([x for x in A if _nid(x) not in taken and x is not p and sim(p, x) >= SIM_T], key=lambda x: -sim(p, x))
        if not sup:
            continue
        taken |= {_nid(p)} | {_nid(x) for x in sup}
        clusters.append((p, sup))
    cats = [d for d in docs if d["kind"] == "product_cat"]
    prods = [d for d in docs if d["kind"] == "product" and not d.get("oos")]
    cl_out = []
    for p, sup in clusters:
        members = [p] + sup
        qt = (p["prim"] - kw.MODIFIERS) or p["prim"]
        best_cat = max(cats, key=lambda c: kw.cover(qt, c, idf, n), default=None)
        if best_cat and kw.cover(qt, best_cat, idf, n) < 0.5:
            best_cat = None
        rel_prod = [x for x in sorted(prods, key=lambda x: -kw.cover(qt, x, idf, n)) if kw.cover(qt, x, idf, n) >= 0.6][:2]
        link_to = lambda t: [m["title"] for m in members if _nid(t) in out[_nid(m)]]
        cl_out.append({
            "pillar": {"id": p["id"], "title": p["title"], "url": p["url"], "inb": len(inb[_nid(p)])},
            "supporters": [{"id": x["id"], "title": x["title"], "url": x["url"], "sim": round(sim(p, x), 2), "to_pillar": _nid(p) in out[_nid(x)], "from_pillar": _nid(x) in out[_nid(p)]} for x in sup],
            "cat": ({"id": best_cat["id"], "title": best_cat["title"], "url": best_cat["url"], "linked_by": len(link_to(best_cat))} if best_cat else None),
            "products": [{"id": x["id"], "title": x["title"], "url": x["url"], "linked_by": len(link_to(x))} for x in rel_prod]})
    single = [d for d in A if _nid(d) not in taken]
    node = lambda d: {"kind": d["kind"], "id": d["id"], "title": d["title"], "url": d["url"], "inb": len(inb[_nid(d)]), "out": len(out[_nid(d)]), "oos": bool(d.get("oos"))}
    res = {
        "built": datetime.now().isoformat(timespec="seconds"), "counts": {k: sum(1 for d in docs if d["kind"] == k) for k in KIND_FA}, "edges": sum(len(v) for v in out.values()),
        "by_type": dict(by_type), "has_stock": any("stock_status" in p for p in snap.get("products", [])),
        "orphans": [node(d) for d in A if not inb[_nid(d)]], "dead_ends": [node(d) for d in A if not out[_nid(d)]],
        "cats_unlinked": [node(d) for d in cats if not inb[_nid(d)]],
        "products_unlinked": [node(d) for d in prods if not inb[_nid(d)]],
        "oos_linked": [{"title": next(d["title"] for d in docs if _nid(d) == k), "url": next(d["url"] for d in docs if _nid(d) == k), "from": sorted(v)} for k, v in oos_from.items()],
        "clusters": cl_out, "single": [node(d) for d in single], "nodes": [node(d) for d in docs]}
    if save:
        GRAPH.mkdir(parents=True, exist_ok=True)
        (GRAPH / "graph.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        _write_reports(res)
    log(f"گراف: {len(docs)} صفحه، {res['edges']} لینک داخلی بین مقاله‌ها و صفحه‌ها؛ {len(cl_out)} خوشهٔ موضوعی، {len(res['orphans'])} مقالهٔ یتیم، "
        f"{len(res['dead_ends'])} مقاله بدون لینک خروجی، {len(res['oos_linked'])} محصول ناموجودِ لینک‌شده.")
    if not res["has_stock"]:
        log("نکته: موجودی محصولات در همگام‌سازی قبلی ذخیره نشده؛ «همگام‌سازی سایت» را دوباره بزنید (تا آن موقع به هیچ محصولی لینک پیشنهاد نمی‌شود).")
    return res


def load():
    f = GRAPH / "graph.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def _write_reports(res):
    REP.mkdir(exist_ok=True)
    with open(REP / "link_graph_nodes.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["kind", "id", "title", "url", "inbound_from_articles", "outbound_internal", "out_of_stock"])
        w.writerows([[x["kind"], x["id"], x["title"], x["url"], x["inb"], x["out"], "yes" if x["oos"] else ""] for x in res["nodes"]])
    L = [f"# گراف لینک و خوشه‌ها ({res['built'][:16].replace('T', ' ')})", "", "تعداد صفحه‌ها: " + "، ".join(f"{KIND_FA[k]} {v}" for k, v in res["counts"].items()) + f" — لینک داخلی: {res['edges']}", ""]
    L += ["## مقاله‌های یتیم (هیچ مقاله‌ای به آن‌ها لینک نداده)"] + [f"- {x['title']} — {x['url']}" for x in res["orphans"]] + [""]
    L += ["## محصولات ناموجود که به آن‌ها لینک داده شده (لینک را بردارید)"] + [f"- {x['title']} ← از: {'، '.join(x['from'])}" for x in res["oos_linked"]] + [""]
    L += ["## خوشه‌ها"]
    for c in res["clusters"]:
        L.append(f"### {c['pillar']['title']} (صفحهٔ مادر؛ لینک ورودی {c['pillar']['inb']})")
        L += [f"- {s['title']} — شباهت {s['sim']}" + ("" if s["to_pillar"] else " — به مادر لینک ندارد") + ("" if s["from_pillar"] else " — مادر به آن لینک ندارد") for s in c["supporters"]]
        if c["cat"]:
            L.append(f"- دستهٔ محصول مرتبط: {c['cat']['title']} (لینک از {c['cat']['linked_by']} مقالهٔ خوشه)")
        L += [f"- محصول مرتبط (موجود): {p['title']} (لینک از {p['linked_by']} مقاله)" for p in c["products"]]
        L.append("")
    (REP / "link_graph.md").write_text("\n".join(L), encoding="utf-8")


# ------------------------------------------------------------------ suggestions in the link-plan format
def _anchors(d, art):
    if d["kind"] == "article" and art:
        return pr.anchors_of(art)
    t = pr.clean_title(d["title"])
    return [(t, 8)] if t and (len(t.split()) >= 2 or len(t) >= 8) else []


def suggest(res=None, log=print):
    """Missing links of every cluster (supporter -> pillar, pillar -> supporter) and cluster -> product category / in-stock product, only where an
    exact natural phrase already exists in the source text. Out-of-stock products are never suggested. -> proposals/links_*.csv"""
    res = res or load()
    if not res:
        raise StepError("اول «ساخت گراف» را بزنید.")
    arts = {a.get("id"): a for a in read_articles() if a.get("url")}
    text = {i: pr.search_text(a["body"]) for i, a in arts.items()}
    have = {i: set(LINK_RE.findall(a["body"])) for i, a in arts.items()}
    used, rows, nophrase = defaultdict(int), [], []

    def add(src_id, tgt, why, score):
        a = arts.get(src_id)
        if not a or used[src_id] >= pr.MAX_NEW_PER_SOURCE or tgt["url"] in have[src_id] or any(_key(tgt["url"]) == _key(u) for u in have[src_id]):
            return
        t_art = arts.get(tgt["id"]) if tgt.get("kind", "article") == "article" else None
        for anc, w in _anchors({"kind": tgt.get("kind", "article"), "title": tgt["title"]}, t_art):
            m = pr.boundary(anc).search(text[src_id])
            if m:
                used[src_id] += 1
                rows.append(["", src_id, a["title"], a["url"], anc, tgt["id"], tgt["title"], tgt["url"], pr.sentence_of(text[src_id], m.start(), m.end()), score + w, a.get("modified", ""), why])
                return
        nophrase.append((a["title"], tgt["title"], why))
    for c in res["clusters"]:
        p = c["pillar"]
        for s in c["supporters"]:
            if not s["to_pillar"]:
                add(s["id"], {**p, "kind": "article"}, "پشتیبان ← صفحهٔ مادر", 20)
            if not s["from_pillar"]:
                add(p["id"], {**s, "kind": "article"}, "صفحهٔ مادر ← پشتیبان", 12)
        members = [p] + c["supporters"]
        if c["cat"] and c["cat"]["linked_by"] == 0:
            for m in members[:4]:
                add(m["id"], {**c["cat"], "kind": "product_cat"}, "مقاله ← دستهٔ محصول مرتبط", 15)
        for pr_ in c["products"]:
            if pr_["linked_by"] == 0:
                for m in members[:4]:
                    add(m["id"], {**pr_, "kind": "product"}, "مقاله ← محصول مرتبط (موجود)", 10)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    path = pr.PROP / f"links_{ts}.csv"
    pr.write_csv(path, ["approve", "source_id", "source_title", "source_url", "anchor", "target_id", "target_title", "target_url", "sentence", "score", "source_modified", "why"], rows)
    log(f"{len(rows)} پیشنهاد لینک از گراف ساخته شد ({len(nophrase)} مورد عبارت طبیعی در متن نداشت).")
    log(f"فایل: {path}\nپنل ← «لینک‌دهی داخلی»: تیک بزنید، پیش‌نمایش و اعمال (با پشتیبان). «ارزیابی AI» همان‌جا هم کار می‌کند.")
    return path, rows, nophrase
