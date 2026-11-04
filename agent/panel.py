"""Local web panel (stdlib only). Runs on 127.0.0.1, protected by a per-session token + Host check.
It is a front-end over the same functions as the menu; every write still shows a preview and needs a confirm tick."""
import builtins
import contextlib
import csv
import html
import io
import json
import os
import re
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import proposals as pr
from .config import ROOT

TOKEN = secrets.token_urlsafe(16)
E = html.escape
CSS = """:root{--red:#9e2a2b;--gold:#e0a526;--bg:#fbf8f3;--card:#fff;--line:#eadfce;--ink:#2b2b2b}
body{font-family:'VazirPanel',Tahoma,sans-serif;margin:0;background:var(--bg);color:var(--ink);line-height:1.8}
nav{background:var(--red);padding:6px 20px;border-bottom:4px solid var(--gold)}
nav a{display:inline-block;color:#fff;padding:6px 14px;margin-left:6px;text-decoration:none;font-weight:700;border-radius:6px}
nav a:hover{background:rgba(255,255,255,.18)}
main{max-width:1150px;margin:18px auto;padding:0 16px}h1{color:var(--red)}h2{margin:22px 0 8px}
button{padding:7px 16px;margin:3px;border:0;border-radius:8px;background:var(--red);color:#fff;cursor:pointer;font-family:inherit;font-size:14px}
button:hover{filter:brightness(1.12)}button.alt{background:#6b5b4b}button.warn{background:#7a1212}
pre{background:#1f1d1a;color:#ece7df;padding:12px;border-radius:10px;white-space:pre-wrap;direction:ltr;text-align:left;max-height:480px;overflow:auto;font-family:Consolas,monospace}
table{border-collapse:collapse;width:100%;background:var(--card);border-radius:8px;overflow:hidden}
td,th{border-bottom:1px solid var(--line);padding:8px;vertical-align:top;font-size:14px}th{background:#f3e9d6;text-align:right}
tr:nth-child(even) td{background:#fdfaf5}mark{background:#ffe08a;padding:0 2px;border-radius:3px}
textarea{width:100%;min-height:140px;font-family:Consolas,monospace;direction:ltr;border:1px solid var(--line);border-radius:8px;padding:8px}
.box{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:10px 0}
.muted{color:#7d7164;font-size:13px}a{color:#8a2a1c}.edit{font-size:12px;margin-right:6px;color:#7d7164}label.sa{font-weight:400;font-size:12px;white-space:nowrap}"""
FONT_FMT = {".woff2": "woff2", ".woff": "woff", ".ttf": "truetype", ".otf": "opentype"}
FONT_DIR = ROOT / "assets" / "fonts"


def font_css():
    """Uses whatever Vazirmatn files you put in assets/fonts (any file names)."""
    files = [f for f in sorted(FONT_DIR.glob("*")) if f.suffix.lower() in FONT_FMT] if FONT_DIR.exists() else []
    if not files:
        return ""
    thin = ("semi", "extra", "light", "thin", "black", "medium")

    def pick(word):
        return next((f for f in files if word in f.name.lower() and not any(t in f.name.lower() for t in thin)), None)

    def face(f, w):
        return (f"@font-face{{font-family:'VazirPanel';src:url('/assets/fonts/{quote(f.name)}') "
                f"format('{FONT_FMT[f.suffix.lower()]}');font-weight:{w};font-display:swap}}")
    var = next((f for f in files if any(k in f.name.lower() for k in ("[", "vf", "variable"))), None)
    if var:
        return face(var, "100 900")
    reg, bold = pick("regular"), pick("bold")
    css = (face(reg, 400) if reg else "") + (face(bold, 700) if bold else "")
    return css or face(files[0], "100 900")


def link(url, text):
    if not str(url).startswith(("http://", "https://")):
        return E(text)
    return f'<a href="{E(url)}" target="_blank" rel="noopener">{E(text)}</a>'


def edit_link(url, pid):
    u = urlparse(str(url))
    if u.scheme in ("http", "https") and str(pid).isdigit():
        return f'<a class="edit" href="{u.scheme}://{u.netloc}/wp-admin/post.php?post={pid}&action=edit" target="_blank" rel="noopener">[ویرایش]</a>'
    return ""


VERSION = "15"
NAV = [("/", "خانه"), ("/tasks", "صف کار و گزارش خطا"), ("/refresh", "به‌روزرسانی و پایش اثر"), ("/health", "سلامت سایت"), ("/gsc", "Search Console"), ("/keywords", "کلمات کلیدی و تقویم"), ("/graph", "گراف لینک"), ("/media", "تصاویر و دسته‌ها"), ("/write", "تولید مقاله"), ("/style", "لحن نوشتار"), ("/seo", "کلمه کلیدی و متا"), ("/links", "لینک‌دهی داخلی"), ("/slugs", "آدرس‌ها (slug)"), ("/inbox", "محصول و مقاله جدید"), ("/reports", "گزارش سئو"), ("/roadmap", "نقشهٔ راه")]


JOBS = {}


def start_job(name, fn):
    """Long read-only work (site crawl) runs in a background thread; the page auto-refreshes."""
    j = JOBS.get(name)
    if j and j["running"]:
        return False
    j = JOBS[name] = {"running": True, "log": [], "error": None}

    def work():
        try:
            fn(lambda m: j["log"].append(m))
        except (Exception, SystemExit) as e:
            j["error"] = f"{type(e).__name__}: {e}"
        finally:
            j["running"] = False
    threading.Thread(target=work, daemon=True).start()
    return True


def run(fn, yes=False):
    from . import llm
    llm.reset_run()
    buf, real = io.StringIO(), builtins.input
    builtins.input = (lambda *a: "y") if yes else (lambda *a: "n")
    try:
        with contextlib.redirect_stdout(buf):
            try:
                fn()
            except SystemExit as e:
                print(e)
            except Exception as e:  # show, never crash the panel
                print(f"ERROR: {type(e).__name__}: {e}")
    finally:
        builtins.input = real
    out = buf.getvalue()
    if "ERROR:" in out:                              # kept for the hand-off bundle (panel -> صف کار و گزارش خطا)
        try:
            from . import tasks
            tasks.log_error("panel", out)
        except Exception:
            pass
    return out


def actions():
    from . import ai, audit, cli, export, llm, site, slugs, stage2, style
    return {  # name: (label, callable, needs_confirm)
        "check": ("بررسی اتصال", cli.check, False), "diagnose": ("عیب‌یابی اتصال", cli.diagnose, False),
        "sync": ("همگام‌سازی سایت", site.sync, False), "export": ("خروجی مقاله‌ها", export.export_articles, False),
        "audit": ("گزارش سئو", audit.audit, False), "style": ("بستهٔ لحن", style.build, False),
        "linkplan": ("ساخت پیشنهاد لینک‌دهی", pr.link_plan, False), "seotodo": ("فهرست و پرامپت سئو", pr.seo_todo, False),
        "inbox_dry": ("پیش‌نمایش محصول و مقاله", lambda: (stage2.run_products(False), print(), stage2.run_articles(False)), False),
        "inbox_send": ("ارسال محصول و مقاله", lambda: (stage2.run_products(True), print(), stage2.run_articles(True)), True),
        "ai_test": ("اتصال به هوش مصنوعی", llm.ping, False),
        "ai_style": ("ساخت راهنمای لحن با AI", lambda: ai.ai_style(True), True),
        "ai_slugs_est": ("برآورد هزینهٔ پیشنهاد slug", lambda: slugs.ai_slugs(False), False),
        "ai_slugs": ("پیشنهاد slug لاتین با AI", lambda: slugs.ai_slugs(True), True),
        "ai_seo3": ("آزمایش AI روی ۳ مقاله", lambda: ai.ai_seo(True, 3), True),
        "ai_seo_est": ("برآورد هزینهٔ AI برای سئو", lambda: ai.ai_seo(False), False),
        "ai_seo": ("پر کردن سئو با AI", lambda: ai.ai_seo(True), True),
        "ai_judge_est": ("برآورد هزینهٔ ارزیابی لینک‌ها", lambda: ai.ai_judge(False), False),
        "ai_judge": ("ارزیابی لینک‌ها با AI", lambda: ai.ai_judge(True), True),
        "ai_gap_est": ("برآورد هزینهٔ پیشنهاد موضوع", lambda: ai.ai_gap(False), False),
        "ai_gap": ("پیشنهاد موضوع برای شکاف محتوا با AI", lambda: ai.ai_gap(True), True),
    }


def page(title, body, head=""):
    nav = "".join(f'<a href="{u}">{n}</a>' for u, n in NAV)
    return (f'<!doctype html><html dir="rtl" lang="fa"><head><meta charset="utf-8">{head}<title>{E(title)}</title>'
            f"<style>{font_css()}{CSS}</style></head><body><nav>{nav}</nav><main>{body}</main>"
            f"<footer class='muted' style='text-align:center;margin:30px'>نسخهٔ پنل {VERSION}</footer>{SA_JS}</body></html>")


def not_found(path):
    return page("آدرس پیدا نشد", f"<h1>این آدرس در پنل وجود ندارد</h1><p><code style='direction:ltr;display:inline-block'>{E(path)}</code></p>"
                f"<p>اگر این پیام را بعد از جایگزین کردن فایل‌های جدید می‌بینید، پنل هنوز با نسخهٔ قدیمی باز است: پنجرهٔ سیاه را ببندید و دوباره "
                f"START.bat ← ۱۴ را بزنید. نسخهٔ در حال اجرا: <b>{VERSION}</b></p><p><a href='/'>خانه</a> · <a href='/health'>سلامت سایت</a></p>")


def form(action, inner):
    return f'<form method="post" action="{action}"><input type="hidden" name="t" value="{TOKEN}">{inner}</form>'


def out_box(text):
    return f"<h2>خروجی</h2><pre>{E(text)}</pre>" if text else ""


def btn(name, label, cls="", extra=""):
    return f'<button class="{cls}" name="do" value="{name}" {extra}>{E(label)}</button>'


def selall(name=""):
    """'Select all' tick for a list: inside a table it ticks every box of that table; with a name it ticks every box with that name in the form."""
    arg = f",'{name}'" if name else ""
    return f"<label class='sa'><input type='checkbox' onclick=\"sa(this{arg})\"> انتخاب همه</label>"


SA_JS = ("<script>function sa(el,n){var r=n?el.form.querySelectorAll('input[name=\"'+n+'\"]'):el.closest('table').querySelectorAll('input[type=checkbox]');"
         "r.forEach(function(c){if(c!==el&&!c.disabled)c.checked=el.checked})}</script>")


def confirm_box(kind="site"):
    txt = {"ai": "مطمئنم؛ از اعتبار هوش مصنوعی مصرف شود (به سایت چیزی ارسال نمی‌شود)",
           "file": "مطمئنم؛ راهنمای اصلی با این نسخه جایگزین شود (نسخهٔ قبلی نگه داشته می‌شود)"}.get(kind, "مطمئنم، تغییرات روی «سایت» اعمال شود")
    return f'<label><input type="checkbox" name="confirm" value="1"> {txt}</label>'


# ---------- pages ----------
def _mt(p):
    from datetime import datetime
    return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")


def workflow():
    from . import health as hl
    from . import slugs as sl
    K, R = ROOT / "knowledge", ROOT / "reports"
    snap, pack, guide = K / "site" / "snapshot.json", K / "style" / "style_pack.md", K / "style" / "style_guide.md"
    arts = list((K / "articles").glob("*.md"))
    audits = sorted(R.glob("seo_audit_*.csv")) if R.exists() else []
    ans, links = pr.PROP / "seo_answer.csv", sorted(pr.PROP.glob("links_2*.csv"))
    bad = sl.scan() if snap.exists() else None
    hrep = None
    if hl.LATEST.exists():
        try:
            hrep = json.loads(hl.LATEST.read_text(encoding="utf-8"))
        except Exception:
            pass
    from . import gsc as gsc_mod
    gsc_saved = gsc_mod.saved()
    kwa = K / "keywords" / "analysis.json"
    I = lambda p: _mt(p) if p.exists() else "هنوز انجام نشده"
    # key, title, done?, info, kind, target, prerequisites(keys)
    return [
        ("check", "بررسی اتصال به سایت", None, "هر زمان", "run", "check", []),
        ("sync", "همگام‌سازی سایت (دسته‌ها، محصولات، فهرست مقاله‌ها)", snap.exists(), I(snap), "run", "sync", []),
        ("export", "خروجی مقاله‌ها (پایهٔ تحلیل و لینک‌دهی)", bool(arts), f"{len(arts)} مقاله" if arts else "هنوز انجام نشده", "run", "export", ["sync"]),
        ("audit", "گزارش سئوی مقاله‌ها", bool(audits), _mt(audits[-1]) if audits else "هنوز انجام نشده", "run", "audit", ["export"]),
        ("health", "سلامت فنی سایت و محصولات (خزش فقط‌خواندنی)", bool(hrep),
         (f"{hrep['time'][:16].replace('T', ' ')} - {hrep['severity'].get('H', 0)} مورد جدی" if hrep else "هنوز انجام نشده"), "page", "/health", ["sync"]),
        ("gsc", "Search Console (خروجی Performance، بدون حساب سرویس)", bool(gsc_saved), (_mt(gsc_saved[-1]) if gsc_saved else "هنوز انجام نشده"), "page", "/gsc", []),
        ("keywords", "کلمات کلیدی، شکاف محتوا و تقویم (بدون AI؛ AI اختیاری)", kwa.exists(), _mt(kwa) if kwa.exists() else "هنوز انجام نشده", "page", "/keywords", ["export"]),
        ("write", "تولید مقاله (از عنوان تا پیش‌نویس؛ AI + کنترل با کد)", None, "هر زمان", "page", "/write", ["sync"]),
        ("pack", "بستهٔ لحن (آمار نوشتار شما)", pack.exists(), I(pack), "run", "style", ["export"]),
        ("guide", "راهنمای لحن (ویرایش دستی / پیش‌نویس AI)", guide.exists(), I(guide), "page", "/style", ["pack"]),
        ("seo", "کلمه کلیدی، عنوان سئو و متا", ans.exists(), _mt(ans) + " (آخرین پیشنهاد)" if ans.exists() else "هر زمان", "page", "/seo", ["export"]),
        ("links", "لینک‌دهی داخلی", bool(links), _mt(links[-1]) + " (آخرین پیشنهاد)" if links else "هر زمان", "page", "/links", ["export"]),
        ("slugs", "آدرس‌های (slug) فارسی", None if bad is None else not bad,
         "ابتدا همگام‌سازی" if bad is None else (f"{len(bad)} مورد فارسی مانده" if bad else "همه لاتین ✓"), "page", "/slugs", ["sync"]),
        ("inbox", "محصول و مقالهٔ جدید", None, "هر زمان", "page", "/inbox", ["sync"]),
    ]


def home(output=""):
    wf = workflow()
    done = {k: d for k, _, d, *_ in wf}
    num = {k: i for i, (k, *_r) in enumerate(wf, 1)}
    nxt = next((k for k in ("sync", "export", "audit", "health", "pack", "guide") if not done[k]), None)
    rows = []
    for i, (k, title, d, info, kind, target, need) in enumerate(wf, 1):
        miss = [num[x] for x in need if not done[x]]
        mark = "✓" if d else ("○" if d is False else "·")
        if miss:
            act = f"<span class='muted'>اول گام {'، '.join(map(str, miss))}</span>"
        elif kind == "run":
            act = form("/run", f'<input type="hidden" name="action" value="{target}">' + btn("x", "اجرا", "alt")).replace("<form", '<form style="display:inline"')
        else:
            act = f'<a href="{target}"><button type="button">باز کردن</button></a>'
        bg = ' style="background:#fff3cd"' if k == nxt else ""
        rows.append(f"<tr{bg}><td><b>{i}</b></td><td>{mark}</td><td>{E(title)}" + (" <b>← گام بعدیِ پیشنهادی</b>" if k == nxt else "")
                    + f"</td><td class='muted'>{E(str(info))}</td><td>{act}</td></tr>")
    body = ("<h1>زعفران ایجنت</h1><p class='muted'>این پنل فقط روی همین کامپیوتر باز است. فقط کارهایی که مستقیم روی «سایت» اثر می‌گذارند (ویرایش یا ثبت محتوا، سئو، slug، دسته) پیش‌نمایش و تیک تأیید دارند؛ "
            "بررسی‌ها و گزارش‌ها فقط می‌خوانند. پیشرفت کلی پروژه: <a href='/roadmap'>نقشهٔ راه</a>.</p>"
            "<p>مسیر کار: گام‌های اول تا ششم را یک بار (و بعد هر چند هفته) انجام دهید؛ بقیه را هر وقت لازم بود.</p>"
            "<table><tr><th>گام</th><th></th><th>کار</th><th>وضعیت</th><th>اقدام</th></tr>" + "".join(rows) + "</table>"
            "<div class='box'><b>ابزارهای دیگر:</b> "
            + form("/run", '<input type="hidden" name="action" value="diagnose">' + btn("x", "عیب‌یابی اتصال", "alt")).replace("<form", '<form style="display:inline"')
            + form("/run", '<input type="hidden" name="action" value="ai_test">' + btn("x", "تست اتصال AI", "alt")).replace("<form", '<form style="display:inline"')
            + ' &nbsp; <a href="/terms">دستهٔ یا برچسب جدید بسازم ←</a></div>')
    return page("خانه", body + out_box(output))


HSEV = {"H": ("جدی", "#b3261e"), "M": ("متوسط", "#a65f00"), "L": ("کم", "#6b5b4b")}


def health_page(output=""):
    from . import health as hl
    job = JOBS.get("health")
    running = bool(job and job["running"])
    head = '<meta http-equiv="refresh" content="3">' if running else ""
    body = ("<h1>سلامت سایت</h1><p class='muted'>خزش فقط‌خواندنی: صفحه‌های منتشرشده را مثل یک بازدیدکننده باز می‌کند و وضعیت، ریدایرکت، noindex، canonical، عنوان/متا/H1، Alt تصویر، Schema، robots/Sitemap و لینک‌های داخلی را می‌سنجد؛ محصولات ووکامرس را هم ممیزی می‌کند. "
            "چیزی را تغییر نمی‌دهد و هوش مصنوعی مصرف نمی‌کند. بررسی کامل چند دقیقه طول می‌کشد؛ می‌توانید صفحه را باز بگذارید یا بعداً برگردید.</p>")
    if running:
        body += "<div class='box'>⏳ در حال اجرا...</div>"
    else:
        body += ("<div class='box'>" + form("/healthrun", btn("full", "بررسی کامل (صفحه‌ها + لینک‌ها + محصولات)")).replace("<form", '<form style="display:inline"')
                 + form("/healthrun", btn("products", "فقط محصولات (سریع)", "alt")).replace("<form", '<form style="display:inline"')
                 + form("/healthrun", btn("quick", "آزمایش سریع (۴۰ صفحه)", "alt")).replace("<form", '<form style="display:inline"') + "</div>")
    if job:
        if job["error"]:
            body += f"<div class='box' style='color:#b3261e'>خطا: {E(job['error'])}</div>"
        body += f"<details {'open' if running else ''}><summary>گزارش پیشرفت</summary><pre>{E(chr(10).join(job['log'][-15:]))}</pre></details>"
    rep = None
    if hl.LATEST.exists():
        try:
            rep = json.loads(hl.LATEST.read_text(encoding="utf-8"))
        except Exception:
            pass
    if rep:
        from . import healthfix as hf
        sv = rep["severity"]
        emap = hf.entity_map()
        body += (f"<h2>آخرین نتیجه ({E(rep['time'][:16].replace('T', ' '))}، {E({'full': 'بررسی کامل', 'pages': 'صفحه‌ها', 'products': 'محصولات'}.get(rep.get('scope'), ''))})</h2>"
                 "<div class='box'>" + " &nbsp; ".join(f"<b style='color:{HSEV[k][1]}'>{HSEV[k][0]}: {sv.get(k, 0)}</b>" for k in "HML")
                 + f" &nbsp; <span class='muted'>فایل کامل: reports\\{E(rep['csv'])}</span></div>")
        if hf.sitewide_noindex(rep):
            body += ("<div class='box' style='border-color:#b3261e;background:#fdecea'><b>⚠ تقریباً همهٔ صفحه‌ها noindex هستند.</b> این معمولاً یک علت دارد: "
                     "وردپرس ← تنظیمات ← خواندن ← تیک «Discourage search engines from indexing this site» روشن است (یا افزونهٔ حالت نگهداری/سئو آن را گذاشته). "
                     "اول همین را خاموش کنید و دوباره «آزمایش سریع» بزنید؛ بقیهٔ فهرست بعد از آن معنی پیدا می‌کند.</div>")
        ov = hf.overview(rep)
        if ov:
            rows = "".join(
                f"<tr><td><b style='color:{HSEV[o['sev']][1]}'>{HSEV[o['sev']][0]}</b></td><td>{E(o['title'])}</td><td><b>{o['n']}</b></td>"
                f"<td style='color:{hf.MODE_LABEL['ai' if o['ai'] else 'auto'][1]}'>{'با AI (با تأیید شما)' if o['ai'] else 'خودکار با کد'}</td>"
                f"<td><a href='/healthfix?g={o['group']}'><button type='button'>مرور و اصلاح ←</button></a></td></tr>" for o in ov)
            body += ("<h2>از کجا شروع کنم؟</h2><div class='box'>مورد‌هایی که همین‌جا اصلاح می‌شوند، به ترتیب اهمیت. در هر صفحه فهرست را می‌بینید، تیک می‌زنید، "
                     "پیش‌نمایش می‌گیرید و بعد اعمال می‌کنید. بقیهٔ مشکل‌ها (پایین‌تر) راهنمای «چه کنم» دارند.<table><tr><th>شدت</th><th>گروه</th><th>تعداد</th><th>روش</th><th></th></tr>" + rows + "</table></div>")
        by = {}
        for r in rep["rows"]:
            by.setdefault(r[2], []).append(r)
        order = sorted(by, key=lambda c: (-max(hl.SEV[r[0]] for r in by[c]), -len(by[c])))
        if not order:
            body += "<p>✓ هیچ مشکلی پیدا نشد.</p>"
        if order:
            body += "<h2>همهٔ مشکل‌ها، با توضیح</h2>"
        for c in order:
            rs = by[c]
            top = max(rs, key=lambda r: hl.SEV[r[0]])[0]
            mode, why, how = hf.GUIDE.get(c, ("manual", "", ""))
            lab, col = hf.MODE_LABEL[mode]
            g = hf.GROUP_OF.get(c)

            def one_row(r):
                e = emap.get(hl.nurl(r[3]))
                ed = edit_link(r[3], e["id"]) if e else ""
                extra = ""
                if c == "noindex":
                    v, w = hf.noindex_verdict(e["kind"] if e else ("home" if r[1] == "home" else r[1]), r[3], r[4])
                    cl, tx = {"ok": ("#1b6e3c", "✓ درست"), "problem": ("#b3261e", "⚠ باید index شود"), "check": ("#a65f00", "؟ بررسی کنید")}[v]
                    extra = f"<b style='color:{cl}'>{tx}</b> <span class='muted'>{E(w)}</span><br>"
                return f"<tr><td>{link(r[3], r[4] or r[3])}{ed}</td><td class='muted'>{extra}{E(r[5])}</td></tr>"
            lis = "".join(one_row(r) for r in rs[:80])
            more = f"<tr><td colspan=2 class='muted'>... و {len(rs) - 80} مورد دیگر در فایل CSV</td></tr>" if len(rs) > 80 else ""
            fixbtn = f"<p><a href='/healthfix?g={g}'><button type='button'>اصلاح همین‌جا ←</button></a></p>" if g else ""
            body += (f"<details class='box'><summary><b style='color:{HSEV[top][1]}'>[{HSEV[top][0]}]</b> {E(hl.HELP.get(c, c))} "
                     f"<b>({sv_count(rep, c)})</b> <span style='color:{col};font-size:12px'>[{lab}]</span></summary>"
                     f"<p><span class='muted'>چرا مهم است:</span> {E(why)}<br><span class='muted'>چه کنم:</span> {E(how)}</p>{fixbtn}"
                     f"<table>{lis}{more}</table></details>")
    elif not job:
        body += "<p class='muted'>هنوز بررسی انجام نشده است. اگر فهرست صفحه‌ها خالی بود، اول در صفحهٔ خانه «همگام‌سازی سایت» را بزنید.</p>"
    return page("سلامت سایت", body + out_box(output), head)


def sv_count(rep, c):
    return rep["codes"].get(c, 0)


def roadmap_page(output=""):
    import markdown
    f = ROOT / "ROADMAP.md"
    txt = f.read_text(encoding="utf-8") if f.exists() else "ROADMAP.md پیدا نشد."
    return page("نقشهٔ راه", "<div class='box' style='line-height:2'>" + markdown.markdown(txt, extensions=["extra", "sane_lists"]) + "</div>")


def keywords_page(output=""):
    from . import panel_extra as px
    return px.keywords_page(output)


def style_page(output=""):
    from . import style
    pack, guide, aig = style.OUT / "style_pack.md", style.OUT / "style_guide.md", style.OUT / "style_guide_ai.md"
    body = ("<h1>لحن نوشتار</h1>"
            "<div class='box'><b>فرق «بسته» و «راهنما» چیست؟</b><br>"
            "<b>بستهٔ لحن</b> = داده‌های خام: آمار و نمونه‌های واقعی از مقاله‌های خودتان (بدون هوش مصنوعی ساخته می‌شود؛ بزرگ و پرنویز است).<br>"
            "<b>راهنمای لحن</b> = خلاصهٔ یک‌صفحه‌ای دستورهایی که از آن داده‌ها نتیجه می‌شود؛ نویسنده (AI) هر بار فقط همین را می‌خواند، چون فرستادن کل بسته گران است و دقت را کم می‌کند.</div>"
            "<div class='box'><b>الف) ساخت/به‌روزرسانی بستهٔ لحن</b> - با کد، <u>بدون AI و بدون هزینه</u>؛ فقط آمار و نمونه‌های مقاله‌های خودتان را از نو می‌خواند.<br>"
            + form("/run", '<input type="hidden" name="action" value="style">' + btn("x", "ساخت/به‌روزرسانی بستهٔ لحن", "alt"))
            + "</div><div class='box'><b>ب) پیش‌نویس راهنمای لحن با AI</b> - <u>فقط این دکمه</u> اعتبار AI مصرف می‌کند (یک تماس) و راهنمای اصلی را عوض نمی‌کند؛ فقط یک پیش‌نویس برای مقایسه می‌سازد.<br>"
            + form("/run", '<input type="hidden" name="action" value="ai_style">' + confirm_box("ai") + "<br>" + btn("x", "ساخت پیش‌نویس راهنما با AI", "alt"))
            + "</div><div class='box muted'>اگر راهنمای اصلی (پایین) خوب است، لازم نیست کاری بکنید: «تولید مقاله» همان را می‌خواند. بستهٔ لحن فقط ماده‌ٔ خام برای ساختن راهنماست و با AI «عوض» نمی‌شود؛ "
            "اگر لحن راهنما را دوست ندارید، آن را همین‌جا دستی ویرایش کنید یا پیش‌نویس AI (ب) را بسازید و مقایسه کنید.</div>" + out_box(output))
    if guide.exists():
        body += ("<h2>راهنمای اصلی (همین را نویسنده می‌خواند؛ قابل ویرایش)</h2>"
                 + form("/styleguide", f"<textarea name='guide' style='direction:rtl;min-height:380px'>{E(guide.read_text(encoding='utf-8'))}</textarea><br>"
                        + btn("save", "ذخیرهٔ راهنمای اصلی")))
    else:
        body += "<p class='muted'>هنوز راهنمای اصلی ندارید: یا فایل knowledge\\style\\style_guide.md را بگذارید، یا پیش‌نویس AI را بسازید و انتخاب کنید.</p>"
    if aig.exists():
        body += (f"<h2>پیش‌نویس AI (فقط برای مقایسه)</h2><pre style='direction:rtl;text-align:right'>{E(aig.read_text(encoding='utf-8'))}</pre>"
                 + form("/styleguide", confirm_box("file") + btn("use_ai", "استفاده از پیش‌نویس AI به‌عنوان راهنمای اصلی", "warn")))
    if pack.exists():
        body += f"<details class='box'><summary>بستهٔ لحن (آمار و نمونه‌ها؛ فقط برای مشاهده)</summary><pre style='direction:rtl;text-align:right'>{E(pack.read_text(encoding='utf-8'))}</pre></details>"
    return page("لحن", body)


def terms_page(output=""):
    from . import site
    f = site.NEW_TERMS
    text = f.read_text(encoding="utf-8") if f.exists() else site.TEMPLATE
    body = ("<h1>دسته یا برچسب جدید</h1><p class='muted'>برنامه هیچ‌وقت خودش دسته یا برچسب نمی‌سازد. اینجا فهرست را می‌نویسید، «پیش‌نمایش» می‌زنید و فقط "
            "اگر همان فهرست پیش‌نمایش‌شده باشد، «ساخت» با تیک تأیید کار می‌کند. هر خط: tax (category / post_tag / product_cat / product_tag)، name، slug لاتین، و در صورت لزوم parent.</p>"
            + form("/terms", f"<textarea name='yaml' style='min-height:240px'>{E(text)}</textarea><br>"
                   + btn("save", "ذخیره", "alt") + btn("preview", "۱) پیش‌نمایش") + "<hr>" + confirm_box() + btn("create", "۲) ساخت در سایت", "warn"))
            + out_box(output))
    return page("دسته جدید", body)


TERM_LABEL = {"category": "دستهٔ مقاله", "post_tag": "برچسب مقاله", "product_cat": "دستهٔ محصول", "product_tag": "برچسب محصول"}


def slugs_state_file():
    return pr.PROP / "slugs_review_state.json"


def slug_rows():
    fs = sorted(pr.PROP.glob("slugs_2*.csv"))
    if not fs:
        return None
    f = fs[-1]
    try:
        rows, mt = pr.read_csv(f), f.stat().st_mtime
    except OSError:
        return None
    st = {}
    try:
        d = json.loads(slugs_state_file().read_text(encoding="utf-8"))
        if d.get("mtime") == mt:
            st = d.get("rows", {})
    except Exception:
        pass
    out = []
    for r in rows:
        k = f"{r['kind']}:{r['id']}"
        s_ = st.get(k, {})
        out.append({"key": k, "r": r, "slug": s_.get("slug", r.get("new_slug", "")), "ok": s_.get("ok", False)})
    return out, mt


def slugs_page(output=""):
    from . import slugs as sl
    items, terms = sl.scan(), sl.bad_term_slugs()
    body = "<h1>آدرس‌ها (slug)</h1>"
    if items is None:
        return page("آدرس‌ها", body + "<p>اول در صفحهٔ خانه مرحلهٔ ۲ (همگام‌سازی) را اجرا کنید.</p>" + out_box(output))
    body += (f"<div class='box'>مقاله، صفحه و محصولی که slug فارسی دارند: <b>{len(items)}</b>"
             + ("" if items else " ✓ همه لاتین است") + "<br><span class='muted'>ترتیب: ۱) پیشنهاد AI (فقط پیشنهاد؛ سایت تغییر نمی‌کند) ← ۲) بازبینی و ویرایش ← ۳) اعمال. "
                "وردپرس معمولاً آدرس قدیمی مقاله/محصول را خودکار به آدرس جدید ریدایرکت (۳۰۱) می‌کند؛ بعد از اولین تغییر، یک آدرس قدیمی را در مرورگر امتحان کنید.</span><br>"
             + form("/run", '<input type="hidden" name="action" value="ai_slugs_est">' + btn("x", "برآورد هزینه", "alt")).replace("<form", '<form style="display:inline"')
             + form("/run", '<input type="hidden" name="action" value="ai_slugs">' + confirm_box("ai") + btn("x", "۱) پیشنهاد slug لاتین با AI")).replace("<form", '<form style="display:inline"')
             + "</div>" + out_box(output))
    res = slug_rows()
    if res:
        rows, _ = res
        trs = "".join(
            f'<tr><td><input type="checkbox" name="ok" value="{E(x["key"])}" {"checked" if x["ok"] else ""}></td>'
            f'<td>{link(x["r"].get("url", ""), x["r"]["title"])}{edit_link(x["r"].get("url", ""), x["r"]["id"])}<div class="muted">{E(x["r"]["kind"])}</div></td>'
            f'<td style="direction:ltr;text-align:left">{E(x["r"]["old_slug"])}</td>'
            f'<td><input style="width:100%;direction:ltr" name="s_{E(x["key"])}" value="{E(x["slug"])}" data-slug="1"><span class="muted">{E(x["r"].get("ai_notes", ""))}</span></td></tr>'
            for x in rows)
        js = ("<script>document.querySelectorAll('[data-slug]').forEach(function(i){function c(){var ok=/^[a-z0-9]+(-[a-z0-9]+)*$/.test(i.value);"
              "i.style.borderColor=ok?'#1b7a2e':'#b3261e'}i.addEventListener('input',c);c()})</script>")
        body += ("<h2>۲) بازبینی، ویرایش و ۳) اعمال</h2>" + form("/slugsreview",
                 "<p class='muted'>slug فقط باید حروف کوچک لاتین، عدد و خط تیره باشد (کادر سبز = معتبر).</p><table><tr><th>" + selall() + "</th><th>عنوان</th><th>slug فعلی</th><th>slug پیشنهادی (قابل ویرایش)</th></tr>"
                 + trs + "</table>" + btn("save", "ذخیرهٔ ویرایش‌ها", "alt") + btn("preview", "پیش‌نمایش اعمال") + "<hr>" + confirm_box() + btn("apply", "اعمال تیک‌خورده‌ها در سایت", "warn")) + js)
    if terms:
        body += ("<h2>دسته و برچسب با slug فارسی (فعلاً فقط فهرست)</h2><p class='muted'>هر دسته یا برچسب یک صفحهٔ آدرس‌دار دارد (مثلاً سایت/category/<b>slug</b>/) و slug همان قسمت آخر است. "
                 "تغییر آن، آدرس قدیمی آن صفحه را ۴۰۴ می‌کند مگر ریدایرکت داشته باشید؛ به همین دلیل خودکار تغییر نمی‌دهم.</p><table><tr><th>نوع</th><th>نام</th><th>slug (آخرِ آدرس)</th><th>آدرس صفحهٔ آن</th></tr>"
                 + "".join(f"<tr><td>{E(TERM_LABEL.get(t, t))}</td><td>{E(n)}</td><td>{E(sg)}</td><td>{link(lk, lk) if lk else '-'}</td></tr>" for t, n, sg, lk in terms) + "</table>")
    return page("آدرس‌ها", body)


def latest_links():
    fs = sorted(pr.PROP.glob("links_2*.csv"))
    return fs[-1] if fs else None


def ai_td(r):
    return f"<td>{E(str(r['ai_score']))}★</td>" if r.get("ai_score") not in (None, "") else ("<td></td>" if "ai_score" in r else "")


def links_state(f, rows):
    st = pr.PROP / (f.name + ".panel.json")
    if st.exists():
        try:
            return st, set(json.loads(st.read_text(encoding="utf-8"))["ok"])
        except Exception:
            pass
    return st, {str(i) for i, r in enumerate(rows) if str(r.get("approve", "")).strip().lower() in pr.YES}


def links_page(output=""):
    f = latest_links()
    body = ("<h1>لینک‌دهی داخلی</h1>" + form("/run", '<input type="hidden" name="action" value="linkplan">' + btn("x", "ساخت پیشنهاد جدید", "alt"))
            + form("/run", '<input type="hidden" name="action" value="ai_judge_est">' + btn("x", "برآورد هزینهٔ ارزیابی AI", "alt"))
            + form("/run", '<input type="hidden" name="action" value="ai_judge">' + confirm_box("ai") + btn("x", "ارزیابی لینک‌ها با AI")))
    if not f:
        return page("لینک‌دهی", body + "<p>هنوز پیشنهادی نیست. اول «خروجی مقاله‌ها» را در صفحهٔ خانه اجرا کنید، بعد پیشنهاد بسازید.</p>" + out_box(output))
    try:
        rows = pr.read_csv(f)
    except OSError as e:
        return page("لینک‌دهی", body + f"<p>فایل {E(f.name)} خوانده نشد ({E(str(e))}). اگر در Excel باز است ببندید.</p>")
    _, ok = links_state(f, rows)
    trs = []
    for i, r in enumerate(rows):
        sent = E(r.get("sentence", "")).replace(E(r.get("anchor", "")), f"<mark>{E(r.get('anchor', ''))}</mark>", 1)
        trs.append(f'<tr><td><input type="checkbox" name="ok" value="{i}" {"checked" if str(i) in ok else ""}></td>'
                   f'<td>{link(r["source_url"], r["source_title"])}{edit_link(r["source_url"], r.get("source_id", ""))}</td>'
                   f'<td>{sent}</td><td>{link(r["target_url"], r["target_title"])}{('<div class=muted>' + E(r['why']) + '</div>') if r.get('why') else ''}</td><td>{E(r.get("score", ""))}</td>{ai_td(r)}</tr>')
    inner = (f'<input type="hidden" name="file" value="{E(f.name)}">'
             f"<p class='muted'>{E(f.name)} - {len(rows)} پیشنهاد. روی عنوان‌ها بزنید تا مقاله در تب جدید باز شود. فقط لینک‌های طبیعی و مرتبط را تیک بزنید.</p>"
             "<table><tr><th>" + selall() + "</th><th>مقالهٔ مبدأ (لینک در آن گذاشته می‌شود)</th><th>جمله (عبارت لینک‌شونده زرد است)</th>"
             "<th>مقالهٔ مقصد</th><th>امتیاز</th>" + ("<th>AI</th>" if rows and "ai_score" in rows[0] else "") + "</tr>" + "".join(trs) + "</table>"
             + btn("save", "ذخیرهٔ تیک‌ها", "alt") + btn("preview", "پیش‌نمایش اعمال")
             + "<hr>" + confirm_box() + btn("apply", "اعمال لینک‌های تیک‌خورده", "warn"))
    return page("لینک‌دهی", body + form("/links", inner) + out_box(output))


def seo_state_file():
    return pr.PROP / "seo_review_state.json"


def seo_rows():
    f = pr.PROP / "seo_answer.csv"
    if not f.exists():
        return []
    try:
        rows, mt = pr.read_csv(f), f.stat().st_mtime
    except OSError:
        return []
    st = {}
    try:
        d = json.loads(seo_state_file().read_text(encoding="utf-8"))
        if d.get("mtime") == mt:  # a newer AI answer discards old edits
            st = d.get("rows", {})
    except Exception:
        pass
    arts = {str(a["id"]): a for a in pr.read_articles()}
    out = []
    for r in rows:
        pid = pr.num(r.get("id"))
        if pid not in arts:
            continue
        s_ = st.get(pid, {})
        out.append({"id": pid, "a": arts[pid], "ok": s_.get("ok", False), "note": r.get("ai_notes", ""),
                    "k": s_.get("k", r.get("new_focus_keyword", "")), "t": s_.get("t", r.get("new_seo_title", "")),
                    "m": s_.get("m", r.get("new_meta_description", ""))})
    return out, mt


SEO_JS = """<script>
function upd(id){var g=function(p){return document.getElementsByName(p+id)[0].value};var k=g('k_'),t=g('t_'),m=g('m_');
function b(ok,x){return '<span style="color:'+(ok?'#1b7a2e':'#b3261e')+'">'+x+'</span>'}
document.getElementById('c_'+id).innerHTML=b(t.length>0&&t.length<=60,'عنوان '+t.length+'/60')+' · '+b(m.length>=110&&m.length<=160,'متا '+m.length+' (۱۱۰-۱۶۰)')+' · '+b(k&&t.indexOf(k)>=0,'کلمه در عنوان')+' · '+b(k&&m.indexOf(k)>=0,'کلمه در متا');}
document.querySelectorAll('[data-seo]').forEach(function(x){x.addEventListener('input',function(){upd(x.dataset.seo)});upd(x.dataset.seo)});
</script>"""


def seo_review():
    res = seo_rows()
    if not res:
        return ""
    rows, _ = res
    arts = pr.read_articles()
    trs = []
    for r in rows:
        a = r["a"]
        others = {norm_(x.get("focus_keyword") or "") for x in arts if str(x["id"]) != r["id"]}
        probs = ai_problems({"k": r["k"], "t": r["t"], "m": r["m"]}, others)
        trs.append(
            f'<tr><td><input type="checkbox" name="ok" value="{r["id"]}" {"checked" if r["ok"] else ""}></td>'
            f'<td>{link(a.get("url", ""), a["title"])}{edit_link(a.get("url", ""), r["id"])}'
            f'<div class="muted">فعلی:<br>کلمه: {E(a.get("focus_keyword") or "-")}<br>عنوان: {E(a.get("seo_title") or "-")}<br>متا: {E(a.get("meta_description") or "-")}</div></td>'
            f'<td><input style="width:100%" name="k_{r["id"]}" value="{E(r["k"])}" data-seo="{r["id"]}" placeholder="کلمه کلیدی">'
            f'<input style="width:100%;margin-top:4px" name="t_{r["id"]}" value="{E(r["t"])}" data-seo="{r["id"]}" placeholder="عنوان سئو">'
            f'<textarea style="min-height:70px;direction:rtl;margin-top:4px" name="m_{r["id"]}" data-seo="{r["id"]}" placeholder="متا دیسکریپشن">{E(r["m"])}</textarea>'
            f'<div id="c_{r["id"]}" class="muted"></div>'
            + (f'<div class="muted" style="color:#b3261e">{E("; ".join(probs))}</div>' if probs else "")
            + "</td></tr>")
    inner = ("<p class='muted'>پیشنهادها را بخوانید و هر فیلد را آزادانه ویرایش کنید؛ شمارنده‌ها زنده‌اند. فقط ردیف‌های تیک‌خورده اعمال می‌شوند. "
             "اول «ذخیره» یا «پیش‌نمایش» را بزنید تا تغییرات (قدیم ← جدید) و هشدارها را ببینید.</p>"
             "<table><tr><th>" + selall() + "</th><th>مقاله و مقدار فعلی</th><th>پیشنهاد (قابل ویرایش)</th></tr>" + "".join(trs) + "</table>"
             + btn("save", "ذخیرهٔ ویرایش‌ها", "alt") + btn("preview", "پیش‌نمایش اعمال")
             + "<hr>" + confirm_box() + btn("apply", "اعمال تیک‌خورده‌ها در Yoast", "warn")) + SEO_JS
    return "<h2>ب - بازبینی، ویرایش و اعمال در سایت</h2>" + form("/seoreview", inner)


def norm_(s):
    from .common import norm
    return norm(s)


def ai_problems(r, others):
    from . import ai
    return ai._problems(r, others) if all(r.values()) else ["یکی از فیلدها خالی است"]


def seo_page(output=""):
    body = ("<h1>کلمه کلیدی، عنوان سئو و متا</h1><div class='box'><b>الف - پیشنهاد هوش مصنوعی</b> (فقط پیشنهاد می‌سازد و اعتبار AI مصرف می‌کند؛ در سایت هیچ تغییری نمی‌دهد)<br>"
            + form("/run", '<input type="hidden" name="action" value="ai_seo_est">' + btn("x", "برآورد هزینه", "alt")).replace("<form", '<form style="display:inline"')
            + form("/run", '<input type="hidden" name="action" value="ai_seo3">' + confirm_box("ai") + btn("x", "آزمایش روی ۳ مقاله")).replace("<form", '<form style="display:inline"')
            + form("/run", '<input type="hidden" name="action" value="ai_seo">' + confirm_box("ai") + btn("x", "همهٔ مقاله‌های ضعیف")).replace("<form", '<form style="display:inline"')
            + "</div>")
    body += out_box(output) + seo_review()
    todos = sorted(pr.PROP.glob("seo_todo_*.csv"))
    manual = form("/seo", btn("todo", "ساخت فهرست مقاله‌های ضعیف و پرامپت (روش دستی با چت‌بات)", "alt"))
    groups = sorted(pr.PROP.glob("seo_prompt_*.md"))
    if groups:
        key = groups[-1].name.rsplit("_", 1)[0]
        for i, g in enumerate(f for f in groups if f.name.rsplit("_", 1)[0] == key):
            manual += (f"<div class='box'><b>پرامپت {i + 1}</b> ({E(g.name)}) "
                       f"<button type='button' class='alt' onclick=\"navigator.clipboard.writeText(document.getElementById('p{i}').value)\">کپی</button>"
                       f"<textarea id='p{i}' readonly>{E(g.read_text(encoding='utf-8'))}</textarea></div>")
    if todos:
        try:
            tr = "".join(f"<tr><td>{link(r['url'], r['title'])}{edit_link(r['url'], r['id'])}</td><td>{E(r['focus_keyword'])}</td>"
                         f"<td>{E(r['meta_description'][:90])}</td></tr>" for r in pr.read_csv(todos[-1]))
            manual += ("<h3>مقاله‌های نیازمند اصلاح</h3><table><tr><th>مقاله</th><th>کلمه کلیدی فعلی</th><th>متای فعلی</th></tr>" + tr + "</table>")
        except OSError:
            pass
    manual += form("/seo", "<p>جواب CSV چت‌بات را اینجا بچسبانید (id,new_focus_keyword,new_seo_title,new_meta_description):</p>"
                   "<textarea name='answer'></textarea><br>" + btn("preview", "ذخیره و پیش‌نمایش") + "<hr>" + confirm_box() + btn("apply", "اعمال", "warn"))
    body += f"<details class='box'><summary>روش دستی (کپی/پیست با چت‌بات)</summary>{manual}</details>"
    return page("سئو", body)


def inbox_page(output=""):
    def status(p, resf):
        return "قبلاً ارسال شده" if resf.exists() else "آمادهٔ ارسال"
    rows = []
    for d in sorted((ROOT / "inbox" / "products").glob("*")):
        if d.is_dir() and not d.name.startswith("_"):
            rows.append(f"<tr><td>محصول</td><td>{E(d.name)}</td><td>{status(d, d / 'result.json')}</td></tr>")
    for f in sorted((ROOT / "inbox" / "articles").glob("*.md")):
        if not f.name.startswith("_"):
            rows.append(f"<tr><td>مقاله</td><td>{E(f.name)}</td><td>{status(f, f.with_suffix('.result.json'))}</td></tr>")
    body = ("<h1>محصول و مقالهٔ جدید</h1><p class='muted'>فایل‌ها را طبق README در پوشهٔ inbox بسازید. همه به‌صورت پیش‌نویس ثبت می‌شوند؛ ویرایش آیتم موجود فقط با update: true.</p>"
            "<table><tr><th>نوع</th><th>نام</th><th>وضعیت</th></tr>" + "".join(rows or ["<tr><td colspan=3>چیزی در inbox نیست</td></tr>"]) + "</table>"
            + form("/run", '<input type="hidden" name="action" value="inbox_dry">' + btn("x", "پیش‌نمایش (چیزی ارسال نمی‌شود)"))
            + form("/run", '<input type="hidden" name="action" value="inbox_send">' + confirm_box() + btn("x", "ارسال به سایت", "warn")))
    return page("ورودی", body + out_box(output))


def reports_page(output=""):
    mds = sorted((ROOT / "reports").glob("seo_audit_*.md")) if (ROOT / "reports").exists() else []
    csvs = sorted((ROOT / "reports").glob("seo_audit_*.csv")) if (ROOT / "reports").exists() else []
    body = "<h1>گزارش سئو</h1>" + form("/run", '<input type="hidden" name="action" value="audit">' + btn("x", "ساخت گزارش جدید", "alt"))
    if mds:
        body += f"<h2>{E(mds[-1].name)}</h2><pre>{E(mds[-1].read_text(encoding='utf-8'))}</pre>"
    if csvs:
        rows = pr.read_csv(csvs[-1])[:40]
        body += ("<h2>۴۰ مقالهٔ اولویت‌دار (فایل کامل CSV در پوشهٔ reports)</h2><table><tr><th>امتیاز</th><th>مقاله</th><th>مشکل‌ها</th></tr>"
                 + "".join(f"<tr><td>{E(r['score'])}</td><td>{link(r['url'], r['title'])}{edit_link(r['url'], r['id'])}</td><td>{E(r['issues'])}</td></tr>" for r in rows) + "</table>")
    elif not mds:
        body += "<p>هنوز گزارشی نیست. اول «خروجی مقاله‌ها» (صفحهٔ خانه) و بعد «ساخت گزارش».</p>"
    return page("گزارش", body + out_box(output))


# ---------- server ----------
GONE = (ConnectionError, TimeoutError)   # the browser closed/refreshed the tab (or an antivirus cut the link) before the answer was written: not an error of ours


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def handle(self):
        try:
            super().handle()
        except GONE:
            pass                                    # no traceback for a closed connection (WinError 10053/10054)

    def _send(self, code, text):
        b = text.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        except GONE:
            self.close_connection = True            # the work was already done and saved; the page can simply be reloaded

    def _redirect(self, where):
        try:
            self.send_response(303)
            self.send_header("Location", where)
            self.send_header("Content-Length", "0")
            self.end_headers()
        except GONE:
            self.close_connection = True

    def _host_ok(self):
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def do_GET(self):
        try:
            self._get()
        except GONE:
            self.close_connection = True
        except Exception as e:
            self._send(500, page('خطا', f"<h1>خطا</h1><pre>{E(type(e).__name__ + ': ' + str(e))}</pre><p><a href='/'>بازگشت</a></p>"))

    def _get(self):
        if not self._host_ok():
            return self._send(403, "forbidden")
        path = urlparse(self.path).path
        if path.startswith("/assets/fonts/"):
            name = unquote(path.rsplit("/", 1)[-1])
            fp = FONT_DIR / name
            if not re.fullmatch(r"[\w.\[\]() -]+", name) or fp.suffix.lower() not in FONT_FMT or not fp.is_file():
                return self._send(404, "not found")
            b = fp.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "font/" + FONT_FMT[fp.suffix.lower()].replace("truetype", "ttf").replace("opentype", "otf"))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            return self.wfile.write(b)
        if path == "/ping":
            return self._send(200, f"zafaran-panel {VERSION}")
        if path == "/healthrun":
            return self._redirect("/health")
        if path == "/write":
            from . import panel_write as pw
            q = parse_qs(urlparse(self.path).query)
            return self._send(200, pw.job_page(q["job"][0]) if q.get("job") else pw.list_page())
        if path == "/tasks":
            from . import panel_extra as px
            return self._send(200, px.tasks_page())
        if path == "/refresh":
            from . import panel_extra as px
            return self._send(200, px.refresh_page())
        if path == "/graph":
            from . import panel_extra as px
            return self._send(200, px.graph_page())
        if path == "/media":
            from . import panel_extra as px
            return self._send(200, px.media_page())
        if path == "/keywords":
            from . import panel_extra as px
            q = parse_qs(urlparse(self.path).query)
            try:
                mi = max(1, int(q.get("min", ["10"])[0]))
            except ValueError:
                mi = 10
            return self._send(200, px.keywords_page(min_impr=mi, st=q.get("st", ["gap"])[0]))
        if path in ("/healthfix", "/gsc"):
            from . import panel_extra as px
            q = parse_qs(urlparse(self.path).query)
            if path == "/healthfix":
                return self._send(200, px.fix_page(q.get("g", [""])[0]))
            try:
                mi = max(1, int(q.get("min", ["10"])[0]))
            except ValueError:
                mi = 10
            return self._send(200, px.gsc_page(min_impr=mi))
        pages = {"/": home, "/health": health_page, "/roadmap": roadmap_page, "/style": style_page, "/slugs": slugs_page, "/terms": terms_page, "/links": links_page, "/seo": seo_page, "/inbox": inbox_page, "/reports": reports_page}
        fn = pages.get(urlparse(self.path).path)
        self._send(200, fn()) if fn else self._send(404, not_found(path))

    def do_POST(self):
        try:
            self._post()
        except GONE:
            self.close_connection = True
        except Exception as e:  # never show a traceback; tell the user what happened
            self._send(500, page('خطا', f"<h1>خطا</h1><pre>{E(type(e).__name__ + ': ' + str(e))}</pre><p><a href='/'>بازگشت</a></p>"))

    def _post(self):
        if not self._host_ok():
            return self._send(403, "forbidden")
        n = int(self.headers.get("Content-Length", 0))
        ctype, files = self.headers.get("Content-Type", ""), []
        if ctype.startswith("multipart/form-data"):
            if n > 80 * 1024 * 1024:
                return self._send(413, page("فایل بزرگ است", "<h1>فایل بیش از ۸۰ مگابایت است</h1>"))
            from . import panel_extra as px
            f, files = px.parse_multipart(ctype, self.rfile.read(n))
        else:
            f = parse_qs(self.rfile.read(n).decode("utf-8"), keep_blank_values=True)
        if f.get("t", [""])[0] != TOKEN:
            return self._send(403, "bad token - reload the page")
        one = lambda k: f.get(k, [""])[0]
        path, sure = urlparse(self.path).path, one("confirm") == "1"
        if path == "/run":
            acts = actions()
            a = acts.get(one("action"))
            if not a:
                return self._send(400, "unknown action")
            label, fn, needs = a
            if needs and not sure:
                return self._send(200, home(f"«{label}» اجرا نشد: تیک تأیید را بزنید."))
            txt = f"== {label} ==\n" + run(fn, yes=needs)
            back = {"linkplan": links_page, "seotodo": seo_page, "ai_judge": links_page, "ai_judge_est": links_page,
                    "ai_seo": seo_page, "ai_seo_est": seo_page, "ai_seo3": seo_page, "ai_style": style_page, "style": style_page,
                    "ai_slugs": slugs_page, "ai_slugs_est": slugs_page, "inbox_dry": inbox_page, "inbox_send": inbox_page,
                    "audit": reports_page, "ai_gap": keywords_page, "ai_gap_est": keywords_page}.get(one("action"), home)
            return self._send(200, back(txt))
        if path == "/links":
            name = one("file")
            if not re.fullmatch(r"links_\d{8}_\d{4}(_ai)?\.csv", name) or not (pr.PROP / name).exists():
                return self._send(400, "bad file")
            fp = pr.PROP / name
            rows = pr.read_csv(fp)
            ok = set(f.get("ok", []))
            st, _ = links_state(fp, rows)
            st.write_text(json.dumps({"ok": sorted(ok, key=int)}), encoding="utf-8")  # our own file: never locked by Excel
            approved = fp.parent / "_panel_approved.csv"
            hdr = list(rows[0].keys())
            pr.write_csv(approved, hdr, [[("y" if k == "approve" else r[k]) for k in hdr] for i, r in enumerate(rows) if str(i) in ok])
            do = one("do")
            if do == "save":
                txt = f"ذخیره شد: {len(ok)} لینک تأییدشده."
            elif not ok:
                txt = "هیچ لینکی تیک نخورده است."
            elif do == "preview":
                txt = run(lambda: pr.apply_links(False, approved))
            elif do == "apply" and sure:
                txt = run(lambda: pr.apply_links(True, approved), yes=True)
            else:
                txt = "اعمال نشد: تیک تأیید را بزنید."
            return self._send(200, links_page(txt))
        if path == "/healthrun":
            from . import health as hl
            do = one("do")
            kw = {"full": {}, "products": {"products_only": True}, "quick": {"limit": 40, "pages_only": True}}.get(do)
            if kw is None:
                return self._send(400, "unknown")
            start_job("health", lambda log: hl.run(log, **kw))
            return self._redirect("/health")
        if path == "/hfbuild":
            from . import healthfix as hf, panel_extra as px
            g, do = one("g"), one("do")
            if g not in hf.GROUPS:
                return self._send(400, "unknown group")
            if do == "est":
                return self._send(200, px.fix_page(g, run(lambda: print(hf.ai_estimate(g)))))
            if do == "ai":
                if not sure:
                    return self._send(200, px.fix_page(g, "ساخته نشد: تیک مصرف اعتبار AI را بزنید."))
                start_job("hf_" + g, lambda log: hf.build_ai(g, log))
            else:
                start_job("hf_" + g, lambda log: hf.build(g, log))
            return self._redirect("/healthfix?g=" + g)
        if path == "/hfapply":
            from . import healthfix as hf, panel_extra as px
            g, do = one("g"), one("do")
            if g not in hf.GROUPS:
                return self._send(400, "unknown group")
            sel, ed = px.parse_edits(f, 0)
            if do == "apply" and not sure:
                return self._send(200, px.fix_page(g, "اعمال نشد: تیک تأیید را بزنید."))
            go = do == "apply"
            return self._send(200, px.fix_page(g, run(lambda: hf.apply(g, sel, ed, go), yes=go)))
        if path == "/gsc":
            from . import gsc, panel_extra as px
            try:
                if one("do") == "inbox":
                    _, msg = gsc.import_inbox()
                elif files:
                    _, msg = gsc.import_files(files)
                else:
                    return self._send(200, px.gsc_page("فایلی انتخاب نشده است."))
            except (ValueError, OSError) as e:
                return self._send(200, px.gsc_page(f"خطا: {e}"))
            px.FLASH["gsc"] = msg
            return self._redirect("/gsc")
        if path in ("/write", "/writeq", "/writeact", "/writeauto"):
            from . import panel_write as pw
            kind, val = pw.post(path, f, sure, run)
            return self._redirect(val) if kind == "redirect" else self._send(200, val)
        if path == "/tasksact":
            from . import panel_extra as px, tasks as tk
            do, key, msg = one("do"), one("key"), ""
            try:
                if do in ("done", "dismissed"):
                    tk.set_status(key, do)
                elif do == "snoozed":
                    tk.set_status(key, "snoozed", 7)
                elif do == "reset":
                    tk.set_status(key, "reset")
                elif do == "note_add":
                    tk.add_note(one("kind"), one("title"), one("note"), one("prio") or "normal")
                    msg = "ثبت شد."
                elif do == "note_done":
                    tk.finish_note(key, (one("v") or "1") == "1")
                elif do == "note_del":
                    tk.remove_note(key)
                elif do == "bundle":
                    p_, text = tk.bundle()
                    return self._send(200, px.tasks_page(f"بسته ساخته شد: {p_}", bundle=text))
            except ValueError as e:
                msg = str(e)
            return self._send(200, px.tasks_page(msg))
        if path == "/refreshact":
            from . import articlegen as ag_, panel_extra as px
            do = one("do")
            if do in ("import_prev", "import_go"):
                pid = one("id")
                holder = {}
                txt = run(lambda: holder.update(job=ag_.import_existing(int(pid), do == "import_go", one("okfid") == "1", one("why"))) if pid.isdigit() else print("ERROR: شناسه نامعتبر"), yes=True)
                if holder.get("job"):
                    return self._redirect("/write?job=" + holder["job"]["id"])
                return self._send(200, px.refresh_page(txt))
            return self._send(200, px.refresh_page())
        if path == "/mediaact":
            from . import media_seo as ms, panel_extra as px
            do = one("do")
            ids = lambda name: set(f.get(name, []))
            if do in ("alt_go", "cats_go", "cats_ai") and not sure:
                return self._send(200, px.media_page("اجرا نشد: تیک تأیید را بزنید."))
            if do == "audit_media":
                txt = run(lambda: ms.audit_media(print))
            elif do == "alt_build":
                txt = run(lambda: ms.build_alt(None, print))
            elif do in ("alt_prev", "alt_go"):
                txt = run(lambda: ms.apply_alt(ids("alt"), {k[4:]: v[0] for k, v in f.items() if k.startswith("alt_") and v}, do == "alt_go", print))
            elif do == "cats_audit":
                txt = run(lambda: ms.audit_cats(print))
            elif do in ("cats_est", "cats_ai"):
                txt = run(lambda: ms.draft_cats(do == "cats_ai", print), yes=(do == "cats_ai"))
            elif do in ("cats_prev", "cats_go"):
                txt = run(lambda: ms.apply_cats(ids("cat"), {k[4:]: v[0] for k, v in f.items() if k.startswith("cat_") and v}, do == "cats_go", print))
            elif do == "schema_run":
                txt = run(lambda: ms.validate_schema(12, None, print))
            else:
                txt = "دستور نامعتبر."
            return self._send(200, px.media_page(txt))
        if path == "/graphbuild":
            from . import linkgraph as lg, panel_extra as px
            if one("do") == "links":
                txt = run(lambda: lg.suggest(lg.load()))
                if "ساخته شد" in txt:
                    return self._redirect("/links")
                return self._send(200, px.graph_page(txt))
            return self._send(200, px.graph_page(run(lambda: lg.build(print))))
        if path == "/kwbuild":
            from . import keywords as kw, panel_extra as px
            try:
                mi = max(1, int(one("min") or 10))
            except ValueError:
                mi = 10
            return self._send(200, px.keywords_page(run(lambda: kw.build(mi, print)), min_impr=mi))
        if path == "/kwsave":
            from . import keywords as kw
            kw.save_manual(one("manual"))
            return self._send(200, keywords_page("کلمات دستی ذخیره شد. برای دیدن اثرشان «ساخت/به‌روزرسانی تحلیل» را بزنید."))
        if path == "/terms":
            import hashlib, yaml
            from . import site
            txt, do = one("yaml"), one("do")
            try:
                yaml.safe_load(txt)
            except yaml.YAMLError as e:
                return self._send(200, terms_page(f"فایل YAML خطا دارد؛ ذخیره نشد: {e}"))
            site.KNOW.mkdir(exist_ok=True)
            site.NEW_TERMS.write_text(txt, encoding="utf-8")
            h = hashlib.sha256(txt.strip().encode()).hexdigest()
            hf = pr.PROP / "terms_preview.hash"
            if do == "save":
                return self._send(200, terms_page("ذخیره شد."))
            if do == "preview":
                pr.PROP.mkdir(exist_ok=True)
                hf.write_text(h)
                return self._send(200, terms_page(run(lambda: site.run_newterms(False))))
            if not sure:
                return self._send(200, terms_page("ساخته نشد: تیک تأیید را بزنید."))
            if not hf.exists() or hf.read_text() != h:
                return self._send(200, terms_page("ساخته نشد: اول همین فهرست را «پیش‌نمایش» کنید (از آخرین پیش‌نمایش تغییر کرده)."))
            hf.unlink()
            return self._send(200, terms_page(run(lambda: site.run_newterms(True), yes=True)))
        if path == "/styleguide":
            from . import style
            if one("do") == "use_ai":
                src, gf = style.OUT / "style_guide_ai.md", style.OUT / "style_guide.md"
                if not sure or not src.exists():
                    return self._send(200, style_page("جایگزین نشد: تیک تأیید را بزنید."))
                import shutil, time
                if gf.exists():
                    shutil.copy(gf, style.OUT / f"style_guide_prev_{time.strftime('%Y%m%d_%H%M%S')}.md")
                shutil.copy(src, gf)
                return self._send(200, style_page("پیش‌نویس AI جایگزین راهنمای اصلی شد (نسخهٔ قبلی نگه داشته شد)."))
            g = one("guide").strip()
            if not g:
                return self._send(200, style_page("متن خالی است؛ ذخیره نشد."))
            gf = style.OUT / "style_guide.md"
            if gf.exists():
                import shutil, time
                shutil.copy(gf, style.OUT / f"style_guide_prev_{time.strftime('%Y%m%d_%H%M%S')}.md")
            gf.write_text(g + "\n", encoding="utf-8")
            return self._send(200, style_page("راهنمای لحن ذخیره شد (نسخهٔ قبلی هم نگه داشته شد)."))
        if path == "/slugsreview":
            from . import slugs as sl
            res = slug_rows()
            if not res:
                return self._send(200, slugs_page("پیشنهادی برای بازبینی نیست."))
            rows, mt = res
            okset, st = set(f.get("ok", [])), {}
            for x in rows:
                st[x["key"]] = {"slug": one("s_" + x["key"]).strip(), "ok": x["key"] in okset}
            slugs_state_file().write_text(json.dumps({"mtime": mt, "rows": st}, ensure_ascii=False), encoding="utf-8")
            appr = pr.PROP / "_panel_slugs.csv"
            pr.write_csv(appr, ["id", "kind", "new_slug"], [[x["r"]["id"], x["r"]["kind"], st[x["key"]]["slug"]] for x in rows if st[x["key"]]["ok"]])
            n_ok, do = sum(1 for v in st.values() if v["ok"]), one("do")
            if do == "save":
                txt = f"ذخیره شد ({n_ok} ردیف تیک‌خورده)."
            elif not n_ok:
                txt = "هیچ ردیفی تیک نخورده است."
            elif do == "preview":
                txt = run(lambda: sl.apply_slugs(False, appr))
            elif do == "apply" and sure:
                txt = run(lambda: sl.apply_slugs(True, appr), yes=True)
            else:
                txt = "اعمال نشد: تیک تأیید را بزنید."
            return self._send(200, slugs_page(txt))
        if path == "/seoreview":
            res = seo_rows()
            if not res:
                return self._send(200, seo_page("پیشنهادی برای بازبینی نیست."))
            rows, mt = res
            st = {}
            for r in rows:
                pid = r["id"]
                st[pid] = {"k": one(f"k_{pid}").strip(), "t": one(f"t_{pid}").strip(), "m": one(f"m_{pid}").strip(), "ok": pid in set(f.get("ok", []))}
            seo_state_file().write_text(json.dumps({"mtime": mt, "rows": st}, ensure_ascii=False), encoding="utf-8")
            appr = pr.PROP / "_panel_seo.csv"
            pr.write_csv(appr, ["id", "new_focus_keyword", "new_seo_title", "new_meta_description"],
                         [[pid, v["k"], v["t"], v["m"]] for pid, v in st.items() if v["ok"]])
            n_ok, do = sum(1 for v in st.values() if v["ok"]), one("do")
            if do == "save":
                txt = f"ذخیره شد ({n_ok} ردیف تیک‌خورده)."
            elif not n_ok:
                txt = "هیچ ردیفی تیک نخورده است."
            elif do == "preview":
                txt = run(lambda: pr.apply_seo(False, appr))
            elif do == "apply" and sure:
                txt = run(lambda: pr.apply_seo(True, appr), yes=True)
            else:
                txt = "اعمال نشد: تیک تأیید را بزنید."
            return self._send(200, seo_page(txt))
        if path == "/seo":
            do = one("do")
            if do == "todo":
                return self._send(200, seo_page(run(pr.seo_todo)))
            ans = one("answer").strip()
            if not ans:
                return self._send(200, seo_page("جوابی وارد نشده است."))
            pr.PROP.mkdir(exist_ok=True)
            (pr.PROP / "seo_answer.csv").write_text(ans + "\n", encoding="utf-8")
            if do == "apply" and not sure:
                return self._send(200, seo_page("اعمال نشد: تیک تأیید را بزنید."))
            txt = run(lambda: pr.apply_seo(do == "apply"), yes=True) if do in ("preview", "apply") else ""
            return self._send(200, seo_page(txt))
        self._send(404, not_found(path))


class Server(HTTPServer):
    def handle_error(self, request, client_address):
        import sys
        if isinstance(sys.exc_info()[1], GONE):     # a closed browser connection is not worth a traceback in the black window
            return
        super().handle_error(request, client_address)


def make_server(port=0):
    return Server(("127.0.0.1", port), Handler)


LOCK_PORT = 29417                  # odd, unregistered, below the OS "ephemeral" ranges; held by the running copy only
PANEL_PORTS = range(2000, 2011)   # the panel's address: http://127.0.0.1:2000/ (next free one if 2000 is taken); loopback only


def configured_port():
    """PANEL_PORT from .env (e.g. PANEL_PORT=2000) -> int, or None when it is not set. Invalid values stop with a clear message."""
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    v = os.environ.get("PANEL_PORT", "").strip()
    if not v:
        return None
    if not v.isdigit() or not 1024 <= int(v) <= 65535 or int(v) == LOCK_PORT:
        raise SystemExit(f"PANEL_PORT in .env must be a number between 1024 and 65535 (and not {LOCK_PORT}); got '{v}'.")
    return int(v)


def _ports():
    """The port from .env alone (predictable address), else the default range starting at 2000."""
    p = configured_port()
    return [p] if p else list(PANEL_PORTS)


def _open_server(ports):
    for port in ports:
        try:
            return make_server(port), port
        except OSError:
            continue
    return None, None


def _acquire_lock():
    import socket
    sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: forbid sharing the port
        sk.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        sk.bind(("127.0.0.1", LOCK_PORT))
        sk.listen(1)
    except OSError:
        sk.close()
        return None
    return sk


def _running_panel():
    """-> (port, version) of an already running panel, or None."""
    import urllib.request
    for port in _ports():
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/ping", timeout=1.5) as r:
                t = r.read().decode("utf-8", "replace").strip()
            if t.startswith("zafaran-panel"):
                return port, t.split()[-1]
        except Exception:
            continue
    return None


def _old_panels():
    """Panels from versions before 12 listened on 8765-8775 and know nothing about the lock; a stale tab or window of
    one of them serves OLD code (this caused confusing 'not found' pages). -> list of ports."""
    import urllib.request
    found = []
    for port in range(8765, 8776):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=0.6) as r:
                if "زعفران ایجنت" in r.read(4000).decode("utf-8", "replace"):
                    found.append(port)
        except Exception:
            continue
    return found


def serve():
    old = _old_panels()
    if old:
        print("WARNING: an OLD panel is still running on port " + ", ".join(map(str, old)) + ".")
        print("Close its black window (and its browser tab) so you do not use outdated code.\n")
    lock = _acquire_lock()
    if lock is None:
        other = _running_panel()
        if other:
            port, ver = other
            url = f"http://127.0.0.1:{port}/"
            print(f"The panel is already running (version {ver}) at {url}")
            print("Not starting a second copy. Opening the running one in your browser.")
            if ver != VERSION:
                print(f"NOTE: it is an OLDER version ({ver}; this folder has {VERSION}). Close its black window, then start again.")
            webbrowser.open(url)
            return
        print(f"Warning: port {LOCK_PORT} is used by another program, so the one-copy check is off.")
    srv, port = _open_server(_ports())
    if srv is None:
        ps = _ports()
        print(f"Port {ps[0]} (PANEL_PORT in .env) is used by another program. Change PANEL_PORT in .env or close that program." if configured_port()
              else f"No free port in {PANEL_PORTS.start}-{PANEL_PORTS.stop - 1}. You can set PANEL_PORT in .env.")
        return
    pf = ROOT / "logs" / "panel.port"
    pf.parent.mkdir(exist_ok=True)
    pf.write_text(str(port), encoding="utf-8")
    url = f"http://127.0.0.1:{port}/"
    print(f"Panel {VERSION} running at {url}  (keep this window open; Ctrl+C to stop)")
    webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nPanel stopped.")
    finally:
        try:
            pf.unlink()
        except OSError:
            pass
