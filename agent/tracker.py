"""Stage 7 - content decay and impact tracking. Read-only, code only.
decay(): articles whose Search Console clicks/position got worse between two same-length exports, or that are old but still shown.
impact(): for every change this program made on the site (logs/actions.jsonl), compare the page's Search Console rates BEFORE and AFTER, relative to the whole site.
Honest limits: Search Console exports are periods, not daily series - the verdict needs an export that ENDED before the change and one that STARTED after it;
small numbers, seasonality and Google's own delays make any single verdict a hint, not proof."""
import json
from datetime import date, datetime, timedelta

from . import gsc
from .config import ROOT
from .health import nurl
from .site import load_snapshot

ACTIONS = ROOT / "logs" / "actions.jsonl"
CHANGE_KINDS = {"article_publish": "انتشار مقاله", "article_update": "به‌روزرسانی مقاله", "article_images": "افزودن تصویر", "links_added": "لینک داخلی", "seo_update": "تغییر سئو",
                "product_update": "به‌روزرسانی محصول", "slug_changed": "تغییر آدرس", "media_alt": "Alt تصویر", "cat_desc": "توضیح دسته", "hf_noindex": "noindex"}


def _snaps():
    out = []
    for f in gsc.saved():
        d = gsc.load(f)
        if d and d.get("pages"):
            out.append(d)
    return sorted(out, key=lambda d: (d.get("from") or "", d["_file"]))


def _rates(d):
    days = max(d.get("days") or 1, 1)
    m = {nurl(r["key"]): r for r in d.get("pages", [])}
    tot = {"clicks": sum(r["clicks"] for r in d["pages"]) / days, "impr": sum(r["impr"] for r in d["pages"]) / days}
    return m, days, tot


def _articles():
    snap = load_snapshot() or {}
    return {p["id"]: p for p in snap.get("posts", []) if p.get("url")}


def decay(min_clicks=8, drop=0.3, stale_days=365, today=None):
    """-> {"items": [...], "note": str}. Items are articles, worst first."""
    today = today or date.today()
    arts, snaps, items, note = _articles(), _snaps(), [], ""
    cur = prev = None
    if len(snaps) >= 2:
        cur, prev = snaps[-1], snaps[-2]
        a, b = cur.get("days"), prev.get("days")
        if not a or not b or abs(a - b) > 0.25 * max(a, b):
            note = f"دو خروجی آخر Search Console هم‌اندازه نیستند ({b} و {a} روز)؛ افت قابل‌اعتماد محاسبه نشد. دفعهٔ بعد همان بازه را انتخاب کنید."
            prev = None
    else:
        note = "برای تشخیص افت دو خروجی Search Console هم‌بازه لازم است؛ فعلاً فقط مقاله‌های قدیمی بررسی شد."
    cm = _rates(cur)[0] if cur else {}
    pm = _rates(prev)[0] if prev else {}
    for pid, p in arts.items():
        if str(p.get("status", "publish")) != "publish":
            continue
        k = nurl(p["url"])
        c, o = cm.get(k), pm.get(k)
        reasons, score = [], 0.0
        if c and o and prev:
            scale = (cur.get("days") or 1) / (prev.get("days") or 1)
            before = o["clicks"] * scale
            lost = before - c["clicks"]
            if before >= min_clicks and lost >= drop * before:
                reasons.append(f"کلیک {round(before)} ← {c['clicks']}")
                score += lost
            if o["impr"] * scale >= 50 and c["impr"] <= 0.6 * o["impr"] * scale:
                reasons.append(f"نمایش {round(o['impr'] * scale)} ← {c['impr']}")
                score += 0.1 * (o["impr"] * scale - c["impr"])
            if c["pos"] - o["pos"] >= 2 and c["impr"] >= 30:
                reasons.append(f"جایگاه {o['pos']:.1f} ← {c['pos']:.1f}")
                score += 5 * (c["pos"] - o["pos"])
        mod = str(p.get("modified", ""))[:10]
        try:
            age = (today - datetime.fromisoformat(mod).date()).days
        except ValueError:
            age = None
        if age is not None and age >= stale_days and c and c["impr"] >= 30:
            reasons.append(f"{age} روز از آخرین ویرایش گذشته و هنوز دیده می‌شود")
            score += 3 + min(c["impr"], 300) / 30
        if reasons:
            items.append({"id": pid, "title": p.get("title", ""), "url": p["url"], "reasons": reasons, "score": round(score, 1), "age": age,
                          "clicks": c["clicks"] if c else 0, "impr": c["impr"] if c else 0, "pos": c["pos"] if c else 0})
    items.sort(key=lambda x: -x["score"])
    return {"items": items, "note": note, "cur": (cur or {}).get("_file", ""), "prev": (prev or {}).get("_file", "")}


def changes(since_days=400):
    """What this program changed on the site (from logs/actions.jsonl) -> [{time, kind, label, id, url, title}]"""
    if not ACTIONS.exists():
        return []
    arts = _articles()
    cut = datetime.now() - timedelta(days=since_days)
    out = []
    for line in ACTIONS.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("kind") not in CHANGE_KINDS:
            continue
        pid = r.get("id") or r.get("post")
        art = arts.get(pid)
        t = datetime.fromisoformat(r["time"])
        if art and t >= cut:
            out.append({"time": r["time"], "kind": r["kind"], "label": CHANGE_KINDS[r["kind"]], "id": pid, "url": art["url"], "title": art.get("title", "")})
    return out


def impact(min_days=7, today=None):
    """-> list of rows {change.., verdict, before, after, site_ratio, rel}. Needs a before-export (ended before the change) and an after-export (started after it)."""
    snaps = _snaps()
    rows = []
    for ch in changes():
        t = ch["time"][:10]
        before = [s for s in snaps if s.get("to") and s["to"] <= t]
        after = [s for s in snaps if s.get("from") and s["from"] >= t and (s.get("days") or 0) >= min_days]
        row = {**ch, "verdict": "no_data", "before": None, "after": None, "rel": None}
        if before and after:
            b, a = before[-1], after[0]
            (bm, bd, bt), (am, ad, at) = _rates(b), _rates(a)
            k = nurl(ch["url"])
            rb, ra = bm.get(k), am.get(k)
            if rb and ra:
                cb, ca = rb["clicks"] / bd, ra["clicks"] / ad
                ib, ia = rb["impr"] / bd, ra["impr"] / ad
                site = at["clicks"] / bt["clicks"] if bt["clicks"] else None          # whole-site change over the same two periods
                page = ca / cb if cb else None
                rel = (page / site) if (page is not None and site) else page
                row.update(before={"clicks_day": round(cb, 2), "impr_day": round(ib, 1), "pos": rb["pos"], "file": b["_file"]}, after={"clicks_day": round(ca, 2), "impr_day": round(ia, 1), "pos": ra["pos"], "file": a["_file"]},
                           rel=round(rel, 2) if rel is not None else None, site_ratio=round(site, 2) if site else None)
                if max(cb, ca) < 0.3 and max(ib, ia) < 3:
                    row["verdict"] = "tiny"
                elif rel is None:
                    row["verdict"] = "no_data"
                else:
                    row["verdict"] = "better" if rel >= 1.25 else ("worse" if rel <= 0.8 else "same")
        rows.append(row)
    rows.sort(key=lambda r: r["time"], reverse=True)
    return rows


VERDICT = {"better": "بهتر شد", "worse": "بدتر شد", "same": "تقریباً بی‌تغییر", "tiny": "عدد خیلی کوچک (قابل‌قضاوت نیست)", "no_data": "هنوز داده کافی نیست (به خروجی قبل و بعد از تغییر نیاز است)"}
