"""Panel pages for the article engine (stage 4): /write (list + new) and /write?job=ID (the step-by-step page).
Imported lazily by panel.py. Every action that touches the site has its own tick; AI actions have an estimate button first."""
from . import articlegen as ag

FLASH = {}
STATE_COL = {"new": "#6b5b4b", "brief": "#1c5d99", "written": "#a65f00", "draft": "#1b6e3c", "published": "#1b6e3c"}


def _P():
    from . import panel
    return panel


def _inline(h):
    return h.replace("<form", '<form style="display:inline"')


def idea_form(P, items, source, label="ارسال تیک‌خورده‌ها به «تولید مقاله»"):
    """items: [(title, note)] -> a ready tick-list form (with Select all) that creates article jobs. Used by the keywords page."""
    E = P.E
    if not items:
        return ""
    rows = "".join(f"<tr><td><input type='checkbox' name='idea' value='{E(t)}||{E(n)}'></td><td>{E(t)}</td><td class='muted'>{E(n)}</td></tr>" for t, n in items)
    return P.form("/writeq", f"<input type='hidden' name='source' value='{E(source)}'><table><tr><th>{P.selall()}</th><th>ایدهٔ عنوان</th><th></th></tr>{rows}</table>"
                  + P.btn("x", label, "alt") + "<span class='muted'> فقط یک پروندهٔ کار ساخته می‌شود؛ AI و سایت دست نمی‌خورد.</span>")


def list_page(output=""):
    P = _P()
    E, form, btn = P.E, P.form, P.btn
    flash = FLASH.pop("w", "")
    body = ("<h1>تولید مقاله</h1><p class='muted'>مسیر: ۱) فقط عنوان را بدهید، AI بریف می‌سازد (دسته، slug، برچسب، کلمهٔ کلیدی، عنوان و متا، فهرست تیترها، پیشنهاد تصویر با پرامپت) "
            "← ۲) شما ویرایش می‌کنید ← ۳) AI متن را مرحله‌به‌مرحله می‌نویسد ← ۴) کد پاک‌سازی و کنترل کیفیت می‌کند و لینک داخلی پیشنهاد می‌دهد ← ۵) پیش‌نویس در وردپرس "
            "← ۶) عکس‌ها را با نام ۰۱، ۰۲ ... می‌گذارید؛ تبدیل به WebP، نام فایل، Alt و Schema خودکار ← ۷) با تأیید شما منتشر می‌شود. هر مقاله فقط پیش‌نویس ساخته می‌شود.</p>")
    if flash:
        body += f"<div class='box' style='border-color:#1b6e3c'>✓ {E(flash)}</div>"
    body += P.out_box(output)
    body += ("<div class='box'><b>مقالهٔ جدید</b>" + form("/write", "<input name='title' placeholder='عنوان مقاله' style='width:70%'> <input name='note' placeholder='یادداشت اختیاری برای AI' style='width:25%'><br>"
                                                         + btn("x", "ساخت پروندهٔ مقاله"))
             + "<span class='muted'>ایده‌های آماده را از صفحهٔ «کلمات کلیدی و تقویم» (شکاف‌ها، پیشنهاد AI، مناسبت‌ها) هم می‌توانید تیک بزنید و بفرستید.</span></div>")
    js = ag.jobs()
    if not js:
        return P.page("تولید مقاله", body + "<p class='muted'>هنوز مقاله‌ای نیست.</p>")
    rows = ""
    for j in js:
        st = j["state"]
        rows += (f"<tr><td><input type='checkbox' name='j' value='{j['id']}' {'disabled' if j.get('wp') else ''}></td><td>{E(j['title'])}</td>"
                 f"<td><b style='color:{STATE_COL[st]}'>{ag.STATES[st]}</b></td><td class='muted'>{E(j['created'][:16].replace('T', ' '))}</td>"
                 f"<td class='muted'>${j.get('cost', 0):.4f}</td><td><a href='/write?job={j['id']}'><button type='button'>باز کردن ←</button></a></td></tr>")
    body += ("<h2>مقاله‌ها</h2>" + form("/writeauto", f"<table><tr><th>{P.selall()}</th><th>عنوان</th><th>وضعیت</th><th>ساخت</th><th>هزینه AI</th><th></th></tr>{rows}</table>"
             "<div class='box'><b>تولید خودکار تا پیش‌نویس برای تیک‌خورده‌ها</b> (بدون بازبینی؛ فقط دستهٔ موجود، برچسب موجود، لینک داخلی کدمحور؛ هیچ دسته/برچسب جدیدی ساخته نمی‌شود؛ "
             "اگر کنترل کیفیت مشکل جدی ببیند پیش‌نویس ساخته نمی‌شود):<br>" + btn("est", "برآورد هزینه", "alt") + "<br>" + P.confirm_box("ai") + "<br>"
             + "<label><input type='checkbox' name='confirm_site' value='1'> مطمئنم؛ پیش‌نویس‌ها در «سایت» ساخته شوند (منتشر نمی‌شوند)</label><br>" + btn("go", "اجرای خودکار", "warn") + "</div>"))
    return P.page("تولید مقاله", body)


def job_page(jid, output=""):
    P = _P()
    E, form, btn = P.E, P.form, P.btn
    from .panel_extra import COUNTER_JS
    j = ag.load(jid)
    if not j:
        return P.page("تولید مقاله", "<h1>این مقاله پیدا نشد</h1><p><a href='/write'>بازگشت</a></p>")
    hid = f"<input type='hidden' name='job' value='{j['id']}'>"
    act = lambda do, label, cls="": btn(do, label, cls)
    body = (f"<p><a href='/write'>← همهٔ مقاله‌ها</a></p><h1>{E(j['title'])}</h1><p class='muted'>وضعیت: <b style='color:{STATE_COL[j['state']]}'>{ag.STATES[j['state']]}</b> · "
            f"هزینهٔ AI تا اینجا: ${j.get('cost', 0):.4f} · شناسه {j['id']}</p>")
    body += _site_box(P, j)
    body += P.out_box(output)
    b = j.get("brief")
    # ---- step 1
    body += "<h2>۱) بریف</h2>"
    if not b:
        body += ("<div class='box'><b>ایده یا عنوان اولیه</b> <span class='muted'>(لازم نیست عنوان نهایی باشد؛ AI آن را با توجه به سایت و کلمه‌های کلیدی بهتر می‌کند)</span><br>"
                 + form("/writeact", hid + "<input name='title' value='" + E(j["title"]) + "' style='width:70%'><br>"
                 + "<label><input type='checkbox' name='existing_only' value='1' checked> فقط دسته و برچسب‌های موجود (ساخت دستهٔ جدید پیشنهاد نشود)</label><br>"
                 + act("brief_est", "برآورد هزینه", "alt") + P.confirm_box("ai") + act("brief", "ساخت بریف با AI"))
                 + "<span class='muted'>یک تماس. فقط پیشنهاد است؛ چیزی روی سایت تغییر نمی‌کند.</span></div>")
        return P.page(j["title"], body + _danger(P, j))
    body += _brief_form(P, j, hid, act)
    # ---- step 2
    body += "<h2>۲) نوشتن متن با AI</h2>"
    body += ("<div class='box'>مرحله‌به‌مرحله (دو بخش در هر تماس) تا کار ناقص و خام نشود. بخش‌هایی که نوشته شده‌اند دوباره هزینه نمی‌شوند.<br>"
             + form("/writeact", hid + act("write_est", "برآورد هزینه", "alt") + P.confirm_box("ai") + act("write", "نوشتن متن")
                    + act("write_redo", "نوشتن دوباره بخش‌های خالی", "alt")) + "</div>")
    if j.get("text"):
        body += _text_and_qc(P, j, hid, act, COUNTER_JS)
        body += _links_box(P, j, hid, act)
        body += _images_box(P, j, hid, act)
        body += _send_box(P, j, hid, act)
        if j.get("wp") and j["state"] != "published":
            body += _publish_box(P, j, hid, act)
        body += _revise_box(P, j, hid)
    return P.page(j["title"], body + COUNTER_JS + _danger(P, j))


def _site_box(P, j):
    """Always-visible link to the article in the WordPress editor (and a warning while local changes are not on the site)."""
    if not j.get("wp"):
        return ""
    E = P.E
    url = ag.wp_edit_url(j)
    live = j["state"] == "published"
    t = (f"<div class='box' style='border-color:#1c5d99'><b>در سایت:</b> {'منتشرشده' if live else 'پیش‌نویس'} (id {E(str(j['wp']['id']))}) · "
         f"<a href='{E(url)}' target='_blank' rel='noopener'><b>باز کردن در ویرایشگر وردپرس ←</b></a>"
         + (f" · {P.link(j['wp']['link'], 'پیوند مقاله')}" if j["wp"].get("link") else "") + f"<div class='muted' style='direction:ltr;text-align:left'>{E(url)}</div>")
    if j.get("dirty"):
        t += "<div style='color:#a65f00'>⚠ تغییرات این پرونده (متن، سئو، تیترها، لینک‌ها) هنوز در سایت اعمال نشده؛ پایین صفحه «۷) ویرایش و بهبود با AI ← به‌روزرسانی مقاله در سایت».</div>"
    return t + "</div>"


def _brief_form(P, j, hid, act):
    E = P.E
    b, ctx = j["brief"], ag.site_context((j.get("wp") or {}).get("id"))
    warn = ag.brief_warnings(b, ctx, check_slug=not j.get("wp"), own_id=(j.get("wp") or {}).get("id"))
    t = "<div class='box'>" + (f"<div style='color:#a65f00'>⚠ " + "<br>⚠ ".join(E(w) for w in warn) + "</div>" if warn else "<div style='color:#1b6e3c'>✓ بریف از نظر کد مشکلی ندارد</div>")
    cats = (f"<option value='' {'selected' if not b['cat'] and b.get('new_cat') is None else ''}>— انتخاب کنید —</option>"
            + "".join(f"<option value='{E(c['name'])}' {'selected' if b['cat'] == c['name'] else ''}>{E(c['name'])}</option>" for c in ctx["cats"]))
    newsel = b.get("new_cat") is not None
    nc = b.get("new_cat") or {"name": "", "slug": ""}
    row = lambda lab, inner: f"<tr><td style='width:130px'><b>{lab}</b></td><td>{inner}</td></tr>"
    cc = lambda k, mx, mn=0: f"data-max='{mx}' data-min='{mn}' oninput='cc(this)'"
    tags = "".join(f"<label style='display:inline-block;margin-left:12px'><input type='checkbox' name='tag' value='{E(tg['name'])}' {'checked' if tg['name'] in b['tags'] else ''}> {E(tg['name'])}</label>"
                   for tg in ctx["tags"][:40])
    idea = (f"<div class='muted'>ایدهٔ اولیهٔ شما: «{E(j['idea'])}» (AI عنوان را بر اساس سایت و جست‌وجوهای واقعی بهبود داده؛ هر طور خواستید ویرایش کنید)</div>"
            if j.get("idea") and j["idea"] != j["title"] else "")
    tbl = ("<table>" + row("عنوان مقاله", f"<input name='title' value='{E(j['title'])}' style='width:95%'>{idea}")
           + row("دسته", f"<select name='cat'>{cats}<option value='__new__' {'selected' if newsel else ''}>— دستهٔ جدید (با تیک ساخت) —</option></select> "
                 f"<span class='muted'>جدید:</span> <input name='new_cat_name' value='{E(nc['name'])}' placeholder='نام'> <input name='new_cat_slug' value='{E(nc['slug'])}' placeholder='latin-slug' style='direction:ltr'>")
           + row("برچسب‌ها", f"{P.selall('tag')}<br>{tags or '<span class=muted>برچسبی در سایت نیست</span>'}<br><span class='muted'>برچسب جدید (هر خط: نام|latin-slug؛ با تیک ساخت):</span>"
                 f"<textarea name='new_tags' style='min-height:50px;direction:rtl'>{E(chr(10).join(x['name'] + '|' + x['slug'] for x in b.get('new_tags', [])))}</textarea>")
           + row("slug", f"<input name='b_slug' value='{E(b['slug'])}' style='width:60%;direction:ltr'>")
           + row("کلمه کلیدی", f"<input name='b_k' value='{E(b['k'])}' style='width:60%'>")
           + row("عنوان سئو", f"<input name='b_t' value='{E(b['t'])}' style='width:95%' {cc('t', 60)}><span class='cc muted'></span>")
           + row("متا", f"<textarea name='b_m' rows='3' style='min-height:60px;direction:rtl' {cc('m', 160, 110)}>{E(b['m'])}</textarea><span class='cc muted'></span>")
           + row("خلاصه (excerpt)", f"<textarea name='b_x' rows='2' style='min-height:50px;direction:rtl'>{E(b['x'])}</textarea>") + "</table>")
    hs = "".join(f"<tr><td><input type='checkbox' name='hon_{i}' {'checked' if h['on'] else ''}></td><td><input name='h_{i}' value='{E(h['h'])}' style='width:95%'></td>"
                 f"<td><textarea name='hs_{i}' rows='3' style='min-height:54px;direction:rtl'>{E(chr(10).join(h.get('s', [])))}</textarea></td>"
                 f"<td><input name='hn_{i}' value='{E(h['n'])}' style='width:95%'></td></tr>" for i, h in enumerate(b["h"]))
    hs += "".join(f"<tr><td><input type='checkbox' name='hon_{len(b['h']) + k}' checked></td><td><input name='h_{len(b['h']) + k}' placeholder='تیتر جدید' style='width:95%'></td><td><textarea name='hs_{len(b['h']) + k}' rows='2' style='min-height:40px;direction:rtl'></textarea></td><td></td></tr>" for k in range(2))
    tbl += (f"<h3>فهرست تیترها (H2 و زیرتیترهای H3؛ بی‌تیک = حذف)</h3><table><tr><th>{P.selall()}</th><th>تیتر H2</th><th>تیترهای H3 (هر خط یکی)</th><th>یادداشت برای نویسنده</th></tr>{hs}</table>")
    ims = ""
    for im in b["img"]:
        n = im["n"]
        wsel = "".join(f"<option value='{v}' {'selected' if str(im['w']) == str(v) else ''}>{lab}</option>" for v, lab in
                       [("cover", "تصویر شاخص")] + [(i, f"بعد از تیتر {i}") for i in range(1, len([h for h in b['h'] if h['on']]) + 1)])
        ims += (f"<tr><td><input type='checkbox' name='ion_{n}' {'checked' if im['on'] else ''}></td><td><b>{n:02d}</b></td>"
                f"<td><select name='iw_{n}'>{wsel}</select><br><select name='ik_{n}'><option value='photo' {'selected' if im['kind'] == 'photo' else ''}>عکس</option>"
                f"<option value='infographic' {'selected' if im['kind'] == 'infographic' else ''}>اینفوگرافیک</option></select></td>"
                f"<td><textarea name='ip_{n}' rows='4' style='direction:rtl;min-height:80px'>{E(im['p'])}</textarea>"
                f"<div class='muted'>داده برای اینفوگرافیک:</div><input name='id_{n}' value='{E(im['d'])}' style='width:95%'></td>"
                f"<td><input name='if_{n}' value='{E(im['f'])}' style='width:95%;direction:ltr' placeholder='نام فایل لاتین'><br>"
                f"<input name='ia_{n}' value='{E(im['a'])}' style='width:95%' placeholder='Alt فارسی'></td></tr>")
    tbl += ("<h3>پیشنهاد تصویر (بی‌تیک = این تصویر حذف شود)</h3><p class='muted'>پرامپت را به ابزار تصویرسازی بدهید یا عکس خودتان را بگذارید. "
            f"فایل‌ها را با نام <b>01</b> (شاخص)، <b>02</b>، ... در پوشهٔ <code style='direction:ltr;display:inline-block'>{E(str(ag.img_dir(j)))}</code> بگذارید.</p>"
            f"<table><tr><th>{P.selall()}</th><th>شماره</th><th>جایگاه / نوع</th><th>پرامپت</th><th>نام فایل و Alt</th></tr>{ims}</table>"
            f"<label><input type='checkbox' name='schema' {'checked' if b.get('schema', False) else ''}> افزودن Schema تصویر (ImageObject) به متن (پیش‌فرض خاموش: وردپرس بسیاری از سایت‌ها آن را حذف و متنش را در صفحه رها می‌کند؛ Yoast خودش Schema می‌سازد)</label>")
    return t + P.form("/writeact", hid + tbl + "<br>" + act_btn(P, "save_brief", "ذخیرهٔ ویرایش‌های بریف", "alt")) + "</div>"


def act_btn(P, do, label, cls=""):
    return P.btn(do, label, cls)


def _text_and_qc(P, j, hid, act, js):
    E = P.E
    q = ag.qc(j)
    col = {"H": "#b3261e", "M": "#a65f00", "L": "#6b5b4b", "OK": "#1b6e3c"}
    lab = {"H": "جدی", "M": "متوسط", "L": "کم", "OK": "✓"}
    t = ("<h3>کنترل کیفیت (با کد)</h3><div class='box'>" + "".join(f"<div><b style='color:{col[s]}'>[{lab[s]}]</b> {E(x)}</div>" for s, x in q)
         + "<hr><span class='muted'>چک‌لیست E-E-A-T (دستی):</span><br>" + "<br>".join("☐ " + E(x) for x in ag.EEAT) + "</div>")
    t += ("<h3>متن مقاله (قابل ویرایش؛ Markdown؛ <code>[[IMG:n]]</code> جای تصویر است)</h3>"
          + P.form("/writeact", hid + f"<textarea name='text' style='direction:rtl;min-height:420px'>{E(j['text'])}</textarea><br>"
                   f"<span class='muted'>{ag.words_of(j['text'])} کلمه</span><br>" + P.btn("save_text", "ذخیرهٔ متن (و اجرای دوبارهٔ پاک‌سازی و کنترل)", "alt")))
    return t


def _links_box(P, j, hid, act):
    E = P.E
    t = "<h2>۳) لینک داخلی (کدمحور؛ روی متن، قبل از ارسال به سایت)</h2><div class='box'>"
    if j["links"]:
        rows = "".join(f"<tr><td><input type='checkbox' name='lk' value='{i}' checked></td><td>{E(x['phrase'])}</td><td>{P.link(x['url'], x['title'])} <span class='muted'>({E(x['kind'])}{' · AI' if x.get('src') == 'ai' else ''})</span>"
                       f"{('<div class=muted>' + E(x['why']) + '</div>') if x.get('why') else ''}</td><td class='muted'>…{E(x['ctx'])}…</td></tr>" for i, x in enumerate(j["links"]))
        t += (f"<div style='color:#a65f00'>⚠ {len(j['links'])} پیشنهاد لینک داخلی منتظر تصمیم شماست؛ تیک بزنید و اعمال کنید (یا همه را رد کنید) تا در متن بیاید.</div>"
              + P.form("/writeact", hid + f"<table><tr><th>{P.selall()}</th><th>عبارت در متن</th><th>مقصد</th><th>جمله</th></tr>{rows}</table>" + P.btn("links_apply", "اعمال لینک‌های تیک‌خورده در متن", "alt")
                       + P.btn("links_skip", "رد همهٔ پیشنهادها", "alt")))
    else:
        t += "پیشنهاد بازی نیست. " + P.form("/writeact", hid + P.btn("links_find", "جست‌وجوی دوبارهٔ جای لینک (بعد از ویرایش متن)", "alt"))
    t += ("<hr><b>پیشنهاد لینک داخلی با AI</b> (فهرست عنوان‌های مقاله‌ها، دسته‌ها و محصولات سایت به AI داده می‌شود؛ AI مقصد و عبارت را انتخاب می‌کند و کد هر پیشنهاد را راستی‌آزمایی می‌کند):"
          + P.form("/writeact", hid + "حداکثر <input name='maxl' value='6' size='2' style='width:50px'> لینک " + P.btn("links_ai_est", "برآورد هزینه", "alt") + P.confirm_box("ai") + P.btn("links_ai", "پیشنهاد لینک با AI")))
    return t + "<span class='muted'>فقط روی عبارتی که از قبل در متن است؛ هر مقصد حداکثر یک بار؛ زیر تیتر و داخل لینک نمی‌رود. بعد از نوشتن متن خودکار پیشنهاد داده می‌شود؛ به سایت چیزی نمی‌رود.</span></div>"


def _send_box(P, j, hid, act):
    E = P.E
    t = "<h2>۵) ارسال به سایت (آخرین مرحله)</h2><div class='box'>"
    if j.get("wp"):
        return t + f"✓ در سایت است ({'منتشرشده' if j['state'] == 'published' else 'پیش‌نویس'}): id <b>{j['wp']['id']}</b> " + P.link(j["wp"]["link"], "(پیوند)") + P.edit_link(j["wp"]["link"], j["wp"]["id"]) + "</div>"
    probs = ag.send_problems(j, "draft")
    if ag.links_pending(j):
        probs = probs + [f"{len(j['links'])} پیشنهاد لینک داخلی هنوز اعمال یا رد نشده (مرحلهٔ ۳)؛ برای ارسال مستقیم منتشرشده الزامی است"]
    need = ag.new_term_keys(j)
    nt = ""
    if need:
        rows = "".join(f"<tr><td><input type='checkbox' name='term' value='{E(k)}'></td><td>{E(k.split('|')[1])}</td><td class='muted'>{E(k.split('|')[0])}</td></tr>" for k in need)
        nt = f"<b>دسته/برچسب جدید (فقط با تیک ساخته می‌شود)</b><table><tr><th>{P.selall()}</th><th>نام</th><th>نوع</th></tr>{rows}</table>"
    return (t + "همهٔ مراحل قبل روی این کامپیوتر انجام شده است؛ فقط الان چیزی به سایت می‌رود: تصاویر بارگذاری می‌شوند و مقالهٔ کامل با دسته، برچسب، سئو و Schema ساخته می‌شود."
            + (("<div style='color:#b3261e'>⚠ " + "<br>⚠ ".join(E(x) for x in probs) + "</div>") if probs else "")
            + P.form("/writeact", hid + nt + "<label><input type='radio' name='status' value='draft' checked> پیش‌نویس (پیشنهادی؛ بعداً در وردپرس ببینید و منتشر کنید)</label><br>"
                     "<label><input type='radio' name='status' value='publish'> منتشر شود</label> "
                     "<label><input type='checkbox' name='pubok' value='1'> مطمئنم؛ مستقیم منتشر شود</label><br>"
                     + P.btn("draft_prev", "پیش‌نمایش (چیزی ارسال نمی‌شود)", "alt") + "<br>" + P.confirm_box() + P.btn("draft_make", "ارسال به سایت", "warn"))
            + "<span class='muted'>برای انتشار، کنترل کیفیت نباید مشکل «جدی» داشته باشد و جای تصویر خالی نباشد.</span></div>")


def _images_box(P, j, hid, act):
    E = P.E
    ST = {"removed": ("حذف شده", "#6b5b4b"), "attached": ("✓ در سایت", "#1b6e3c"), "found": ("فایل پیدا شد", "#1b6e3c"), "missing": ("فایل نیست", "#b3261e")}
    rows = ""
    for im, st in ag.slot_status(j):
        lab, col = ST[st]
        rows += (f"<tr><td><b>{im['n']:02d}</b></td><td>{'تصویر شاخص' if im['w'] == 'cover' else 'بعد از تیتر ' + str(im['w'])}</td><td>{E(im['f'])}.webp</td><td>{E(im['a'])}</td>"
                 f"<td><b style='color:{col}'>{lab}</b></td></tr>")
    t = ("<h2>۴) تصاویر (روی همین کامپیوتر)</h2><div class='box'>پرامپت‌ها در بریف است. تصویرها را بسازید و با نام <b>01</b> (شاخص)، <b>02</b>، ... (jpg/png/webp) در پوشهٔ <code style='direction:ltr;display:inline-block'>"
         + E(str(ag.img_dir(j))) + "</code> بگذارید. هر جای خالی را می‌توانید با برداشتن تیکِ همان تصویر در بریف حذف کنید. "
         "برنامه کوچک و WebP می‌کند، نام فایل را از بریف می‌گیرد و Alt، ابعاد و Schema می‌گذارد؛ بارگذاری در سایت فقط در مرحلهٔ ارسال انجام می‌شود."
         f"<table><tr><th>#</th><th>جایگاه</th><th>نام فایل نهایی</th><th>Alt</th><th>وضعیت</th></tr>{rows}</table>"
         + P.form("/writeact", hid + P.btn("img_prev", "بررسی و تبدیل آزمایشی (چیزی ارسال نمی‌شود)", "alt")))
    if j.get("wp"):
        t += ("<hr><b>پر کردن تصاویر تأخیری (مقاله از قبل در سایت است)</b>" + P.form("/writeact", hid + P.confirm_box() + P.btn("img_go", "بارگذاری و پر کردن جای خالی در سایت", "warn")))
    return t + "</div>"


def _publish_box(P, j, hid, act):
    if j["state"] == "published":
        return f"<h2>۶) انتشار</h2><div class='box'>✓ منتشر شد: {P.link(j['wp']['link'], j['wp']['link'])}</div>"
    return ("<h2>۶) انتشار</h2><div class='box'>پیش از انتشار پیش‌نمایش پیش‌نویس را در وردپرس ببینید. اگر جایی هنوز تصویر خالی است، تصویر را بگذارید یا پیشنهاد عکسش را در بریف حذف کنید.<br>"
            + P.form("/writeact", hid + P.btn("pub_prev", "بررسی پیش از انتشار", "alt") + "<br>" + P.confirm_box() + P.btn("pub_go", "انتشار مقاله", "warn")) + "</div>")


PRESETS = [("", "— یک دستور آماده انتخاب کنید (یا خودتان بنویسید) —"),
           ("مقدمه را جذاب‌تر و کوتاه‌تر کن؛ در دو جمله پاسخ اصلی نیت جست‌وجو را بده و خواننده را به ادامه ترغیب کن.", "مقدمه جذاب‌تر"),
           ("هر بخش را یونیک‌تر کن: کلیشه‌ها و جمله‌های عمومی را حذف کن و به‌جایش یک نکتهٔ عملی یا مثال واقعی از خرید، مصرف و نگهداری زعفران بگذار.", "یونیک‌تر و کاربردی‌تر"),
           ("لحن را صمیمی‌تر و کارشناسانه‌تر کن، جمله‌های بلند را بشکن و پاراگراف‌ها را کوتاه کن.", "لحن صمیمی‌تر"),
           ("برای هر بخش مناسب، یک لیست یا جدول ساده اضافه کن تا متن قابل اسکن‌تر شود.", "لیست/جدول اضافه کن"),
           ("کلمهٔ کلیدی را طبیعی‌تر و در جاهای بهتر بیاور و تکرار اجباری را بردار.", "کلمهٔ کلیدی طبیعی‌تر"),
           ("هر ادعای مبهم یا اغراق‌آمیز را ملایم و دقیق کن و هر عدد یا آمار بی‌منبع را حذف کن.", "ادعاها دقیق و بی‌اغراق")]


def _revise_box(P, j, hid):
    from . import articlegen as _ag
    E, form, btn, cb = P.E, P.form, P.btn, P.confirm_box
    b = j["brief"]
    _, secs = _ag.split_text(j["text"])
    t = ("<h2 id='ai'>۷) ویرایش و بهبود با AI (قبل یا بعد از ارسال به سایت)</h2><p class='muted'>هر تغییر AI اول فقط روی این کامپیوتر انجام می‌شود، قبلش یک نقطهٔ برگشت ساخته می‌شود و هزینه از قبل برآورد می‌شود. "
         "آدرس (slug) هیچ‌وقت تغییر نمی‌کند. بعد از راضی شدن، «به‌روزرسانی مقاله در سایت» تغییرات را (با پیش‌نمایش، تیک و پشتیبان) به همان مقالهٔ سایت می‌فرستد.</p>")
    if j.get("undo"):
        u = j["undo"][-1]
        t += ("<div class='box'>" + form("/writeact", hid + f"آخرین نقطهٔ برگشت: <b>{E(u['why'])}</b> ({E(u['when'])}) · " + btn("undo", "برگرداندن آخرین تغییر AI", "alt")) + "</div>")
    ai = lambda est, go, label: btn(est, "برآورد هزینه", "alt") + cb("ai") + btn(go, label)
    # (a) SEO
    chk = "".join(f"<label style='margin-left:14px'><input type='checkbox' name='w' value='{k}' {'checked' if k != 'title' else ''}> {E(v)}</label>" for k, v in ag.SEO_FIELDS.items())
    t += ("<div class='box'><b>الف) پرسیدن دوبارهٔ سئو از AI</b> (بر اساس متن واقعی مقاله و جست‌وجوهای واقعی؛ هم‌نوع‌خواری با مقاله‌های دیگر کنترل می‌شود)"
          + form("/writeact", hid + chk + "<br><input name='note' placeholder='راهنمای اختیاری، مثلاً: کلمهٔ کلیدی جنبهٔ خرید داشته باشد' style='width:80%'><br>" + ai("seo_est", "seo_ai", "پرسیدن از AI")) + "</div>")
    # (b) outline
    t += ("<div class='box'><b>ب) طراحی مجدد تیترها (H2 و H3) با AI</b> — فهرست جدید در «بریف» می‌نشیند؛ بعد در مرحلهٔ ۲ «نوشتن متن» را بزنید تا فقط بخش‌های جدید/تغییرکرده نوشته شوند؛ بخش‌های بی‌تغییر و ویرایش‌های دستی شما حفظ می‌شود."
          + form("/writeact", hid + "<input name='note' placeholder='راهنمای اختیاری، مثلاً: یک بخش سوالات متداول اضافه کن و H3 بیشتر بگذار' style='width:80%'><br>" + ai("outline_est", "outline_ai", "طراحی مجدد تیترها")) + "</div>")
    # (c) rewrite
    pre = "".join(f"<option value='{E(v)}'>{E(lab)}</option>" for v, lab in PRESETS)
    picks = "".join(f"<label style='display:block'><input type='checkbox' name='sec' value='{E(h)}'> {E(h)}</label>" for h, _ in secs)
    t += ("<div class='box'><b>پ) بازنویسی با دستور دلخواه</b> (متن، لینک‌های داخلی و جای تصویرها حفظ می‌شود؛ دو بخش در هر تماس)"
          + form("/writeact", hid + f"<select onchange=\"this.form.instruction.value=this.value\">{pre}</select><br>"
                 "<textarea name='instruction' rows='3' style='min-height:70px;direction:rtl' placeholder='مثلاً: بخش‌های ضعیف را یونیک‌تر و کاربردی‌تر بنویس، مثال بیشتر بیاور'></textarea><br>"
                 "<label><input type='radio' name='scope' value='all' checked> کل مقاله</label> <label><input type='radio' name='scope' value='intro'> فقط مقدمه</label> "
                 f"<label><input type='radio' name='scope' value='pick'> فقط بخش‌های تیک‌خورده:</label>{P.selall('sec')}<div style='margin-right:18px'>{picks}</div>" + ai("rewrite_est", "rewrite_ai", "بازنویسی")) + "</div>")
    # (d) update site
    if j.get("wp"):
        live = j["state"] == "published"
        need = ag.new_term_keys(j)
        nt = ""
        if need:
            rows = "".join(f"<tr><td><input type='checkbox' name='term' value='{E(k)}'></td><td>{E(k.split('|')[1])}</td><td class='muted'>{E(k.split('|')[0])}</td></tr>" for k in need)
            nt = f"<b>دسته/برچسب جدید (فقط با تیک ساخته می‌شود)</b><table><tr><th>{P.selall()}</th><th>نام</th><th>نوع</th></tr>{rows}</table>"
        t += ("<div class='box'><b>ت) به‌روزرسانی مقاله در سایت</b> — عنوان، متن، لینک‌ها، خلاصه، سئوی Yoast، دسته/برچسب و تصویرها؛ آدرس (slug) و وضعیت (پیش‌نویس/منتشرشده) دست نمی‌خورد. "
              "قبل از ارسال، نسخهٔ فعلی سایت در پوشهٔ backups ذخیره می‌شود."
              + ("<div style='color:#a65f00'>⚠ مقاله منتشر شده است؛ تغییر مستقیم روی صفحهٔ زنده می‌نشیند.</div>" if live else "")
              + form("/writeact", hid + nt + "<label><input type='checkbox' name='overwrite' value='1'> اگر نسخهٔ سایت با دست تغییر کرده، جایگزین شود (پیش‌نمایش می‌گوید)</label><br>"
                     + ("<label><input type='checkbox' name='liveok' value='1'> مطمئنم؛ روی صفحهٔ منتشرشده اعمال شود</label><br>" if live else "")
                     + btn("update_prev", "پیش‌نمایش (چیزی ارسال نمی‌شود)", "alt") + "<br>" + cb() + btn("update_go", "به‌روزرسانی مقاله در سایت", "warn"))
              + "<hr><b>دریافت نسخهٔ سایت</b> (اگر مقاله را در وردپرس ویرایش کرده‌اید و می‌خواهید AI از همان نسخه ادامه دهد):"
              + form("/writeact", hid + btn("pull_prev", "پیش‌نمایش", "alt") + "<label><input type='checkbox' name='okpull' value='1'> مطمئنم؛ متن این پرونده با نسخهٔ سایت جایگزین شود (نقطهٔ برگشت ساخته می‌شود)</label> " + btn("pull_go", "دریافت نسخهٔ سایت", "alt"))
              + "</div>")
    return t


def _danger(P, j):
    return ("<hr>" + P.form("/writeact", f"<input type='hidden' name='job' value='{j['id']}'><label><input type='checkbox' name='confirm' value='1'> مطمئنم؛ این پروندهٔ کار پاک شود "
                            "(فقط پروندهٔ محلی؛ اگر پیش‌نویسی در سایت ساخته شده بود در سایت می‌ماند)</label>" + P.btn("delete", "حذف پرونده", "alt")))


# ------------------------------------------------------------------ POST
def post(path, f, sure, run, sure_site=False):
    """-> ('page', html) or ('redirect', url)"""
    P = _P()
    one = lambda k, d="": (f.get(k, [d])[0] if f.get(k) else d)
    if path == "/writeq":
        n = 0
        for v in f.get("idea", []):
            title, _, note = v.partition("||")
            if title.strip():
                ag.new_job(title, note, one("source", "idea"))
                n += 1
        FLASH["w"] = f"{n} پروندهٔ مقاله ساخته شد. برای هرکدام «باز کردن» و سپس «ساخت بریف»؛ یا همه را با «تولید خودکار» جلو ببرید." if n else "چیزی تیک نخورده بود."
        return "redirect", "/write"
    if path == "/write":
        try:
            j = ag.new_job(one("title"), one("note"))
        except ValueError as e:
            return "page", list_page(str(e))
        return "redirect", "/write?job=" + j["id"]
    if path == "/writeauto":
        ids = f.get("j", [])
        if not ids:
            return "page", list_page("هیچ مقاله‌ای تیک نخورده است.")
        do = one("do")
        if do == "go" and not (sure and one("confirm_site") == "1"):
            return "page", list_page("اجرا نشد: هر دو تیک (مصرف اعتبار AI و ساخت پیش‌نویس در سایت) لازم است.")
        out = run(lambda: ag.auto(ids, do == "go"), yes=(do == "go"))
        return "page", list_page(out)
    job = ag.load(one("job"))
    if not job:
        return "page", list_page("پروندهٔ مقاله پیدا نشد.")
    do = one("do")
    page = lambda out="": ("page", job_page(job["id"], out))
    NEEDS_AI, NEEDS_SITE = {"brief", "write", "write_redo", "seo_ai", "outline_ai", "rewrite_ai", "links_ai"}, {"draft_make", "img_go", "pub_go", "update_go"}
    if do in NEEDS_AI and not sure:
        return page("اجرا نشد: تیک مصرف اعتبار AI را بزنید.")
    if do in NEEDS_SITE and not sure:
        return page("اجرا نشد: تیک تأیید تغییر روی سایت را بزنید.")
    if do == "delete":
        if not sure:
            return page("حذف نشد: تیک تأیید را بزنید.")
        ag.delete_job(job["id"])
        FLASH["w"] = "پرونده حذف شد."
        return "redirect", "/write"
    if do in ("brief", "brief_est"):
        job["title"] = one("title", job["title"]).strip() or job["title"]
        job["idea"] = job["title"]                       # what you typed is the IDEA; the AI proposes the final title
        ag.save(job)
        return page(run(lambda: ag.make_brief(job, do == "brief", one("existing_only") == "1"), yes=(do == "brief")))
    if not job.get("brief"):
        return page("اول بریف را بسازید.")
    if do in ("save_brief",):
        ag.parse_brief_form(f, job)
        ag.mark_dirty(job)
        ag.save(job)
        return page("بریف ذخیره شد." + (" (هنوز در سایت اعمال نشده؛ «۷) ← به‌روزرسانی مقاله در سایت»)" if job.get("wp") else ""))
    if do in ("write", "write_est", "write_redo"):
        return page(run(lambda: ag.write(job, do != "write_est", redo=(do == "write_redo")), yes=(do != "write_est")))
    if do == "save_text":
        job["text"] = ag.fix_text(one("text"))
        ag.mark_dirty(job)
        ag.save(job)
        return page("متن ذخیره شد." + (" (هنوز در سایت اعمال نشده؛ «۷) ← به‌روزرسانی مقاله در سایت»)" if job.get("wp") else ""))
    if do == "links_find":
        n = len(ag.suggest_links(job))
        return page(f"{n} جای لینک پیدا شد." if n else "جای مناسبی برای لینک داخلی پیدا نشد (ممکن است «خروجی مقاله‌ها» و «همگام‌سازی» لازم باشد).")
    if do == "links_skip":
        job["links"] = []
        ag.save(job)
        return page("پیشنهادهای لینک رد شد.")
    if do == "links_apply":
        n = ag.apply_links(job, {int(x) for x in f.get("lk", []) if x.isdigit()})
        return page(f"{n} لینک در متن گذاشته شد." if n else "لینکی اعمال نشد.")
    if do in ("draft_prev", "draft_make"):
        st = "publish" if one("status") == "publish" else "draft"
        if st == "publish" and do == "draft_make" and one("pubok") != "1":
            return page("اجرا نشد: برای انتشار مستقیم تیک «مطمئنم؛ مستقیم منتشر شود» را هم بزنید (یا پیش‌نویس را انتخاب کنید).")
        return page(run(lambda: ag.create_draft(job, do == "draft_make", set(f.get("term", [])), st), yes=True))
    if do in ("seo_est", "seo_ai"):
        want = [w for w in f.get("w", []) if w in ag.SEO_FIELDS]
        return page(run(lambda: ag.revise_seo(job, do == "seo_ai", want, one("note")), yes=(do == "seo_ai")))
    if do in ("outline_est", "outline_ai"):
        return page(run(lambda: ag.revise_outline(job, do == "outline_ai", one("note")), yes=(do == "outline_ai")))
    if do in ("rewrite_est", "rewrite_ai"):
        return page(run(lambda: ag.rewrite(job, do == "rewrite_ai", one("instruction"), one("scope", "all"), f.get("sec", [])), yes=(do == "rewrite_ai")))
    if do in ("links_ai_est", "links_ai"):
        mx = one("maxl", "6")
        return page(run(lambda: ag.ai_links(job, do == "links_ai", int(mx) if mx.isdigit() else 6), yes=(do == "links_ai")))
    if do == "undo":
        u = ag.undo_last(job)
        return page(f"برگردانده شد: {u['why']} ({u['when']})" if u else "نقطهٔ برگشتی نیست.")
    if do in ("update_prev", "update_go"):
        return page(run(lambda: ag.update_post(job, do == "update_go", set(f.get("term", [])), one("overwrite") == "1", one("liveok") == "1"), yes=True))
    if do in ("pull_prev", "pull_go"):
        if do == "pull_go" and one("okpull") != "1":
            return page("اجرا نشد: تیک «مطمئنم؛ متن این پرونده با نسخهٔ سایت جایگزین شود» را بزنید.")
        return page(run(lambda: ag.pull_from_site(job, do == "pull_go"), yes=True))
    if do in ("img_prev", "img_go"):
        return page(run(lambda: ag.attach_images(job, do == "img_go"), yes=True))
    if do in ("pub_prev", "pub_go"):
        return page(run(lambda: ag.publish(job, do == "pub_go"), yes=True))
    return page("دستور ناشناخته.")
