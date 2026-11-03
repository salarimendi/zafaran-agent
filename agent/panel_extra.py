"""Panel pages added after stage 1: the fix center (/healthfix) and the Search Console import (/gsc).
Imported lazily by panel.py (so this file may import panel at call time)."""
import json
from datetime import datetime

FLASH = {}


def parse_multipart(ctype, raw):
    """-> (fields {name: [values]}, files [(filename, bytes)]) using only the standard library."""
    from email.parser import BytesParser
    from email.policy import HTTP
    msg = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + ctype.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + raw)
    fields, files = {}, []
    for part in msg.iter_parts():
        name, fn = part.get_param("name", header="content-disposition"), part.get_filename()
        data = part.get_payload(decode=True) or b""
        if fn:
            if data:
                files.append((fn, data))
        elif name:
            fields.setdefault(name, []).append(data.decode("utf-8", "replace"))
    return fields, files


def _P():
    from . import panel
    return panel


def _inline(form_html):
    return form_html.replace("<form", '<form style="display:inline"')


COUNTER_JS = ("<script>function cc(el){var s=el.parentNode.querySelector('.cc');if(s){s.textContent=el.value.length+' کاراکتر';"
              "var mx=+el.dataset.max,mn=+el.dataset.min||0;s.style.color=(mx&&el.value.length>mx)||el.value.length<mn?'#b3261e':'#1b6e3c'}}"
              "document.addEventListener('DOMContentLoaded',function(){document.querySelectorAll('input[data-max],textarea[data-max]').forEach(cc)})</script>")


# ---------------------------------------------------------------- fix center
def fix_page(g, output=""):
    P = _P()
    from . import healthfix as hf
    E, form, btn = P.E, P.form, P.btn
    if g not in hf.GROUPS:
        return P.page("خطا", "<h1>گروه ناشناخته</h1><p><a href='/health'>بازگشت به سلامت سایت</a></p>")
    title, codes, ai = hf.GROUPS[g]
    job = P.JOBS.get("hf_" + g)
    running = bool(job and job["running"])
    head = '<meta http-equiv="refresh" content="3">' if running else ""
    from . import health as hl
    body = f"<p><a href='/health'>← بازگشت به سلامت سایت</a></p><h1>اصلاح: {E(title)}</h1>"
    for c in codes:
        mode, why, how = hf.GUIDE[c]
        lab, col = hf.MODE_LABEL[mode]
        body += (f"<div class='box'><b>{E(hl.HELP.get(c, c))}</b> <span style='color:{col};font-weight:700'>[{lab}]</span><br>"
                 f"<span class='muted'>چرا مهم است:</span> {E(why)}<br><span class='muted'>چه کنم:</span> {E(how)}</div>")
    if running:
        body += "<div class='box'>⏳ در حال ساخت...</div>"
    else:
        if ai:
            body += ("<div class='box'><b>این گروه از AI استفاده می‌کند.</b> اول برآورد هزینه را ببینید (چیزی ارسال نمی‌شود):<br>"
                     + _inline(form("/hfbuild", f'<input type="hidden" name="g" value="{g}">' + btn("est", "برآورد هزینه", "alt")))
                     + _inline(form("/hfbuild", f'<input type="hidden" name="g" value="{g}">' + P.confirm_box("ai") + btn("ai", "ساخت پیشنهاد با AI")))
                     + "<br><span class='muted'>AI فقط پیشنهاد می‌دهد. چیزی روی سایت تغییر نمی‌کند تا شما بازبینی و تأیید کنید.</span></div>")
        else:
            body += ("<div class='box'>" + _inline(form("/hfbuild", f'<input type="hidden" name="g" value="{g}">' + btn("build", "ساخت فهرست (فقط می‌خواند)")))
                     + " <span class='muted'>فهرست از آخرین «سلامت سایت» ساخته می‌شود؛ به سایت چیزی نوشته نمی‌شود.</span></div>")
    if job and job["error"]:
        body += f"<div class='box' style='color:#b3261e'>خطا: {E(job['error'])}</div>"
    data = hf.load(g)
    if data:
        body += _items(g, data, ai)
    elif not running:
        body += "<p class='muted'>هنوز فهرستی ساخته نشده است.</p>"
    return P.page("اصلاح: " + title, body + P.out_box(output) + COUNTER_JS, head)


def _src_links(P, srcs):
    out = []
    for s in srcs:
        if s.get("theme"):
            out.append("<span class='muted'>منو/قالب (دستی)</span>")
        else:
            out.append(P.link(s["url"], s["title"]) + P.edit_link(s["url"], s["id"]))
    return "<br>".join(out)


def _items(g, data, ai):
    P = _P()
    E, form, btn = P.E, P.form, P.btn
    items = data["items"]
    if not items:
        return "<p>✓ موردی برای اصلاح نیست.</p>"
    rows = []
    for i, it in enumerate(items):
        dis = it.get("theme") or (g == "noindex" and "id" not in it) or (g in ("seo", "short") and not any(
            v["new"] for v in it["fields"].values()))
        chk = "" if dis else f"<input type='checkbox' name='ok' value='{i}' {'checked' if it.get('default_on') else ''}>"
        if g == "links":
            mid = f"{E(it['old'])}<br>← <b>{E(it['to'])}</b>" + (f"<br><span style='color:#b3261e'>{E(it['risky'])}</span>" if it["risky"] else "")
            mid += f"</td><td>{_src_links(P, it['from'])}"
        elif g == "broken":
            mid = (f"{E(it['old'])}</td><td>{_src_links(P, it['from'])}</td><td>"
                   f"<select name='act_{i}'><option value='remove'>حذف لینک (متن می‌ماند)</option><option value='replace'>جایگزین با آدرس زیر</option>"
                   f"<option value='skip'>کاری نکن</option></select><br><input name='new_{i}' placeholder='https://...' style='width:95%;direction:ltr'>")
        elif g == "alt":
            if it.get("theme"):
                mid = f"{P.link(it['url'], it['title'])}</td><td class='muted'>{E(it['note'])}"
            else:
                mid = (f"{P.link(it['url'], it['title'])}{P.edit_link(it['url'], it['id'])}</td><td>"
                       f"Alt پایه: <input name='alt_{i}' value='{E(it['alt'])}' style='width:80%'>")
        elif g == "mixed":
            mid = f"{P.link(it['url'], it['title'])}" + (f"</td><td class='muted'>{E(it.get('note', ''))}" if it.get("theme") else "</td><td>")
        elif g == "noindex":
            col = {"ok": "#1b6e3c", "problem": "#b3261e", "check": "#a65f00"}[it["verdict"]]
            lab = {"ok": "✓ درست است", "problem": "⚠ باید index شود", "check": "؟ بررسی کنید"}[it["verdict"]]
            mid = (f"{P.link(it['url'], it['title'])}{P.edit_link(it['url'], it['id']) if 'id' in it else ''}</td><td>"
                   f"<b style='color:{col}'>{lab}</b> <span class='muted'>{E(it['why'])}</span>"
                   + (f"<br><span class='muted'>{E(it['note'])}</span>" if it.get("note") else ""))
        else:  # seo / short
            cells = []
            for f, v in it["fields"].items():
                lab = {"k": "کلمه کلیدی", "t": "عنوان سئو", "m": "متا", "short_description": "توضیح کوتاه"}[f]
                mx = {"t": 60, "m": 160}.get(f, 0)
                mn = {"m": 110}.get(f, 0)
                old = f"<div class='muted'>فعلی: {E(v['old'])}</div>" if v["old"] else ""
                if f in ("m", "short_description"):
                    inp = f"<textarea name='f_{i}_{f}' rows='3' style='min-height:60px;direction:rtl' data-max='{mx}' data-min='{mn}' oninput='cc(this)'>{E(v['new'])}</textarea>"
                else:
                    inp = f"<input name='f_{i}_{f}' value='{E(v['new'])}' style='width:95%' data-max='{mx}' data-min='{mn}' oninput='cc(this)'>"
                cells.append(f"<div><b>{lab}</b>{old}{inp}<span class='cc muted'></span></div>")
            mid = (f"{P.link(it['url'], it['title'])}{P.edit_link(it['url'], it['id'])}</td><td>" + "".join(cells)
                   + (f"<div style='color:#a65f00'>⚠ {E(it['notes'])}</div>" if it.get("notes") else ""))
        rows.append(f"<tr><td>{chk}</td><td>{mid}</td></tr>")
    hdr = {"links": "<th>" + P.selall() + "</th><th>لینک ← آدرس نهایی</th><th>در کدام صفحه‌ها</th>", "broken": "<th>" + P.selall() + "</th><th>لینک شکسته</th><th>در کدام صفحه‌ها</th><th>چه شود؟</th>",
           "alt": "<th>" + P.selall() + "</th><th>صفحه</th><th>متن Alt</th>", "mixed": "<th>" + P.selall() + "</th><th>صفحه</th><th></th>", "noindex": "<th>" + P.selall() + "</th><th>صفحه</th><th>تشخیص</th>",
           "seo": "<th>" + P.selall() + "</th><th>صفحه</th><th>پیشنهاد (قابل ویرایش)</th>", "short": "<th>" + P.selall() + "</th><th>محصول</th><th>پیشنهاد (قابل ویرایش)</th>"}[g]
    built = E(data["built"][:16].replace("T", " "))
    return (f"<h2>{len(items)} مورد <span class='muted'>(ساخته‌شده {built})</span></h2>"
            + form("/hfapply", f'<input type="hidden" name="g" value="{g}"><table><tr>{hdr}</tr>' + "".join(rows) + "</table>"
                   + "<div class='box'>" + P.confirm_box("site") + "<br>" + btn("preview", "پیش‌نمایش (چیزی تغییر نمی‌کند)", "alt")
                   + btn("apply", "اعمال روی سایت", "warn") + "<br><span class='muted'>فقط ردیف‌های تیک‌خورده. قبل از هر تغییر نسخهٔ قبلی در پوشهٔ backups\\health ذخیره می‌شود.</span></div>"))


def parse_edits(f, n):
    """form fields -> (selected set, {idx: {field: value}})"""
    sel = {int(x) for x in f.get("ok", []) if x.isdigit()}
    ed = {}
    for k, v in f.items():
        val = v[0] if v else ""
        for pre, name in (("act_", "action"), ("new_", "new"), ("alt_", "alt")):
            if k.startswith(pre) and k[len(pre):].isdigit():
                ed.setdefault(int(k[len(pre):]), {})[name] = val
        if k.startswith("f_"):
            _, i, fld = k.split("_", 2)
            if i.isdigit():
                ed.setdefault(int(i), {})[fld] = val
    return sel, ed


# ---------------------------------------------------------------- Search Console
def gsc_page(output="", min_impr=10):
    P = _P()
    from . import gsc
    E, form, btn, link = P.E, P.form, P.btn, P.link
    flash = FLASH.pop("gsc", "")
    body = ("<h1>Search Console</h1><p class='muted'>بدون حساب سرویس و بدون Google Cloud: خروجی خودِ Search Console را اینجا می‌دهید و من تحلیل می‌کنم. "
            "هیچ‌چیز روی سایت تغییر نمی‌کند و AI مصرف نمی‌شود.</p>")
    if flash:
        body += f"<div class='box' style='border-color:#1b6e3c'>✓ {E(flash)}</div>"
    body += ("<div class='box' style='line-height:2.1'><b>چه چیزی از Search Console بگیرم؟</b><ol>"
             "<li>در <b>search.google.com/search-console</b> وارد سایت شوید و از منوی کنار <b>Performance</b> (عملکرد) ← <b>Search results</b> (نتایج جستجو) را بزنید.</li>"
             "<li>بالای صفحه روی <b>Date</b> (تاریخ) بزنید و <b>Last 16 months</b> (۱۶ ماه گذشته) را انتخاب کنید ← Apply. "
             "این بیشترین بازه‌ای است که گوگل نگه می‌دارد؛ برای بار اول همین را بگیرید.</li>"
             "<li>بالا سمت راست <b>Export</b> (خروجی) ← <b>Download CSV</b>. یک فایل <b>zip</b> دانلود می‌شود "
             "(گزینهٔ Excel یا Google Sheets را نزنید).</li>"
             "<li>همان zip را <b>باز نکنید</b>؛ یکی از دو کار: ◂ پایین «انتخاب فایل» و «وارد کن»، یا ◂ فایل را در پوشهٔ "
             f"<code style='direction:ltr;display:inline-block'>{E(str(gsc.INBOX))}</code> بگذارید و «پردازش پوشه» را بزنید.</li></ol>"
             "<b>دفعه‌های بعد (هر ۴ تا ۶ هفته):</b> دوباره همین کار، با <u>همان بازه</u> (۱۶ ماه). با دو خروجی هم‌بازه، افت و رشد را هم نشان می‌دهم. "
             "اگر زبان Search Console فارسی است، همین کار با نام‌های فارسی منو انجام می‌شود؛ فایل را از روی محتوا تشخیص می‌دهم.</div>")
    body += ("<div class='box'><form method='post' action='/gsc' enctype='multipart/form-data'>"
             f"<input type='hidden' name='t' value='{P.TOKEN}'><input type='file' name='file' accept='.zip,.csv' multiple> "
             + btn("upload", "وارد کن") + "</form>"
             + _inline(form("/gsc", btn("inbox", "پردازش پوشهٔ inbox\\searchconsole", "alt"))) + "</div>")
    files = gsc.saved()
    if not files:
        return P.page("Search Console", body + P.out_box(output))
    cur = gsc.load(files[-1])
    prev = gsc.load(files[-2]) if len(files) > 1 else None
    res = gsc.analyze(cur, min_impr, prev)
    tot = cur.get("totals", {})
    rng = f"{cur['from']} تا {cur['to']} ({cur['days']} روز)" if cur.get("from") else "بازه مشخص نیست"
    body += (f"<h2>آخرین خروجی: {E(cur['imported'][:16].replace('T', ' '))}</h2><div class='box'>بازه: <b>{E(rng)}</b> &nbsp; "
             f"کلیک: <b>{tot.get('clicks', 0):,}</b> &nbsp; نمایش: <b>{tot.get('impr', 0):,}</b> &nbsp; "
             f"عبارت‌ها: {len(cur.get('queries', []))} &nbsp; صفحه‌ها: {len(cur.get('pages', []))}<br>"
             f"<span class='muted'>سهم کلیک عبارت‌های برند ({E('، '.join(res['brand']['terms']))}): {round(res['brand']['share'] * 100)}٪ — "
             f"برای تغییر، BRAND_TERMS را در .env بنویسید. توجه: خروجی خودِ گوگل حداکثر ۱۰۰۰ ردیف در هر جدول دارد.</span></div>")
    body += ("<form method='get' action='/gsc' class='muted'>حداقل نمایش برای فهرست‌ها: "
             f"<input name='min' value='{min_impr}' style='width:60px'> <button class='alt'>اعمال</button></form>")
    if res["compare"]:
        cp = res["compare"]
        body += "<h2>تغییر نسبت به خروجی قبلی</h2>"
        if "note" in cp:
            body += f"<div class='box'>{E(cp['note'])}</div>"
        else:
            for kind, lab in (("queries", "عبارت‌ها"), ("pages", "صفحه‌ها")):
                for k, t in (("up", "رشد"), ("down", "افت")):
                    rows = cp[kind][k][:10]
                    if rows:
                        body += _tbl(f"{lab}: {t}", ["", "تغییر کلیک", "کلیک", "تغییر رتبه"],
                                     [[E(r["key"]), f"{r['d_clicks']:+d}", r["clicks"], "جدید" if r.get("new") else ("" if r["d_pos"] is None else f"{r['d_pos']:+.1f}")] for r in rows])
    sections = [
        ("near_page_one", "فرصت ۱: نزدیک صفحهٔ اول (رتبه ۸ تا ۲۰)", "نمایش دارند ولی کلیک نمی‌گیرند. معمولاً با تقویت همان مقاله (لینک داخلی، تکمیل متن) به صفحهٔ اول می‌رسند."),
        ("quick_wins", "فرصت ۲: رتبه ۴ تا ۷", "با کمی بهبود به سه‌تای اول می‌رسند و کلیکشان چند برابر می‌شود."),
        ("low_ctr_queries", "فرصت ۳: عبارت‌هایی که رتبهٔ خوب دارند ولی کم کلیک می‌گیرند", "یعنی عنوان یا متای صفحه جذاب نیست؛ بازنویسی عنوان/متا ارزان‌ترین اصلاح است (صفحهٔ «کلمه کلیدی و متا»)."),
    ]
    for key, ttl, tip in sections:
        rows = res[key][:25]
        if rows:
            body += _tbl(ttl, ["عبارت", "نمایش", "کلیک", "CTR", "رتبه"], [[E(r["key"]), r["impr"], r["clicks"], f"{r['ctr'] * 100:.1f}٪", r["pos"]] for r in rows], tip)
    pg = lambda r: (link(r["key"], r.get("title") or r["key"]) + (P.edit_link(r["key"], r["id"]) if r.get("id") else ""))
    if res["low_ctr_pages"]:
        body += _tbl("صفحه‌هایی با رتبهٔ خوب و CTR پایین", ["صفحه", "نمایش", "کلیک", "CTR", "رتبه"],
                     [[pg(r), r["impr"], r["clicks"], f"{r['ctr'] * 100:.1f}٪", r["pos"]] for r in res["low_ctr_pages"][:25]], "اول عنوان و متای همین صفحه‌ها را بازنویسی کنید.")
    if res["zero_click_pages"]:
        body += _tbl("صفحه‌هایی که دیده می‌شوند ولی هیچ کلیکی ندارند", ["صفحه", "نمایش", "رتبه"],
                     [[pg(r), r["impr"], r["pos"]] for r in res["zero_click_pages"][:25]])
    body += _tbl("پرکلیک‌ترین عبارت‌ها", ["عبارت", "کلیک", "نمایش", "رتبه"], [[E(r["key"]), r["clicks"], r["impr"], r["pos"]] for r in res["top_queries"][:15]])
    body += _tbl("پرکلیک‌ترین صفحه‌ها", ["صفحه", "کلیک", "نمایش", "رتبه"], [[pg(r), r["clicks"], r["impr"], r["pos"]] for r in res["top_pages"][:15]])
    if res["no_impressions"]:
        body += _tbl(f"منتشرشده‌ها که در این خروجی اصلاً نمایش نداشته‌اند ({len(res['no_impressions'])}+)", ["صفحه"],
                     [[link(r["url"], r["title"])] for r in res["no_impressions"][:25]],
                     "یا تازه‌اند، یا گوگل ایندکسشان نکرده (گروه noindex و Sitemap در «سلامت سایت» را ببینید)، یا خروجی گوگل ۱۰۰۰ ردیف را بریده است.")
    f = gsc.write_report(cur, res)
    body += f"<p class='muted'>فایل کامل فرصت‌ها: reports\\{E(f.name)}</p>"
    return P.page("Search Console", body + P.out_box(output))


def _tbl(title, heads, rows, tip=""):
    t = (f"<h2>{title}</h2>" if title else "") + (f"<p class='muted'>{tip}</p>" if tip else "")
    return (t + "<table><tr>" + "".join(f"<th>{h}</th>" for h in heads) + "</tr>"
            + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) + "</table>")


# ---------------------------------------------------------------- Keywords / content gaps / calendar (stage 3)
def keywords_page(output="", min_impr=10, st="gap"):
    P = _P()
    from . import keywords as kw, occasions, gsc
    from .corpus import read_articles
    from .site import load_snapshot
    E, form, btn, link = P.E, P.form, P.btn, P.link
    body = ("<h1>کلمات کلیدی، شکاف محتوا و تقویم</h1><p class='muted'>همه‌چیز با کد و از داده‌های خودتان (مقاله‌ها، دسته‌ها، خروجی Search Console) ساخته می‌شود؛ "
            "هیچ‌چیز روی سایت تغییر نمی‌کند و AI فقط در یک دکمهٔ جدا و با تأیید شما (برآورد هزینه اول) مصرف می‌شود.</p>")
    res = kw.load_analysis()
    snap, arts, gfiles = load_snapshot(), read_articles(), gsc.saved()
    ok = lambda b: "✓" if b else "○"
    body += (f"<div class='box'><b>ورودی‌ها:</b> {ok(snap)} همگام‌سازی سایت &nbsp; {ok(arts)} خروجی مقاله‌ها ({len(arts)}) &nbsp; "
             f"{ok(gfiles)} Search Console ({len(gfiles)} خروجی؛ <a href='/gsc'>صفحهٔ Search Console</a>)<br>"
             + form("/kwbuild", f"حداقل نمایش برای فهرست‌ها: <input name='min' value='{min_impr}' style='width:60px'> "
                    + btn("x", "ساخت/به‌روزرسانی تحلیل (فقط می‌خواند، بدون AI)")) + "</div>")
    if output:
        body += P.out_box(output)
    if not snap:
        body += "<div class='box'>اول در صفحهٔ خانه «همگام‌سازی سایت» (و بعد «خروجی مقاله‌ها») را اجرا کنید.</div>"
    if res:
        c = {}
        for r in res["bank"]:
            c[r["status"]] = c.get(r["status"], 0) + 1
        body += (f"<p class='muted'>آخرین ساخت: {E(res['built'][:16].replace('T', ' '))}"
                 + (f" · داده Search Console: {E(res['gsc_file'])} ({res.get('gsc_days') or '؟'} روز)" if res["has_gsc"] else " · بدون داده Search Console")
                 + f" · {res['n_docs']} صفحهٔ شناخته‌شده</p>"
                 f"<div class='box'>بانک: <b>{len(res['bank'])}</b> عبارت · پوشش کامل <b>{c.get('covered', 0)}</b> · پوشش ضعیف <b>{c.get('weak', 0)}</b> · "
                 f"شکاف <b>{c.get('gap', 0)}</b> · برند {c.get('brand', 0)}<br>"
                 f"هم‌نوع‌خواری: <b>{len(res['cannibal_focus'])}</b> گروه کلمه کلیدی مشابه، <b>{len(res['cannibal_queries'])}</b> عبارت با چند صفحهٔ رقیب · "
                 f"فایل‌های کامل: reports\\keywords_bank.csv، content_gaps.csv، keywords_cannibal.csv</div>"
                 "<p>پرش: <a href='#gaps'>شکاف محتوا</a> · <a href='#cannibal'>هم‌نوع‌خواری</a> · <a href='#map'>نقشهٔ کلمه←صفحه</a> · "
                 "<a href='#rising'>در حال رشد</a> · <a href='#calendar'>تقویم</a> · <a href='#manual'>کلمات دستی</a></p>")
        body += _kw_gaps(P, res, E, form, btn, link)
        body += _kw_cannibal(res, E, link)
        body += _kw_map(res, E, link, st, min_impr)
        body += _kw_rising(res, E)
    body += _kw_calendar(P, arts, E, link)
    man = kw.MANUAL.read_text(encoding="utf-8") if kw.MANUAL.exists() else ""
    body += ("<h2 id='manual'>کلمات کلیدی دستی</h2><p class='muted'>هر خط یک عبارت که می‌خواهید در بانک و بررسی شکاف دیده شود (مثلاً از ابزار رقبا یا ذهن خودتان). "
             "این فقط یک فایل محلی است (knowledge\\keywords\\manual.txt)؛ بعد از ذخیره «ساخت/به‌روزرسانی تحلیل» را بزنید.</p>"
             + form("/kwsave", f"<textarea name='manual' style='direction:rtl;min-height:120px'>{E(man)}</textarea><br>" + btn("save", "ذخیره", "alt")))
    return P.page("کلمات کلیدی", body)


def _kw_gaps(P, res, E, form, btn, link):
    from . import keywords as kw
    t = ("<h2 id='gaps'>شکاف محتوا (عبارت‌هایی که نمایش می‌گیرند ولی صفحه‌ای برایشان نیست)</h2>"
         "<p class='muted'>عبارت‌های Search Console که هیچ مقاله/محصول/دسته‌ای بیش از نصف آن‌ها را پوشش نمی‌دهد، با کد دسته‌بندی شده‌اند. "
         "هر ردیف یک فرصت: مقاله یا دستهٔ جدید. «نیت» یعنی جویندهٔ خرید است یا اطلاعات.</p>")
    cl = res["gap_clusters"]
    if not res["has_gsc"]:
        t += "<div class='box'>برای این بخش خروجی Search Console لازم است (<a href='/gsc'>راهنما</a>).</div>"
    elif not cl:
        t += "<p>✓ عبارتی بدون صفحه پیدا نشد.</p>"
    else:
        t += _tbl("", ["موضوع (پرنمایش‌ترین عبارت)", "نمایش", "کلیک", "رتبه", "نیت", "پیشنهاد", "عبارت‌های دیگر"],
                          [[E(c["label"]), c["impr"], c["clicks"], c["pos"], kw.INTENT_FA[c["intent"]], E(c["suggest"]),
                            "<span class='muted'>" + E("؛ ".join(q["key"] for q in c["queries"][1:5])) + (f" (+{c['n'] - 5})" if c["n"] > 5 else "") + "</span>"] for c in cl[:40]])
        from . import panel_write as pw
        t += "<details class='box'><summary><b>ارسال خوشه‌های شکاف به «تولید مقاله»</b> (تیک بزنید؛ عنوان را بعداً ویرایش می‌کنید)</summary>" + pw.idea_form(
            P, [(c["label"], f"نمایش {c['impr']}؛ عبارت‌های مرتبط: " + "، ".join(q["key"] for q in c["queries"][:4])) for c in cl[:40]], "gap") + "</details>"
        t += ("<div class='box'><b>خوشه‌بندی معنایی و پیشنهاد موضوع با AI</b> (یک تماس دسته‌ای؛ فقط ایده، چیزی روی سایت تغییر نمی‌کند):<br>"
              + _inline(form("/run", '<input type="hidden" name="action" value="ai_gap_est">' + btn("x", "برآورد هزینه", "alt")))
              + _inline(form("/run", '<input type="hidden" name="action" value="ai_gap">' + P.confirm_box("ai") + btn("x", "پیشنهاد موضوع با AI")))
              + "</div>")
    ai = kw.KW / "topics_ai.json"
    if ai.exists():
        try:
            d = json.loads(ai.read_text(encoding="utf-8"))
        except ValueError:
            d = None
        if d and d.get("items"):
            TY = {"article": "مقاله", "category": "دستهٔ محصول", "product": "محصول"}
            t += _tbl(f"پیشنهاد AI ({E(d['built'][:16].replace('T', ' '))}، هزینه ${d.get('cost', 0)})", ["نوع", "عنوان پیشنهادی", "کلمه کلیدی", "نمایش", "از عبارت‌های", "هشدار"],
                      [[TY.get(i["type"], E(str(i["type"]))), E(i["t"]), E(i["k"]), i["impr"], "<span class='muted'>" + E("؛ ".join(i["queries"][:3])) + "</span>",
                        f"<span style='color:#a65f00'>{E(i['notes'])}</span>" if i["notes"] else "✓"] for i in d["items"]],
                      "این‌ها فقط ایده‌اند. با تیک‌زدن و ارسال به «تولید مقاله»، AI طبق لحن شما بریف و متن می‌نویسد و پیش‌نویس در وردپرس ساخته می‌شود.")
            from . import panel_write as pw
            t += pw.idea_form(P, [(i["t"], f"کلمه کلیدی پیشنهادی: {i['k']}") for i in d["items"] if i["type"] == "article"], "ai_topic")
    if res["cats_without_article"]:
        t += _tbl("دستهٔ محصولی که هیچ مقالهٔ پشتیبانی ندارد", ["دسته", "تعداد محصول"],
                  [[link(x["url"], x["name"]) if x["url"] else E(x["name"]), x["count"]] for x in res["cats_without_article"][:30]],
                  "مقالهٔ راهنما برای این دسته‌ها هم ترافیک اطلاعاتی می‌آورد و هم بعداً در مرحلهٔ لینک‌دهی به صفحهٔ دسته لینک می‌دهد.")
    if res["gap_manual"]:
        t += _tbl("کلمات دستی شما که پوشش کامل ندارند", ["عبارت", "امتیاز پوشش"], [[E(r["key"]), f"{round(r['score'] * 100)}٪"] for r in res["gap_manual"]])
    return t


def _kw_cannibal(res, E, link):
    t = ("<h2 id='cannibal'>هم‌نوع‌خواری (چند صفحه برای یک موضوع)</h2>"
         "<p class='muted'>وقتی دو مقاله برای یک عبارت می‌جنگند، گوگل هر بار یکی را نشان می‌دهد و هیچ‌کدام رتبهٔ خوب نمی‌گیرد. معمولاً درمان: یکی را تقویت و دیگری را به آن لینک یا ادغام کنید، "
         "یا کلمه کلیدی یکی را عوض کنید (صفحهٔ «کلمه کلیدی و متا»). توجه: خروجی Search Console «کدام صفحه برای کدام عبارت» را نمی‌دهد، پس بخش دوم بر اساس موضوع صفحه‌ها حدس می‌زند، نه آمار قطعی.</p>")
    g = res["cannibal_focus"]
    if g:
        t += _tbl("کلمه کلیدی یکسان یا بسیار شبیه بین مقاله‌ها", ["شدت", "کلمه کلیدی", "مقاله‌ها"],
                  [["<b style='color:#b3261e'>یکسان</b>" if x["exact"] else "<b style='color:#a65f00'>شبیه</b>", E(x["label"]),
                    "<br>".join(link(p["url"], p["title"]) + f" <span class='muted'>({E(p['focus'])})</span>" for p in x["pages"])] for x in g])
    else:
        t += "<p>✓ دو مقاله کلمه کلیدی یکسان یا بسیار شبیه ندارند.</p>"
    q = res["cannibal_queries"]
    if q:
        t += _tbl("عبارت‌هایی که چند صفحه دربارهٔ آن‌اند", ["عبارت", "نمایش", "رتبه", "صفحه‌های رقیب"],
                  [[E(x["query"]), x["impr"], x["pos"], "<br>".join(link(p["url"], p["title"]) + f" <span class='muted'>({kwk(p['kind'])})</span>" for p in x["pages"])] for x in q[:30]])
    return t


def kwk(kind):
    from . import keywords as kw
    return kw.KIND_FA.get(kind, kind)


def _kw_map(res, E, link, st, min_impr):
    from . import keywords as kw
    ST = {"covered": ("پوشش کامل", "#1b6e3c"), "weak": ("پوشش ضعیف", "#a65f00"), "gap": ("شکاف", "#b3261e"), "brand": ("برند", "#6b5b4b")}
    flt = {"gap": lambda r: r["status"] == "gap", "weak": lambda r: r["status"] == "weak", "covered": lambda r: r["status"] == "covered",
           "mismatch": lambda r: r["mismatch"], "all": lambda r: True}.get(st, lambda r: r["status"] == "gap")
    rows = [r for r in res["bank"] if flt(r)]
    nav = " · ".join(f"<b>{lab}</b>" if k == st else f"<a href='/keywords?st={k}&min={min_impr}#map'>{lab}</a>"
                     for k, lab in (("gap", "شکاف"), ("weak", "ضعیف"), ("covered", "پوشش کامل"), ("mismatch", "ناهماهنگ با نیت"), ("all", "همه")))
    t = (f"<h2 id='map'>نقشهٔ کلمه ← صفحهٔ هدف ({len(rows)} مورد)</h2><p class='muted'>برای هر کلمه، مناسب‌ترین صفحه (بر اساس پوشش واژه‌ها). {nav}<br>"
         "«ناهماهنگ با نیت»: جویندهٔ خرید به مقاله می‌رسد یا جویندهٔ اطلاعات به صفحهٔ محصول. ۸۰ ردیف اول؛ فایل کامل: reports\\keywords_bank.csv</p>")
    t += _tbl("", ["کلمه", "منبع", "نمایش", "کلیک", "رتبه", "نیت", "صفحهٔ هدف", "پوشش"],
              [[E(r["key"]), E("+".join({"gsc": "GSC", "focus": "کلمه کلیدی", "term": "دسته/برچسب", "manual": "دستی"}.get(s, s) for s in r["sources"])), r["impr"] or "", r["clicks"] or "",
                r["pos"] or "", kw.INTENT_FA[r["intent"]] + (" ⚠" if r["mismatch"] else ""),
                (("<span class='muted'>نزدیک‌ترین (ضعیف): </span>" if r["status"] == "gap" else "") + link(r["target"]["url"], r["target"]["title"])
                 + f" <span class='muted'>({kwk(r['target']['kind'])})</span>") if r["target"] else "—",
                f"<b style='color:{ST[r['status']][1]}'>{ST[r['status']][0]}</b> {round(r['score'] * 100)}٪"] for r in rows[:80]])
    return t


def _kw_rising(res, E):
    rs = res["rising"]
    t = "<h2 id='rising'>عبارت‌های در حال رشد (نسخهٔ سبک «رادار ترند»)</h2>"
    if rs["note"]:
        return t + f"<p class='muted'>{E(rs['note'])}</p>"
    if not rs["rows"]:
        return t + "<p class='muted'>نسبت به خروجی قبلی رشد قابل‌توجهی دیده نشد.</p>"
    return t + _tbl("", ["عبارت", "نمایش قبلی", "نمایش فعلی", "رشد", "رتبه"],
                    [[E(r["key"]) + (" <b>(جدید)</b>" if r["new"] else ""), r["before"], r["impr"], f"+{r['gain']}", r["pos"]] for r in rs["rows"][:25]],
                    "عبارتی که جست‌وجویش بالا می‌رود؛ اگر صفحه‌ای برایش ندارید در بخش شکاف هم آمده است.")


def _kw_calendar(P, arts, E, link):
    from . import occasions as oc
    ST = {"late": ("⚠ باید زودتر منتشر می‌شد؛ همین حالا", "#b3261e"), "soon": ("الان وقت شروع است", "#a65f00"), "later": ("هنوز زود است", "#6b5b4b")}
    try:
        rows = oc.calendar(arts or None)
    except ValueError as e:
        return f"<h2 id='calendar'>تقویم محتوا</h2><div class='box' style='color:#b3261e'>{E(str(e))}</div>"
    t = (f"<h2 id='calendar'>تقویم محتوا (یک سال آینده)</h2><p class='muted'>مناسبت‌های ثابت از فایل knowledge\\keywords\\occasions.yaml (قابل ویرایش). "
         "«انتشار تا» = تاریخی که مقاله باید منتشر و ایندکس شده باشد. تاریخ‌های قمری تقریبی‌اند (۱ تا ۲ روز جابه‌جایی). "
         "پوشش با کد بررسی می‌شود: عنوان/کلمه کلیدی/تیترهای مقاله‌های منتشرشده.</p>")
    trs = []
    for r in rows:
        lab, col = ST[r["state"]]
        if r["have"] is None:
            cov = "<span class='muted'>بررسی نشد (خروجی مقاله‌ها)</span>"
        elif not r["have"]:
            cov = "<b style='color:#b3261e'>مقاله ندارید</b>"
        else:
            cov = f"{len(r['have'])} مقاله: " + "<br>".join(link(h["url"], h["title"]) for h in r["have"][:3])
            if r["stale_days"] is not None and r["stale_days"] > 300:
                cov += f"<br><span style='color:#a65f00'>آخرین ویرایش {r['stale_days']} روز پیش؛ به‌روزرسانی کنید</span>"
        trs.append([f"<b>{E(r['name'])}</b>", f"{E(r['jalali'])}{' (تقریبی)' if r['approx'] else ''}<br><span class='muted'>{r['date']}</span>", r["days"],
                    f"{E(r['publish_jalali'])}<br><span class='muted'>{r['publish_by']}</span>", f"<b style='color:{col}'>{lab}</b>", cov,
                    "<br>".join("· " + E(x) for x in r["topics"][:3])])
    from . import panel_write as pw
    ideas = [(x, f"مناسبت: {r['name']} ({r['jalali']})") for r in rows for x in r["topics"]]
    return (t + _tbl("", ["مناسبت", "تاریخ", "روز مانده", "انتشار تا", "وضعیت", "پوشش فعلی", "ایدهٔ موضوع"], trs)
            + "<details class='box'><summary><b>ارسال ایده‌های مناسبتی به «تولید مقاله»</b></summary>" + pw.idea_form(P, ideas, "calendar") + "</details>")


# ------------------------------------------------------------------ stage 5: link graph and clusters (/graph)
def graph_page(output=""):
    P = _P()
    from . import linkgraph as lg
    E, form, btn, link, edit = P.E, P.form, P.btn, P.link, P.edit_link
    res = lg.load()
    body = ("<h1>گراف لینک و خوشه‌ها</h1><p class='muted'>فقط می‌خواند و بدون AI و بدون هزینه است. از متن مقاله‌ها و آخرین همگام‌سازی، نقشهٔ لینک‌های داخلی، مقاله‌های یتیم، خوشه‌های موضوعی "
            "(صفحهٔ مادر و مقاله‌های پشتیبان) و لینک مقاله ↔ دسته/محصول ساخته می‌شود. محدودیت: فقط متن مقاله‌ها خوانده می‌شود (لینک‌های خود صفحهٔ محصول/دسته دیده نمی‌شود) و خوشه‌ها از روی عنوان، کلمه کلیدی و تیترها حدس زده می‌شوند، نه آمار جست‌وجو.</p>"
            + form("/graphbuild", btn("build", "ساخت/به‌روزرسانی گراف (فقط می‌خواند)")))
    if output:
        body += P.out_box(output)
    if not res:
        return P.page("گراف لینک", body + "<div class='box'>اول «همگام‌سازی سایت» و «خروجی مقاله‌ها» (صفحهٔ خانه) و بعد «ساخت گراف».</div>")
    c = res["counts"]
    body += (f"<div class='box'>ساخت: {E(res['built'][:16].replace('T', ' '))} · مقاله {c['article']}، محصول {c['product']}، دستهٔ محصول {c['product_cat']}، صفحه {c['page']} · "
             f"<b>{res['edges']}</b> لینک داخلی · <b>{len(res['clusters'])}</b> خوشه · <b style='color:#b3261e'>{len(res['orphans'])}</b> مقالهٔ یتیم · {len(res['dead_ends'])} مقاله بدون لینک خروجی · "
             f"نوع لینک‌ها: {E('، '.join(f'{k} ({v})' for k, v in sorted(res['by_type'].items())))}</div>")
    if not res.get("has_stock"):
        body += "<div class='box' style='border-color:#a65f00'>⚠ موجودی محصولات ذخیره نشده؛ «همگام‌سازی سایت» را دوباره بزنید. تا آن موقع به هیچ محصولی لینک پیشنهاد نمی‌شود.</div>"
    body += ("<div class='box'><b>ساخت پیشنهاد لینک از گراف</b> (هم‌خوان با خوشه‌ها؛ فقط جایی که عبارت طبیعی در متن هست؛ هرگز محصول ناموجود؛ با تیک و پشتیبان در «لینک‌دهی داخلی» اعمال می‌شود)"
             + form("/graphbuild", btn("links", "ساخت پیشنهادها", "alt")) + "</div>")
    if res["oos_linked"]:
        rows = "".join(f"<tr><td>{link(x['url'], x['title'])}</td><td>{E('، '.join(x['from']))}</td></tr>" for x in res["oos_linked"])
        body += f"<h2 style='color:#b3261e'>لینک به محصول ناموجود</h2><p class='muted'>این لینک‌ها را از متن مقاله بردارید.</p><table><tr><th>محصول ناموجود</th><th>لینک‌شده از</th></tr>{rows}</table>"
    row = lambda x: f"<tr><td>{link(x['url'], x['title'])}{edit(x['url'], x['id'])}</td><td>{x['inb']}</td><td>{x['out']}</td></tr>"
    tbl = lambda items: "<table><tr><th>صفحه</th><th>ورودی</th><th>خروجی</th></tr>" + "".join(row(x) for x in items[:60]) + "</table>"
    body += f"<h2>مقاله‌های یتیم ({len(res['orphans'])})</h2>" + (tbl(res["orphans"]) if res["orphans"] else "<p>✓ همه حداقل یک لینک ورودی دارند.</p>")
    body += f"<h2>خوشه‌های موضوعی ({len(res['clusters'])})</h2>"
    for cl in res["clusters"]:
        p = cl["pillar"]
        sup = "".join(f"<tr><td>{link(s['url'], s['title'])}{edit(s['url'], s['id'])}</td><td>{s['sim']}</td><td>{'✓' if s['to_pillar'] else '<b style=color:#b3261e>✗</b>'}</td>"
                      f"<td>{'✓' if s['from_pillar'] else '<b style=color:#a65f00>✗</b>'}</td></tr>" for s in cl["supporters"])
        extra = ""
        if cl["cat"]:
            extra += f"<div class='muted'>دستهٔ محصول مرتبط: {link(cl['cat']['url'], cl['cat']['title'])} — لینک از {cl['cat']['linked_by']} مقالهٔ خوشه</div>"
        for x in cl["products"]:
            extra += f"<div class='muted'>محصول مرتبط (موجود): {link(x['url'], x['title'])} — لینک از {x['linked_by']} مقاله</div>"
        body += (f"<div class='box'><b>صفحهٔ مادر: {link(p['url'], p['title'])}</b>{edit(p['url'], p['id'])} <span class='muted'>(لینک ورودی {p['inb']})</span>"
                 f"<table><tr><th>مقالهٔ پشتیبان</th><th>شباهت</th><th>به مادر لینک دارد</th><th>مادر به آن لینک دارد</th></tr>{sup}</table>{extra}</div>")
    if res["single"]:
        body += f"<details class='box'><summary>مقاله‌های بدون خوشه ({len(res['single'])}؛ موضوع مستقل یا هنوز کم‌همتا)</summary>{tbl(res['single'])}</details>"
    body += (f"<details class='box'><summary>بدون لینک خروجی ({len(res['dead_ends'])})</summary>{tbl(res['dead_ends'])}</details>"
             f"<details class='box'><summary>دسته‌های محصول بدون لینک از مقاله ({len(res['cats_unlinked'])})</summary>{tbl(res['cats_unlinked'])}</details>"
             f"<details class='box'><summary>محصولات موجود بدون لینک از مقاله ({len(res['products_unlinked'])})</summary>{tbl(res['products_unlinked'])}</details>"
             "<p class='muted'>گزارش کامل: reports\\link_graph.md و link_graph_nodes.csv</p>")
    return P.page("گراف لینک", body)
