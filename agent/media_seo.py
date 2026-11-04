"""Stage 6 - images, product-category texts and Schema validation.
1. Media library audit (code, read-only): size, format, dimensions, file names, missing/duplicate/name-like Alt.
2. Alt text from the CONTEXT of the article that uses the image (nearest heading / the Alt already written in the article) - no vision, no AI. Apply = tick -> backup -> log.
3. Product-category descriptions: audit (code) + ONE optional AI call that drafts texts only from the category name and its real products; you edit, tick, apply (backup + log).
4. Schema validation (read-only): fetches a sample of pages, parses the JSON-LD and checks the fields Google needs. Producing Schema stays with Yoast/WooCommerce."""
import csv
import html
import json
import os
import re
import time
from collections import Counter
from datetime import datetime
from urllib.parse import unquote, urlparse

from . import llm
from .common import StepError, ensure, log_action, norm
from .config import ROOT, load_config
from .corpus import read_articles
from .site import fetch_all, load_snapshot
from .wp_client import WPClient

MEDIA, PROP, BACKUP, REP = ROOT / "knowledge" / "media", ROOT / "proposals", ROOT / "backups", ROOT / "reports"
BIG, HUGE, MAX_W = 250 * 1024, 500 * 1024, 2400
NAME_BAD = re.compile(r"^(img|image|dsc|dscn|photo|pic|screenshot|screen[-_ ]?shot|whatsapp[-_ ].*|wp|untitled|download|unnamed|\d+)[-_ ]?[\d_ -]*$|copy|\(\d+\)|^[\d_ -]+$", re.I)
SEV = {"H": "جدی", "M": "متوسط", "L": "کم"}
M_TXT = {"m_no_alt": "بدون Alt", "m_alt_name": "Alt شبیه نام فایل", "m_alt_long": "Alt بیش از ۱۲۵ کاراکتر", "m_alt_dup": "Alt تکراری در چند تصویر", "m_huge": "حجم بیش از ۵۰۰ کیلوبایت",
         "m_big": "حجم بیش از ۲۵۰ کیلوبایت", "m_dims": f"عرض بیش از {MAX_W} پیکسل", "m_format": "JPEG/PNG به‌جای WebP", "m_name": "نام فایل غیرتوصیفی (IMG_123 و مانند آن)"}
C_TXT = {"c_no_desc": "بدون توضیح", "c_short": "توضیح کمتر از ۵۰ کلمه", "c_dup": "توضیح تکراری", "c_no_image": "بدون تصویر", "c_empty": "بدون محصول", "c_no_name": "نام دسته در توضیح نیست"}


def client():
    c = WPClient(load_config())
    c.detect_mode()
    return c


def _plain(h):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", h or "")).split())


def stem(url):
    s = unquote(urlparse(str(url)).path.rsplit("/", 1)[-1])
    s = re.sub(r"\.[A-Za-z0-9]+$", "", s)
    return re.sub(r"-(?:scaled|e\d{10,}|\d{2,5}x\d{2,5})$", "", s).lower()


# ------------------------------------------------------------------ 1) media library audit
def audit_media(log=print, save=True):
    c = client()
    items = fetch_all(c, "wp/v2/media", "wp", {"media_type": "image", "_fields": "id,source_url,alt_text,mime_type,media_details,post"})
    alts = Counter(norm(i.get("alt_text") or "") for i in items if (i.get("alt_text") or "").strip())
    rows, cnt = [], Counter()
    for i in items:
        md, url = i.get("media_details") or {}, i.get("source_url", "")
        alt, st, size, w = (i.get("alt_text") or "").strip(), stem(url), md.get("filesize"), md.get("width") or 0
        f = []
        if not alt:
            f.append(("M", "m_no_alt"))
        else:
            if norm(alt) == norm(st.replace("-", " ").replace("_", " ")) or alt.lower() == st:
                f.append(("M", "m_alt_name"))
            if len(alt) > 125:
                f.append(("L", "m_alt_long"))
            if alts[norm(alt)] >= 3:
                f.append(("L", "m_alt_dup"))
        if size and size > HUGE:
            f.append(("H", "m_huge"))
        elif size and size > BIG:
            f.append(("M", "m_big"))
        if w > MAX_W:
            f.append(("M", "m_dims"))
        if i.get("mime_type") in ("image/jpeg", "image/png") and (not size or size > 100 * 1024):
            f.append(("L", "m_format"))
        if NAME_BAD.search(st):
            f.append(("L", "m_name"))
        for _, code in f:
            cnt[code] += 1
        rows.append({"id": i["id"], "url": url, "alt": alt, "mime": i.get("mime_type", ""), "size": size, "w": w, "h": md.get("height") or 0, "flags": f})
    res = {"built": datetime.now().isoformat(timespec="seconds"), "total": len(rows), "counts": dict(cnt), "has_size": any(r["size"] for r in rows),
           "items": [r for r in rows if r["flags"]], "no_alt": sum(1 for r in rows if not r["alt"])}
    if save:
        MEDIA.mkdir(parents=True, exist_ok=True)
        (MEDIA / "media.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        REP.mkdir(exist_ok=True)
        with open(REP / "media_audit.csv", "w", newline="", encoding="utf-8-sig") as fh:
            w_ = csv.writer(fh)
            w_.writerow(["id", "url", "alt", "mime", "size_kb", "width", "problems"])
            w_.writerows([[r["id"], r["url"], r["alt"], r["mime"], round((r["size"] or 0) / 1024), r["w"], "؛ ".join(M_TXT[c_] for _, c_ in r["flags"])] for r in res["items"]])
    log(f"کتابخانهٔ رسانه: {res['total']} تصویر؛ {res['no_alt']} بدون Alt، " + "، ".join(f"{M_TXT[k]} {v}" for k, v in sorted(cnt.items(), key=lambda x: -x[1]) if k != "m_no_alt") + ".")
    if not res["has_size"]:
        log("نکته: وردپرس حجم فایل را نمی‌دهد (نسخه‌های قدیمی)؛ بررسی حجم انجام نشد.")
    log("گزارش: reports\\media_audit.csv. تبدیل به WebP و کم‌کردن حجم را با افزونهٔ بهینه‌سازی تصویر (مثل ShortPixel/Imagify/EWWW) انجام دهید؛ این برنامه فایل‌ها را دست نمی‌زند.")
    return res


def load_media():
    f = MEDIA / "media.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


# ------------------------------------------------------------------ 2) Alt from the context of the article that uses the image
def _informative(a):
    a = (a or "").strip()
    return len(a) >= 8 and " " in a and not NAME_BAD.search(a.replace(" ", "-"))


def article_images():
    """{file stem: {heading, title, md_alt, url, id}} for every image used in an exported article."""
    out = {}
    for a in read_articles():
        head = ""
        for line in a["body"].splitlines():
            m = re.match(r"^#{2,3}\s+(.+?)\s*$", line)
            if m:
                head = m.group(1)
                continue
            for alt, src in re.findall(r"!\[([^\]]*)\]\(\s*([^)\s]+)", line):
                out.setdefault(stem(src), {"heading": head, "title": a["title"], "md_alt": alt, "url": a.get("url", ""), "id": a.get("id")})
    return out


def context_alt(info):
    if _informative(info["md_alt"]):
        return " ".join(info["md_alt"].split())[:110], "Alt همان تصویر در متن مقاله"
    h = re.sub(r"[؟?!:]+$", "", info["heading"]).strip()
    if h and 6 <= len(h) <= 90:
        t = info["title"]
        return (h if norm(h) in norm(t) or len(h) > 45 else f"{h} - {t}")[:110], "عنوان بخشی از مقاله که تصویر در آن است"
    return info["title"][:110], "عنوان مقاله"


def build_alt(res=None, log=print):
    res = res or load_media()
    if not res:
        raise StepError("اول «ممیزی تصاویر» را بزنید.")
    ctx = article_images()
    rows, none = [], 0
    for r in res["items"]:
        codes = {c for _, c in r["flags"]}
        if not codes & {"m_no_alt", "m_alt_name"}:
            continue
        info = ctx.get(stem(r["url"]))
        if not info:
            none += 1
            continue
        alt, why = context_alt(info)
        rows.append({"id": r["id"], "url": r["url"], "cur": r["alt"], "new": alt, "why": why, "article": info["title"], "article_url": info["url"]})
    PROP.mkdir(exist_ok=True)
    f = PROP / "media_alt.json"
    f.write_text(json.dumps({"built": datetime.now().isoformat(timespec="seconds"), "items": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"{len(rows)} پیشنهاد Alt ساخته شد؛ {none} تصویر در هیچ مقاله‌ای استفاده نشده (Alt محصول‌ها را «اصلاح سلامت ← Alt تصویرها» می‌سازد).")
    return rows


def load_alt():
    f = PROP / "media_alt.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def apply_alt(selected, edits, go, log=print):
    pr = load_alt() or {"items": []}
    todo = [r for r in pr["items"] if str(r["id"]) in selected]
    for r in todo:
        r["new"] = " ".join(str(edits.get(str(r["id"]), r["new"])).split())[:125]
    todo = [r for r in todo if r["new"]]
    log(f"{len(todo)} تصویر انتخاب شده است.")
    for r in todo[:8]:
        log(f"  #{r['id']}: «{r['cur']}» ← «{r['new']}»")
    if not go:
        log("Preview only. Nothing sent.")
        return 0
    if not todo:
        return 0
    c = client()
    BACKUP.mkdir(exist_ok=True)
    (BACKUP / f"media_alt_{datetime.now():%Y%m%d_%H%M%S}.json").write_text(json.dumps([{"id": r["id"], "alt": r["cur"]} for r in todo], ensure_ascii=False, indent=1), encoding="utf-8")
    n = 0
    for r in todo:
        ensure(c.request("POST", f"wp/v2/media/{r['id']}", auth="wp", json={"alt_text": r["new"]}), f"update media {r['id']}")
        log_action("media_alt", {"id": r["id"], "old": r["cur"], "new": r["new"]})
        n += 1
    done = {str(r["id"]) for r in todo}
    pr["items"] = [r for r in pr["items"] if str(r["id"]) not in done]
    (PROP / "media_alt.json").write_text(json.dumps(pr, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"{n} Alt روی سایت ثبت شد؛ مقدار قبلی در backups ذخیره شد.")
    return n


# ------------------------------------------------------------------ 3) product-category texts
def audit_cats(log=print, save=True):
    c = client()
    cats = [x for x in fetch_all(c, "wc/v3/products/categories", "wc", {"hide_empty": "false"}) if x.get("slug") != "uncategorized"]
    plain = {x["id"]: _plain(x.get("description")) for x in cats}
    dups = Counter(norm(t) for t in plain.values() if t)
    out = []
    for x in cats:
        t, f = plain[x["id"]], []
        n = len(t.split())
        if not t:
            f.append(("M", "c_no_desc"))
        elif n < 50:
            f.append(("M", "c_short"))
        if t and dups[norm(t)] > 1:
            f.append(("M", "c_dup"))
        if t and norm(x["name"]) not in norm(t):
            f.append(("L", "c_no_name"))
        if not x.get("image"):
            f.append(("L", "c_no_image"))
        if not x.get("count"):
            f.append(("L", "c_empty"))
        out.append({"id": x["id"], "name": html.unescape(x["name"]), "slug": x.get("slug", ""), "parent": x.get("parent", 0), "count": x.get("count", 0), "words": n, "desc": t,
                    "link": x.get("link", ""), "flags": f})
    res = {"built": datetime.now().isoformat(timespec="seconds"), "items": out}
    if save:
        MEDIA.mkdir(parents=True, exist_ok=True)
        (MEDIA / "cats.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    bad = [x for x in out if any(c_ in ("c_no_desc", "c_short", "c_dup") for _, c_ in x["flags"])]
    log(f"دسته‌های محصول: {len(out)}؛ {len(bad)} دسته توضیح ندارد، کوتاه یا تکراری است.")
    return res


def load_cats():
    f = MEDIA / "cats.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


CAT_SYS = ("تو نویسندهٔ محتوای «زعفران مارکت» (فروشگاه زعفران) هستی و فارسی روان و طبیعی می‌نویسی. برای هر دستهٔ محصول (cats) یک توضیح صفحهٔ دسته بنویس. فقط یک شیء JSON بده (بدون ``` و توضیح) با کلید شناسهٔ دسته و مقدار متن. "
           "قواعد: ۸۰ تا ۱۴۰ کلمه در دو پاراگراف کوتاه؛ فقط از اطلاعاتی که در cats آمده (نام دسته، نام والد، نام محصولات واقعی آن) استفاده کن و هیچ ویژگی، منشأ، درجه، قیمت، عدد یا آمار از خودت نساز؛ "
           "پاراگراف اول بگوید این دسته چیست و برای چه کسی مناسب است و نام دسته را طبیعی یک بار بیاورد؛ پاراگراف دوم به شیوهٔ انتخاب و تفاوت محصولات همین دسته کمک کند (بر پایهٔ نام محصولات)؛ "
           "ادعای درمانی، پزشکی و اغراق و تضمین ننویس؛ هیچ لینک و آدرس ننویس؛ متن هر دسته با دسته‌های دیگر متفاوت و یونیک باشد و کلیشه («طلای سرخ»، «در دنیای امروز») نداشته باشد.")
MAX_CATS = 12


def _cat_msgs(targets, snap):
    prods = [p for p in snap.get("products", []) if p.get("status") == "publish"]
    from .keywords import in_stock
    data = []
    for t in targets:
        names = [p["name"] for p in prods if t["name"] in (p.get("categories") or []) and in_stock(p)][:8]
        data.append({"id": str(t["id"]), "name": t["name"], "count": t["count"], "products": names})
    return [{"role": "system", "content": CAT_SYS}, {"role": "user", "content": json.dumps({"cats": data}, ensure_ascii=False, separators=(",", ":"))}]


def draft_cats(go, log=print):
    from .articlegen import _gate, med_findings
    res = load_cats()
    if not res:
        raise StepError("اول «ممیزی دسته‌ها» را بزنید.")
    targets = [x for x in res["items"] if {c for _, c in x["flags"]} & {"c_no_desc", "c_short"} and x["count"] > 0][:MAX_CATS]
    if not targets:
        log("هیچ دستهٔ دارای محصول و بدون توضیح کافی نیست.")
        return []
    snap = load_snapshot() or {}
    msgs = _cat_msgs(targets, snap)
    c = llm.cfg()
    if not _gate(c, [msgs], min(c["max_out"], 1600), go, f"نوشتن توضیح برای {len(targets)} دستهٔ محصول"):
        return []
    text, info = llm.chat(msgs, max_out=min(c["max_out"], 1600), task="cat_desc")
    r = llm.parse_json_obj(text)
    items = []
    for t in targets:
        new = " ".join(str(r.get(str(t["id"]), "")).split())
        new = re.sub(r"https?://\S+|www\.\S+", "", new).strip()
        if not new:
            continue
        flags = (["پزشکی/اغراق: " + "، ".join(sorted({f["word"] for f in med_findings(new)}))] if med_findings(new) else []) + (["کوتاه"] if len(new.split()) < 40 else [])
        items.append({"id": t["id"], "name": t["name"], "old": t["desc"], "new": new, "flags": flags})
    PROP.mkdir(exist_ok=True)
    (PROP / "cat_desc.json").write_text(json.dumps({"built": datetime.now().isoformat(timespec="seconds"), "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"{len(items)} توضیح نوشته شد (هزینه ${info['cost']:.4f}). بخوانید، ویرایش کنید و تیک بزنید؛ چیزی هنوز در سایت نیست.")
    return items


def load_cat_drafts():
    f = PROP / "cat_desc.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def apply_cats(selected, edits, go, log=print):
    pr = load_cat_drafts() or {"items": []}
    todo = []
    for r in pr["items"]:
        if str(r["id"]) in selected:
            txt = str(edits.get(str(r["id"]), r["new"])).strip()
            if txt:
                todo.append({**r, "new": txt})
    log(f"{len(todo)} دسته انتخاب شده است.")
    for r in todo:
        log(f"  #{r['id']} {r['name']}: {len(r['new'].split())} کلمه" + (f" ⚠ {'؛ '.join(r['flags'])}" if r["flags"] else ""))
    if not go:
        log("Preview only. Nothing sent.")
        return 0
    if not todo:
        return 0
    c = client()
    BACKUP.mkdir(exist_ok=True)
    (BACKUP / f"cat_desc_{datetime.now():%Y%m%d_%H%M%S}.json").write_text(json.dumps([{"id": r["id"], "description": r["old"]} for r in todo], ensure_ascii=False, indent=1), encoding="utf-8")
    for r in todo:
        body = "".join(f"<p>{html.escape(p.strip())}</p>" for p in re.split(r"\n+", r["new"]) if p.strip())
        ensure(c.request("POST", f"wc/v3/products/categories/{r['id']}", auth="wc", json={"description": body}), f"update category {r['id']}")
        log_action("cat_desc", {"id": r["id"], "name": r["name"]})
    done = {str(r["id"]) for r in todo}
    pr["items"] = [r for r in pr["items"] if str(r["id"]) not in done]
    (PROP / "cat_desc.json").write_text(json.dumps(pr, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"{len(todo)} توضیح دسته روی سایت ثبت شد؛ متن قبلی در backups ذخیره شد.")
    return len(todo)


# ------------------------------------------------------------------ 4) Schema validation (read-only)
LD = re.compile(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", re.S | re.I)


def _types(o):
    t = o.get("@type")
    return [t] if isinstance(t, str) else [x for x in (t or []) if isinstance(x, str)]


def _flat(data):
    for o in data if isinstance(data, list) else [data]:
        if isinstance(o, dict):
            if "@graph" in o:
                yield from _flat(o["@graph"])
            else:
                yield o


def check_page(kind, url, page_html):
    """-> list of (severity, message) for one page."""
    out, objs = [], []
    blocks = LD.findall(page_html or "")
    for b in blocks:
        try:
            objs += list(_flat(json.loads(b.strip())))
        except ValueError:
            out.append(("H", "یک بلوک JSON-LD نامعتبر است (گوگل آن را نمی‌خواند)"))
    types = Counter(t for o in objs for t in _types(o))
    if not blocks:
        return [("M", "هیچ Schema (JSON-LD) ندارد")]
    for o in objs:
        ts = _types(o)
        if "Product" in ts:
            miss = [k for k in ("name", "image") if not o.get(k)]
            off = o.get("offers")
            off = (off[0] if isinstance(off, list) and off else off) or {}
            if isinstance(off, dict):
                inner = (off.get("offers") or [off])[0] if off.get("@type") == "AggregateOffer" and off.get("offers") else off
                miss += [f"offers.{k}" for k in ("price", "priceCurrency", "availability") if not (inner.get(k) or off.get(k) or off.get("lowPrice"))]
            else:
                miss.append("offers")
            if miss:
                out.append(("M", "Product: فیلد ناقص: " + "، ".join(miss)))
        if {"Article", "BlogPosting", "NewsArticle"} & set(ts):
            miss = [k for k in ("headline", "datePublished", "author") if not o.get(k)]
            if miss:
                out.append(("M", "Article: فیلد ناقص: " + "، ".join(miss)))
            if not o.get("image"):
                out.append(("L", "Article: image ندارد"))
        if "BreadcrumbList" in ts and not o.get("itemListElement"):
            out.append(("M", "BreadcrumbList خالی است"))
    if kind == "product" and not types["Product"]:
        out.append(("M", "صفحهٔ محصول Schema نوع Product ندارد"))
    if kind == "article" and not ({"Article", "BlogPosting", "NewsArticle"} & set(types)):
        out.append(("L", "مقاله Schema نوع Article ندارد"))
    if kind == "home" and not ({"Organization", "WebSite", "WebPage"} & set(types)):
        out.append(("L", "صفحهٔ اصلی Organization/WebSite ندارد"))
    if types["Product"] > 1:
        out.append(("L", "بیش از یک Product در یک صفحه (احتمال تداخل افزونه‌ها)"))
    return out


def _http_get(url):
    import requests
    r = requests.get(url, timeout=20, headers={"User-Agent": "ZafaranAgent/1.0 (read-only schema check)"})
    return r.text if r.status_code == 200 else ""


def validate_schema(limit=12, fetch=None, log=print):
    fetch = fetch or _http_get
    snap = load_snapshot() or {}
    from .keywords import in_stock
    arts = sorted([a for a in read_articles() if a.get("url") and str(a.get("status", "publish")) == "publish"], key=lambda a: str(a.get("modified", "")), reverse=True)[:limit]
    prods = [p for p in snap.get("products", []) if p.get("status") == "publish" and p.get("url")]
    prods = sorted(prods, key=lambda p: not in_stock(p))[:limit]
    pages = ([("home", snap["site"])] if snap.get("site") else []) + [("article", a["url"]) for a in arts] + [("product", p["url"]) for p in prods]
    if not pages:
        raise StepError("اول «همگام‌سازی سایت» و «خروجی مقاله‌ها» را بزنید.")
    delay = float(os.getenv("HEALTH_DELAY", "0.25"))
    rows = []
    for kind, url in pages:
        h = fetch(url)
        rows.append({"kind": kind, "url": url, "issues": check_page(kind, url, h) if h else [("H", "صفحه باز نشد")]})
        time.sleep(delay)
    res = {"built": datetime.now().isoformat(timespec="seconds"), "pages": rows}
    MEDIA.mkdir(parents=True, exist_ok=True)
    (MEDIA / "schema.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    bad = [r for r in rows if r["issues"]]
    log(f"Schema: {len(rows)} صفحه بررسی شد؛ {len(bad)} صفحه مشکل دارد. (فقط بررسی است؛ تولید Schema با Yoast/ووکامرس.)")
    return res


def load_schema():
    f = MEDIA / "schema.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
