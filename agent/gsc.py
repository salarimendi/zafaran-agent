"""Stage 2 without any Google Cloud account: import the 'Performance' export that Search Console lets you download
(a zip of CSV files), keep a dated copy, and find opportunities with plain code (no AI, nothing is changed on the site)."""
import csv
import io
import json
import os
import re
import zipfile
from datetime import datetime, timedelta
from urllib.parse import urlparse

from .common import DIGITS
from .config import ROOT
from .health import nurl
from .site import load_snapshot

GSC = ROOT / "knowledge" / "gsc"
INBOX = ROOT / "inbox" / "searchconsole"
REP = ROOT / "reports"
# typical share of clicks by position (rough industry curve) - only used to spot pages that under-perform their position
EXPECTED_CTR = {1: .28, 2: .15, 3: .10, 4: .07, 5: .05, 6: .04, 7: .032, 8: .027, 9: .023, 10: .02}
KINDS = (("queries", ("quer", "جستار", "عبارت", "پرسش", "کلمه")), ("pages", ("page", "صفحه", "url", "آدرس")),
         ("dates", ("date", "تاریخ", "روز")), ("countries", ("countr", "کشور")), ("devices", ("device", "دستگاه")))
METRICS = {"clicks": ("click", "کلیک"), "impr": ("impression", "نمایش"), "ctr": ("ctr", "نرخ"), "pos": ("position", "جایگاه", "رتبه", "موقعیت", "میانگین")}


def fnum(s):
    s = str(s or "").strip().translate(DIGITS)
    pct = "%" in s or "٪" in s
    s = s.replace("٬", "").replace("،", "").replace(",", "").replace(" ", "").replace("٫", ".").replace("%", "").replace("٪", "")
    try:
        v = float(s)
    except ValueError:
        return 0.0
    return v / 100 if pct else v


def decode(b):
    for enc in ("utf-8-sig", "utf-16", "cp1256"):
        try:
            return b.decode(enc)
        except UnicodeError:
            continue
    return b.decode("utf-8", "replace")


def read_table(b):
    txt = decode(b)
    try:
        dialect = csv.Sniffer().sniff(txt[:2000], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(txt), dialect) if any(x.strip() for x in r)]
    return (rows[0], rows[1:]) if rows else ([], [])


def _col(header, words, default):
    for i, h in enumerate(header):
        if any(w in h.lower() for w in words):
            return i
    return default


def classify(header, rows):
    h0 = (header[0] if header else "").lower()
    for kind, words in KINDS:
        if any(w in h0 for w in words):
            return kind
    vals = [r[0] for r in rows[:30] if r]
    if vals and sum(v.startswith("http") for v in vals) > len(vals) / 2:
        return "pages"
    if vals and sum(bool(re.match(r"\d{4}-\d{2}-\d{2}", v.translate(DIGITS))) for v in vals) > len(vals) / 2:
        return "dates"
    if vals and {v.upper() for v in vals} <= {"DESKTOP", "MOBILE", "TABLET"}:
        return "devices"
    return None


def parse_rows(header, rows):
    ic, ii, it, ip = (_col(header, METRICS[k], d) for k, d in (("clicks", 1), ("impr", 2), ("ctr", 3), ("pos", 4)))
    out = []
    for r in rows:
        if len(r) <= max(ic, ii):
            continue
        clicks, impr = int(fnum(r[ic])), int(fnum(r[ii]))
        out.append({"key": r[0].strip(), "clicks": clicks, "impr": impr,
                    "ctr": round(clicks / impr, 4) if impr else fnum(r[it]) if len(r) > it else 0.0,
                    "pos": round(fnum(r[ip]), 2) if len(r) > ip else 0.0})
    return out


def explode(files):
    """[(name, bytes)] -> [(name, bytes)] of CSVs (unzips archives)."""
    out = []
    for name, b in files:
        if name.lower().endswith(".zip") or b[:2] == b"PK":
            try:
                with zipfile.ZipFile(io.BytesIO(b)) as z:
                    for n in z.namelist():
                        if n.lower().endswith(".csv") and not n.endswith("/"):
                            out.append((n, z.read(n)))
            except zipfile.BadZipFile:
                raise ValueError(f"«{name}» یک فایل zip سالم نیست.")
        elif name.lower().endswith(".csv"):
            out.append((name, b))
        elif name.lower().endswith((".xlsx", ".xls")):
            raise ValueError("فایل Excel پشتیبانی نمی‌شود. در Search Console گزینهٔ «Download CSV» را بزنید (نه Excel).")
    return out


def import_files(files):
    """-> (data dict, message). Saves knowledge/gsc/gsc_<time>.json."""
    csvs = explode(files)
    if not csvs:
        raise ValueError("هیچ فایل CSV پیدا نشد. باید همان zip که Search Console می‌دهد را بدهید.")
    data = {"imported": datetime.now().isoformat(timespec="seconds"), "files": [n for n, _ in csvs]}
    for name, b in csvs:
        header, rows = read_table(b)
        kind = classify(header, rows)
        if kind and kind not in data:
            data[kind] = parse_rows(header, rows)
    if not data.get("queries") and not data.get("pages"):
        raise ValueError("در این فایل‌ها جدول «Queries» یا «Pages» پیدا نشد. مطمئن شوید از بخش Performance خروجی گرفته‌اید.")
    dates = sorted(d["key"].translate(DIGITS) for d in data.get("dates", []) if re.match(r"\d{4}-\d{2}-\d{2}", d["key"].translate(DIGITS)))
    if dates:
        data["from"], data["to"] = dates[0], dates[-1]
        data["days"] = (datetime.fromisoformat(dates[-1]) - datetime.fromisoformat(dates[0])).days + 1
    src = data.get("dates") or data.get("pages") or []
    data["totals"] = {"clicks": sum(d["clicks"] for d in src), "impr": sum(d["impr"] for d in src)}
    GSC.mkdir(parents=True, exist_ok=True)
    f = GSC / f"gsc_{datetime.now():%Y%m%d_%H%M%S}.json"
    f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    data["_file"] = f.name
    msg = (f"وارد شد: {len(data.get('queries', []))} عبارت، {len(data.get('pages', []))} صفحه"
           + (f"، بازهٔ {data['from']} تا {data['to']} ({data['days']} روز)" if dates else ""))
    return data, msg


def import_inbox():
    """Takes the newest zip (or all loose CSVs) from inbox\\searchconsole, imports, and moves them to _done."""
    INBOX.mkdir(parents=True, exist_ok=True)
    zips = sorted(INBOX.glob("*.zip"), key=lambda p: p.stat().st_mtime)
    csvs = sorted(INBOX.glob("*.csv"))
    use = zips[-1:] if zips else csvs
    if not use:
        raise ValueError("پوشهٔ inbox\\searchconsole خالی است.")
    data, msg = import_files([(p.name, p.read_bytes()) for p in use])
    done = INBOX / "_done"
    done.mkdir(exist_ok=True)
    for p in use:
        p.replace(done / f"{datetime.now():%Y%m%d_%H%M%S}_{p.name}")
    return data, msg


def saved():
    return sorted(GSC.glob("gsc_*.json")) if GSC.exists() else []


def load(f=None):
    f = f or (saved() or [None])[-1]
    if not f:
        return None
    d = json.loads(f.read_text(encoding="utf-8"))
    d["_file"] = f.name
    return d


def brand_terms():
    env = os.getenv("BRAND_TERMS", "").strip()
    if env:
        return [t.strip().lower() for t in env.split(",") if t.strip()]
    host = urlparse(os.getenv("WP_SITE_URL", "")).netloc.lower().removeprefix("www.")
    stem = host.split(".")[0] if host else ""
    return [t for t in (stem, "زعفران مارکت") if t]


def expected(pos):
    return EXPECTED_CTR.get(int(round(pos)) if pos >= 1 else 1, 0.02)


def entity_by_url():
    snap = load_snapshot() or {}
    m = {}
    for lst, kind, tk in (("posts", "post", "title"), ("pages", "page", "title"), ("products", "product", "name")):
        for it in snap.get(lst, []):
            if it.get("url"):
                m[nurl(it["url"])] = {"kind": kind, "id": it["id"], "title": it.get(tk, "")}
    return m


def analyze(d, min_impr=10, prev=None):
    q, p = d.get("queries", []), d.get("pages", [])
    ents = entity_by_url()
    brand = brand_terms()
    isb = lambda k: any(b in k.lower() for b in brand)
    for r in p:
        e = ents.get(nurl(r["key"]))
        r["title"], r["kind"], r["id"] = (e["title"], e["kind"], e["id"]) if e else ("", "", "")
    res = {"top_queries": sorted(q, key=lambda r: -r["clicks"])[:25], "top_pages": sorted(p, key=lambda r: -r["clicks"])[:25]}
    res["near_page_one"] = sorted([r for r in q if 8 <= r["pos"] <= 20 and r["impr"] >= min_impr], key=lambda r: -r["impr"])[:40]
    res["quick_wins"] = sorted([r for r in q if 4 <= r["pos"] < 8 and r["impr"] >= min_impr], key=lambda r: -r["impr"])[:40]
    res["low_ctr_queries"] = sorted([r for r in q if 1 <= r["pos"] <= 10 and r["impr"] >= min_impr and r["ctr"] < 0.5 * expected(r["pos"])], key=lambda r: -r["impr"])[:40]
    res["low_ctr_pages"] = sorted([r for r in p if 1 <= r["pos"] <= 10 and r["impr"] >= min_impr and r["ctr"] < 0.5 * expected(r["pos"])], key=lambda r: -r["impr"])[:40]
    res["zero_click_pages"] = sorted([r for r in p if r["clicks"] == 0 and r["impr"] >= max(min_impr * 3, 30)], key=lambda r: -r["impr"])[:40]
    bq = [r for r in q if isb(r["key"])]
    tot_c = sum(r["clicks"] for r in q) or 1
    res["brand"] = {"queries": len(bq), "clicks": sum(r["clicks"] for r in bq), "share": round(sum(r["clicks"] for r in bq) / tot_c, 3), "terms": brand}
    # published pages that never show up in the export (no impressions at all)
    seen = {nurl(r["key"]) for r in p}
    snap = load_snapshot() or {}
    res["no_impressions"] = [{"title": it.get("title") or it.get("name"), "url": it["url"]} for lst in ("posts", "products") for it in snap.get(lst, [])
                             if it.get("status") == "publish" and it.get("url") and nurl(it["url"]) not in seen][:60] if p else []
    res["compare"] = compare(d, prev) if prev else None
    return res


def compare(cur, prev):
    """Same-length periods only; otherwise the numbers would mislead."""
    a, b = cur.get("days"), prev.get("days")
    if not a or not b or abs(a - b) > 0.25 * max(a, b):
        return {"note": f"بازهٔ دو خروجی هم‌اندازه نیست ({b} روز در برابر {a} روز)؛ مقایسه گمراه‌کننده است. دفعهٔ بعد همان بازه را انتخاب کنید."}
    out = {}
    for kind in ("queries", "pages"):
        pv = {r["key"]: r for r in prev.get(kind, [])}
        rows = []
        for r in cur.get(kind, []):
            o = pv.get(r["key"])
            rows.append({"key": r["key"], "clicks": r["clicks"], "d_clicks": r["clicks"] - (o["clicks"] if o else 0), "impr": r["impr"],
                         "d_impr": r["impr"] - (o["impr"] if o else 0), "pos": r["pos"], "d_pos": round(r["pos"] - o["pos"], 1) if o else None, "new": o is None})
        for k, o in pv.items():
            if not any(x["key"] == k for x in cur.get(kind, [])) and o["clicks"] >= 3:
                rows.append({"key": k, "clicks": 0, "d_clicks": -o["clicks"], "impr": 0, "d_impr": -o["impr"], "pos": 0, "d_pos": None, "gone": True})
        out[kind] = {"up": sorted([r for r in rows if r["d_clicks"] > 0], key=lambda r: -r["d_clicks"])[:15],
                     "down": sorted([r for r in rows if r["d_clicks"] < 0], key=lambda r: r["d_clicks"])[:15]}
    out["prev_file"] = prev.get("_file", "")
    return out


def write_report(d, res):
    REP.mkdir(exist_ok=True)
    f = REP / ("gsc_opportunities_" + d.get("_file", "latest").replace("gsc_", "").replace(".json", "") + ".csv")
    names = {"near_page_one": "نزدیک صفحهٔ اول", "quick_wins": "رتبهٔ ۴ تا ۷ (رسیدن به سه‌تای اول)", "low_ctr_queries": "کلیک کم برای عبارت",
             "low_ctr_pages": "کلیک کم برای صفحه", "zero_click_pages": "نمایش بدون کلیک"}
    with open(f, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["type", "key", "clicks", "impressions", "ctr_percent", "position", "page_title"])
        for k, label in names.items():
            for r in res[k]:
                w.writerow([label, r["key"], r["clicks"], r["impr"], round(r["ctr"] * 100, 2), r["pos"], r.get("title", "")])
    return f
