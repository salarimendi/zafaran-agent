"""Local web panel (stdlib only). Runs on 127.0.0.1, protected by a per-session token + Host check.
It is a front-end over the same functions as the menu; every write still shows a preview and needs a confirm tick."""
import builtins
import contextlib
import csv
import html
import io
import json
import re
import secrets
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
.muted{color:#7d7164;font-size:13px}a{color:#8a2a1c}.edit{font-size:12px;margin-right:6px;color:#7d7164}"""
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


NAV = [("/", "خانه"), ("/style", "لحن نوشتار"), ("/seo", "کلمه کلیدی و متا"), ("/links", "لینک‌دهی داخلی"), ("/slugs", "آدرس‌ها (slug)"), ("/inbox", "محصول و مقاله جدید"), ("/reports", "گزارش سئو")]


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
    return buf.getvalue()


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
    }


def page(title, body):
    nav = "".join(f'<a href="{u}">{n}</a>' for u, n in NAV)
    return (f'<!doctype html><html dir="rtl" lang="fa"><head><meta charset="utf-8"><title>{E(title)}</title>'
            f"<style>{font_css()}{CSS}</style></head><body><nav>{nav}</nav><main>{body}</main></body></html>")


def form(action, inner):
    return f'<form method="post" action="{action}"><input type="hidden" name="t" value="{TOKEN}">{inner}</form>'


def out_box(text):
    return f"<h2>خروجی</h2><pre>{E(text)}</pre>" if text else ""


def btn(name, label, cls="", extra=""):
    return f'<button class="{cls}" name="do" value="{name}" {extra}>{E(label)}</button>'


def confirm_box(kind="site"):
    txt = {"ai": "مطمئنم؛ از اعتبار هوش مصنوعی مصرف شود (به سایت چیزی ارسال نمی‌شود)",
           "file": "مطمئنم؛ راهنمای اصلی با این نسخه جایگزین شود (نسخهٔ قبلی نگه داشته می‌شود)"}.get(kind, "مطمئنم، تغییرات روی «سایت» اعمال شود")
    return f'<label><input type="checkbox" name="confirm" value="1"> {txt}</label>'


# ---------- pages ----------
def _mt(p):
    from datetime import datetime
    return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")


def workflow():
    from . import slugs as sl
    K, R = ROOT / "knowledge", ROOT / "reports"
    snap, pack, guide = K / "site" / "snapshot.json", K / "style" / "style_pack.md", K / "style" / "style_guide.md"
    arts = list((K / "articles").glob("*.md"))
    audits = sorted(R.glob("seo_audit_*.csv")) if R.exists() else []
    ans, links = pr.PROP / "seo_answer.csv", sorted(pr.PROP.glob("links_2*.csv"))
    bad = sl.scan() if snap.exists() else None
    I = lambda p, ok: _mt(p) if ok else "هنوز انجام نشده"
    # (number, title, done?, info, kind, target, prerequisites)
    return [
        (1, "بررسی اتصال به سایت", None, "هر زمان", "run", "check", []),
        (2, "همگام‌سازی سایت (دسته‌ها، محصولات، فهرست مقاله‌ها)", snap.exists(), I(snap, snap.exists()), "run", "sync", []),
        (3, "خروجی مقاله‌ها (پایهٔ تحلیل و لینک‌دهی)", bool(arts), f"{len(arts)} مقاله" if arts else "هنوز انجام نشده", "run", "export", [2]),
        (4, "گزارش سئو", bool(audits), I(audits[-1], True) if audits else "هنوز انجام نشده", "run", "audit", [3]),
        (5, "بستهٔ لحن (آمار نوشتار شما)", pack.exists(), I(pack, pack.exists()), "run", "style", [3]),
        (6, "راهنمای لحن (ساخت با AI و ویرایش دستی)", guide.exists(), I(guide, guide.exists()), "page", "/style", [5]),
        (7, "کلمه کلیدی، عنوان سئو و متا", ans.exists(), I(ans, ans.exists()) + " (آخرین پیشنهاد)" if ans.exists() else "هر زمان", "page", "/seo", [3]),
        (8, "لینک‌دهی داخلی", bool(links), I(links[-1], True) + " (آخرین پیشنهاد)" if links else "هر زمان", "page", "/links", [3]),
        (9, "آدرس‌های (slug) فارسی", None if bad is None else not bad,
         "ابتدا مرحلهٔ ۲" if bad is None else (f"{len(bad)} مورد فارسی مانده" if bad else "همه لاتین ✓"), "page", "/slugs", [2]),
        (10, "محصول و مقالهٔ جدید", None, "هر زمان", "page", "/inbox", [2]),
    ]


def home(output=""):
    wf = workflow()
    done = {n: d for n, _, d, *_ in wf}
    nxt = next((n for n in (2, 3, 4, 5, 6) if not done[n]), None)
    rows = []
    for n, title, d, info, kind, target, need in wf:
        miss = [x for x in need if not done[x]]
        mark = "✓" if d else ("○" if d is False else "·")
        if miss:
            act = f"<span class='muted'>اول مرحلهٔ {'، '.join(map(str, miss))}</span>"
        elif kind == "run":
            act = form("/run", f'<input type="hidden" name="action" value="{target}">' + btn("x", "اجرا", "alt")).replace("<form", '<form style="display:inline"')
        else:
            act = f'<a href="{target}"><button type="button">باز کردن</button></a>'
        bg = ' style="background:#fff3cd"' if n == nxt else ""
        rows.append(f"<tr{bg}><td><b>{n}</b></td><td>{mark}</td><td>{E(title)}" + (" <b>← مرحلهٔ بعدی پیشنهادی</b>" if n == nxt else "")
                    + f"</td><td class='muted'>{E(str(info))}</td><td>{act}</td></tr>")
    body = ("<h1>زعفران ایجنت</h1><p class='muted'>این پنل فقط روی همین کامپیوتر باز است. هر تغییر روی سایت قبلش پیش‌نمایش دارد و تیک تأیید می‌خواهد. "
            "مسیر کار: مراحل ۲ تا ۶ را به ترتیب انجام دهید (یک بار و بعد هر چند هفته)؛ مراحل ۷ تا ۱۰ را هر وقت لازم بود.</p>"
            "<table><tr><th>#</th><th></th><th>مرحله</th><th>وضعیت</th><th>اقدام</th></tr>" + "".join(rows) + "</table>"
            "<div class='box'><b>ابزارهای دیگر:</b> "
            + form("/run", '<input type="hidden" name="action" value="diagnose">' + btn("x", "عیب‌یابی اتصال", "alt")).replace("<form", '<form style="display:inline"')
            + form("/run", '<input type="hidden" name="action" value="ai_test">' + btn("x", "تست اتصال AI", "alt")).replace("<form", '<form style="display:inline"')
            + ' &nbsp; <a href="/terms">دستهٔ یا برچسب جدید بسازم ←</a></div>')
    return page("خانه", body + out_box(output))


def style_page(output=""):
    from . import style
    pack, guide, aig = style.OUT / "style_pack.md", style.OUT / "style_guide.md", style.OUT / "style_guide_ai.md"
    body = ("<h1>لحن نوشتار</h1>"
            "<div class='box'><b>فرق «بسته» و «راهنما» چیست؟</b><br>"
            "<b>بستهٔ لحن</b> = داده‌های خام: آمار و نمونه‌های واقعی از مقاله‌های خودتان (بدون هوش مصنوعی ساخته می‌شود؛ بزرگ و پرنویز است).<br>"
            "<b>راهنمای لحن</b> = خلاصهٔ یک‌صفحه‌ای دستورهایی که از آن داده‌ها نتیجه می‌شود؛ نویسنده (AI) هر بار فقط همین را می‌خواند، چون فرستادن کل بسته گران است و دقت را کم می‌کند.</div>"
            "<div class='box'>" + form("/run", '<input type="hidden" name="action" value="style">' + btn("x", "۱) ساخت/به‌روزرسانی بستهٔ لحن", "alt")).replace("<form", '<form style="display:inline"')
            + form("/run", '<input type="hidden" name="action" value="ai_style">' + confirm_box("ai") + btn("x", "۲) پیش‌نویس راهنما با AI (جایگزین راهنمای اصلی نمی‌شود)", "alt")).replace("<form", '<form style="display:inline"')
            + "</div>" + out_box(output))
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
                 "<p class='muted'>slug فقط باید حروف کوچک لاتین، عدد و خط تیره باشد (کادر سبز = معتبر).</p><table><tr><th>تأیید</th><th>عنوان</th><th>slug فعلی</th><th>slug پیشنهادی (قابل ویرایش)</th></tr>"
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
                   f'<td>{sent}</td><td>{link(r["target_url"], r["target_title"])}</td><td>{E(r.get("score", ""))}</td>{ai_td(r)}</tr>')
    inner = (f'<input type="hidden" name="file" value="{E(f.name)}">'
             f"<p class='muted'>{E(f.name)} - {len(rows)} پیشنهاد. روی عنوان‌ها بزنید تا مقاله در تب جدید باز شود. فقط لینک‌های طبیعی و مرتبط را تیک بزنید.</p>"
             "<table><tr><th>تأیید</th><th>مقالهٔ مبدأ (لینک در آن گذاشته می‌شود)</th><th>جمله (عبارت لینک‌شونده زرد است)</th>"
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
             "<table><tr><th>تأیید</th><th>مقاله و مقدار فعلی</th><th>پیشنهاد (قابل ویرایش)</th></tr>" + "".join(trs) + "</table>"
             + btn("save", "ذخیرهٔ ویرایش‌ها", "alt") + btn("preview", "پیش‌نمایش اعمال")
             + "<hr>" + confirm_box() + btn("apply", "اعمال تیک‌خورده‌ها در Yoast", "warn")) + SEO_JS
    return "<h2>مرحلهٔ ۲ - بازبینی، ویرایش و اعمال در سایت</h2>" + form("/seoreview", inner)


def norm_(s):
    from .common import norm
    return norm(s)


def ai_problems(r, others):
    from . import ai
    return ai._problems(r, others) if all(r.values()) else ["یکی از فیلدها خالی است"]


def seo_page(output=""):
    body = ("<h1>کلمه کلیدی، عنوان سئو و متا</h1><div class='box'><b>مرحلهٔ ۱ - پیشنهاد هوش مصنوعی</b> (فقط پیشنهاد می‌سازد و اعتبار AI مصرف می‌کند؛ در سایت هیچ تغییری نمی‌دهد)<br>"
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
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, text):
        b = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _host_ok(self):
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def do_GET(self):
        try:
            self._get()
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
        pages = {"/": home, "/style": style_page, "/slugs": slugs_page, "/terms": terms_page, "/links": links_page, "/seo": seo_page, "/inbox": inbox_page, "/reports": reports_page}
        fn = pages.get(urlparse(self.path).path)
        self._send(200, fn()) if fn else self._send(404, "not found")

    def do_POST(self):
        try:
            self._post()
        except Exception as e:  # never show a traceback; tell the user what happened
            self._send(500, page('خطا', f"<h1>خطا</h1><pre>{E(type(e).__name__ + ': ' + str(e))}</pre><p><a href='/'>بازگشت</a></p>"))

    def _post(self):
        if not self._host_ok():
            return self._send(403, "forbidden")
        n = int(self.headers.get("Content-Length", 0))
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
                    "audit": reports_page}.get(one("action"), home)
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
        self._send(404, "not found")


def make_server(port=0):
    return HTTPServer(("127.0.0.1", port), Handler)


def serve():
    for port in range(8765, 8776):
        try:
            srv = make_server(port)
            break
        except OSError:
            continue
    else:
        print("No free port between 8765 and 8775.")
        return
    url = f"http://127.0.0.1:{port}/"
    print(f"Panel running at {url}  (keep this window open; Ctrl+C to stop)")
    webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nPanel stopped.")
