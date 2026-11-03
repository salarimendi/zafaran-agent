"""Stage 4 - article engine. Title -> AI brief (category, slug, tags, keyword, SEO, outline, image prompts) -> YOU edit ->
AI writes section by section -> code cleans + checks (QC) + suggests internal links -> DRAFT in WordPress ->
you drop numbered images (01, 02 ...) in a folder -> code converts to WebP, names, alt text, schema -> you publish.
AI only writes (brief + text). Everything that touches the site is preview -> explicit tick -> backup/log, and new items are DRAFT.
Categories/tags are never created without your tick."""
import hashlib
import json
import re
import shutil
from datetime import datetime
from difflib import get_close_matches
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import keywords as kw
from . import llm
from .common import DIGITS, SLUG_RE, StepError, confirm, ensure, log_action, md, norm, slug_error
from .config import ROOT, load_config
from .corpus import read_articles
from .llm import LLMError
from .site import TAX, load_snapshot, resolve_term, slug_in_use
from .wp_client import WPClient

DRAFTS = ROOT / "knowledge" / "drafts"
IMG_IN = ROOT / "inbox" / "images"
BUILD = ROOT / "build"
BACKUP = ROOT / "backups" / "articlegen"
STYLE_GUIDE = ROOT / "knowledge" / "style" / "style_guide.md"
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
STATES = {"new": "فقط عنوان", "brief": "بریف آماده", "written": "متن نوشته شد", "draft": "پیش‌نویس در سایت", "published": "منتشر شد"}
BATCH_SECTIONS, WORDS_PER_SECTION = 2, 150
MEDICAL = ["درمان", "شفا", "معالجه", "سرطان", "دیابت", "افسردگی", "داروی", "تضمین", "قطعاً", "قطعا", "بیماری", "ضدسرطان", "لاغری", "کاهش وزن", "بدون عوارض", "معجزه"]


def get_client():
    c = WPClient(load_config())
    c.detect_mode()
    return c


# ------------------------------------------------------------------ jobs (one folder per article under knowledge/drafts)
def _file(jid):
    return DRAFTS / jid / "job.json"


def new_job(title, note="", source="manual"):
    title = " ".join(str(title).split())
    if not title:
        raise ValueError("عنوان خالی است.")
    DRAFTS.mkdir(parents=True, exist_ok=True)
    base = f"a{datetime.now():%Y%m%d_%H%M%S}"
    jid, i = base, 1
    while (DRAFTS / jid).exists():
        i += 1
        jid = f"{base}_{i}"
    (DRAFTS / jid).mkdir()
    job = {"id": jid, "created": datetime.now().isoformat(timespec="seconds"), "title": title, "note": note.strip(), "source": source, "state": "new",
           "brief": None, "parts": {}, "text": "", "links": [], "wp": None, "cost": 0.0, "log": []}
    save(job)
    return job


def save(job):
    f = _file(job["id"])
    tmp = f.with_name("job.json.tmp")                                # atomic: a page being read never sees a half-written file
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)


def load(jid):
    if not re.fullmatch(r"a\d{8}_\d{6}(_\d+)?", str(jid)) or not _file(jid).exists():
        return None
    return json.loads(_file(jid).read_text(encoding="utf-8"))


def jobs():
    out = []
    for d in sorted(DRAFTS.glob("a*"), reverse=True) if DRAFTS.exists() else []:
        j = load(d.name)
        if j:
            out.append(j)
    return out


def delete_job(jid):
    j = load(jid)
    if j:
        shutil.rmtree(DRAFTS / jid)
    return bool(j)


def _note(job, msg):
    job["log"] = (job.get("log", []) + [f"{datetime.now():%Y-%m-%d %H:%M} {msg}"])[-30:]


def _cost(job, info):
    job["cost"] = round(job.get("cost", 0) + info.get("cost", 0), 6)


# ------------------------------------------------------------------ context
def style_guide(limit=1800):
    return STYLE_GUIDE.read_text(encoding="utf-8")[:limit] if STYLE_GUIDE.exists() else ""


def site_context(own_id=None):
    snap = load_snapshot() or {}
    cats = [{"name": c["name"], "slug": c.get("slug", ""), "id": c["id"]} for c in snap.get("category", [])]
    tags = sorted(snap.get("post_tag", []), key=lambda t: -t.get("count", 0))
    used = [d["focus"] for d in kw.build_docs() if d["kind"] == "article" and d["focus"] and not (own_id and str(d.get("id")) == str(own_id))] if snap else []
    slugs = {unquote(i.get("slug", "")) for lst in ("posts", "pages", "products") for i in snap.get(lst, [])}
    shop = [{"name": c["name"]} for c in snap.get("product_cat", []) if c.get("count", 0) > 0]
    return {"shop_cats": shop, "cats": cats, "tags": [{"name": t["name"], "slug": t.get("slug", ""), "id": t["id"]} for t in tags], "used_k": used, "slugs": slugs,
            "has_snapshot": bool(snap)}


# ------------------------------------------------------------------ quality charter (shared by every AI prompt of this stage)
QUALITY = ("معیارهای برتری مقاله (همه را رعایت کن): "
           "۱) یونیک و اورجینال: متن باید کاملاً تازه و با نگارش خودت باشد؛ از بازنویسی یا کپی مقاله‌های دیگر سایت‌ها و از کلیشه‌هایی مثل «در دنیای امروز»، «همان‌طور که می‌دانیم»، «طلای سرخ» پرهیز کن؛ "
           "هر بخش زاویه، مثال یا نکتهٔ عملی مخصوص خودش را داشته باشد و مطلب بخش‌های دیگر یا مقاله‌های موجود سایت (existing/avoid) را تکرار نکند. "
           "۲) نیت جست‌وجو: همان چیزی که جست‌وجوگر با کلمهٔ کلیدی دنبالش است را سریع، دقیق و کامل جواب بده؛ پاسخ اصلی در ابتدای مقاله و ابتدای هر بخش بیاید. "
           "۳) عمق و کاربرد: نکتهٔ مشخص، مقایسه، مثال واقعی از خرید، مصرف و نگهداری، خطای رایج و راه‌حلش؛ جملهٔ پرکننده و حشو نه. "
           "۴) E-E-A-T: لحن کارشناس فروشگاه با تجربهٔ عملی و شفاف؛ بدون ادعای بی‌منبع، عدد و آمار ساختگی، ادعای درمانی یا پزشکی و وعدهٔ قطعی. "
           "۵) سئوی طبیعی: کلمهٔ کلیدی کانونی در مقدمه و یکی دو تیتر بیاید؛ مترادف‌ها، عبارت‌های مرتبط و پرسش‌های واقعی کاربران طبیعی پوشش داده شوند؛ تکرار اجباری نه. "
           "۶) ساختار قابل اسکن: تیترهای H2 و H3 توصیفی، پاراگراف کوتاه، و فقط جاهایی که کمک می‌کند لیست یا جدول. "
           "۷) هیچ لینک و آدرس اینترنتی ننویس (لینک داخلی بعداً با کد گذاشته می‌شود).")


# ------------------------------------------------------------------ 1) brief
BRIEF_SYS = ("تو سردبیر سئوی فارسی «زعفران مارکت» (فروشگاه زعفران) هستی. از روی عنوان مقاله بریف بساز. فقط یک شیء JSON بده (بدون ``` و بدون هیچ توضیح دیگری). فیلدها: "
             "cat = «شمارهٔ» یکی از دسته‌های موجود در cats (فقط عدد؛ نام دسته ممکن است انگلیسی باشد، همان را انتخاب کن){new_cat}؛ slug = آدرس لاتین کوچک با خط تیره (۳ تا ۶ کلمه؛ املای رایج لاتین مثل sargol و negin)؛ "
             "tags = آرایهٔ حداکثر ۴ «شمارهٔ» برچسب از tags (فقط عدد؛ برچسب نساز)؛ k = کلمهٔ کلیدی کانونی ۲ تا ۴ کلمه که در used_k نیست؛ t = عنوان سئو حداکثر ۶۰ کاراکتر شامل k؛ "
             "m = متا دیسکریپشن ۱۱۰ تا ۱۶۰ کاراکتر شامل k؛ x = خلاصهٔ کوتاه یک تا دو جمله؛ "
             "title = عنوان نهایی مقاله: idea فقط یک «ایده» است و نباید عیناً تکرار شود؛ آن را با توجه به فروشگاه، کلمهٔ کلیدی k و جست‌وجوهای واقعی q بهتر، طبیعی و جذاب بنویس "
             "(مثلاً از «هدیه برای روز مادر» به «هدیهٔ کادویی زعفران برای روز مادر»؛ حداکثر ۷۰ کاراکتر، بدون کلیک‌بیت و بدون ادعای پزشکی)؛ "
             "h = آرایهٔ ۶ تا ۸ شیء (حداقل ۶ تیتر) {\"h\": تیتر H2, \"n\": یادداشت کوتاه دربارهٔ زاویهٔ یونیک همان بخش, \"s\": آرایهٔ ۰ تا ۳ تیتر H3 زیرمجموعهٔ همان بخش} "
             "(تیتر آخر جمع‌بندی؛ k در یکی از تیترها بیاید؛ دست‌کم ۳ بخش باید ۲ یا ۳ تیتر H3 داشته باشند؛ هر H3 جنبهٔ مشخصی از همان بخش است و تکرار H2 نیست؛ "
             "یک بخش «سوالات متداول» با H3هایی به شکل پرسش واقعی کاربران مجاز و مفید است)؛ existing عنوان مقاله‌های موجود سایت است: موضوع و زاویهٔ مقاله نباید با آن‌ها هم‌پوشانی یا تکرار داشته باشد؛ "
             "img = ۳ تا ۵ شیء پیشنهاد تصویر {\"w\": \"cover\" یا شمارهٔ تیتر (از ۱) که تصویر بعد از آن بیاید, \"kind\": \"photo\" یا \"infographic\", "
             "\"p\": پرامپت کامل برای ابزار تولید تصویر (صحنه، سبک، نور، نسبت تصویر؛ بدون متن روی تصویر مگر اینفوگرافیک), \"f\": نام فایل لاتین با خط تیره ۲ تا ۵ کلمه, "
             "\"a\": متن Alt فارسی توصیفی, \"d\": دادهٔ لازم داخل اینفوگرافیک (برای photo خالی)}؛ اولی cover باشد. در پرامپت تصویر برند «زعفران مارکت» را در نظر بگیر (لوگو و آدرس سایت بعداً خودکار اضافه می‌شود). ادعای درمانی و پزشکی ننویس. " + QUALITY)


DEFAULT_LOGO = "https://zafaranmarket.com/wp-content/uploads/2026/06/logo-160-57.png"


def brand_info():
    import os
    from dotenv import load_dotenv
    from urllib.parse import urlparse
    load_dotenv(ROOT / ".env")
    site = (os.environ.get("WP_SITE_URL") or (load_snapshot() or {}).get("site") or "https://zafaranmarket.com").strip().rstrip("/")
    return {"site": site, "host": urlparse(site).netloc or site, "logo": (os.environ.get("BRAND_LOGO_URL") or DEFAULT_LOGO).strip()}


def brand_line(kind):
    b = brand_info()
    if kind == "infographic":
        return (f"\n\nبرند: لوگوی «زعفران مارکت» (فایل: {b['logo']}) را در بالای اینفوگرافیک و آدرس سایت {b['host']} را خوانا در پایین بگذار؛ "
                "رنگ‌ها را با لوگو هماهنگ کن. اگر ابزار شما آدرس تصویر را نمی‌پذیرد، فایل لوگو را خودتان پیوست کنید.")
    return (f"\n\nبرند: لوگوی «زعفران مارکت» (فایل: {b['logo']}) را خیلی کوچک و ظریف در یک گوشهٔ تصویر بگذار (مثل واترمارک)؛ "
            f"نوشتهٔ دیگری روی تصویر نیاید مگر نام سایت {b['host']} در گوشه. اگر ابزار شما آدرس تصویر را نمی‌پذیرد، فایل لوگو را خودتان پیوست کنید.")


def related_queries(idea, limit=12):
    """Searches people really use (Search Console + keyword bank) that share a specific word with the idea. Code only."""
    res = kw.load_analysis()
    if not res:
        return []
    it = kw.tset(idea) - kw.MODIFIERS
    out = [(r["impr"], r["key"]) for r in res["bank"] if r["impr"] and it & (set(r["tokens"]) - kw.MODIFIERS) and "brand" != r["status"]]
    return [k for _, k in sorted(out, reverse=True)[:limit]]


def _brief_msgs(job, ctx, existing_only):
    sysm = BRIEF_SYS.replace("{new_cat}", " (ساخت دستهٔ جدید ممنوع)" if existing_only else " یا اگر هیچ‌کدام مناسب نیست «NEW:نام دسته|latin-slug»")
    idea = job.get("idea") or job["title"]
    user = {"idea": idea, "note": job.get("note", ""), "q": related_queries(idea), "shop": [c["name"] for c in ctx.get("shop_cats", [])][:15], "cats": {str(i): c["name"] for i, c in enumerate(ctx["cats"], 1)},
            "tags": {str(i): t["name"] for i, t in enumerate(ctx["tags"][:60], 1)}, "used_k": ctx["used_k"][:80],
            "existing": [d["title"] for d in ranked_docs({"title": idea, "brief": {}}, ("article",), 80)]}
    sg = style_guide(700)
    if sg:
        user["style"] = sg
    return [{"role": "system", "content": sysm}, {"role": "user", "content": json.dumps(user, ensure_ascii=False, separators=(",", ":"))}]


def _find(name, items):
    n = norm(name)
    for i in items:
        if norm(i["name"]) == n:
            return i
    return None


def _by_number(v, items):
    v = str(v).strip().rstrip(".)")
    return items[int(v) - 1] if v.isdigit() and 1 <= int(v) <= len(items) else None


def _find_slug(name, items):
    n = str(name).strip().lower()
    return next((i for i in items if n and i.get("slug", "").lower() == n), None)


def _subs(v, limit=4):
    out = []
    for x in v if isinstance(v, list) else []:
        x = " ".join(str(x).split())
        if x and x not in out:
            out.append(x)
    return out[:limit]


def norm_brief(r, ctx, existing_only=False):
    """AI object -> our brief (editable). Anything unknown is dropped, never invented."""
    b = {"title": " ".join(str(r.get("title", "")).split()), "cat": "", "new_cat": None, "slug": str(r.get("slug", "")).strip().lower(), "tags": [], "new_tags": [], "k": str(r.get("k", "")).strip(),
         "t": str(r.get("t", "")).strip(), "m": str(r.get("m", "")).strip(), "x": str(r.get("x", "")).strip(), "h": [], "img": [], "schema": False}
    cat = str(r.get("cat", "")).strip()
    if cat.upper().startswith("NEW:") and not existing_only:
        nm, _, sg = cat[4:].partition("|")
        b["new_cat"] = {"name": nm.strip(), "slug": sg.strip().lower()}
    else:
        hit = _by_number(cat, ctx["cats"]) or _find(cat, ctx["cats"]) or _find_slug(cat, ctx["cats"])
        if not hit and cat and not cat.isdigit():
            close = get_close_matches(cat, [c["name"] for c in ctx["cats"]], n=1, cutoff=0.6)
            hit = _find(close[0], ctx["cats"]) if close else None
        b["cat"] = hit["name"] if hit else ""
    for tg in r.get("tags") or []:
        hit = _by_number(tg, ctx["tags"][:60]) or _find(str(tg), ctx["tags"])
        if hit and hit["name"] not in b["tags"]:
            b["tags"].append(hit["name"])
    for h in r.get("h") or []:
        if isinstance(h, dict) and str(h.get("h", "")).strip():
            b["h"].append({"h": str(h["h"]).strip(), "n": str(h.get("n", "")).strip(), "s": _subs(h.get("s")), "on": True})
    n = 1
    for im in r.get("img") or []:
        if not isinstance(im, dict):
            continue
        w = im.get("w")
        w = "cover" if str(w).strip().lower() == "cover" else (int(w) if str(w).strip().isdigit() else 0)
        kind = "infographic" if im.get("kind") == "infographic" else "photo"
        p_ = str(im.get("p", "")).strip()
        if p_ and brand_info()["logo"] not in p_:
            p_ += brand_line(kind)
        b["img"].append({"n": n, "w": w, "kind": kind, "p": p_,
                         "f": str(im.get("f", "")).strip().lower(), "a": str(im.get("a", "")).strip(), "d": str(im.get("d", "")).strip(), "on": True})
        n += 1
    if b["img"] and b["img"][0]["w"] != "cover":
        b["img"][0]["w"] = "cover"
    return b


def brief_warnings(b, ctx=None, check_slug=True, own_id=None):
    ctx = ctx or site_context(own_id)
    w = []
    if slug_error(b.get("slug")):
        w.append(slug_error(b.get("slug")))
    elif check_slug and ctx["has_snapshot"] and b["slug"] in ctx["slugs"]:
        w.append(f"slug «{b['slug']}» قبلاً در سایت استفاده شده است")
    k, t, m = b.get("k", ""), b.get("t", ""), b.get("m", "")
    if not k:
        w.append("کلمه کلیدی خالی است")
    else:
        if norm(k) not in norm(t):
            w.append("کلمه کلیدی در عنوان سئو نیست")
        if norm(k) not in norm(m):
            w.append("کلمه کلیدی در متا نیست")
        kt = kw.topic_set(k)
        for fk in ctx["used_k"]:
            if norm(fk) == norm(k) or (len(kt) >= 2 and kw.jacc(kt, kw.topic_set(fk)) >= 0.66):
                w.append(f"کلمه کلیدی شبیه کلمه کلیدی یک مقالهٔ موجود است («{fk}»)؛ هم‌نوع‌خواری")
                break
        if not any(norm(k) in norm(h["h"]) for h in b.get("h", []) if h.get("on")):
            w.append("کلمه کلیدی در هیچ تیتری نیست")
    if len(t) > 60:
        w.append(f"عنوان سئو {len(t)} کاراکتر است (حداکثر ۶۰)")
    if not 110 <= len(m) <= 160:
        w.append(f"متا {len(m)} کاراکتر است (۱۱۰ تا ۱۶۰)")
    if not b.get("cat") and not b.get("new_cat"):
        w.append("دسته انتخاب نشده است")
    if b.get("new_cat") and slug_error(b["new_cat"].get("slug")):
        w.append("slug دستهٔ جدید باید لاتین باشد")
    if sum(1 for h in b.get("h", []) if h.get("on")) < 6:
        w.append("کمتر از ۶ تیتر فعال (حداقل ۶ تیتر لازم است)")
    fs = [i["f"] for i in b.get("img", []) if i.get("on")]
    for i in b.get("img", []):
        if i.get("on") and (not i["f"] or not SLUG_RE.match(i["f"])):
            w.append(f"نام فایل تصویر {i['n']} باید لاتین با خط تیره باشد")
    if len(fs) != len(set(fs)):
        w.append("نام فایل دو تصویر یکسان است")
    return w


def _brief(c, calls, est_in, est_out):
    from .ai import _brief as b
    b(c, calls, est_in, est_out)


def make_brief(job, go, existing_only=False):
    c = llm.cfg()
    ctx = site_context()
    msgs = _brief_msgs(job, ctx, existing_only)
    est_in = llm.est_tokens(sum(len(m["content"]) for m in msgs))
    est_out = min(c["max_out"], 1400)
    print(f"بریف مقاله «{job['title']}» - ۱ تماس.")
    _brief(c, 1, est_in, est_out)
    if not go:
        print("Estimate only (nothing sent).")
        return
    if not confirm(1, "AI call", to="to the AI provider (spends credit; NOTHING is sent to your website)"):
        print("Cancelled.")
        return
    text, info = llm.chat(msgs, max_out=est_out, task="brief")
    job["brief"] = norm_brief(llm.parse_json_obj(text), ctx, existing_only)
    new_title = job["brief"].pop("title", "")
    if 8 <= len(new_title) <= 110:
        job.setdefault("idea", job["title"])
        job["title"] = new_title
    job["brief"]["existing_only"] = existing_only
    job["brief"]["avoid"] = [d["title"] for d in ranked_docs(job, ("article",), 40)]
    job["state"] = "brief"
    _cost(job, info)
    _note(job, f"brief (${info['cost']:.4f})")
    save(job)
    print(f"بریف ساخته شد (هزینه ${info['cost']:.4f}). آن را بازبینی و ویرایش کنید؛ چیزی روی سایت تغییر نکرد.")


def parse_brief_form(f, job):
    """Panel form fields -> updated brief (all editable)."""
    one = lambda k, d="": (f.get(k, [d])[0] if f.get(k) else d).strip()
    b = job["brief"]
    job["title"] = one("title", job["title"]) or job["title"]
    cat = one("cat")
    b["cat"] = "" if cat == "__new__" else cat
    nm, sg = one("new_cat_name"), one("new_cat_slug").lower()
    b["new_cat"] = {"name": nm, "slug": sg} if (cat == "__new__" and nm) else None
    b["tags"] = [t for t in f.get("tag", []) if t]
    nt = []
    for ln in one("new_tags").splitlines():
        name, _, s_ = ln.partition("|")
        if name.strip():
            nt.append({"name": name.strip(), "slug": s_.strip().lower()})
    b["new_tags"] = nt
    for k in ("slug", "k", "t", "m", "x"):   # form fields are prefixed: a bare "t" is the panel's CSRF token field
        b[k] = one("b_" + k).lower() if k == "slug" else one("b_" + k)
    hs = []
    for i in range(len(b["h"]) + 2):
        h = one(f"h_{i}")
        if h:
            hs.append({"h": h, "n": one(f"hn_{i}"), "s": _subs([x for x in one(f"hs_{i}").splitlines()], 6), "on": bool(f.get(f"hon_{i}"))})
    b["h"] = hs
    for im in b["img"]:
        i = im["n"]
        im["on"] = bool(f.get(f"ion_{i}"))
        for k in ("p", "f", "a", "d"):
            im[k] = one(f"i{k}_{i}").lower() if k == "f" else one(f"i{k}_{i}")
        w = one(f"iw_{i}")
        im["w"] = "cover" if w == "cover" else (int(w) if w.isdigit() else im["w"])
        im["kind"] = "infographic" if one(f"ik_{i}") == "infographic" else "photo"
    b["schema"] = bool(f.get("schema"))
    return job


# ------------------------------------------------------------------ 2) writing
WRITE_SYS = ("تو نویسندهٔ محتوای «زعفران مارکت» (فروشگاه زعفران) هستی و فارسی روان و طبیعی می‌نویسی. فقط Markdown بنویس. "
             "اگر intro=true اول مقدمهٔ دو پاراگرافی بدون تیتر بنویس و کلمهٔ کلیدی k را طبیعی در پاراگراف اول بیاور و همان‌جا پاسخ اصلی نیت جست‌وجو را بده؛ "
             "سپس برای هر بخش دقیقاً «## » + همان تیتر داده‌شده بنویس و بعد حدود ۱۵۰ تا ۲۲۰ کلمه در پاراگراف‌های کوتاه (در صورت نیاز یک لیست یا جدول ساده). "
             "اگر برای بخش s (تیترهای H3) داده شده، هر کدام را دقیقاً با «### » + همان تیتر و بعد ۴۰ تا ۹۰ کلمه زیرش بنویس؛ اگر s خالی است و بخش چندجنبه است می‌توانی خودت ۲ تا ۳ تیتر «### » توصیفی اضافه کنی. "
             "فقط H2 و H3 مجاز است (نه H1 و نه H4). هیچ لینک و آدرس اینترنتی ننویس. عدد و آمار مشخص، ادعای درمانی یا پزشکی و وعدهٔ قطعی ننویس. تیتر اصلی H1 و عنوان مقاله را تکرار نکن. "
             "مطالب بخش‌های قبلی (prev_headings) را تکرار نکن. فقط متن مقاله، بدون توضیح اضافه. " + QUALITY)


def plan_batches(b, parts=None):
    """parts=None: the full plan. With parts: ONLY what is still missing (the intro when absent, the sections without text), so finished text is never paid for twice."""
    secs = [h for h in b["h"] if h.get("on")]
    intro = True
    if parts is not None:
        secs = [h for h in secs if not parts.get(h["h"])]
        intro = not parts.get("intro")
    batches = []
    if intro:
        batches.append({"intro": True, "secs": secs[:BATCH_SECTIONS]})
        secs = secs[BATCH_SECTIONS:]
    for i in range(0, len(secs), BATCH_SECTIONS):
        batches.append({"intro": False, "secs": secs[i:i + BATCH_SECTIONS]})
    return batches


def _write_msgs(job, batch, done_heads, tail):
    b = job["brief"]
    sysm = WRITE_SYS + (("\n\nراهنمای لحن برند:\n" + style_guide()) if style_guide() else "")
    user = {"title": job["title"], "k": b["k"], "intro": batch["intro"], "sections": [{"h": s["h"], "n": s.get("n", ""), "s": s.get("s", [])} for s in batch["secs"]],
            "prev_headings": done_heads[-8:]}
    if b.get("avoid"):
        user["avoid"] = b["avoid"][:30]
    if tail:
        user["prev_tail"] = tail[-300:]
    return [{"role": "system", "content": sysm}, {"role": "user", "content": json.dumps(user, ensure_ascii=False, separators=(",", ":"))}]


def write_estimate(job):
    c = llm.cfg()
    batches = plan_batches(job["brief"])
    est_in = est_out = 0
    heads = []
    for bt in batches:
        ms = _write_msgs(job, bt, heads, "x" * 300)
        est_in += llm.est_tokens(sum(len(m["content"]) for m in ms))
        est_out += min(c["max_out"], 1400)
        heads += [s["h"] for s in bt["secs"]]
    return c, len(batches), est_in, est_out


def _split_sections(text, n):
    """AI markdown -> (intro, [section bodies]) matched by order of '## ' headings."""
    text = re.sub(r"```(?:markdown|md)?", "", text).strip()
    pieces = re.split(r"(?m)^##\s+.*$", text)
    intro, bodies = pieces[0].strip(), [p.strip() for p in pieces[1:]]
    return intro, bodies[:n] + [""] * max(0, n - len(bodies))


def assemble(job):
    b, parts = job["brief"], job["parts"]
    secs = [h for h in b["h"] if h.get("on")]
    out = [parts.get("intro", "").strip()]
    img_after = {}
    for im in b["img"]:
        if im.get("w") not in ("cover", 0, None):
            img_after.setdefault(int(im["w"]), []).append(im["n"])
    for i, s in enumerate(secs, 1):
        out.append(f"## {s['h']}\n\n{parts.get(s['h'], '').strip()}")
        for n in img_after.get(i, []):
            out.append(f"[[IMG:{n}]]")
    return "\n\n".join(x for x in out if x)


def write(job, go, redo=False):
    """Writes the batches that are not done yet (cached answers are free). redo=True ignores the cache for missing ones."""
    if not job.get("brief"):
        raise ValueError("اول بریف را بسازید.")
    if job.get("text"):
        sync_parts_from_text(job)                                   # keep manual edits of the text when it is re-assembled
    c, n, est_in, est_out = write_estimate(job)
    todo = plan_batches(job["brief"], job["parts"])
    print(f"نوشتن «{job['title']}»: {n} تماس ({len(todo)} مانده). برآورد توکن: ورودی ~{est_in}، خروجی ≤ {est_out}")
    _brief(c, len(todo), est_in * len(todo) // max(n, 1), est_out * len(todo) // max(n, 1))
    if not go:
        print("Estimate only (nothing sent).")
        return
    if not todo:
        print("همهٔ بخش‌ها نوشته شده‌اند.")
    elif not confirm(len(todo), "AI call(s)", to="to the AI provider (spends credit; NOTHING is sent to your website)"):
        print("Cancelled.")
        return
    if todo and job.get("text"):
        snapshot(job, "قبل از نوشتن دوباره")
    heads, tail, spent = [h["h"] for h in job["brief"]["h"] if h.get("on") and job["parts"].get(h["h"])], "", 0.0
    for bt in todo:
        text, info = llm.chat(_write_msgs(job, bt, heads, tail), max_out=min(c["max_out"], 1400), task="write", use_cache=not redo)
        spent += info["cost"]
        intro, bodies = _split_sections(text, len(bt["secs"]))
        if bt["intro"]:
            job["parts"]["intro"] = intro
        for s, body in zip(bt["secs"], bodies):
            job["parts"][s["h"]] = body
        heads += [s["h"] for s in bt["secs"]]
        tail = "\n".join(job["parts"].get(s["h"], "") for s in bt["secs"])
        job["cost"] = round(job.get("cost", 0) + info["cost"], 6)
        save(job)
    mark_dirty(job)
    missing = [s["h"] for s in job["brief"]["h"] if s.get("on") and not job["parts"].get(s["h"])]
    job["text"] = fix_text(assemble(job))
    job["state"] = "written"
    try:
        suggest_links(job)
    except Exception:  # link suggestions are a bonus; never fail the writing
        job["links"] = []
    _note(job, f"write (${spent:.4f})")
    save(job)
    print(f"متن نوشته شد (هزینه این اجرا ${spent:.4f}). " + (f"بخش‌های خالی: {missing}؛ دوباره «نوشتن» را بزنید تا فقط همین‌ها تکرار شوند." if missing else "کنترل کیفیت را ببینید."))


def _batch_done(job, bt):
    p = job["parts"]
    return all(p.get(s["h"]) for s in bt["secs"]) and (not bt["intro"] or p.get("intro"))


def fix_text(t):
    """Free code fixes on the AI output: Arabic letters, headings, spacing, stray H1/fences. Never changes meaning."""
    t = t.replace("ي", "ی").replace("ك", "ک").replace("\r\n", "\n")
    t = re.sub(r"```\w*", "", t)
    t = re.sub(r"(?m)^#\s+", "## ", t)                     # no H1 inside the body (the theme prints the title as H1)
    t = re.sub(r"(?m)^#{3,6}\s+", "### ", t)
    t = re.sub(r"(?m)[ \t]+$", "", t)
    t = re.sub(r"(?m)([^\n])\n(## )", r"\1\n\n\2", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


# ------------------------------------------------------------------ 3) quality control (code only)
def words_of(t):
    return len(re.sub(r"\[\[IMG:\d+\]\]|[#*>`|-]", " ", t).split())


def section_body(t, h):
    m = re.search(r"(?ms)^##\s+" + re.escape(h) + r"\s*$(.*?)(?=^##\s|\Z)", t)
    return re.sub(r"\[\[IMG:\d+\]\]", "", m.group(1)) if m else ""


def qc(job):
    """-> [(sev 'H'|'M'|'L'|'OK', text)]"""
    b, t = job.get("brief") or {}, job.get("text") or ""
    if not t:
        return [("H", "هنوز متنی نوشته نشده است")]
    L, k = [], b.get("k", "")
    plain = re.sub(r"\[\[IMG:\d+\]\]", " ", re.sub(r"[#*>`|]", " ", re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)))
    wc = len(plain.split())
    if wc < 600:
        L.append(("M", f"متن کوتاه است: {wc} کلمه (هدف دست‌کم ۶۰۰)"))
    heads = re.findall(r"(?m)^##\s+(.+?)\s*$", t)
    if len(heads) < 6:
        L.append(("M", f"فقط {len(heads)} تیتر H2"))
    for s in b.get("h", []):
        if s.get("on") and s["h"] not in heads:
            L.append(("H", f"تیتر «{s['h']}» در متن نیست"))
        elif s.get("on") and len(section_body(t, s["h"]).split()) < 40:
            L.append(("H", f"بخش «{s['h']}» خالی یا بسیار کوتاه است"))
    if k:
        first = " ".join(plain.split()[:120])
        if norm(k) not in norm(first):
            L.append(("M", "کلمه کلیدی در ۱۲۰ کلمهٔ اول نیست"))
        if not any(norm(k) in norm(h) for h in heads):
            L.append(("M", "کلمه کلیدی در هیچ H2 نیست"))
        dens = norm(plain).count(norm(k)) * len(k.split()) * 100 / max(wc, 1)
        if dens > 3:
            L.append(("M", f"تراکم کلمه کلیدی زیاد است ({dens:.1f}٪)؛ احتمال خشک‌نویسی"))
        elif dens < 0.3:
            L.append(("L", f"کلمه کلیدی خیلی کم آمده ({dens:.1f}٪)"))
    urls, foreign = body_urls(t)
    if foreign:
        L.append(("M", "در متن لینک یا آدرس بیرونی هست (AI نباید لینک بدهد؛ لینک داخلی را بخش پیشنهاد لینک بگذارد): " + foreign[0][:60]))
    n_int = len(urls) - len(foreign)
    if n_int == 0 and wc >= 400 and not job.get("links"):
        L.append(("L", "لینک داخلی ندارد؛ از بخش «لینک داخلی» (کدمحور یا با AI) چند لینک مرتبط اضافه کنید"))
    oos = [d["title"] for d in kw.build_docs() if d["kind"] == "product" and d.get("oos") and d.get("url") and d["url"] in urls]
    if oos:
        L.append(("H", "لینک به محصول ناموجود (موجودی صفر) در متن هست؛ حذفش کنید: " + "، ".join(oos[:3])))
    h3 = H3_RE.findall(t)
    if wc >= 700 and not h3:
        L.append(("M", "هیچ تیتر H3 در متن نیست؛ برای ساختار بهتر زیرتیتر بگذارید (در بریف H3 بنویسید یا «طراحی مجدد تیترها با AI» را بزنید)"))
    for s_ in b.get("h", []):
        for sub in s_.get("s", []) if s_.get("on") else []:
            if sub not in h3:
                L.append(("L", f"زیرتیتر H3 برنامه‌ریزی‌شده «{sub}» در متن نیست"))
                break
    allh = heads + h3
    if len({norm(x) for x in allh}) != len(allh):
        L.append(("M", "دو تیتر (H2/H3) عیناً تکراری است"))
    for sev, msg in similarity_flags(job):
        L.append((sev, msg))
    for p in re.split(r"\n\n+", t):
        if not p.lstrip().startswith(("#", "-", "|", ">", "[[", "*")) and len(p.split()) > 130:
            L.append(("L", "پاراگراف بلند (بیش از ۱۳۰ کلمه)؛ بهتر است شکسته شود: " + p[:40] + "..."))
            break
    med = sorted({w for w in MEDICAL if w in t})
    if med:
        L.append(("H", "احتمال ادعای پزشکی/اغراق‌آمیز؛ حتماً بخوانید و اصلاح کنید: " + "، ".join(med)))
    nums = re.findall(r"[^.\n]*\d[\d.,٫]*\s*(?:درصد|٪|%|گرم|کیلو|میلی‌گرم|برابر|سال)[^.\n]*", t.translate(DIGITS))
    if nums:
        L.append(("M", f"{len(nums)} جمله عدد/آمار دارد؛ درستی را بررسی کنید: «{nums[0].strip()[:70]}»"))
    if re.search(r"[A-Za-z]{4,}", re.sub(r"\[\[IMG:\d+\]\]", "", t)):
        L.append(("L", "کلمهٔ لاتین در متن هست؛ اگر عمدی نیست اصلاح کنید"))
    sents = [s.strip() for s in re.split(r"[.!؟?\n]", t) if len(s.split()) >= 6]
    if len(sents) != len(set(sents)):
        L.append(("M", "جملهٔ تکراری در متن هست"))
    for w in brief_warnings(b, check_slug=not job.get("wp"), own_id=(job.get("wp") or {}).get("id")) if b else []:
        L.append(("M", "بریف: " + w))
    pend = [i for i in b.get("img", []) if i.get("on") and not i.get("media")]
    if pend:
        L.append(("L", f"{len(pend)} جای تصویر هنوز خالی است (بارگذاری کنید یا پیشنهاد را حذف کنید)"))
    if not L:
        L.append(("OK", "مشکلی با کد پیدا نشد"))
    return L


def body_urls(t):
    """-> (all urls, urls that are NOT on this site). Relative links count as internal."""
    host = brand_info()["host"].lower().removeprefix("www.")
    body = t.replace("[[IMG:", "")
    urls = re.findall(r"\]\(\s*([^)\s]+)", body) + re.findall(r"(?<![(\w/])(?:https?://|www\.)[^\s)\]<>]+", body)

    def internal(u):
        if u.startswith(("/", "#")):
            return True
        return urlparse(u if "//" in u else "//" + u).netloc.lower().removeprefix("www.") == host
    return urls, [u for u in urls if not internal(u)]


_SHINGLES = {}


def _shingles(text, n=5):
    w = norm(re.sub(r"\[\[IMG:\d+\]\]|[#*>`|\[\]()]|https?://\S+", " ", text)).split()
    return {hash(" ".join(w[i:i + n])) for i in range(max(len(w) - n + 1, 0))}


def site_shingles():
    """{article id/file: (title, shingles)} of the exported site articles; recomputed only when the files change."""
    from .corpus import ART
    files = sorted(ART.glob("*.md")) if ART.exists() else []
    sig = (str(ART), tuple((f.name, f.stat().st_mtime_ns) for f in files))
    if _SHINGLES.get("sig") != sig:
        out = {}
        for a in read_articles():
            if str(a.get("status", "publish")) == "publish":
                out[str(a.get("id") or a["file"])] = (a.get("title", ""), _shingles(a.get("body", "")), str(a.get("slug", "")))
        _SHINGLES.update(sig=sig, data=out)
    return _SHINGLES["data"]


def similarity_flags(job, warn=0.12, bad=0.30):
    """Code-only uniqueness check: share of this article's 5-word runs that also appear in one existing site article."""
    t = job.get("text") or ""
    mine = _shingles(t)
    if len(mine) < 30:
        return []
    own, slug = str((job.get("wp") or {}).get("id") or ""), (job.get("brief") or {}).get("slug", "")
    res = []
    for k, (title, sh, sl) in site_shingles().items():
        if k == own or (slug and sl == slug) or not sh:
            continue
        r = len(mine & sh) / len(mine)
        if r >= warn:
            res.append((r, title))
    res.sort(reverse=True)
    return [("H" if r >= bad else "M", f"شباهت متنی {r * 100:.0f}٪ با مقالهٔ موجود «{title}»؛ برای یونیک بودن بخش‌های مشابه را بازنویسی کنید") for r, title in res[:3]]


EEAT = ["هر ادعای مهم منبع یا توضیح روشن دارد؟", "یک نمونه یا تجربهٔ واقعی از فروشگاه/برداشت/بسته‌بندی در متن آمده؟", "نام برند و راه تماس در صفحه یا پاورقی سایت هست؟",
        "اگر از آمار یا پژوهش حرف زده‌اید، منبعش را خودتان بررسی کرده‌اید؟"]


# ------------------------------------------------------------------ 4) internal links (code)
def _pat(phrase):
    """Regex for a phrase that tolerates spaces/ZWNJ differences and Arabic ي/ك."""
    parts = [re.escape(x) for x in re.split(r"[\s\u200c]+", phrase.strip()) if x]
    return re.compile(r"[\s\u200c]+".join(p.replace("ی", "[یي]").replace("ک", "[کك]") for p in parts))


def suggest_links(job, max_links=5):
    """Code only: where an existing category/article name already appears in a paragraph. Not applied until you tick it."""
    t = job.get("text") or ""
    docs = [d for d in kw.build_docs() if d["kind"] in ("product_cat", "article") and d.get("url")]
    cand = []
    for d in docs:
        ph = d["focus"] if d["kind"] == "article" and d["focus"] else d["title"]
        if len(ph) >= 4 and len(ph.split()) <= 6:
            cand.append((ph, d))
    cand.sort(key=lambda x: -len(x[0]))
    pars, out, used_p, used_u = re.split(r"\n\n+", t), [], set(), set()
    for ph, d in cand:
        if d["url"] in used_u or len(out) >= max_links:
            continue
        rx = _pat(ph)
        for i, p in enumerate(pars):
            if i in used_p or p.lstrip().startswith(("#", "-", "|", ">", "[[", "*", "1.")):
                continue
            spans = [m.span() for m in re.finditer(r"\[[^\]]*\]\([^)]*\)", p)]
            m = next((m for m in rx.finditer(p) if not any(a <= m.start() < b_ for a, b_ in spans)), None)
            if not m:
                continue
            out.append({"par": i, "phrase": m.group(0), "url": d["url"], "title": d["title"], "kind": d["kind"], "ctx": p[max(0, m.start() - 40):m.end() + 40]})
            used_p.add(i)
            used_u.add(d["url"])
            break
    job["links"] = out
    save(job)
    return out


def links_pending(job):
    return bool(job.get("links"))


def apply_links(job, picks):
    pars = re.split(r"\n\n+", job["text"])
    n = 0
    for i in sorted(picks):
        if not 0 <= i < len(job["links"]):
            continue
        L = job["links"][i]
        at = L["par"] if L["par"] < len(pars) else -1
        if at < 0 or L["phrase"] not in pars[at]:                   # the text moved since the suggestion: find the phrase again
            at = next((k for k, q in enumerate(pars) if L["phrase"] in q and not q.lstrip().startswith(("#", "[[", "|", ">"))), -1)
        p = pars[at] if at >= 0 else ""
        if p and f"]({L['url']})" not in "\n\n".join(pars) and not _inside_link(p, L["phrase"]):
            pars[at] = p.replace(L["phrase"], f"[{L['phrase']}]({L['url']})", 1)
            n += 1
    if n:
        job["text"] = "\n\n".join(pars)
        job["links"] = []
        _note(job, f"{n} internal link(s)")
        mark_dirty(job)
        save(job)
    return n


def _inside_link(p, phrase):
    i = p.find(phrase)
    return i >= 0 and any(a <= i < b_ for a, b_ in (m.span() for m in re.finditer(r"\[[^\]]*\]\([^)]*\)", p)))


# ------------------------------------------------------------------ 5) images
def img_dir(job):
    return IMG_IN / job["id"]


def find_image(job, n):
    d = img_dir(job)
    for f in sorted(d.glob("*")) if d.exists() else []:
        if f.suffix.lower() in IMG_EXT and f.stem.isdigit() and int(f.stem) == n:
            return f
    return None


def slot_status(job):
    out = []
    for im in (job.get("brief") or {}).get("img", []):
        st = "removed" if not im.get("on") else "attached" if im.get("media") else "found" if find_image(job, im["n"]) else "missing"
        out.append((im, st))
    return out


def fig_html(im, media):
    cap = im["a"]
    return (f'<figure class="wp-block-image size-large"><img src="{media["url"]}" alt="{_attr(im["a"])}" title="{_attr(im["a"])}" width="{media["w"]}" height="{media["h"]}" '
            f'loading="lazy" decoding="async" /><figcaption>{_attr(cap)}</figcaption></figure>')


def _attr(s):
    return str(s).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def schema_html(job):
    imgs = [(im, im["media"]) for im in job["brief"]["img"] if im.get("on") and im.get("media")]
    if not imgs or not job["brief"].get("schema", False):
        return ""
    g = [{"@type": "ImageObject", "contentUrl": m["url"], "name": im["a"], "description": im["a"], "width": m["w"], "height": m["h"]} for im, m in imgs]
    return '<script type="application/ld+json">' + json.dumps({"@context": "https://schema.org", "@graph": g}, ensure_ascii=False, separators=(",", ":")) + "</script>"


BRAND_RED, LINE, ZEBRA = "#9e2a2b", "#eadfce", "#fdfaf5"


def _style_table(m):
    t, n = m.group(0), [0]

    def tr(mm):
        n[0] += 1
        return "<tr" + (f' style="background:{ZEBRA}"' if n[0] % 2 == 1 and n[0] > 1 else "") + ">"
    t = re.sub(r"<tr>", tr, t)
    t = re.sub(r"<th(?:\s[^>]*)?>", f'<th style="background:{BRAND_RED};color:#fff;padding:10px 12px;border:1px solid {LINE};text-align:right;font-weight:700">', t)
    t = re.sub(r"<td(?:\s[^>]*)?>", f'<td style="padding:10px 12px;border:1px solid {LINE};text-align:right;vertical-align:top">', t)
    t = t.replace("<table>", f'<table style="width:100%;border-collapse:collapse;min-width:420px;font-size:0.95em;line-height:1.8">', 1)
    return f'<div class="zaf-table" style="overflow-x:auto;margin:1.4em 0;border-radius:10px;box-shadow:0 1px 4px rgba(0,0,0,.08)">{t}</div>'


def _style_faq(m):
    head, body = m.group(1), m.group(2)
    parts = re.split(r"(?=<h3>)", body)
    out = parts[0]
    for p_ in parts[1:]:
        q = re.match(r"<h3>(.*?)</h3>(.*)", p_, re.S)
        if q:
            out += (f'<div class="zaf-faq" style="border:1px solid {LINE};border-right:4px solid {BRAND_RED};border-radius:10px;padding:4px 16px;margin:12px 0;background:{ZEBRA}">'
                    f'<h3 style="margin:10px 0 6px;font-size:1.05em;color:{BRAND_RED}">{q.group(1)}</h3>{q.group(2)}</div>')
        else:
            out += p_
    return head + out


def style_html(html):
    """Inline-styled tables and FAQ cards (inline style survives WordPress' filters; no <style>/<script> needed, works in any theme and in RTL)."""
    html = re.sub(r"<table>.*?</table>", _style_table, html, flags=re.S)
    return re.sub(r"(<h2>[^<]*(?:سوالات متداول|پرسش‌های متداول|پرسش های متداول|سؤالات متداول)[^<]*</h2>)(.*?)(?=<h2>|\Z)", _style_faq, html, flags=re.S)


def to_html(job, figures=None):
    figures = figures or {}
    t = job["text"]
    on = {im["n"] for im in job["brief"]["img"] if im.get("on")}
    html = style_html(md(t))

    def rep(m):
        n = int(m.group(1))
        if n not in on:
            return ""
        return figures.get(n, f"<!--IMG:{n}-->")
    return re.sub(r"<p>\s*\[\[IMG:(\d+)\]\]\s*</p>", rep, html)


def _upload(c, path, alt, title):
    h = {"Content-Disposition": f'attachment; filename="{path.name}"', "Content-Type": "image/webp"}
    d = ensure(c.request("POST", "wp/v2/media", auth="wp", data=path.read_bytes(), headers=h, timeout=180), f"upload {path.name}")
    ensure(c.request("POST", f"wp/v2/media/{d['id']}", auth="wp", json={"alt_text": alt, "title": title}), f"alt text for media {d['id']}")
    return d["id"], d.get("source_url", "")


def convert_local(job):
    """Local only (no network): convert the numbered images found in the folder to WebP named from the brief. -> [(slot, webp_path, (w, h))]"""
    from . import images as img
    out, prepared = BUILD / job["id"], []
    for im in job["brief"]["img"]:
        f = find_image(job, im["n"]) if im.get("on") and not im.get("media") else None
        if not f:
            continue
        if not im["a"]:
            raise ValueError(f"Alt تصویر {im['n']:02d} خالی است؛ اول در بریف پرش کنید.")
        name = im["f"] if im["f"] and SLUG_RE.match(im["f"]) else f"{job['brief']['slug']}-{im['n']}"
        dest = out / f"{name}.webp"
        before, after, size = img.to_webp(f, dest)
        print(f"  تصویر {im['n']:02d}: {f.name} ({before}KB) -> {dest.name} ({after}KB، {size[0]}x{size[1]}) | alt: {im['a']}")
        prepared.append((im, dest, size))
    return prepared


def _upload_all(job, c, prepared):
    for im, dest, size in prepared:
        mid, url = _upload(c, dest, im["a"], job["title"])
        im["media"] = {"id": mid, "url": url, "w": size[0], "h": size[1], "file": dest.name}
        print(f"  بارگذاری شد: media {mid}")
        save(job)


def _figures(job):
    return {im["n"]: fig_html(im, im["media"]) for im in job["brief"]["img"] if im.get("on") and im.get("media") and im["n"] != 1}


def _cover_id(job):
    return next((im["media"]["id"] for im in job["brief"]["img"] if im["n"] == 1 and im.get("on") and im.get("media")), None)


SCHEMA_LEFT = re.compile(r'\s*\{\s*["“”]@context["“”]\s*:\s*["“”]https?://schema\.org["“”].*?\}\s*\]\s*\}', re.S)


def _check_schema(c, pid, sc):
    if not sc:
        return
    back = ensure(c.get(f"wp/v2/posts/{pid}", auth="wp", params={"context": "edit"}), "verify")
    raw = (back.get("content") or {}).get("raw") or ""
    if "application/ld+json" not in raw:
        if SCHEMA_LEFT.search(raw):                                  # WordPress removed the <script> tag but left the JSON as visible text: clean it up
            ensure(c.request("POST", f"wp/v2/posts/{pid}", auth="wp", json={"content": SCHEMA_LEFT.sub("", raw).rstrip()}), "clean schema text")
            print("  !! وردپرس تگ Schema را حذف کرد و متن آن در صفحه می‌ماند؛ متن اضافه خودکار پاک شد. (Schema تصویر در این سایت کار نمی‌کند؛ Yoast خودش می‌سازد؛ در بریف خاموشش کنید.)")
        else:
            print("  !! وردپرس تگ Schema (JSON-LD) را حذف کرد (نقش کاربری اجازهٔ این کد را ندارد). تصویر و Alt سالم‌اند؛ Schema را با Yoast یا دستی اضافه کنید یا در بریف خاموشش کنید.")
    else:
        print("  Schema تصویر (ImageObject) در متن ذخیره شد.")


def attach_images(job, go):
    """LATE fill: images added after the draft was sent. Converts, uploads, fills the placeholders of the existing draft (backup first)."""
    if not job.get("wp"):
        raise ValueError("اول مقاله را (حتی به‌صورت پیش‌نویس) به سایت بفرستید.")
    prepared = convert_local(job)
    if not prepared:
        print("تصویر تازه‌ای پیدا نشد. فایل‌ها را با نام 01، 02، ... در پوشهٔ " + str(img_dir(job)) + " بگذارید.")
        return
    if not go:
        print("\nPreview only. Nothing uploaded.")
        return
    c = get_client()
    _upload_all(job, c, prepared)
    pid = job["wp"]["id"]
    cur = ensure(c.get(f"wp/v2/posts/{pid}", auth="wp", params={"context": "edit"}), "read draft")
    raw = (cur.get("content") or {}).get("raw") or ""
    BACKUP.mkdir(parents=True, exist_ok=True)
    (BACKUP / f"{job['id']}_{datetime.now():%Y%m%d_%H%M%S}.html").write_text(raw, encoding="utf-8")
    new = raw
    for im, _, _ in prepared:
        new = new.replace(f"<!--IMG:{im['n']}-->", fig_html(im, im["media"]))
    new = re.sub(r'<script type="application/ld\+json">\{"@context":"https://schema\.org","@graph":\[\{"@type":"ImageObject".*?</script>', "", new, flags=re.S)
    sc = schema_html(job)
    payload = {"content": new + ("\n" + sc if sc else "")}
    if _cover_id(job):
        payload["featured_media"] = _cover_id(job)
    res = ensure(c.request("POST", f"wp/v2/posts/{pid}", auth="wp", json=payload), "update post")
    print(f"  مقاله به‌روز شد (وضعیت {res.get('status')}). نسخهٔ قبلی در backups\\articlegen ذخیره شد.")
    _check_schema(c, pid, sc)
    log_action("article_images", {"job": job["id"], "post": pid, "n": len(prepared)})
    _note(job, f"{len(prepared)} image(s) attached")
    job["wp"]["modified"] = res.get("modified_gmt") or job["wp"].get("modified", "")
    save(job)
    print_edit_link(job)


def wp_edit_url(job):
    """Link of the post in the WordPress editor (so you can open it right after sending)."""
    w = job.get("wp") or {}
    if not str(w.get("id", "")).isdigit():
        return ""
    u = urlparse(w.get("link") or "")
    base = f"{u.scheme}://{u.netloc}" if u.scheme in ("http", "https") and u.netloc else brand_info()["site"]
    return f"{base}/wp-admin/post.php?post={w['id']}&action=edit"


def print_edit_link(job):
    url = wp_edit_url(job)
    if url:
        print(f"\nلینک ویرایش مقاله در وردپرس:\n{url}")


def mark_dirty(job):
    """The local text/brief now differs from what the site has (shown in the panel until you update the site)."""
    if job.get("wp"):
        job["dirty"] = True


# ------------------------------------------------------------------ 6) WordPress: ONE final step (images + terms + post), draft or publish
def _new_terms_needed(job):
    b = job["brief"]
    need = []
    if b.get("new_cat"):
        need.append(("category", b["new_cat"]["name"], b["new_cat"]["slug"]))
    for t in b.get("new_tags", []):
        need.append(("post_tag", t["name"], t["slug"]))
    return need


def new_term_keys(job):
    return [f"{tax}|{name}" for tax, name, _ in _new_terms_needed(job)]


def send_problems(job, status):
    """Things that must be fixed before sending. For 'publish' the bar is higher."""
    b = job["brief"]
    p = [w for w in brief_warnings(b) if w.startswith("slug") or "دسته" in w]
    if status == "publish":
        if links_pending(job):
            p.append(f"{len(job['links'])} پیشنهاد لینک داخلی هنوز اعمال یا رد نشده است")
        p += [x for s_, x in qc(job) if s_ == "H"]
        p += [f"جای تصویر {im['n']:02d} خالی است (فایل بگذارید یا پیشنهادش را حذف کنید)" for im, st in slot_status(job) if st == "missing"]
    return p


def _resolve_terms(job, c, go, ok_terms):
    """Existing category/tags -> ids; new ones are only listed (created later, and only when ticked). -> (ids, to_make)"""
    b = job["brief"]
    ids, to_make = {"category": [], "post_tag": []}, []
    if b.get("cat"):
        ids["category"].append(resolve_term(c, "category", b["cat"]))
    for tg in b.get("tags", []):
        ids["post_tag"].append(resolve_term(c, "post_tag", tg))
    for tax, name, slug in _new_terms_needed(job):
        if slug_error(slug):
            raise StepError(f"slug «{name}» باید لاتین باشد")
        try:
            ids[tax].append(resolve_term(c, tax, name))
            print(f"  {tax} «{name}» از قبل وجود دارد؛ همان استفاده می‌شود.")
        except StepError:
            if f"{tax}|{name}" not in ok_terms:
                if go:
                    raise StepError(f"«{name}» ({tax}) در سایت نیست و تیک ساخت آن زده نشده است")
                print(f"  ⚠ «{name}» ({tax}) در سایت نیست؛ برای ارسال باید تیک ساخت آن را بزنید.")
            to_make.append((tax, name, slug))
    return ids, to_make


def create_draft(job, go, ok_terms=(), status="draft"):
    """Final step: everything is already prepared on this computer. Uploads the images, creates new terms (only ticked ones) and
    creates the post as DRAFT (or publishes it when status='publish'). Slots without an image file stay as invisible placeholders in a draft."""
    b = job["brief"]
    if job.get("wp"):
        print(f"قبلاً به سایت فرستاده شده (id {job['wp']['id']}). برای تغییر آن از بخش «ویرایش و بهبود با AI ← به‌روزرسانی مقاله در سایت» یا «پر کردن تصاویر» استفاده کنید.")
        print_edit_link(job)
        return
    if not job.get("text"):
        raise ValueError("اول متن را بنویسید.")
    probs = send_problems(job, status)
    if probs:
        raise ValueError("اول این‌ها را اصلاح کنید: " + "؛ ".join(probs))
    prepared = convert_local(job)
    c = get_client()
    used = [u for u in slug_in_use(c, "post", b["slug"])]
    if used:
        raise StepError(f"slug «{b['slug']}» در سایت استفاده شده است: {used}")
    ids, to_make = _resolve_terms(job, c, go, ok_terms)
    print(f"عنوان: {job['title']}\nslug: {b['slug']}\nدسته: {b.get('cat') or b.get('new_cat')}\nبرچسب‌ها: {b.get('tags')}\nطول متن: {words_of(job['text'])} کلمه\n"
          f"تصاویر آمادهٔ بارگذاری: {len(prepared)}\nوضعیت در سایت: {'منتشرشده' if status == 'publish' else 'پیش‌نویس (draft)'}")
    for tax, name, slug in to_make:
        print(f"  + ساخته می‌شود (فقط با تیک): {tax} «{name}» [{slug}]")
    if not go:
        print("\nPreview only. Nothing sent to the site.")
        return
    for tax, name, slug in to_make:
        d = ensure(c.request("POST", TAX[tax][0], auth=TAX[tax][1], json={"name": name, "slug": slug}), f"create {tax}")
        ids[tax].append(d["id"])
        log_action("term_created", {"tax": tax, "name": name, "slug": slug, "id": d["id"]})
    _upload_all(job, c, prepared)
    sc = schema_html(job)
    meta = {k: v for k, v in (("_yoast_wpseo_focuskw", b["k"]), ("_yoast_wpseo_title", b["t"]), ("_yoast_wpseo_metadesc", b["m"])) if v}
    payload = {"title": job["title"], "slug": b["slug"], "status": "publish" if status == "publish" else "draft",
               "content": to_html(job, _figures(job)) + ("\n" + sc if sc else ""), "excerpt": b.get("x", ""), "meta": meta}
    if ids["category"]:
        payload["categories"] = ids["category"]
    if ids["post_tag"]:
        payload["tags"] = ids["post_tag"]
    if _cover_id(job):
        payload["featured_media"] = _cover_id(job)
    res = ensure(c.request("POST", "wp/v2/posts", auth="wp", json=payload), "create post")
    job["wp"] = {"id": res["id"], "link": res.get("link", ""), "status": res.get("status", payload["status"]), "modified": res.get("modified_gmt", "")}
    job["dirty"] = False
    job["state"] = "published" if payload["status"] == "publish" else "draft"
    log_action("article_publish" if status == "publish" else "article_draft", {"job": job["id"], "id": res["id"], "slug": b["slug"], "title": job["title"]})
    _note(job, f"{job['state']} id {res['id']}")
    save(job)
    print(f"{'منتشر شد' if status == 'publish' else 'پیش‌نویس ساخته شد'}: id={res['id']} {res.get('link', '')}. Yoast: "
          + ("ذخیره شد" if (res.get("meta") or {}).get("_yoast_wpseo_focuskw") == b["k"] else "در ویرایشگر وردپرس بررسی کنید"))
    _check_schema(c, res["id"], sc)
    print_edit_link(job)


def publish(job, go):
    if not job.get("wp"):
        raise ValueError("اول پیش‌نویس را بسازید.")
    pend = [(im["n"], im["a"]) for im, st in slot_status(job) if st in ("found", "missing")]
    if pend:
        raise ValueError(f"جای تصویر شمارهٔ {', '.join(str(n) for n, _ in pend)} هنوز خالی است؛ آن را بارگذاری کنید یا پیشنهاد عکس را حذف کنید.")
    print(f"انتشار «{job['title']}» (id {job['wp']['id']}): وضعیت از پیش‌نویس به منتشرشده تغییر می‌کند.")
    if not go:
        print("\nPreview only. Nothing published.")
        return
    c = get_client()
    res = ensure(c.request("POST", f"wp/v2/posts/{job['wp']['id']}", auth="wp", json={"status": "publish"}), "publish")
    job["wp"].update(status=res.get("status", "publish"), link=res.get("link", job["wp"]["link"]), modified=res.get("modified_gmt") or job["wp"].get("modified", ""))
    job["state"] = "published"
    log_action("article_publish", {"job": job["id"], "id": job["wp"]["id"]})
    _note(job, "published")
    save(job)
    print(f"منتشر شد: {job['wp']['link']}")
    print_edit_link(job)


# ------------------------------------------------------------------ batch: ideas -> drafts without review
def auto(job_ids, go):
    """brief (existing categories/tags only) -> write -> clean -> DRAFT, for every selected idea. No new terms are ever created here."""
    c = llm.cfg()
    js = [j for j in (load(i) for i in job_ids) if j and not j.get("wp")]
    if not js:
        print("موردی برای اجرا نیست.")
        return
    print(f"{len(js)} مقاله، هر کدام: ۱ تماس بریف + چند تماس نوشتن (حدود ۴ تا ۶)؛ هیچ دسته/برچسب جدیدی ساخته نمی‌شود.")
    if not go:
        for j in js:
            if j.get("brief"):
                write(j, False)
            else:
                make_brief(j, False, True)
        print("\nاین فقط برآورد است؛ برآورد نوشتن بعد از ساخت بریف دقیق‌تر می‌شود.")
        return
    if not confirm(len(js), "article(s) (AI writes; DRAFTS are created on the site)", to="(spends credit; each becomes a DRAFT, nothing is published)"):
        print("Cancelled.")
        return
    for j in js:
        print(f"\n== {j['title']}")
        try:
            if not j.get("brief"):
                make_brief(j, True, True)
            write(j, True)
            j = load(j["id"])
            lks = suggest_links(j, 3)
            apply_links(j, range(len(lks)))
            bad = [t for s, t in qc(j) if s == "H"]
            if bad:
                print("  ⚠ مشکل جدی کنترل کیفیت؛ پیش‌نویس ساخته نشد: " + " | ".join(bad))
                continue
            create_draft(j, True)
        except (StepError, LLMError, ValueError) as e:
            print(f"  FAILED: {e}")


# ================================================================== 7) REVISE WITH AI (before OR after the article is on the site)
# Everything here first changes the local job only (with an undo snapshot). The site changes only in update_post(), with preview + ticks + backup.
IMG_RE = re.compile(r"(?m)^\[\[IMG:\d+\]\]\s*$")
H3_RE = re.compile(r"(?m)^###\s+(.+?)\s*$")


def split_text(text):
    """Markdown -> (intro, [(h2, body)]). A body keeps its H3s and its [[IMG:n]] markers."""
    pieces = re.split(r"(?m)^##\s+(.+?)\s*$", text or "")
    return pieces[0].strip(), [(pieces[i].strip(), pieces[i + 1].strip()) for i in range(1, len(pieces) - 1, 2)]


def join_text(intro, secs):
    out = [intro.strip()] + [f"## {h}\n\n{b.strip()}".rstrip() for h, b in secs]
    return "\n\n".join(x for x in out if x)


def sync_parts_from_text(job, add=False):
    """Copy what is in the (possibly hand-edited) text back into job['parts'] so re-assembling never loses your edits.
    add=False only refreshes parts that already exist; add=True also creates the missing ones."""
    intro, secs = split_text(job.get("text") or "")
    parts = job.setdefault("parts", {})
    if intro and (add or "intro" in parts):
        parts["intro"] = intro
    for h, b in secs:
        body = IMG_RE.sub("", b).strip()
        if body and (add or h in parts):
            parts[h] = body


def outline_from_text(text, old=()):
    """H2 (+ H3 under each) found in a text -> brief outline; keeps the old note of a heading that still exists."""
    notes = {h["h"]: h.get("n", "") for h in old}
    return [{"h": h, "n": notes.get(h, ""), "s": H3_RE.findall(b), "on": True} for h, b in split_text(text)[1]]


def snapshot(job, why):
    """Undo point (last 5) before any AI change of the text/SEO/outline."""
    b = job.get("brief") or {}
    u = job.setdefault("undo", [])
    u.append({"when": f"{datetime.now():%Y-%m-%d %H:%M}", "why": why, "text": job.get("text", ""), "title": job.get("title", ""), "parts": json.loads(json.dumps(job.get("parts", {}))),
              "brief": {k: json.loads(json.dumps(b[k])) for k in ("k", "t", "m", "x", "h", "img") if k in b}})
    del u[:-5]


def undo_last(job):
    u = job.get("undo") or []
    if not u:
        return None
    s_ = u.pop()
    job["text"], job["title"], job["parts"] = s_["text"], s_["title"], s_["parts"]
    if job.get("brief"):
        job["brief"].update(s_["brief"])
    job["links"] = []
    mark_dirty(job)
    _note(job, "undo: " + s_["why"])
    save(job)
    return s_


def strip_foreign_links(text):
    """The AI must never add links: keep the anchor text of any link that is not on this site."""
    host = brand_info()["host"].lower().removeprefix("www.")

    def fix(m):
        u = m.group(2).strip()
        h = urlparse(u if "//" in u else "//" + u).netloc.lower().removeprefix("www.")
        return m.group(0) if (u.startswith(("/", "#")) or h == host) else m.group(1)
    t = re.sub(r"(?<!!)\[([^\]]*)\]\(([^)]*)\)", fix, text)
    return re.sub(r"(?<![(\w/])(?:https?://|www\.)[^\s)\]<>]+", "", t)


def md_links(text):
    return set(re.findall(r"(?<!!)\]\(\s*([^)\s]+)", text or ""))


def ranked_docs(job, kinds=("article",), limit=120):
    """Site pages of the given kinds, the most related to this article first (code: shared meaningful words). Never the article itself."""
    b = job.get("brief") or {}
    own = (job.get("wp") or {}).get("id")
    mine = kw.tset(" ".join([job.get("title", ""), b.get("k", "")] + [h["h"] for h in b.get("h", [])])) - kw.MODIFIERS
    me = norm(job.get("title", ""))
    docs = [d for d in kw.build_docs() if d["kind"] in kinds and d.get("title") and not (own and d["kind"] == "article" and str(d.get("id")) == str(own)) and norm(d["title"]) != me and not d.get("oos")]   # never an out-of-stock product
    docs.sort(key=lambda d: (-len(mine & d["prim"]), d["title"]))
    return docs[:limit]


def _gate(c, msg_sets, max_out, go, what):
    """Estimate (always printed). True only when go and the explicit confirmation passed. Nothing goes to the website here."""
    est_in = sum(llm.est_tokens(sum(len(m["content"]) for m in ms)) for ms in msg_sets)
    print(f"{what}: {len(msg_sets)} تماس.")
    _brief(c, len(msg_sets), est_in, max_out * len(msg_sets))
    if not go:
        print("Estimate only (nothing sent).")
        return False
    if not confirm(len(msg_sets), "AI call(s)", to="to the AI provider (spends credit; NOTHING is sent to your website)"):
        print("Cancelled.")
        return False
    return True


def _mj(sysm, user):
    return [{"role": "system", "content": sysm}, {"role": "user", "content": json.dumps(user, ensure_ascii=False, separators=(",", ":"))}]


def _need_text(job):
    if not job.get("brief") or not job.get("text"):
        raise ValueError("اول بریف و متن مقاله را بسازید.")


def _own_id(job):
    return (job.get("wp") or {}).get("id")


# ------------------------------------------------------------------ 7a) ask the SEO fields again
SEO_FIELDS = {"k": "کلمهٔ کلیدی", "t": "عنوان سئو", "m": "متا", "x": "خلاصه (excerpt)", "title": "عنوان مقاله"}
SEO_SYS = ("تو سردبیر سئوی فارسی «زعفران مارکت» (فروشگاه زعفران) هستی. مقاله نوشته شده است؛ بر اساس متن واقعی آن (outline و intro) فقط فیلدهای خواسته‌شده در want را دوباره و بهتر بنویس. "
           "فقط یک شیء JSON بده (بدون ``` و بدون هیچ توضیح دیگری) که فقط همان کلیدهای want را دارد. "
           "k = کلمهٔ کلیدی کانونی ۲ تا ۴ کلمه که واقعاً هدف جست‌وجوی همین مقاله است و در used_k نیست (هم‌نوع‌خواری نکن؛ q جست‌وجوهای واقعی مرتبط است)؛ "
           "t = عنوان سئو حداکثر ۶۰ کاراکتر شامل k و متفاوت از عنوان سئوی مقاله‌های دیگر؛ m = متا ۱۱۰ تا ۱۶۰ کاراکتر شامل k، با فایدهٔ مشخص و دعوت طبیعی به کلیک و بدون اغراق؛ "
           "x = خلاصهٔ یک تا دو جمله؛ title = عنوان نهایی مقاله حداکثر ۷۰ کاراکتر، طبیعی و جذاب، شامل k، بدون کلیک‌بیت و ادعای پزشکی. "
           "مقدار فعلی (cur) را عیناً تکرار نکن مگر اینکه واقعاً بهترین انتخاب باشد. آدرس (slug) را نه تغییر بده و نه بنویس. note دستور کاربر است و اولویت دارد. " + QUALITY)


def revise_seo(job, go, want=("k", "t", "m", "x"), note=""):
    _need_text(job)
    b = job["brief"]
    want = [w for w in SEO_FIELDS if w in want]
    if not want:
        raise ValueError("هیچ موردی برای پرسیدن از AI انتخاب نشده است.")
    c, ctx = llm.cfg(), site_context(_own_id(job))
    intro, secs = split_text(job["text"])
    user = {"want": want, "note": " ".join(str(note).split())[:400], "title": job["title"], "cur": {"k": b.get("k", ""), "t": b.get("t", ""), "m": b.get("m", ""), "x": b.get("x", ""), "title": job["title"]},
            "intro": " ".join(intro.split())[:600], "outline": [{"h": h, "s": H3_RE.findall(body)} for h, body in secs], "q": related_queries(job["title"] + " " + b.get("k", "")),
            "used_k": ctx["used_k"][:80], "existing": [d["title"] for d in ranked_docs(job, ("article",), 40)]}
    msgs = _mj(SEO_SYS, user)
    if not _gate(c, [msgs], 700, go, "پرسیدن دوبارهٔ سئو از AI (" + "، ".join(SEO_FIELDS[w] for w in want) + ")"):
        return
    text, info = llm.chat(msgs, max_out=700, task="seo_revise")
    r = llm.parse_json_obj(text)
    snapshot(job, "سئو (قبل از پرسیدن دوباره)")
    for k in want:
        v = " ".join(str(r.get(k, "")).split())
        if not v:
            continue
        old = job["title"] if k == "title" else b.get(k, "")
        if k == "title":
            if not 8 <= len(v) <= 110:
                print(f"  عنوان مقاله پیشنهادی نامعتبر بود و رد شد: {v[:50]}")
                continue
            job["title"] = v
        else:
            b[k] = v
        print(f"  {SEO_FIELDS[k]}:\n     قبل: {old}\n     بعد: {v}")
    _cost(job, info)
    _note(job, f"seo re-asked ({','.join(want)}) ${info['cost']:.4f}")
    mark_dirty(job)
    save(job)
    for w in brief_warnings(b, ctx, check_slug=False, own_id=_own_id(job)):
        print("  ⚠ " + w)
    print(f"انجام شد (هزینه ${info['cost']:.4f}). همه‌چیز در «بریف» قابل ویرایش است؛ آدرس (slug) دست نخورد. با «برگرداندن آخرین تغییر AI» می‌توانید به حالت قبل برگردید.")


# ------------------------------------------------------------------ 7b) redesign the headings (H2 + H3)
OUTLINE_SYS = ("تو سردبیر سئوی فارسی «زعفران مارکت» (فروشگاه زعفران) هستی. فهرست تیترهای مقاله را بازطراحی کن. ورودی: title، k (کلمهٔ کلیدی)، cur (تیترهای فعلی: H2 با H3ها و شروع متن هر بخش)، "
               "note (دستور کاربر؛ اولویت دارد)، existing (عنوان مقاله‌های موجود سایت). فقط یک شیء JSON بده (بدون ``` و بدون توضیح): "
               "{\"h\": [{\"h\": تیتر H2, \"n\": یادداشت برای نویسنده دربارهٔ زاویهٔ یونیک همان بخش, \"s\": [تیتر H3, ...]}], \"why\": یک جمله دلیل تغییرها}. "
               "قواعد: ۶ تا ۹ تیتر H2؛ k در یکی دو H2 طبیعی بیاید؛ دست‌کم ۴ بخش ۲ تا ۳ تیتر H3 داشته باشند و هر H3 جنبهٔ مشخصی از همان بخش باشد (نه تکرار H2)؛ "
               "ترتیب منطقی از نیت جست‌وجو تا جمع‌بندی (تیتر آخر جمع‌بندی)؛ پرسش‌های واقعی کاربران پوشش داده شود و اگر برای موضوع مفید است یک بخش «سوالات متداول» با ۳ تا ۴ H3 پرسشی بیاید؛ "
               "هیچ هم‌پوشانی با existing نداشته باش؛ تیتر خوب فعلی را بی‌دلیل عوض نکن و عیناً نگه دار؛ تیترها توصیفی، طبیعی و بدون کلیک‌بیت باشند. " + QUALITY)


def revise_outline(job, go, note=""):
    _need_text(job)
    b = job["brief"]
    sync_parts_from_text(job, add=True)
    c = llm.cfg()
    intro, secs = split_text(job["text"])
    cur = [{"h": h, "s": H3_RE.findall(body), "start": " ".join(IMG_RE.sub("", body).split())[:120]} for h, body in secs]
    user = {"title": job["title"], "k": b.get("k", ""), "note": " ".join(str(note).split())[:500], "cur": cur, "existing": [d["title"] for d in ranked_docs(job, ("article",), 60)]}
    msgs = _mj(OUTLINE_SYS, user)
    mo = min(c["max_out"], 1400)
    if not _gate(c, [msgs], mo, go, "طراحی مجدد تیترها (H2/H3) با AI"):
        return
    text, info = llm.chat(msgs, max_out=mo, task="outline")
    r = llm.parse_json_obj(text)
    new = []
    for h in r.get("h") or []:
        if isinstance(h, dict) and str(h.get("h", "")).strip():
            new.append({"h": " ".join(str(h["h"]).split()), "n": str(h.get("n", "")).strip(), "s": _subs(h.get("s")), "on": True})
    if len(new) < 4 or len({norm(h["h"]) for h in new}) != len(new):
        raise ValueError("AI فهرست قابل‌استفاده‌ای نداد؛ چیزی تغییر نکرد. دوباره امتحان کنید یا دستور (note) را دقیق‌تر بنویسید.")
    snapshot(job, "فهرست تیترها")
    old = {h["h"]: h for h in b["h"] if h.get("on")}
    todo, same = [], []
    for h in new:
        o = old.get(h["h"])
        if o and not h["n"]:
            h["n"] = o.get("n", "")
        if o and list(o.get("s", [])) == h["s"] and job["parts"].get(h["h"]):
            same.append(h["h"])
        else:
            job["parts"].pop(h["h"], None)
            todo.append(h["h"])
    removed = [h for h in old if h not in {x["h"] for x in new}]
    b["h"] = new
    n_sec = len(new)
    for im in b.get("img", []):                                     # an image placed after a heading that no longer exists goes to the last one
        if isinstance(im.get("w"), int) and im["w"] > n_sec:
            im["w"] = n_sec
    _cost(job, info)
    _note(job, f"outline redesigned ${info['cost']:.4f}")
    mark_dirty(job)
    save(job)
    print(f"فهرست جدید: {len(new)} تیتر H2، {sum(len(h['s']) for h in new)} تیتر H3. دلیل AI: {str(r.get('why', ''))[:200]}")
    print(f"  بدون تغییر (متن حفظ می‌شود): {len(same)} بخش")
    print(f"  جدید یا تغییرکرده (باید دوباره نوشته شود): {len(todo)} بخش" + (": " + "، ".join(todo) if todo else ""))
    if removed:
        print("  حذف‌شده از فهرست (از متن هم برداشته می‌شود): " + "، ".join(removed))
    print(f"هزینه ${info['cost']:.4f}. فهرست را در «بریف» ببینید و ویرایش کنید؛ بعد در مرحلهٔ «۲) نوشتن متن» دکمهٔ «نوشتن متن» را بزنید تا فقط بخش‌های جدید/تغییرکرده نوشته شوند (متن فعلی تا آن موقع سر جایش است).")


# ------------------------------------------------------------------ 7c) rewrite with a free instruction
REWRITE_SYS = ("تو نویسندهٔ محتوای «زعفران مارکت» (فروشگاه زعفران) هستی و فارسی روان و طبیعی می‌نویسی. فقط Markdown بنویس. متن فعلی (cur / cur_intro) و دستور ویرایش (instruction) داده می‌شود؛ "
               "همان بخش‌ها را طبق دستور بازنویسی کن و فقط همان‌ها را برگردان. اگر intro=true اول مقدمه را بدون تیتر بنویس. هر بخش را با «## » + همان تیتر H2 عیناً شروع کن (تیتر H2 را تغییر نده). "
               "اگر s (تیترهای H3) داده شده همه را دقیقاً با «### » رعایت کن؛ اگر s خالی و بخش بلند است می‌توانی ۲ تا ۳ «### » توصیفی اضافه کنی. فقط H2 و H3. "
               "لینک‌های Markdown موجود در متن را عیناً نگه دار و لینک یا آدرس تازه ننویس. ادعای درست متن را نگه دار؛ عدد و آمار ساختگی، ادعای پزشکی و وعدهٔ قطعی اضافه نکن. "
               "مطالب بخش‌های دیگر (other_headings) را تکرار نکن. دستور ویرایش اولویت دارد. فقط متن مقاله، بدون توضیح اضافه. " + QUALITY)
SCOPES = {"all": "کل مقاله", "intro": "فقط مقدمه", "pick": "فقط بخش‌های انتخاب‌شده"}


def _rewrite_plan(job, scope, picked):
    intro, secs = split_text(job["text"])
    names = [h for h, _ in secs]
    if scope == "all":
        targets, do_intro = names, True
    elif scope == "intro":
        targets, do_intro = [], True
    else:
        targets, do_intro = [h for h in names if h in set(picked)], False
    batches = []
    if do_intro:
        batches.append({"intro": True, "secs": targets[:BATCH_SECTIONS]})
        targets = targets[BATCH_SECTIONS:]
    for i in range(0, len(targets), BATCH_SECTIONS):
        batches.append({"intro": False, "secs": targets[i:i + BATCH_SECTIONS]})
    return intro, dict(secs), names, batches


def _rewrite_msgs(job, bt, instruction, intro, bodies, names):
    b = job["brief"]
    subs = {h["h"]: h.get("s", []) for h in b.get("h", [])}
    user = {"title": job["title"], "k": b.get("k", ""), "instruction": instruction, "intro": bt["intro"], "other_headings": [n for n in names if n not in bt["secs"]][:14],
            "sections": [{"h": h, "s": subs.get(h, []), "cur": IMG_RE.sub("", bodies[h]).strip()} for h in bt["secs"]]}
    if bt["intro"]:
        user["cur_intro"] = intro
    if b.get("avoid"):
        user["avoid"] = b["avoid"][:20]
    sg = style_guide(700)
    return _mj(REWRITE_SYS + (("\n\nراهنمای لحن برند:\n" + sg) if sg else ""), user)


def rewrite(job, go, instruction, scope="all", picked=()):
    """Free-form AI edit of the whole article, the intro or chosen sections (2 sections per call). Links and image slots are kept."""
    _need_text(job)
    instruction = str(instruction).strip()[:1500]
    if not instruction:
        raise ValueError("دستور ویرایش خالی است؛ بنویسید AI چه کاری بکند.")
    if scope not in SCOPES:
        scope = "all"
    c = llm.cfg()
    intro, bodies, names, batches = _rewrite_plan(job, scope, picked)
    if not batches or not (batches[0]["secs"] or batches[0]["intro"]):
        raise ValueError("بخشی برای بازنویسی انتخاب نشده است.")
    mo = min(c["max_out"], 1400)
    sets = [_rewrite_msgs(job, bt, instruction, intro, bodies, names) for bt in batches]
    if not _gate(c, sets, mo, go, f"بازنویسی با دستور شما ({SCOPES[scope]})"):
        return
    snapshot(job, "بازنویسی با AI: " + instruction[:60])
    links_before, spent, lost = md_links(job["text"]), 0.0, []
    for bt in batches:
        text, info = llm.chat(_rewrite_msgs(job, bt, instruction, intro, bodies, names), max_out=mo, task="rewrite")
        spent += info["cost"]
        new_intro, new_bodies = _split_sections(text, len(bt["secs"]))
        if bt["intro"]:
            if new_intro.strip():
                intro = fix_text(strip_foreign_links(new_intro))
            else:
                lost.append("مقدمه")
        for h, nb in zip(bt["secs"], new_bodies):
            if not nb.strip():
                lost.append(h)
                continue
            markers = IMG_RE.findall(bodies[h])
            bodies[h] = fix_text(strip_foreign_links(nb)) + ("\n\n" + "\n\n".join(m.strip() for m in markers) if markers else "")
        job["text"] = join_text(intro, [(h, bodies[h]) for h in names])
        job["cost"] = round(job.get("cost", 0) + info["cost"], 6)
        save(job)
    sync_parts_from_text(job, add=True)
    for n_ in lost:
        print(f"  ⚠ «{n_}» را AI خالی برگرداند؛ بدون تغییر ماند.")
    gone = sorted(links_before - md_links(job["text"]))
    if gone:
        print("  ⚠ این لینک‌های داخلی در بازنویسی از متن افتادند (در بخش لینک داخلی دوباره پیشنهاد می‌شوند): " + "، ".join(gone[:4]))
    try:
        suggest_links(job)
    except Exception:
        job["links"] = []
    mark_dirty(job)
    _note(job, f"rewrite ({scope}) ${spent:.4f}")
    save(job)
    print(f"بازنویسی انجام شد (هزینه ${spent:.4f}). {words_of(job['text'])} کلمه. کنترل کیفیت را ببینید؛ اگر راضی نبودید «برگرداندن آخرین تغییر AI» را بزنید. به سایت چیزی نرفت.")


# ------------------------------------------------------------------ 7d) internal links decided by the AI from the list of site titles
LINKS_SYS = ("تو متخصص لینک‌سازی داخلی سایت «زعفران مارکت» هستی. متن مقاله به‌صورت پاراگراف‌های شماره‌دار (pars) و فهرست صفحه‌های قابل لینک سایت (targets، شماره: [عنوان، نوع]) داده می‌شود. "
             "فقط یک آرایهٔ JSON بده (بدون ``` و بدون توضیح) از حداکثر max شیء {\"p\": شمارهٔ پاراگراف, \"a\": عبارت لنگر, \"t\": شمارهٔ مقصد, \"why\": دلیل کوتاه}. قواعد: "
             "a باید عیناً و حرف‌به‌حرف (همان املا و نیم‌فاصله) در همان پاراگراف p آمده باشد، ۲ تا ۵ کلمه، طبیعی و توصیفی (نه «اینجا» و «کلیک کنید»)؛ "
             "هر مقصد حداکثر یک بار و هر پاراگراف حداکثر یک لینک؛ فقط مقصدی که از نظر موضوعی واقعاً به خواننده کمک می‌کند و مکمل همان پاراگراف است (نه صرفاً شباهت کلمه)؛ "
             "لینک‌ها در سراسر مقاله پخش شوند و همه در مقدمه نباشند؛ اگر مقصد مناسب هست دست‌کم یک لینک به دستهٔ محصول یا محصول مرتبط (نوع product_cat یا product) بگذار؛ "
             "مقصدهای already قبلاً در متن لینک شده‌اند و نباید دوباره بیایند؛ اگر مقصد مناسبی نیست کمتر بده و لینک اجباری نساز.")
LINK_KINDS = {"article": "مقاله", "product_cat": "دستهٔ محصول", "product": "محصول", "category": "دستهٔ مقاله", "page": "صفحه"}


def _link_input(job, max_links):
    text = job["text"]
    pars = re.split(r"\n\n+", text)
    use = {str(i): p for i, p in enumerate(pars) if not p.lstrip().startswith(("#", "-", "|", ">", "[[", "*", "1.", "!", "<")) and len(p.split()) >= 12}
    have = md_links(text)
    docs = ranked_docs(job, ("article",), 220) + ranked_docs(job, ("product_cat", "category"), 40) + ranked_docs(job, ("product",), 25)
    docs = [d for d in docs if d.get("url") and d["url"] not in have][:260]
    user = {"title": job["title"], "k": (job.get("brief") or {}).get("k", ""), "max": max_links, "pars": {k: v[:700] for k, v in use.items()},
            "targets": {str(i): [d["title"], d["kind"]] for i, d in enumerate(docs, 1)}, "already": sorted(have)[:20]}
    return _mj(LINKS_SYS, user), docs, pars, use


def _find_phrase(p, phrase):
    spans = [m.span() for m in re.finditer(r"\[[^\]]*\]\([^)]*\)", p)]
    return next((m for m in _pat(phrase).finditer(p) if not any(a <= m.start() < b_ for a, b_ in spans)), None) if phrase.strip() else None


def ai_links(job, go, max_links=6):
    """The AI chooses the anchors and targets (it sees the titles of the site's articles, categories and some products); code verifies every
    suggestion (anchor really in the paragraph, target exists, once per target/paragraph) and only then lists it. Nothing is applied here."""
    _need_text(job)
    c = llm.cfg()
    max_links = max(1, min(int(max_links), 8))
    msgs, docs, pars, use = _link_input(job, max_links)
    if not docs:
        raise ValueError("فهرست صفحه‌های سایت خالی است؛ اول «همگام‌سازی سایت» و «خروجی مقاله‌ها» را بزنید.")
    mo = min(c["max_out"], 900)
    if not _gate(c, [msgs], mo, go, f"پیشنهاد لینک داخلی با AI ({len(docs)} عنوان از سایت)"):
        return
    text, info = llm.chat(msgs, max_out=mo, task="links_ai")
    raw = llm.parse_json_list(text)
    out, used_p, used_u, dropped = [], set(), set(), 0
    for r in raw:
        if not isinstance(r, dict):
            continue
        p_, a, t_ = str(r.get("p", "")).strip(), " ".join(str(r.get("a", "")).split()), str(r.get("t", "")).strip()
        if not (p_.isdigit() and t_.isdigit() and 1 <= int(t_) <= len(docs) and p_ in use and a):
            dropped += 1
            continue
        d, i = docs[int(t_) - 1], int(p_)
        m = _find_phrase(pars[i], a)
        if not m or i in used_p or d["url"] in used_u or len(out) >= max_links:
            dropped += 1
            continue
        out.append({"par": i, "phrase": m.group(0), "url": d["url"], "title": d["title"], "kind": d["kind"], "src": "ai", "why": str(r.get("why", ""))[:120],
                    "ctx": pars[i][max(0, m.start() - 40):m.end() + 40]})
        used_p.add(i)
        used_u.add(d["url"])
    _cost(job, info)
    _note(job, f"ai links {len(out)} ${info['cost']:.4f}")
    if out:
        job["links"] = out
    save(job)
    print(f"{len(out)} پیشنهاد لینک معتبر ساخته شد" + (f" ({dropped} پیشنهاد نامعتبر یا تکراری AI کنار گذاشته شد)" if dropped else "") + f"؛ هزینه ${info['cost']:.4f}.")
    for L in out:
        print(f"  «{L['phrase']}» ← {L['title']} ({LINK_KINDS.get(L['kind'], L['kind'])})" + (f" - {L['why']}" if L["why"] else ""))
    print("در بخش «۳) لینک داخلی» تیک بزنید و «اعمال» را بزنید؛ لینک‌ها فقط روی متن این کامپیوتر می‌نشینند و با «به‌روزرسانی مقاله در سایت» به سایت می‌روند." if out
          else "پیشنهاد قابل‌استفاده‌ای نیامد؛ لیست قبلی لینک‌ها دست نخورد.")


# ------------------------------------------------------------------ 7e) UPDATE the article that is already on the site (slug is never touched)
def _remote(c, pid):
    return ensure(c.get(f"wp/v2/posts/{pid}", auth="wp", params={"context": "edit"}), "read post")


def _html_stats(h):
    host = brand_info()["host"].lower().removeprefix("www.")
    txt = re.sub(r"<[^>]+>", " ", re.sub(r"(?s)<(script|style).*?</\1>", " ", h or ""))
    return {"words": len(txt.split()), "h2": len(re.findall(r"<h2\b", h or "")), "h3": len(re.findall(r"<h3\b", h or "")),
            "links": len([u for u in re.findall(r'<a\s[^>]*href="([^"]+)"', h or "") if host in u or u.startswith("/")])}


def update_problems(job, live):
    b = job["brief"]
    p = [w for w in brief_warnings(b, check_slug=False, own_id=_own_id(job)) if "دسته" in w]
    if live:                                                         # a published page is public at once: the publish bar applies
        if links_pending(job):
            p.append(f"{len(job['links'])} پیشنهاد لینک داخلی هنوز اعمال یا رد نشده است")
        p += [x for s_, x in qc(job) if s_ == "H"]
    return p


def update_post(job, go, ok_terms=(), overwrite=False, live_ok=False):
    """Send the CURRENT local title, text, links, excerpt, Yoast keyword/title/meta, category/tags and images to the post that already exists.
    Never changes slug or status. Preview first; the real update needs the ticks, makes a backup of what the site has and logs it."""
    b = job.get("brief")
    if not job.get("wp"):
        raise ValueError("این مقاله هنوز در سایت نیست؛ اول «ارسال به سایت» را بزنید.")
    if not (b and job.get("text")):
        raise ValueError("اول متن را بنویسید.")
    c = get_client()
    pid = job["wp"]["id"]
    cur = _remote(c, pid)
    status = cur.get("status") or job["wp"].get("status", "draft")
    live = status == "publish"
    raw = (cur.get("content") or {}).get("raw") or ""
    rmod, known = cur.get("modified_gmt") or "", job["wp"].get("modified", "")
    changed = None if not (known and rmod) else known != rmod
    probs = update_problems(job, live)
    if probs:
        raise ValueError("اول این‌ها را اصلاح کنید: " + "؛ ".join(probs))
    prepared = convert_local(job)
    ids, to_make = _resolve_terms(job, c, go, ok_terms)
    new_html = to_html(job, _figures(job))
    old, new = _html_stats(raw), _html_stats(new_html)
    old_title = ((cur.get("title") or {}).get("raw") or "").strip()
    print(f"به‌روزرسانی مقالهٔ موجود در سایت (id {pid}، وضعیت: {'منتشرشده (زنده)' if live else 'پیش‌نویس'})\nآدرس (slug): بدون تغییر\n"
          f"عنوان: {'بدون تغییر' if old_title == job['title'] else old_title + ' ← ' + job['title']}\n"
          f"طول متن: {old['words']} ← {new['words']} کلمه | H2: {old['h2']} ← {new['h2']} | H3: {old['h3']} ← {new['h3']} | لینک داخلی: {old['links']} ← {new['links']}\n"
          f"سئو: کلمه کلیدی «{b['k']}» | عنوان سئو {len(b['t'])} و متا {len(b['m'])} کاراکتر | دسته: {b.get('cat') or (b.get('new_cat') or {}).get('name', '-')} | برچسب‌ها: {b.get('tags')}\n"
          f"تصاویر آمادهٔ بارگذاری: {len(prepared)}")
    for tax, name, slug in to_make:
        print(f"  + ساخته می‌شود (فقط با تیک): {tax} «{name}» [{slug}]")
    if changed is None:
        print("⚠ نمی‌دانم نسخهٔ سایت بعد از آخرین ارسال با دست ویرایش شده یا نه (ثبت زمان ویرایش نیست)؛ ارسال، متن سایت را با نسخهٔ این برنامه جایگزین می‌کند.")
    elif changed:
        print("⚠ نسخهٔ سایت بعد از آخرین ارسال این برنامه تغییر کرده (احتمالاً در وردپرس ویرایش شده)؛ ارسال، آن تغییرات را جایگزین می‌کند. اگر می‌خواهید آن‌ها را نگه دارید اول «دریافت نسخهٔ سایت» را بزنید.")
    if not go:
        print("\nPreview only. Nothing sent to the site.")
        return
    if changed is not False and not overwrite:
        raise StepError("نسخهٔ سایت ممکن است با دست تغییر کرده باشد؛ تیک «جایگزین شود» را بزنید (یا اول «دریافت نسخهٔ سایت» را بزنید).")
    if live and not live_ok:
        raise StepError("مقاله منتشر شده است و تغییر روی صفحهٔ زندهٔ سایت می‌نشیند؛ تیک مربوط را بزنید.")
    BACKUP.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    (BACKUP / f"{job['id']}_before_update_{stamp}.html").write_text(raw, encoding="utf-8")
    (BACKUP / f"{job['id']}_before_update_{stamp}.json").write_text(json.dumps({"title": old_title, "excerpt": (cur.get("excerpt") or {}).get("raw", ""), "status": status}, ensure_ascii=False, indent=1), encoding="utf-8")
    for tax, name, slug in to_make:
        d = ensure(c.request("POST", TAX[tax][0], auth=TAX[tax][1], json={"name": name, "slug": slug}), f"create {tax}")
        ids[tax].append(d["id"])
        log_action("term_created", {"tax": tax, "name": name, "slug": slug, "id": d["id"]})
    _upload_all(job, c, prepared)
    sc = schema_html(job)
    meta = {k: v for k, v in (("_yoast_wpseo_focuskw", b["k"]), ("_yoast_wpseo_title", b["t"]), ("_yoast_wpseo_metadesc", b["m"])) if v}
    payload = {"title": job["title"], "content": to_html(job, _figures(job)) + ("\n" + sc if sc else ""), "excerpt": b.get("x", ""), "meta": meta}
    if ids["category"]:
        payload["categories"] = ids["category"]
    if ids["post_tag"]:
        payload["tags"] = ids["post_tag"]
    if _cover_id(job):
        payload["featured_media"] = _cover_id(job)
    res = ensure(c.request("POST", f"wp/v2/posts/{pid}", auth="wp", json=payload), "update post")
    job["wp"].update(status=res.get("status", status), link=res.get("link") or job["wp"].get("link", ""), modified=res.get("modified_gmt") or "")
    job["dirty"] = False
    log_action("article_update", {"job": job["id"], "id": pid, "slug": b["slug"], "title": job["title"]})
    _note(job, f"site updated id {pid}")
    save(job)
    print(f"\nمقالهٔ سایت به‌روز شد (وضعیت {res.get('status', status)}). نسخهٔ قبلی سایت در backups\\articlegen ذخیره شد. Yoast: "
          + ("ذخیره شد" if (res.get("meta") or {}).get("_yoast_wpseo_focuskw") == b["k"] else "در ویرایشگر وردپرس بررسی کنید"))
    _check_schema(c, pid, sc)
    print_edit_link(job)


def pull_from_site(job, go):
    """Take the text/title/excerpt that is on the site NOW (for example after you edited it in WordPress) into this job, so AI edits start from it."""
    from .htmlmd import html_to_md
    if not job.get("wp"):
        raise ValueError("این مقاله هنوز در سایت نیست.")
    c = get_client()
    cur = _remote(c, job["wp"]["id"])
    raw = (cur.get("content") or {}).get("raw") or ""
    html = re.sub(r"<!--IMG:(\d+)-->", r"<p>[[IMG:\1]]</p>", SCHEMA_LEFT.sub("", raw))
    text = html_to_md(html)
    b = job["brief"]
    for im in b.get("img", []):                                      # our own figures go back to their [[IMG:n]] slot
        m = (im.get("media") or {}).get("url")
        if m and im.get("on") and im.get("w") != "cover":
            text = re.sub(r"!\[[^\]]*\]\(" + re.escape(m) + r"\)\s*(?:\n\n" + re.escape(im.get("a", "x")) + r"\s*)?", f"[[IMG:{im['n']}]]\n\n", text)
    text = fix_text(text)
    old, new = _html_stats(to_html(job, _figures(job))), _html_stats(raw)
    print(f"نسخهٔ سایت (id {job['wp']['id']}): {new['words']} کلمه، H2: {new['h2']}، H3: {new['h3']}، لینک داخلی: {new['links']}\n"
          f"نسخهٔ این برنامه: {old['words']} کلمه، H2: {old['h2']}، H3: {old['h3']}، لینک داخلی: {old['links']}\n"
          "اگر ادامه دهید متن، عنوان و خلاصهٔ این پرونده با نسخهٔ سایت جایگزین می‌شود (یک نقطهٔ برگشت ساخته می‌شود).")
    if not go:
        print("\nPreview only. Nothing changed.")
        return
    snapshot(job, "قبل از دریافت نسخهٔ سایت")
    job["text"] = text
    t_ = ((cur.get("title") or {}).get("raw") or "").strip()
    if t_:
        job["title"] = t_
    ex = ((cur.get("excerpt") or {}).get("raw") or "").strip()
    if ex:
        b["x"] = ex
    b["h"] = outline_from_text(text, b["h"]) or b["h"]
    job["parts"] = {}
    sync_parts_from_text(job, add=True)
    job["links"] = []
    job["wp"]["modified"] = cur.get("modified_gmt") or job["wp"].get("modified", "")
    job["dirty"] = False
    _note(job, "pulled from site")
    save(job)
    print("نسخهٔ سایت دریافت شد. کنترل کیفیت و بریف را ببینید (کلمه کلیدی، عنوان سئو و متا از سایت خوانده نمی‌شوند و همان‌هایی که در بریف هست می‌ماند).")
