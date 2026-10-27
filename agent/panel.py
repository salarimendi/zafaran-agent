"""Local web panel (stdlib only). Runs on 127.0.0.1, protected by a per-session token + Host check.
It is a front-end over the same functions as the menu; every write still shows a preview and needs a confirm tick."""
import builtins
import contextlib
import csv
import html
import io
import re
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from . import proposals as pr
from .config import ROOT

TOKEN = secrets.token_urlsafe(16)
E = html.escape
CSS = """body{font-family:Tahoma,Vazirmatn,sans-serif;margin:0;background:#f6f4ef;color:#222}
nav{background:#7a2e1d;padding:10px 20px}nav a{color:#fff;margin-left:18px;text-decoration:none;font-weight:bold}
main{max-width:1150px;margin:18px auto;padding:0 16px}h2{margin:22px 0 8px}
button{padding:7px 14px;margin:3px;border:0;border-radius:6px;background:#7a2e1d;color:#fff;cursor:pointer;font-family:inherit}
button.alt{background:#555}button.warn{background:#a31515}
pre{background:#1e1e1e;color:#e8e8e8;padding:12px;border-radius:8px;white-space:pre-wrap;direction:ltr;text-align:left;max-height:480px;overflow:auto}
table{border-collapse:collapse;width:100%;background:#fff}td,th{border:1px solid #ddd;padding:6px;vertical-align:top;font-size:14px}
th{background:#eee}mark{background:#ffe08a}textarea{width:100%;min-height:140px;font-family:monospace;direction:ltr}
.box{background:#fff;border:1px solid #ddd;border-radius:8px;padding:12px;margin:10px 0}.muted{color:#777;font-size:13px}"""
NAV = [("/", "خانه"), ("/links", "لینک‌دهی داخلی"), ("/seo", "کلمه کلیدی و متا"), ("/inbox", "محصول و مقاله جدید"), ("/reports", "گزارش سئو")]


def run(fn, yes=False):
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
    from . import audit, cli, export, site, stage2, style
    return {  # name: (label, callable, needs_confirm)
        "check": ("بررسی اتصال", cli.check, False), "diagnose": ("عیب‌یابی اتصال", cli.diagnose, False),
        "sync": ("همگام‌سازی سایت", site.sync, False), "export": ("خروجی مقاله‌ها", export.export_articles, False),
        "audit": ("گزارش سئو", audit.audit, False), "style": ("بستهٔ لحن", style.build, False),
        "linkplan": ("ساخت پیشنهاد لینک‌دهی", pr.link_plan, False), "seotodo": ("فهرست و پرامپت سئو", pr.seo_todo, False),
        "inbox_dry": ("پیش‌نمایش محصول و مقاله", lambda: (stage2.run_products(False), print(), stage2.run_articles(False)), False),
        "inbox_send": ("ارسال محصول و مقاله", lambda: (stage2.run_products(True), print(), stage2.run_articles(True)), True),
        "newterms": ("ساخت دسته/برچسب جدید", lambda: site.run_newterms(True), True),
    }


def page(title, body):
    nav = "".join(f'<a href="{u}">{n}</a>' for u, n in NAV)
    return (f'<!doctype html><html dir="rtl" lang="fa"><head><meta charset="utf-8"><title>{E(title)}</title>'
            f"<style>{CSS}</style></head><body><nav>{nav}</nav><main>{body}</main></body></html>")


def form(action, inner):
    return f'<form method="post" action="{action}"><input type="hidden" name="t" value="{TOKEN}">{inner}</form>'


def out_box(text):
    return f"<h2>خروجی</h2><pre>{E(text)}</pre>" if text else ""


def btn(name, label, cls="", extra=""):
    return f'<button class="{cls}" name="do" value="{name}" {extra}>{E(label)}</button>'


def confirm_box():
    return '<label><input type="checkbox" name="confirm" value="1"> مطمئنم، تغییرات روی سایت اعمال شود</label>'


# ---------- pages ----------
def home(output=""):
    def run_btn(a, label, cls=""):
        return form("/run", f'<input type="hidden" name="action" value="{a}">{btn(a, label, cls)}').replace("<form", '<form style="display:inline"')
    body = ("<h1>زعفران ایجنت</h1><p class='muted'>این پنل فقط روی همین کامپیوتر باز است. هر تغییر روی سایت قبلش پیش‌نمایش دارد و تیک تأیید می‌خواهد.</p>"
            "<div class='box'><b>۱) به‌روزرسانی داده‌ها (فقط خواندن):</b><br>"
            + run_btn("check", "بررسی اتصال", "alt") + run_btn("sync", "همگام‌سازی سایت") + run_btn("export", "خروجی مقاله‌ها")
            + run_btn("audit", "گزارش سئو") + run_btn("style", "بستهٔ لحن", "alt") + run_btn("diagnose", "عیب‌یابی", "alt") + "</div>"
            "<div class='box'><b>۲) اصلاح مقاله‌های موجود:</b> صفحه‌های «لینک‌دهی داخلی» و «کلمه کلیدی و متا» را باز کنید.</div>"
            "<div class='box'><b>۳) محصول و مقالهٔ جدید:</b> صفحهٔ «محصول و مقاله جدید».</div>"
            "<div class='box'><b>ساخت دستهٔ جدید:</b> فایل knowledge\\new_terms.yaml را پر کنید و "
            + form("/run", f'<input type="hidden" name="action" value="newterms">{confirm_box()} {btn("newterms", "ساخت", "warn")}') + "</div>")
    return page("خانه", body + out_box(output))


def latest_links():
    fs = sorted(pr.PROP.glob("links_2*.csv"))
    return fs[-1] if fs else None


def links_page(output=""):
    f = latest_links()
    body = "<h1>لینک‌دهی داخلی</h1>" + form("/run", '<input type="hidden" name="action" value="linkplan">' + btn("x", "ساخت پیشنهاد جدید", "alt"))
    if not f:
        return page("لینک‌دهی", body + "<p>هنوز پیشنهادی نیست. اول «خروجی مقاله‌ها» را در صفحهٔ خانه اجرا کنید، بعد پیشنهاد بسازید.</p>" + out_box(output))
    rows = pr.read_csv(f)
    trs = []
    for i, r in enumerate(rows):
        chk = "checked" if str(r.get("approve", "")).strip().lower() in pr.YES else ""
        sent = E(r.get("sentence", "")).replace(E(r.get("anchor", "")), f"<mark>{E(r.get('anchor', ''))}</mark>", 1)
        trs.append(f'<tr><td><input type="checkbox" name="ok" value="{i}" {chk}></td><td>{E(r["source_title"])}</td>'
                   f'<td>{sent}</td><td>{E(r["target_title"])}</td><td>{E(r.get("score", ""))}</td></tr>')
    inner = (f'<input type="hidden" name="file" value="{E(f.name)}">'
             f"<p class='muted'>{E(f.name)} - {len(rows)} پیشنهاد. جمله را بخوانید و فقط لینک‌های طبیعی و مرتبط را تیک بزنید.</p>"
             "<table><tr><th>تأیید</th><th>مقالهٔ مبدأ (لینک در آن گذاشته می‌شود)</th><th>جمله (عبارت لینک‌شونده زرد است)</th>"
             "<th>مقالهٔ مقصد</th><th>امتیاز</th></tr>" + "".join(trs) + "</table>"
             + btn("save", "ذخیرهٔ تیک‌ها", "alt") + btn("preview", "پیش‌نمایش اعمال")
             + "<hr>" + confirm_box() + btn("apply", "اعمال لینک‌های تیک‌خورده", "warn"))
    return page("لینک‌دهی", body + form("/links", inner) + out_box(output))


def seo_page(output=""):
    body = ("<h1>کلمه کلیدی، عنوان سئو و متا</h1>"
            + form("/seo", btn("todo", "۱) ساخت فهرست مقاله‌های ضعیف و پرامپت", "alt")))
    groups = sorted(pr.PROP.glob("seo_prompt_*.md"))
    if groups:
        key = groups[-1].name.rsplit("_", 1)[0]
        for i, g in enumerate(f for f in groups if f.name.rsplit("_", 1)[0] == key):
            body += (f"<div class='box'><b>پرامپت {i + 1}</b> ({E(g.name)}) "
                     f"<button type='button' class='alt' onclick=\"navigator.clipboard.writeText(document.getElementById('p{i}').value)\">کپی</button>"
                     f"<textarea id='p{i}' readonly>{E(g.read_text(encoding='utf-8'))}</textarea></div>")
    cur = (pr.PROP / "seo_answer.csv")
    inner = ("<p>۲) جواب CSV چت‌بات را اینجا بچسبانید (سرستون: id,new_focus_keyword,new_seo_title,new_meta_description):</p>"
             f"<textarea name='answer'>{E(cur.read_text(encoding='utf-8-sig')) if cur.exists() else ''}</textarea><br>"
             + btn("preview", "ذخیره و پیش‌نمایش") + "<hr>" + confirm_box() + btn("apply", "۳) اعمال در Yoast", "warn"))
    return page("سئو", body + form("/seo", inner) + out_box(output))


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
                 + "".join(f"<tr><td>{E(r['score'])}</td><td>{E(r['title'])}</td><td>{E(r['issues'])}</td></tr>" for r in rows) + "</table>")
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
        if not self._host_ok():
            return self._send(403, "forbidden")
        pages = {"/": home, "/links": links_page, "/seo": seo_page, "/inbox": inbox_page, "/reports": reports_page}
        fn = pages.get(urlparse(self.path).path)
        self._send(200, fn()) if fn else self._send(404, "not found")

    def do_POST(self):
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
            back = {"linkplan": links_page, "seotodo": seo_page, "inbox_dry": inbox_page, "inbox_send": inbox_page,
                    "audit": reports_page}.get(one("action"), home)
            return self._send(200, back(txt))
        if path == "/links":
            name = one("file")
            if not re.fullmatch(r"links_\d{8}_\d{4}\.csv", name) or not (pr.PROP / name).exists():
                return self._send(400, "bad file")
            fp = pr.PROP / name
            rows = pr.read_csv(fp)
            ok = set(f.get("ok", []))
            for i, r in enumerate(rows):
                r["approve"] = "y" if str(i) in ok else ""
            with open(fp, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
            do = one("do")
            if do == "save":
                txt = f"ذخیره شد: {len(ok)} لینک تأییدشده."
            elif do == "preview":
                txt = run(lambda: pr.apply_links(False, fp))
            elif do == "apply" and sure:
                txt = run(lambda: pr.apply_links(True, fp), yes=True)
            else:
                txt = "اعمال نشد: تیک تأیید را بزنید."
            return self._send(200, links_page(txt))
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
