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
    hdr = {"links": "<th></th><th>لینک ← آدرس نهایی</th><th>در کدام صفحه‌ها</th>", "broken": "<th></th><th>لینک شکسته</th><th>در کدام صفحه‌ها</th><th>چه شود؟</th>",
           "alt": "<th></th><th>صفحه</th><th>متن Alt</th>", "mixed": "<th></th><th>صفحه</th><th></th>", "noindex": "<th></th><th>صفحه</th><th>تشخیص</th>",
           "seo": "<th></th><th>صفحه</th><th>پیشنهاد (قابل ویرایش)</th>", "short": "<th></th><th>محصول</th><th>پیشنهاد (قابل ویرایش)</th>"}[g]
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
    t = f"<h2>{title}</h2>" + (f"<p class='muted'>{tip}</p>" if tip else "")
    return (t + "<table><tr>" + "".join(f"<th>{h}</th>" for h in heads) + "</tr>"
            + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) + "</table>")
