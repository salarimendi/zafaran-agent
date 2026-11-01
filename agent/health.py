"""Stage 1 - READ-ONLY technical + WooCommerce health check. No AI. Changes nothing on the site."""
import csv
import hashlib
import json
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import unquote, urldefrag, urljoin, urlparse

import requests

from .config import ROOT, load_config
from .site import fetch_all, load_snapshot
from .wp_client import WPClient

REP = ROOT / "reports"
LATEST = ROOT / "knowledge" / "site" / "health_latest.json"
PREV = ROOT / "knowledge" / "site" / "health_prev.json"
UA = "ZafaranAgent/1.0 (read-only site health check)"
SEV = {"H": 3, "M": 2, "L": 1}
SKIP_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".pdf", ".css", ".js", ".ico", ".zip", ".mp4", ".xml", ".txt")
SKIP_PATH = ("/wp-admin", "/wp-json", "/wp-content", "/wp-includes", "/feed", "/xmlrpc", "/cart", "/checkout", "/my-account", "?add-to-cart")

HELP = {  # code: (Persian explanation, what to do)
    "unreachable": "اتصال به صفحه برقرار نشد", "http_error": "صفحهٔ منتشرشده خطای ۴xx/۵xx می‌دهد؛ درست یا ریدایرکت شود",
    "redirected": "آدرس منتشرشده خودش به جای دیگری ریدایرکت می‌شود", "redirect_chain": "زنجیرهٔ ریدایرکت بیش از یک مرحله است",
    "redirect_loop": "حلقهٔ ریدایرکت", "noindex": "صفحه noindex است؛ اگر عمدی نیست در Yoast اصلاح شود",
    "canonical_missing": "canonical ندارد", "canonical_other": "canonical به آدرس دیگری اشاره می‌کند (ممکن است عمدی باشد)",
    "title_missing": "عنوان صفحه خالی است", "title_dup": "عنوان با صفحهٔ دیگری یکی است", "title_long": "عنوان بیش از ۶۵ کاراکتر",
    "meta_missing": "متا دیسکریپشن ندارد", "meta_dup": "متا دیسکریپشن با صفحهٔ دیگری یکی است",
    "h1_none": "H1 ندارد", "h1_multi": "بیش از یک H1", "img_no_alt": "تصویر بدون Alt", "schema_none": "هیچ Schema ندارد",
    "schema_no_product": "صفحهٔ محصول Schema نوع Product ندارد", "schema_no_article": "مقاله Schema نوع Article ندارد",
    "mixed_content": "منبع ناامن (http) داخل صفحهٔ https", "robots_blocks_all": "robots.txt کل سایت را مسدود کرده!",
    "robots_missing": "robots.txt پیدا نشد", "sitemap_missing": "Sitemap پیدا نشد", "sitemap_missing_url": "صفحهٔ منتشرشده در Sitemap نیست",
    "sitemap_dead_url": "آدرس داخل Sitemap خطا یا ریدایرکت دارد", "broken_internal_link": "لینک داخلی شکسته",
    "link_redirects": "لینک داخلی به آدرسی می‌رود که ریدایرکت می‌شود؛ لینک مستقیم به آدرس نهایی بهتر است",
    "p_no_short": "محصول توضیح کوتاه ندارد", "p_desc_tiny": "توضیح محصول کمتر از ۴۰ کلمه", "p_desc_short": "توضیح محصول کمتر از ۱۰۰ کلمه",
    "p_dup_desc": "توضیح محصول با محصول دیگری یکی است", "p_img_no_alt": "تصویر محصول بدون Alt", "p_no_images": "محصول تصویر ندارد",
    "p_no_category": "محصول دسته ندارد", "p_no_price": "محصول منتشرشده قیمت ندارد", "p_oos": "محصول منتشرشده ناموجود است",
    "p_no_focus": "محصول کلمه کلیدی Yoast ندارد", "p_no_meta": "محصول متا دیسکریپشن Yoast ندارد",
}


def bare(h):
    return h.lower().removeprefix("www.")


def nurl(u):
    p = urlparse(unquote(urldefrag(u)[0]))
    return f"{bare(p.netloc)}{p.path.rstrip('/')}" + (f"?{p.query}" if p.query else "")


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.meta, self.canonical, self.h1 = "", {}, None, 0
        self.imgs, self.links, self.ld = [], [], []
        self._t = self._l = False
        self._buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._t = True
        elif tag == "meta":
            n = (a.get("name") or a.get("property") or "").lower()
            if n:
                self.meta.setdefault(n, a.get("content") or "")
        elif tag == "link" and "canonical" in (a.get("rel") or "").lower().split():
            self.canonical = a.get("href")
        elif tag == "h1":
            self.h1 += 1
        elif tag == "img":
            self.imgs.append((a.get("src") or a.get("data-src") or "", a.get("alt")))
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "script" and "ld+json" in (a.get("type") or ""):
            self._l, self._buf = True, []

    def handle_endtag(self, tag):
        if tag == "title":
            self._t = False
        elif tag == "script" and self._l:
            self._l = False
            self.ld.append("".join(self._buf))

    def handle_data(self, d):
        if self._t:
            self.title += d
        elif self._l:
            self._buf.append(d)


def ld_types(blocks):
    out = set()

    def walk(o):
        if isinstance(o, dict):
            t = o.get("@type")
            for x in (t if isinstance(t, list) else [t]):
                if x:
                    out.add(str(x))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    for b in blocks:
        try:
            walk(json.loads(b))
        except ValueError:
            pass
    return out


class Checker:
    def __init__(self, progress=print):
        self.cfg = load_config()
        self.host = bare(urlparse(self.cfg.site_url).netloc)
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.delay = float(os.getenv("HEALTH_DELAY", "0.25"))
        self.say = progress or (lambda m: None)
        self.rows = []  # (sev, kind, code, url, title, detail)
        self.extra = {}  # "code|url" -> structured details for the fix tools
        self.last_hdr = {}

    def add(self, sev, kind, code, url, title="", detail=""):
        self.rows.append((sev, kind, code, url, title, detail))

    def fetch(self, url, method="GET"):
        """Follow redirects by hand. -> (chain[(url,status)], final_status, html)"""
        chain, cur = [], url
        for _ in range(6):
            try:
                r = self.s.request(method, cur, allow_redirects=False, timeout=25)
            except requests.RequestException as e:
                chain.append((cur, type(e).__name__))
                return chain, None, ""
            chain.append((cur, r.status_code))
            self.last_hdr = r.headers
            time.sleep(self.delay)
            if 300 <= r.status_code < 400 and r.headers.get("Location"):
                nxt = urljoin(cur, r.headers["Location"])
                if nxt in [c for c, _ in chain]:
                    return chain, "loop", ""
                cur = nxt
                continue
            return chain, r.status_code, (r.text if method == "GET" and r.status_code == 200 else "")
        return chain, "loop", ""

    # ---------- site level ----------
    def robots_and_sitemaps(self):
        base = self.cfg.site_url
        sitemaps = []
        try:
            r = self.s.get(base + "/robots.txt", timeout=20)
            if r.status_code != 200:
                self.add("L", "site", "robots_missing", base + "/robots.txt", detail=f"HTTP {r.status_code}")
            else:
                ua_all = False
                for ln in r.text.splitlines():
                    k, _, v = ln.partition(":")
                    k, v = k.strip().lower(), v.strip()
                    if k == "user-agent":
                        ua_all = v == "*"
                    elif k == "disallow" and ua_all and v == "/":
                        self.add("H", "site", "robots_blocks_all", base + "/robots.txt", detail="Disallow: / برای همهٔ رباتها")
                    elif k == "sitemap":
                        sitemaps.append(v)
        except requests.RequestException as e:
            self.add("L", "site", "robots_missing", base + "/robots.txt", detail=type(e).__name__)
        urls, seen, queue = set(), set(), sitemaps or [base + "/sitemap_index.xml", base + "/sitemap.xml", base + "/wp-sitemap.xml"]
        while queue and len(seen) < 25:
            u = queue.pop(0)
            if u in seen:
                continue
            seen.add(u)
            try:
                r = self.s.get(u, timeout=25)
            except requests.RequestException:
                continue
            if r.status_code != 200 or "<loc" not in r.text:
                continue
            locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text)
            if "<sitemapindex" in r.text:
                queue += locs
            else:
                urls.update(locs)
            time.sleep(self.delay)
        if not urls:
            self.add("M", "site", "sitemap_missing", base + "/sitemap_index.xml", detail="هیچ Sitemap معتبری پیدا نشد")
        return {nurl(u): u for u in urls}

    # ---------- pages ----------
    def targets(self, limit=None):
        snap = load_snapshot() or {}
        items = [(self.cfg.site_url + "/", "home", "صفحهٔ اصلی")]
        for lst, kind, tk in (("posts", "post", "title"), ("pages", "page", "title"), ("products", "product", "name")):
            for it in snap.get(lst, []):
                if it.get("status") == "publish" and it.get("url"):
                    items.append((it["url"], kind, it[tk]))
        for t in ("category", "product_cat"):
            for it in snap.get(t, []):
                if it.get("link") and it.get("count", 0) > 0:
                    items.append((it["link"], "term", it["name"]))
        seen, out = set(), []
        for u, k, t in items:
            if nurl(u) not in seen:
                seen.add(nurl(u))
                out.append((u, k, t))
        return out[:limit] if limit else out

    def check_pages(self, targets, sitemap):
        pages, titles, metas, blocked = {}, defaultdict(list), defaultdict(list), 0
        for i, (url, kind, name) in enumerate(targets, 1):
            if i % 20 == 0 or i == len(targets):
                self.say(f"صفحه‌ها: {i}/{len(targets)}")
            chain, st, html = self.fetch(url)
            final = chain[-1][0]
            pages[nurl(url)] = {"status": st, "final": final, "chain": chain}
            if st is None:
                self.add("H", kind, "unreachable", url, name, chain[-1][1])
                continue
            if st == "loop":
                self.add("H", kind, "redirect_loop", url, name)
                continue
            if isinstance(st, int) and st in (403, 429):
                blocked += 1
                if i <= 15 and blocked >= 10:
                    self.add("H", "site", "http_error", url, "", "سرور خزنده را مسدود می‌کند (۴۰۳/۴۲۹)؛ بررسی متوقف شد. HEALTH_DELAY را بالا ببرید")
                    self.say("توقف: میزبان خزنده را مسدود کرد")
                    break
            if isinstance(st, int) and st >= 400:
                self.add("H", kind, "http_error", url, name, f"HTTP {st}")
                continue
            if len(chain) > 1:
                self.add("M", kind, "redirected", url, name, f"→ {final}")
                if len(chain) > 2:
                    self.add("M", kind, "redirect_chain", url, name, f"{len(chain) - 1} مرحله")
                continue
            if not html:
                continue
            p = _Page()
            p.feed(html)
            pages[nurl(url)].update(links=p.links, base=final)
            title, meta = " ".join(p.title.split()), p.meta.get("description", "").strip()
            important = kind in ("post", "page", "product", "home")
            robots = p.meta.get("robots", "").lower()
            hdr = (self.last_hdr.get("X-Robots-Tag") or "").lower()
            if "noindex" in robots or "noindex" in hdr:
                self.add("H" if important else "L", kind, "noindex", url, name,
                         (robots or "") + ((" | هدر: " + hdr) if hdr else ""))
            if not p.canonical:
                self.add("M", kind, "canonical_missing", url, name)
            elif nurl(urljoin(final, p.canonical)) != nurl(final):
                self.add("M", kind, "canonical_other", url, name, f"→ {p.canonical}")
            if not title:
                self.add("H", kind, "title_missing", url, name)
            else:
                titles[title].append((url, name))
                if len(title) > 65:
                    self.add("L", kind, "title_long", url, name, f"{len(title)} کاراکتر")
            if important and not meta:
                self.add("M", kind, "meta_missing", url, name)
            elif meta:
                metas[meta].append((url, name))
            if p.h1 == 0:
                self.add("M", kind, "h1_none", url, name)
            elif p.h1 > 1:
                self.add("L", kind, "h1_multi", url, name, f"{p.h1} عدد")
            noalt = [s for s, a in p.imgs if s and not s.startswith("data:") and not s.lower().endswith(".svg") and not (a or "").strip()]
            if noalt:
                self.add("M" if len(noalt) > 2 else "L", kind, "img_no_alt", url, name, f"{len(noalt)} تصویر")
                self.extra[f"img_no_alt|{url}"] = {"srcs": noalt[:20]}
            types = ld_types(p.ld)
            if important and not types:
                self.add("M", kind, "schema_none", url, name)
            elif kind == "product" and "Product" not in types:
                self.add("M", kind, "schema_no_product", url, name, ", ".join(sorted(types)))
            elif kind == "post" and not types & {"Article", "BlogPosting", "NewsArticle"}:
                self.add("L", kind, "schema_no_article", url, name, ", ".join(sorted(types)))
            if final.startswith("https://") and re.search(r'(?:src|href)="http://[^"]*%s' % re.escape(self.host), html):
                self.add("M", kind, "mixed_content", url, name)
        for t, lst in titles.items():
            if len(lst) > 1:
                for u, n in lst:
                    self.add("M", "page", "title_dup", u, n, f"{len(lst)} صفحه: {t[:60]}")
        for m, lst in metas.items():
            if len(lst) > 1:
                for u, n in lst:
                    self.add("L", "page", "meta_dup", u, n, f"{len(lst)} صفحه")
        return pages

    def check_sitemap(self, targets, sitemap, pages):
        if not sitemap:
            return
        pub = {nurl(u): (u, k, t) for u, k, t in targets if k in ("post", "page", "product")}
        noindex = {r[3] for r in self.rows if r[2] == "noindex"}
        for n, (u, k, t) in pub.items():
            if n in pages and pages[n]["status"] == 200 and len(pages[n]["chain"]) == 1 and n not in sitemap and u not in noindex:
                self.add("M", k, "sitemap_missing_url", u, t)
        extra = [(n, u) for n, u in sitemap.items() if n not in pages][:40]
        for n, u in extra:
            chain, st, _ = self.fetch(u, "HEAD")
            if st is None or st == "loop" or (isinstance(st, int) and st >= 300) or len(chain) > 1:
                self.add("M", "site", "sitemap_dead_url", u, "", f"HTTP {st}")

    def check_links(self, pages, max_links=250):
        found = defaultdict(set)
        for n, pg in pages.items():
            for href in pg.get("links", []):
                if href.startswith(("#", "mailto:", "tel:", "javascript:")):
                    continue
                u = urldefrag(urljoin(pg["base"], href))[0]
                pu = urlparse(u)
                if bare(pu.netloc) != self.host or any(x in u for x in SKIP_PATH) or pu.path.lower().endswith(SKIP_EXT):
                    continue
                if nurl(u) != n:
                    found[nurl(u)].add((u, n))
        todo = []
        for n, srcs in found.items():
            u = sorted(srcs)[0][0]
            todo.append((n, u, sorted({s for _, s in srcs}), sorted({x for x, _ in srcs})))
        res = {}
        for i, (n, u, srcs, variants) in enumerate(todo[:max_links], 1):
            if i % 25 == 0:
                self.say(f"لینک‌های داخلی: {i}/{min(len(todo), max_links)}")
            if n in pages:
                st, chain = pages[n]["status"], pages[n]["chain"]
            else:
                chain, st, _ = self.fetch(u, "HEAD")
                if st in (405, 403, 501):
                    chain, st, _ = self.fetch(u, "GET")
            res[n] = (u, st, chain, srcs, variants)
        for n, (u, st, chain, srcs, variants) in res.items():
            names = [pages[x]["final"] if x in pages else x for x in srcs[:3]]
            where = "در: " + "، ".join(names) + (f" (+{len(srcs) - 3})" if len(srcs) > 3 else "")
            if st is None or st == "loop" or (isinstance(st, int) and st >= 400):
                self.add("H", "link", "broken_internal_link", u, "", f"HTTP {st}  {where}")
                self.extra[f"broken_internal_link|{u}"] = {"from_n": srcs, "variants": variants}
            elif len(chain) > 1:
                self.add("M", "link", "link_redirects", u, "", f"→ {chain[-1][0]}  {where}")
                self.extra[f"link_redirects|{u}"] = {"to": chain[-1][0], "from_n": srcs, "variants": variants}

    # ---------- products ----------
    def check_products(self):
        c = WPClient(self.cfg)
        c.detect_mode()
        self.say("محصولات: دریافت از ووکامرس...")
        prods = fetch_all(c, "wc/v3/products", "wc", {"status": "publish"})
        hashes = defaultdict(list)
        for p in prods:
            name, url, pid = p.get("name", ""), p.get("permalink", ""), p["id"]
            plain = lambda h: " ".join(re.sub(r"<[^>]+>", " ", h or "").split())
            short, desc = plain(p.get("short_description")), plain(p.get("description"))
            words = len(desc.split())
            if not short:
                self.add("M", "product", "p_no_short", url, name)
            if words < 40:
                self.add("H", "product", "p_desc_tiny", url, name, f"{words} کلمه")
            elif words < 100:
                self.add("M", "product", "p_desc_short", url, name, f"{words} کلمه")
            if desc:
                hashes[hashlib.md5(norm_text(desc).encode()).hexdigest()].append((url, name))
            imgs = p.get("images") or []
            if not imgs:
                self.add("H", "product", "p_no_images", url, name)
            elif any(not (i.get("alt") or "").strip() for i in imgs):
                self.add("M", "product", "p_img_no_alt", url, name, f"{sum(1 for i in imgs if not (i.get('alt') or '').strip())} از {len(imgs)}")
            cats = [x.get("slug") for x in p.get("categories", [])]
            if not cats or cats == ["uncategorized"]:
                self.add("M", "product", "p_no_category", url, name)
            if not str(p.get("price") or "").strip():
                self.add("H", "product", "p_no_price", url, name)
            if p.get("stock_status") == "outofstock":
                self.add("L", "product", "p_oos", url, name)
            md = {m.get("key"): m.get("value") for m in p.get("meta_data", [])}
            if not md.get("_yoast_wpseo_focuskw"):
                self.add("M", "product", "p_no_focus", url, name)
            if not md.get("_yoast_wpseo_metadesc"):
                self.add("M", "product", "p_no_meta", url, name)
        for lst in hashes.values():
            if len(lst) > 1:
                for u, n in lst:
                    self.add("M", "product", "p_dup_desc", u, n, f"{len(lst)} محصول")
        self.say(f"محصولات: {len(prods)} بررسی شد")


def norm_text(s):
    return re.sub(r"\W+", " ", s.lower()).strip()


def run(progress=print, limit=None, products_only=False, pages_only=False):
    """-> summary dict. Writes reports/health_*.csv/.md and knowledge/site/health_latest.json"""
    ck = Checker(progress)
    t0 = time.time()
    crawled = 0
    if not products_only:
        targets = ck.targets(limit)
        if not targets:
            raise RuntimeError("اول «همگام‌سازی سایت» را اجرا کنید (فهرست صفحه‌ها خالی است).")
        crawled = len(targets)
        ck.say(f"{len(targets)} صفحه برای بررسی")
        sitemap = ck.robots_and_sitemaps()
        pages = ck.check_pages(targets, sitemap)
        ck.check_sitemap(targets, sitemap, pages)
        ck.check_links(pages)
    if not pages_only:
        try:
            ck.check_products()
        except Exception as e:
            ck.add("M", "site", "http_error", ck.cfg.site_url, "", f"بررسی محصولات انجام نشد: {type(e).__name__}: {e}")
    rows = sorted(ck.rows, key=lambda r: (-SEV[r[0]], r[2], r[3]))
    codes, sev = Counter(r[2] for r in rows), Counter(r[0] for r in rows)
    now = datetime.now()
    REP.mkdir(exist_ok=True)
    f = REP / f"health_{now:%Y%m%d_%H%M}.csv"
    with open(f, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["severity", "kind", "code", "url", "title", "detail", "meaning"])
        w.writerows([[*r, HELP.get(r[2], "")] for r in rows])
    if LATEST.exists():
        PREV.write_text(LATEST.read_text(encoding="utf-8"), encoding="utf-8")
    summary = {"time": now.isoformat(timespec="seconds"), "seconds": round(time.time() - t0), "csv": f.name,
               "scope": "products" if products_only else ("pages" if pages_only else "full"),
               "severity": dict(sev), "codes": dict(codes), "rows": [list(r) for r in rows[:3000]], "extra": ck.extra, "crawled": crawled}
    LATEST.parent.mkdir(parents=True, exist_ok=True)
    LATEST.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    ck.say(f"تمام شد: {sev.get('H', 0)} جدی، {sev.get('M', 0)} متوسط، {sev.get('L', 0)} کم. فایل کامل: reports\\{f.name}")
    return summary


def delta():
    """Counts per code now vs the previous run (for the routine report)."""
    try:
        cur, prev = json.loads(LATEST.read_text(encoding="utf-8")), json.loads(PREV.read_text(encoding="utf-8"))
    except Exception:
        return None
    return {c: cur["codes"].get(c, 0) - prev["codes"].get(c, 0) for c in set(cur["codes"]) | set(prev["codes"])}
