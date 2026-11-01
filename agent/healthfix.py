"""Fixing what the health check finds, case by case.
Code first (links, alt text, https, noindex), AI only where writing is needed (meta/title/focus keyword, short descriptions).
Every change that touches the site: proposal -> preview -> explicit tick -> backup + log."""
import html
import json
import re
from collections import defaultdict
from datetime import datetime
from urllib.parse import unquote, urljoin, urlparse

import requests

from . import llm
from .common import StepError, confirm, ensure, log_action, norm
from .config import ROOT, load_config
from .health import LATEST, nurl
from .site import load_snapshot
from .wp_client import WPClient

PROP = ROOT / "proposals"
BACKUP = ROOT / "backups"
ROUTE = {"post": "wp/v2/posts", "page": "wp/v2/pages", "product": "wc/v3/products"}
AUTH = {"post": "wp", "page": "wp", "product": "wc"}
YOAST = {"k": "_yoast_wpseo_focuskw", "t": "_yoast_wpseo_title", "m": "_yoast_wpseo_metadesc"}
NOINDEX_KEY = "_yoast_wpseo_meta-robots-noindex"

# ---------------------------------------------------------------- guides
# mode: auto = code fixes it here | ai = AI writes a suggestion here | manual = you do it in WordPress | info = usually nothing to do
MODE_LABEL = {"auto": ("خودکار با کد", "#1b6e3c"), "ai": ("با AI", "#1c5d99"), "manual": ("دستی در وردپرس", "#a65f00"), "info": ("معمولاً نیازی نیست", "#6b5b4b")}
GUIDE = {
    "robots_blocks_all": ("manual", "گوگل اجازهٔ دیدن هیچ صفحه‌ای را ندارد؛ سایت از نتایج بیرون می‌افتد.",
                          "وردپرس ← تنظیمات ← خواندن ← تیک «Discourage search engines» باید خاموش باشد. اگر خاموش است، فایل robots.txt (در Yoast: ابزارها ← ویرایشگر فایل) را بررسی کنید."),
    "unreachable": ("manual", "صفحه اصلاً باز نشد (قطعی، کندی یا مسدودسازی).",
                    "یک بار خودتان صفحه را باز کنید. اگر باز می‌شود و فقط خزندهٔ ما مسدود است، HEALTH_DELAY را در .env بالا ببرید (مثلاً 2)."),
    "http_error": ("manual", "بازدیدکننده و گوگل خطا می‌بینند؛ صفحه از نتایج حذف می‌شود.",
                   "اگر صفحه باید باشد، وضعیتش را در وردپرس ببینید (پیش‌نویس؟ حذف‌شده؟). اگر عمداً حذف شده، با افزونهٔ ریدایرکت (Redirection یا بخش Redirects در Yoast Premium) آن را ۳۰۱ به نزدیک‌ترین صفحه ببرید."),
    "redirected": ("info", "آدرس منتشرشده خودش به جای دیگری می‌رود؛ معمولاً بعد از تغییر slug.",
                   "اگر ریدایرکت عمدی است کاری لازم نیست. مهم این است که لینک‌های داخلی مستقیم به آدرس نهایی بروند (گروه «لینک‌های ریدایرکت‌شونده»)."),
    "redirect_chain": ("manual", "چند ریدایرکت پشت هم؛ کند است و اعتبار لینک را کم می‌کند.", "ریدایرکت قدیمی را مستقیم به آدرس نهایی بدهید."),
    "redirect_loop": ("manual", "حلقهٔ ریدایرکت؛ صفحه هرگز باز نمی‌شود.", "ریدایرکت‌های این آدرس را در افزونهٔ ریدایرکت یا .htaccess بررسی و اصلاح کنید."),
    "noindex": ("auto", "صفحهٔ noindex در گوگل نمایش داده نمی‌شود.",
                "فقط صفحه‌های کاربردی (سبد خرید، پرداخت، حساب کاربری، ورود، تشکر) باید noindex باشند. مقاله، محصول و صفحه‌های معرفی باید index شوند. ستون «تشخیص» کنارِ هر مورد می‌گوید کدام است."),
    "canonical_missing": ("manual", "بدون canonical گوگل ممکن است نسخه‌های تکراری آدرس را جدا بشمارد.", "معمولاً Yoast خودش می‌گذارد. بررسی کنید Yoast فعال است و قالب یا افزونهٔ دیگری آن را حذف نمی‌کند."),
    "canonical_other": ("manual", "canonical به صفحهٔ دیگری اشاره می‌کند؛ گوگل این صفحه را نادیده می‌گیرد.",
                        "اگر عمدی نیست: در Yoast، تب Advanced همان صفحه، فیلد Canonical URL را خالی کنید."),
    "title_missing": ("manual", "بدون عنوان، گوگل خودش عنوان می‌سازد و معمولاً بد می‌شود.", "در Yoast عنوان سئو را پر کنید، یا قالب عنوان (Search appearance) را بررسی کنید."),
    "title_dup": ("ai", "عنوان تکراری باعث می‌شود گوگل نداند کدام صفحه را نشان دهد.", "AI برای هر صفحه یک عنوان یکتا پیشنهاد می‌دهد؛ شما می‌بینید و ویرایش می‌کنید."),
    "title_long": ("ai", "عنوان بلند در نتایج گوگل بریده می‌شود.", "AI عنوان کوتاه‌تر (حداکثر ۶۰ کاراکتر) پیشنهاد می‌دهد."),
    "meta_missing": ("ai", "بدون متا گوگل متن دلخواه برمی‌دارد و نرخ کلیک کم می‌شود.", "AI متا (۱۱۰ تا ۱۶۰ کاراکتر) می‌نویسد؛ شما بازبینی می‌کنید."),
    "meta_dup": ("ai", "متای تکراری یعنی دو صفحه در نتایج یکسان دیده می‌شوند.", "AI متای یکتا پیشنهاد می‌دهد."),
    "h1_none": ("manual", "H1 مهم‌ترین تیتر صفحه است.", "معمولاً مشکل قالب است (عنوان صفحه باید H1 باشد). از پشتیبان قالب بپرسید یا در ویرایشگر یک H1 بگذارید."),
    "h1_multi": ("info", "بیش از یک H1 مشکل بزرگی نیست، ولی بهتر است یکی باشد.", "اگر داخل متن مقاله H1 گذاشته‌اید آن را H2 کنید؛ اگر از قالب است نادیده بگیرید."),
    "img_no_alt": ("auto", "Alt هم برای دسترسی‌پذیری و هم برای جست‌وجوی تصویر مهم است.",
                   "کد برای تصویرهای داخل متن و تصویر شاخص Alt می‌گذارد (پایه: عنوان صفحه). تصویرهای قالب/لوگو/اسلایدر را باید در خود قالب اصلاح کنید. Alt توصیفی‌تر در مرحلهٔ ۶ نقشهٔ راه می‌آید."),
    "schema_none": ("manual", "Schema به گوگل کمک می‌کند صفحه را بهتر بفهمد.", "Yoast ← تنظیمات ← Site representation و Schema را کامل کنید؛ اگر Yoast فعال است و باز هم نیست، تداخل افزونه/قالب را بررسی کنید."),
    "schema_no_product": ("manual", "بدون Schema محصول، قیمت و موجودی در نتایج گوگل نشان داده نمی‌شود.", "ووکامرس خودش Schema محصول را می‌دهد؛ اگر نیست، افزونهٔ سئو یا قالب آن را غیرفعال کرده. با «Rich Results Test» گوگل یک محصول را آزمایش کنید."),
    "schema_no_article": ("info", "اهمیتش کم است.", "Yoast ← تنظیمات ← Content types ← Posts ← نوع Schema را روی Article بگذارید."),
    "mixed_content": ("auto", "منبع http داخل صفحهٔ https، قفل امنیتی مرورگر را خراب می‌کند.", "کد در متن همان صفحه http را به https تبدیل می‌کند. اگر بعد از رفع باز هم بود، منبعش در قالب/افزونه است."),
    "robots_missing": ("info", "بدون robots.txt همه‌چیز مجاز است؛ معمولاً مشکلی نیست.", "Yoast یک robots.txt مجازی می‌سازد؛ نبودنش معمولاً به کش یا هاست برمی‌گردد."),
    "sitemap_missing": ("manual", "Sitemap به گوگل می‌گوید چه صفحه‌هایی دارید.", "Yoast ← تنظیمات ← Site features ← XML sitemaps را روشن کنید و آدرسش را در Search Console ثبت کنید."),
    "sitemap_missing_url": ("manual", "صفحهٔ منتشرشده در Sitemap نیست.", "معمولاً به خاطر noindex یا تنظیم Yoast است. اول گروه noindex را درست کنید، بعد دوباره بررسی کنید."),
    "sitemap_dead_url": ("manual", "آدرس مرده داخل Sitemap به گوگل سیگنال بد می‌دهد.", "کش Sitemap را پاک کنید: Yoast ← ابزارها، یا یک بار «ذخیره» در تنظیمات پیوند یکتا."),
    "broken_internal_link": ("auto", "لینک شکسته تجربهٔ بد می‌سازد و اعتبار سایت را کم می‌کند.",
                             "برای هر لینک انتخاب کنید: حذف لینک (متن می‌ماند) یا جایگزینی با آدرس درست. اگر لینک در منو/قالب است، از وردپرس ← نمایش ← فهرست‌ها اصلاحش کنید."),
    "link_redirects": ("auto", "لینکی که به آدرس ریدایرکت‌شونده می‌رود کند است؛ بعد از تغییر slug طبیعی است.", "کد لینک را در متن مقاله/صفحه/محصول با آدرس نهایی عوض می‌کند (قبلش پشتیبان می‌گیرد)."),
    "p_no_short": ("ai", "توضیح کوتاه در صفحهٔ محصول بالای دکمهٔ خرید دیده می‌شود.", "AI از روی توضیح کامل محصول، دو جملهٔ کوتاه می‌نویسد؛ فقط از اطلاعات خودِ محصول."),
    "p_desc_tiny": ("manual", "توضیح خیلی کوتاه یعنی محتوای کم برای گوگل و خریدار.", "نیاز به اطلاعات واقعی محصول (منشأ، درجه، بسته‌بندی) دارد؛ AI نباید حدس بزند. ابتدا پرفروش‌ها را بنویسید؛ در مرحلهٔ ۴ نقشهٔ راه پیش‌نویس‌ساز می‌آید."),
    "p_desc_short": ("manual", "توضیح کمتر از ۱۰۰ کلمه است.", "در اولویت بعد از موارد جدی؛ همان روش مورد قبل."),
    "p_dup_desc": ("manual", "توضیح یکسان برای دو محصول، گوگل را سردرگم می‌کند.", "برای هر محصول توضیح اختصاصی بنویسید (وزن، منشأ، کاربرد را متمایز کنید)."),
    "p_img_no_alt": ("auto", "Alt تصویر محصول به جست‌وجوی تصویر کمک می‌کند.", "کد Alt را از نام محصول می‌سازد و روی خودِ تصویر ذخیره می‌کند."),
    "p_no_images": ("manual", "محصول بی‌تصویر تقریباً فروش نمی‌رود.", "در ویرایش محصول تصویر اضافه کنید."),
    "p_no_category": ("manual", "محصول بدون دسته در منو و فیلترها دیده نمی‌شود.", "در ویرایش محصول دسته را انتخاب کنید."),
    "p_no_price": ("manual", "محصول بدون قیمت قابل خرید نیست.", "قیمت را وارد کنید یا محصول را پیش‌نویس کنید."),
    "p_oos": ("info", "ناموجود بودن موقت طبیعی است.", "اگر دیگر نمی‌فروشید، پیش‌نویسش کنید یا به محصول مشابه ریدایرکت دهید."),
    "p_no_focus": ("ai", "بدون کلمهٔ کلیدی Yoast نمی‌تواند صفحه را ارزیابی کند.", "AI کلمهٔ کلیدی مناسب از روی نام و توضیح محصول پیشنهاد می‌دهد."),
    "p_no_meta": ("ai", "بدون متا گوگل متن دلخواه برمی‌دارد.", "AI متای ۱۱۰ تا ۱۶۰ کاراکتری پیشنهاد می‌دهد."),
}

GROUPS = {
    "links": ("لینک‌های ریدایرکت‌شونده", ["link_redirects"], False),
    "broken": ("لینک‌های شکسته", ["broken_internal_link"], False),
    "alt": ("Alt تصویرها", ["img_no_alt", "p_img_no_alt"], False),
    "mixed": ("محتوای ناامن (http)", ["mixed_content"], False),
    "noindex": ("noindex", ["noindex"], False),
    "seo": ("عنوان، متا و کلمهٔ کلیدی", ["meta_missing", "p_no_meta", "p_no_focus", "title_long", "title_dup", "meta_dup"], True),
    "short": ("توضیح کوتاه محصول", ["p_no_short"], True),
}
GROUP_OF = {c: g for g, (_, codes, _) in GROUPS.items() for c in codes}


# ---------------------------------------------------------------- helpers
def latest():
    try:
        return json.loads(LATEST.read_text(encoding="utf-8"))
    except Exception:
        return None


def entity_map():
    snap = load_snapshot() or {}
    m = {}
    for lst, kind, tk in (("posts", "post", "title"), ("pages", "page", "title"), ("products", "product", "name")):
        for it in snap.get(lst, []):
            if it.get("url"):
                m[nurl(it["url"])] = {"kind": kind, "id": it["id"], "title": it.get(tk, ""), "url": it["url"], "status": it.get("status")}
    return m


def _client():
    c = WPClient(load_config())
    c.detect_mode()
    return c


def get_rec(c, e):
    params = {} if e["kind"] == "product" else {"context": "edit"}
    return ensure(c.get(f"{ROUTE[e['kind']]}/{e['id']}", auth=AUTH[e["kind"]], params=params), f"read {e['kind']} {e['id']}")


def text_fields(kind, rec):
    if kind == "product":
        return {"description": rec.get("description") or "", "short_description": rec.get("short_description") or ""}
    return {"content": (rec.get("content") or {}).get("raw") or ""}


def plain(h):
    h = re.sub(r"<!--.*?-->", " ", h or "", flags=re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", h)).split())


def yoast_of(kind, rec):
    if kind == "product":
        md = {m.get("key"): m.get("value") for m in rec.get("meta_data", [])}
    else:
        md = rec.get("meta") or {}
        if isinstance(md, list):
            md = {}
    return {k: (md.get(v) or "") for k, v in YOAST.items()}


def _yoast_payload(kind, vals):
    """vals: {'k':..,'t':..,'m':..} -> request json for the right API."""
    meta = {YOAST[k]: v for k, v in vals.items()}
    if kind == "product":
        return {"meta_data": [{"key": k, "value": v} for k, v in meta.items()]}
    return {"meta": meta}


def _backup(group, key, data):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    d = BACKUP / "health" / ts[:8]
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{group}-{key}-{ts}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- pure text transforms (unit-tested)
HREF = re.compile(r"(href\s*=\s*)([\"'])(.*?)\2", re.I | re.S)
A_RE = re.compile(r"<a\b([^>]*?)>(.*?)</a>", re.I | re.S)
IMG = re.compile(r"<img\b[^>]*>", re.I | re.S)


def _key(site, h):
    h = html.unescape((h or "").strip())
    if not h or h.startswith(("#", "mailto:", "tel:", "javascript:")):
        return None
    return nurl(urljoin(site.rstrip("/") + "/", h))


def rewrite_links(raw, site, mapping):
    """mapping: nurl(old) -> new absolute url. -> (new_raw, count)"""
    n = 0

    def f(m):
        nonlocal n
        k = _key(site, m.group(3))
        if k in mapping:
            n += 1
            return f"{m.group(1)}{m.group(2)}{mapping[k]}{m.group(2)}"
        return m.group(0)
    return HREF.sub(f, raw), n


def drop_links(raw, site, keys):
    """Remove <a> wrappers that point to any nurl in keys; the link text stays."""
    n = 0

    def f(m):
        nonlocal n
        hm = HREF.search(m.group(1))
        if hm and _key(site, hm.group(3)) in keys:
            n += 1
            return m.group(2)
        return m.group(0)
    return A_RE.sub(f, raw), n


def add_alts(raw, alt):
    n = 0

    def f(m):
        nonlocal n
        tag = m.group(0)
        src = re.search(r"\b(?:data-)?src\s*=\s*[\"']([^\"']*)", tag, re.I)
        if not src or not src.group(1) or src.group(1).startswith("data:") or src.group(1).lower().split("?")[0].endswith(".svg"):
            return tag
        am = re.search(r"\balt\s*=\s*([\"'])(.*?)\1", tag, re.I | re.S)
        if am and am.group(2).strip():
            return tag
        n += 1
        a = 'alt="%s"' % html.escape(alt if n == 1 else f"{alt} - {n}", quote=True)
        if am:
            return tag[:am.start()] + a + tag[am.end():]
        return "<img " + a + tag[4:]
    return IMG.sub(f, raw), n


def https_fix(raw, host):
    return re.subn(r"http://((?:www\.)?" + re.escape(host) + ")", r"https://\1", raw, flags=re.I)


# ---------------------------------------------------------------- noindex verdict
EXPECT_NOINDEX = re.compile(r"(cart|checkout|my-account|account|login|register|lost-password|thank|order-received|wishlist|compare|سبد|پرداخت|تسویه|حساب|ورود|ثبت[- ‌]?نام|سفارش|تشکر|علاقه|مقایسه)", re.I)


def noindex_verdict(kind, url, title):
    """-> ('ok'|'problem'|'check', explanation)"""
    path = unquote(urlparse(url).path)
    if kind == "page" and EXPECT_NOINDEX.search(path + " " + (title or "")):
        return "ok", "صفحهٔ کاربردی؛ noindex بودنش درست است"
    if kind == "home":
        return "problem", "صفحهٔ اصلی باید index باشد"
    if kind == "term":
        return "check", "دسته‌ها معمولاً باید index باشند، مگر خالی یا تکراری باشند"
    return "problem", {"post": "مقاله باید در گوگل باشد", "product": "محصول باید در گوگل باشد", "page": "صفحهٔ معرفی باید در گوگل باشد"}.get(kind, "باید index شود")


def sitewide_noindex(rep):
    n = (rep.get("codes") or {}).get("noindex", 0)
    crawled = rep.get("crawled") or 0
    return bool(crawled and n >= max(3, 0.7 * crawled))


# ---------------------------------------------------------------- build proposals
def _save(group, items):
    PROP.mkdir(exist_ok=True)
    f = PROP / f"hf_{group}.json"
    f.write_text(json.dumps({"built": datetime.now().isoformat(timespec="seconds"), "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    return f


def load(group):
    f = PROP / f"hf_{group}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


def _rows(rep, codes):
    return [r for r in rep["rows"] if r[2] in codes]


def build(group, log=print):
    """Code-only proposals (no AI, no site changes). -> number of items"""
    rep = latest()
    if not rep:
        raise RuntimeError("اول «سلامت سایت» را اجرا کنید.")
    emap, extra = entity_map(), rep.get("extra") or {}
    items = []
    if group in ("links", "broken"):
        code = "link_redirects" if group == "links" else "broken_internal_link"
        for r in _rows(rep, [code]):
            ex = extra.get(f"{code}|{r[3]}") or {}
            srcs = []
            for n in ex.get("from_n", []):
                e = emap.get(n)
                srcs.append({"n": n, "kind": e["kind"], "id": e["id"], "title": e["title"], "url": e["url"]} if e else {"n": n, "theme": True})
            risky = ""
            if group == "links":
                to = urlparse(ex.get("to", ""))
                if to.path.strip("/") == "" or "login" in to.path:
                    risky = "مقصد صفحهٔ اصلی/ورود است؛ احتمالاً ریدایرکتِ «صفحهٔ حذف‌شده» است. فقط اگر مطمئنید تیک بزنید."
            items.append({"old": r[3], "to": ex.get("to", ""), "variants": ex.get("variants", [r[3]]), "from": srcs, "risky": risky,
                          "default_on": group == "links" and not risky, "action": "remove", "new": ""})
    elif group == "alt":
        seen = {}
        for r in _rows(rep, ["img_no_alt", "p_img_no_alt"]):
            e = emap.get(nurl(r[3]))
            if not e:
                items.append({"url": r[3], "title": r[4] or r[3], "theme": True, "note": "تصویرهای قالب/ابزارک؛ از خود قالب اصلاح می‌شود", "default_on": False})
                continue
            if (e["kind"], e["id"]) in seen:
                continue
            seen[(e["kind"], e["id"])] = 1
            items.append({**e, "alt": e["title"], "default_on": True})
    elif group == "mixed":
        for r in _rows(rep, ["mixed_content"]):
            e = emap.get(nurl(r[3]))
            items.append({**e, "default_on": True} if e else {"url": r[3], "title": r[4] or r[3], "theme": True, "note": "از قالب/افزونه است", "default_on": False})
    elif group == "noindex":
        for r in _rows(rep, ["noindex"]):
            e = emap.get(nurl(r[3]))
            kind = e["kind"] if e else ("home" if r[1] == "home" else ("term" if r[1] == "term" else r[1]))
            v, why = noindex_verdict(kind, r[3], r[4])
            it = {"url": r[3], "title": r[4] or r[3], "kind": kind, "detail": r[5], "verdict": v, "why": why}
            if e and v != "ok":
                it.update({"id": e["id"], "default_on": v == "problem"})
            else:
                it["default_on"] = False
                if not e:
                    it["note"] = "برای این نوع صفحه (دسته/صفحهٔ اصلی) تغییر خودکار نداریم؛ در Yoast ← Search appearance اصلاح کنید"
            items.append(it)
    else:
        raise ValueError(group)
    _save(group, items)
    log(f"{len(items)} مورد آماده شد")
    return len(items)


# ---------------------------------------------------------------- AI proposals (SEO + short description)
SEO_SYS = ("تو متخصص سئوی فارسی برای یک فروشگاه زعفران و ادویه هستی. برای هر مورد فقط فیلدهای فهرست need را بساز: "
           "k = کلمه کلیدی کانونی (۲ تا ۴ کلمه، دقیقاً موضوع)، t = عنوان سئو (حداکثر ۶۰ کاراکتر، شامل k)، "
           "m = متا دیسکریپشن (۱۱۰ تا ۱۶۰ کاراکتر، شامل k، جذاب و بدون اغراق). "
           "فقط از اطلاعات داده‌شده استفاده کن؛ عدد، منشأ، ادعای پزشکی یا مزیتی که در داده نیست ننویس. "
           "اگر cur مقدار مناسبی برای k دارد همان را نگه دار و m را بر اساس آن بساز. "
           "t و m هر مورد باید با بقیهٔ موردهای همین فهرست متفاوت باشد. "
           "فقط یک آرایهٔ JSON با اشیای {\"id\",\"k\",\"t\",\"m\"} بده؛ هیچ توضیح دیگری ننویس.")
SHORT_SYS = ("برای هر محصول یک توضیح کوتاه فروشگاهی فارسی بنویس: ۲۰ تا ۴۰ کلمه، یک یا دو جمله، روان. "
             "فقط از اطلاعات داده‌شده (desc) استفاده کن؛ عدد، منشأ، ادعای پزشکی یا مزیتی که در desc نیست اضافه نکن. "
             "فقط یک آرایهٔ JSON با اشیای {\"id\",\"s\"} بده؛ هیچ توضیح دیگری ننویس.")
FIX_SYS = ("هر مورد مشکلاتی (problems) دارد. فقط فیلدهای need را طوری اصلاح کن که مشکل‌ها برطرف شود و قواعد قبلی رعایت شود "
           "(k در t و m باشد؛ t حداکثر ۶۰ و m بین ۱۱۰ تا ۱۶۰ کاراکتر). فقط آرایهٔ JSON با {\"id\",\"k\",\"t\",\"m\"}.")
BATCH, OUT_PER = 8, 190


def _fetch_records(c, ents):
    by, out = defaultdict(list), {}
    for e in ents:
        by[e["kind"]].append(e["id"])
    for kind, ids in by.items():
        for i in range(0, len(ids), 50):
            ch = ",".join(map(str, ids[i:i + 50]))
            params = {"include": ch, "per_page": 100}
            if kind != "product":
                params["context"] = "edit"
            for r in ensure(c.get(ROUTE[kind], auth=AUTH[kind], params=params), f"read {kind}s"):
                out[(kind, r["id"])] = r
    return out


def _title_of(kind, rec):
    if kind == "product":
        return html.unescape(rec.get("name") or "")
    t = rec.get("title") or {}
    return html.unescape(t.get("raw") or t.get("rendered") or "")


def _seo_needs(rep, emap):
    """-> {(kind,id): {'e':entity, 'need':set('k','t','m'), 'codes':set}}"""
    need = {}
    spec = {"meta_missing": "m", "p_no_meta": "m", "p_no_focus": "k", "title_long": "t", "title_dup": "t", "meta_dup": "m"}
    for r in _rows(rep, list(spec)):
        e = emap.get(nurl(r[3]))
        if not e or (e["kind"] == "page" and noindex_verdict("page", e["url"], e["title"])[0] == "ok"):
            continue  # unknown page, or a utility page (cart/checkout...) that should stay noindex
        d = need.setdefault((e["kind"], e["id"]), {"e": e, "need": set(), "codes": set()})
        d["need"].add(spec[r[2]])
        d["codes"].add(r[2])
    for d in need.values():
        if "m" in d["need"] or "t" in d["need"]:
            d["need"].add("k")  # the keyword anchors both; kept as-is when it already exists
    return need


def _problems(r, need, cur_k, others):
    k = (r.get("k") or cur_k or "").strip()
    p = []
    if "k" in need and not (r.get("k") or cur_k):
        p.append("کلمه کلیدی خالی")
    if "t" in need:
        t = r.get("t") or ""
        if not t or len(t) > 60:
            p.append(f"عنوان {len(t)} کاراکتر (حداکثر ۶۰)")
        elif k and norm(k) not in norm(t):
            p.append("کلمه کلیدی در عنوان نیست")
    if "m" in need:
        m = r.get("m") or ""
        if not 110 <= len(m) <= 160:
            p.append(f"متا {len(m)} کاراکتر (باید ۱۱۰ تا ۱۶۰)")
        elif k and norm(k) not in norm(m):
            p.append("کلمه کلیدی در متا نیست")
    if r.get("k") and not cur_k and norm(r["k"]) in others:
        p.append("کلمه کلیدی با مورد دیگری تکراری است")
    return p


def _ctx_item(kind, rec, need, cur, title):
    body = text_fields(kind, rec)
    main = body.get("content") or body.get("description") or ""
    h2 = [plain(x) for x in re.findall(r"<h2[^>]*>(.*?)</h2>", main, re.S | re.I)][:4]
    o = {"id": f"{kind}:{rec['id']}", "title": title, "need": sorted(need), "h2": h2,
         "intro": plain(main)[:300] if kind != "product" else plain(main)[:380]}
    if kind == "product":
        o["short"] = plain(body.get("short_description"))[:150]
    c = {k: v for k, v in cur.items() if v}
    if c:
        o["cur"] = c
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def ai_estimate(group):
    """-> text. Nothing is sent."""
    from .ai import _brief
    cfgd = llm.cfg()
    rep = latest()
    if not rep:
        return "اول «سلامت سایت» را اجرا کنید."
    emap = entity_map()
    n = len(_seo_needs(rep, emap)) if group == "seo" else sum(1 for r in _rows(rep, ["p_no_short"]) if emap.get(nurl(r[3])))
    if not n:
        return "موردی برای AI نیست."
    calls = -(-n // BATCH)
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print(f"{n} مورد، {calls} تماس AI (هر تماس تا {BATCH} مورد).")
        _brief(cfgd, calls, calls * (BATCH * 230 + 300), min(cfgd["max_out"], BATCH * OUT_PER + 40) * calls)
    return buf.getvalue()


def build_ai(group, log=print):
    """Calls the AI (spends credit). Writes proposals only; the site is untouched."""
    rep = latest()
    if not rep:
        raise RuntimeError("اول «سلامت سایت» را اجرا کنید.")
    c, emap = _client(), entity_map()
    cfgd = llm.cfg()
    llm.reset_run()
    items = []
    if group == "seo":
        needs = _seo_needs(rep, emap)
        recs = _fetch_records(c, [d["e"] for d in needs.values()])
        existing = {}
        try:
            from .corpus import read_articles
            existing = {norm(a["focus_keyword"]): str(a["id"]) for a in read_articles() if a.get("focus_keyword")}
        except Exception:
            pass
        todo = []
        for key, d in needs.items():
            rec = recs.get(key)
            if not rec:
                continue
            cur = yoast_of(key[0], rec)
            need = {x for x in d["need"] if x == "k" and not cur["k"] or x in ("t", "m") and x in d["need"]}
            if "m" in d["need"] and cur["m"] and not (set(d["codes"]) & {"meta_dup"}):
                need.discard("m")
            if not need:
                continue
            todo.append((key, d, rec, cur, need))
        by_id = {f"{k[0]}:{k[1]}": (k, d, rec, cur, need) for k, d, rec, cur, need in todo}
        batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
        log(f"{len(todo)} مورد در {len(batches)} دسته")
        results = {}
        for b in batches:
            text = "\n".join(_ctx_item(k[0], rec, need, cur, _title_of(k[0], rec)) for k, d, rec, cur, need in b)
            try:
                out, info = llm.chat([{"role": "system", "content": SEO_SYS}, {"role": "user", "content": text}],
                                     max_out=min(cfgd["max_out"], len(b) * OUT_PER + 40), task="hf_seo")
            except llm.LLMError as e:
                log(f"خطای AI: {e}")
                if any(x in str(e) for x in ("Budget", "cap", "HTTP 40")):
                    break
                continue
            for r in llm.parse_json_list(out):
                if str(r.get("id")) in by_id:
                    results[str(r["id"])] = {x: str(r.get(x) or "").strip() for x in "ktm"}
        taken = set(existing)
        bad = {}
        for i, r in results.items():
            k, d, rec, cur, need = by_id[i]
            pb = _problems(r, need, cur["k"], taken - {norm(cur["k"])})
            if pb:
                bad[i] = pb
            elif r["k"] and not cur["k"]:
                taken.add(norm(r["k"]))
        if bad:
            log(f"{len(bad)} مورد استاندارد را رد کرد؛ یک تماس اصلاحی")
            msgs = "\n".join(json.dumps({"id": i, "title": _title_of(by_id[i][0][0], by_id[i][2]), **results[i], "need": sorted(by_id[i][4]), "problems": bad[i]},
                                         ensure_ascii=False, separators=(",", ":")) for i in bad)
            try:
                out, info = llm.chat([{"role": "system", "content": FIX_SYS}, {"role": "user", "content": msgs}],
                                     max_out=min(cfgd["max_out"], len(bad) * OUT_PER + 40), task="hf_seo_fix")
                for r in llm.parse_json_list(out):
                    if str(r.get("id")) in bad:
                        results[str(r["id"])] = {x: str(r.get(x) or results[str(r["id"])][x]).strip() for x in "ktm"}
            except llm.LLMError as e:
                log(f"تماس اصلاحی ناموفق: {e}")
        for i, r in results.items():
            k, d, rec, cur, need = by_id[i]
            pb = _problems(r, need, cur["k"], taken - {norm(cur["k"])})
            if not pb and r["k"] and not cur["k"]:
                taken.add(norm(r["k"]))
            e = d["e"]
            fields = {}
            for x in ("k", "t", "m"):
                if x in need or (x == "k" and r["k"] and not cur["k"] and need & {"m", "t"}):
                    fields[x] = {"old": cur[x], "new": r[x] or cur[x]}
            items.append({**e, "fields": fields, "codes": sorted(d["codes"]), "notes": "؛ ".join(pb), "default_on": not pb})
    else:  # short descriptions
        cand = []
        for r in _rows(rep, ["p_no_short"]):
            e = emap.get(nurl(r[3]))
            if e and e["kind"] == "product":
                cand.append(e)
        recs = _fetch_records(c, cand)
        todo = []
        for e in cand:
            rec = recs.get(("product", e["id"]))
            desc = plain((rec or {}).get("description"))
            if rec and len(desc.split()) >= 15 and not plain(rec.get("short_description")):
                todo.append((e, desc))
            elif rec:
                items.append({**e, "fields": {"short_description": {"old": "", "new": ""}}, "notes": "توضیح کامل برای ساختن توضیح کوتاه کافی نیست؛ خودتان بنویسید", "default_on": False})
        batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
        log(f"{len(todo)} محصول در {len(batches)} دسته")
        for b in batches:
            text = "\n".join(json.dumps({"id": f"product:{e['id']}", "name": e["title"], "desc": d[:600]}, ensure_ascii=False, separators=(",", ":")) for e, d in b)
            try:
                out, info = llm.chat([{"role": "system", "content": SHORT_SYS}, {"role": "user", "content": text}],
                                     max_out=min(cfgd["max_out"], len(b) * 130 + 40), task="hf_short")
            except llm.LLMError as e:
                log(f"خطای AI: {e}")
                break
            got = {str(r.get("id")): str(r.get("s") or "").strip() for r in llm.parse_json_list(out)}
            for e, d in b:
                s = got.get(f"product:{e['id']}", "")
                w = len(s.split())
                pb = "" if 12 <= w <= 60 else f"{w} کلمه؛ خارج از بازهٔ مناسب"
                items.append({**e, "fields": {"short_description": {"old": "", "new": s}}, "notes": pb, "default_on": bool(s) and not pb})
    _save(group, items)
    log(f"{len(items)} پیشنهاد ذخیره شد؛ چیزی روی سایت تغییر نکرد")
    return len(items)


# ---------------------------------------------------------------- apply (preview / go)
def _plan_text(c, e, fn):
    rec = get_rec(c, e)
    old = text_fields(e["kind"], rec)
    new, total = {}, 0
    for k, v in old.items():
        nv, n = fn(v)
        if n:
            new[k], total = nv, total + n
    return rec, old, new, total


def _write_text(c, e, new):
    ensure(c.request("POST", f"{ROUTE[e['kind']]}/{e['id']}", auth=AUTH[e["kind"]], json=new), f"update {e['kind']} {e['id']}")


def _media_alt_updates(c, e, rec, alt):
    """-> list of (media_id, alt) that lack alt (product images; featured image of posts/pages)."""
    out = []
    if e["kind"] == "product":
        n = 0
        for im in rec.get("images") or []:
            if im.get("id") and not (im.get("alt") or "").strip():
                n += 1
                out.append((im["id"], alt if n == 1 else f"{alt} - {n}"))
    elif rec.get("featured_media"):
        try:
            m = ensure(c.get(f"wp/v2/media/{rec['featured_media']}", auth="wp", params={"_fields": "id,alt_text"}), "read media")
            if not (m.get("alt_text") or "").strip():
                out.append((m["id"], alt))
        except StepError:
            pass
    return out


def apply(group, selected, edits, go):
    """selected: set of item indexes; edits: {idx: {field: value}}. Prints a preview (go=False) or applies (go=True)."""
    data = load(group)
    if not data:
        print("پیشنهادی ساخته نشده است.")
        return
    items = data["items"]
    chosen = [(i, items[i]) for i in sorted(selected) if 0 <= i < len(items)]
    if not chosen:
        print("هیچ موردی تیک نخورده است.")
        return
    c = _client()
    site = c.cfg.site_url
    host = urlparse(site).netloc.removeprefix("www.")
    plans = []  # (item, e, new_text_fields, old_text_fields, media_updates, meta_payload, label)
    if group in ("links", "broken"):
        by_src = defaultdict(lambda: {"map": {}, "drop": set(), "e": None})
        theme = []
        for i, it in chosen:
            ed = edits.get(i, {})
            act = "replace" if group == "links" else ed.get("action", it.get("action", "remove"))
            new = it["to"] if group == "links" else (ed.get("new") or "").strip()
            if act == "skip":
                continue
            if act == "replace" and not new.startswith(("http://", "https://", "/")):
                print(f"! {it['old']}: آدرس جایگزین معتبر نیست؛ رد شد")
                continue
            oldk = nurl(it["old"])
            for s in it["from"]:
                if s.get("theme"):
                    theme.append(it["old"])
                    continue
                g = by_src[(s["kind"], s["id"])]
                g["e"] = s
                if act == "replace":
                    g["map"][oldk] = new if new.startswith("http") else urljoin(site + "/", new)
                else:
                    g["drop"].add(oldk)
        for (k, i), g in by_src.items():
            def fn(raw, g=g):
                raw, a = rewrite_links(raw, site, g["map"]) if g["map"] else (raw, 0)
                raw, b = drop_links(raw, site, g["drop"]) if g["drop"] else (raw, 0)
                return raw, a + b
            rec, old, new, total = _plan_text(c, g["e"], fn)
            label = f"{g['e']['title']} ({k} {i}): {total} لینک"
            plans.append({"e": g["e"], "old": old, "new": new, "label": label, "n": total, "fn": fn})
        for u in sorted(set(theme)):
            print(f"! {u}: در متن پیدا نمی‌شود؛ در منو/ابزارک/قالب است (دستی: نمایش ← فهرست‌ها)")
    elif group in ("alt", "mixed"):
        for i, it in chosen:
            if it.get("theme"):
                continue
            alt = (edits.get(i, {}).get("alt") or it.get("alt") or it["title"]).strip()
            fn = (lambda raw, a=alt: add_alts(raw, a)) if group == "alt" else (lambda raw: https_fix(raw, host))
            rec, old, new, total = _plan_text(c, it, fn)
            media = _media_alt_updates(c, it, rec, alt) if group == "alt" else []
            if total or media:
                plans.append({"e": it, "old": old, "new": new, "media": media, "fn": fn, "n": total,
                              "label": f"{it['title']} ({it['kind']} {it['id']}): {total} تغییر در متن"
                                       + (f"، {len(media)} Alt روی فایل تصویر" if media else "")})
    elif group == "noindex":
        for i, it in chosen:
            if "id" not in it:
                print(f"! {it['title']}: {it.get('note', 'تغییر خودکار ندارد')}")
                continue
            if it["verdict"] == "ok":
                print(f"! {it['title']}: noindex در این مورد درست است؛ رد شد")
                continue
            plans.append({"e": it, "label": f"{it['title']} ({it['kind']} {it['id']}): index شود", "noindex": True})
    elif group in ("seo", "short"):
        for i, it in chosen:
            ed = edits.get(i, {})
            vals = {}
            for f, v in it["fields"].items():
                nv = (ed.get(f) if ed.get(f) is not None else v["new"]).strip()
                if nv and nv != v["old"]:
                    vals[f] = nv
            if vals:
                plans.append({"e": it, "vals": vals, "olds": {f: v["old"] for f, v in it["fields"].items()},
                              "label": f"{it['title']} ({it['kind']} {it['id']}): " + "، ".join({"k": "کلمه کلیدی", "t": "عنوان", "m": "متا", "short_description": "توضیح کوتاه"}[f] for f in vals)})
                for f, v in vals.items():
                    print(f"   • {it['title']}  [{f}] {v}")
                    if group == "seo":
                        if f == "t" and len(v) > 60:
                            print("     هشدار: عنوان بلندتر از ۶۰ کاراکتر")
                        if f == "m" and not 110 <= len(v) <= 160:
                            print(f"     هشدار: متا {len(v)} کاراکتر")
    print()
    for p in plans:
        print("•", p["label"])
    if not plans:
        print("چیزی برای تغییر نیست (یا قبلاً اعمال شده).")
        return
    if not go:
        print(f"\nفقط پیش‌نمایش: {len(plans)} مورد تغییر می‌کند. برای اعمال، تیک «مطمئنم» را بزنید.")
        return
    if not confirm(len(plans), "مورد روی سایت (قبلش پشتیبان گرفته می‌شود)"):
        print("لغو شد.")
        return
    done = fail = 0
    for p in plans:
        e = p["e"]
        key = f"{e['kind']}{e['id']}"
        try:
            if p.get("noindex"):
                _backup(group, key, {"id": e["id"], "kind": e["kind"], "was": "noindex"})
                payload = ({"meta_data": [{"key": NOINDEX_KEY, "value": "2"}]} if e["kind"] == "product" else {"meta": {NOINDEX_KEY: "2"}})
                res = ensure(c.request("POST", f"{ROUTE[e['kind']]}/{e['id']}", auth=AUTH[e["kind"]], json=payload), "update")
                got = (res.get("meta") or {}).get(NOINDEX_KEY) if e["kind"] != "product" else next((m.get("value") for m in res.get("meta_data", []) if m.get("key") == NOINDEX_KEY), None)
                state = _live_noindex(e["url"])
                print(f"   {e['title']}: ذخیره={'بله' if str(got) == '2' else 'تأیید نشد'}؛ صفحهٔ زنده: "
                      + ("دیگر noindex نیست ✓" if state is False else "هنوز noindex دیده می‌شود (کش؟ یا منبعش جای دیگری است)" if state else "بررسی نشد"))
                if str(got) != "2":
                    print("   ← وردپرس این فیلد را از REST نمی‌پذیرد. دستی: ویرایش صفحه ← Yoast ← Advanced ← «Allow search engines to show this page» روی Yes.")
                log_action("hf_noindex", {"id": e["id"], "kind": e["kind"]})
            elif "vals" in p:
                _backup(group, key, {"id": e["id"], "kind": e["kind"], "old": p["olds"]})
                if group == "short":
                    res = ensure(c.request("POST", f"{ROUTE['product']}/{e['id']}", auth="wc", json={"short_description": f"<p>{html.escape(p['vals']['short_description'])}</p>"}), "update")
                    ok = bool(plain(res.get("short_description")))
                else:
                    payload = _yoast_payload(e["kind"], p["vals"])
                    res = ensure(c.request("POST", f"{ROUTE[e['kind']]}/{e['id']}", auth=AUTH[e["kind"]], json=payload), "update")
                    now = yoast_of(e["kind"], res)
                    ok = all(now.get(f) == v for f, v in p["vals"].items())
                print(f"   {'OK' if ok else 'ارسال شد ولی تأیید نشد'}: {e['title']}")
                log_action("hf_" + group, {"id": e["id"], "fields": list(p["vals"])})
            else:
                _backup(group, key, p["old"])
                if p["new"]:
                    _write_text(c, e, p["new"])
                for mid, alt in p.get("media", []):
                    ensure(c.request("POST", f"wp/v2/media/{mid}", auth="wp", json={"alt_text": alt}), f"alt media {mid}")
                chk = text_fields(e["kind"], get_rec(c, e))
                left = sum(p["fn"](v)[1] for v in chk.values()) if p["new"] else 0
                print(f"   OK: {e['title']} (باقی‌مانده بعد از ذخیره: {left})")
                log_action("hf_" + group, {"id": e["id"], "kind": e["kind"], "n": p["n"]})
            done += 1
        except StepError as ex:
            fail += 1
            print(f"   ناموفق: {e.get('title')}: {ex}")
    print(f"\n{done} مورد انجام شد، {fail} ناموفق. نسخهٔ قبلی هر مورد: backups\\health\\")
    print("برای دیدن اثر، چند دقیقه بعد (بعد از پاک شدن کش) «سلامت سایت» را دوباره اجرا کنید.")


def _live_noindex(url):
    try:
        r = requests.get(url, timeout=25, headers={"User-Agent": "ZafaranAgent/1.0 (verify)", "Cache-Control": "no-cache"})
        txt = r.text.lower()
        hdr = (r.headers.get("X-Robots-Tag") or "").lower()
        return "noindex" in hdr or bool(re.search(r"<meta[^>]+name=[\"']robots[\"'][^>]*noindex", txt))
    except requests.RequestException:
        return None


# ---------------------------------------------------------------- overview for the page
def overview(rep):
    """-> list of dicts for the 'start here' box: only groups that have rows, sorted by importance."""
    out = []
    codes = rep.get("codes") or {}
    for g, (title, cs, ai) in GROUPS.items():
        n = sum(codes.get(c, 0) for c in cs)
        if n:
            sev = max((r[0] for r in rep["rows"] if r[2] in cs), key=lambda s: {"H": 3, "M": 2, "L": 1}[s], default="L")
            out.append({"group": g, "title": title, "n": n, "ai": ai, "sev": sev})
    return sorted(out, key=lambda x: (-{"H": 3, "M": 2, "L": 1}[x["sev"]], -x["n"]))
