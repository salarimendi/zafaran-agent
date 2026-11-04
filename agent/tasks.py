"""Stage 7 - one prioritized task queue (dashboard) built from what every stage already knows, plus your own notes and the hand-off bundle.
Read-only: it only reads reports/knowledge files the other stages wrote. It never calls AI and never touches the site.
Task state (done / snoozed / dismissed) and your notes live in knowledge/tasks/state.json."""
import hashlib
import json
import platform
import re
import sys
from datetime import date, datetime, timedelta

from .config import ROOT

TASKS = ROOT / "knowledge" / "tasks"
STATE = TASKS / "state.json"
REP, LOGS = ROOT / "reports", ROOT / "logs"
KINDS = {"task": "کار من", "bug": "خطا", "idea": "پیشنهاد"}
PRIO = {"high": 80, "normal": 50, "low": 20}


def _state():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"status": {}, "manual": []}


def _save(s):
    TASKS.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_name("state.json.tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def _key(kind, what):
    return kind + ":" + hashlib.sha1(str(what).encode("utf-8")).hexdigest()[:10]


def _load(p):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except ValueError:
        return None


def _age_days(p):
    return (datetime.now() - datetime.fromtimestamp(p.stat().st_mtime)).days if p.exists() else None


def collect(today=None):
    """-> list of task dicts (unfiltered by state). Each source is isolated: one failing source never hides the others."""
    T = []
    add = lambda kind, what, title, why, score, path, effort="": T.append({"key": _key(kind, what), "src": kind, "title": title, "why": why, "score": round(score), "path": path, "effort": effort})
    sources = (_health, _decay, _graph, _gsc, _occasions, _media, _jobs, _inbox, _fresh)
    for fn in sources:
        try:
            fn(add, today or date.today())
        except Exception as e:                                    # a broken source becomes a visible task, not a crash
            add("error", fn.__name__, f"منبع «{fn.__name__.strip('_')}» خوانده نشد ({type(e).__name__})", str(e)[:120], 10, "/feedback")
    return T


def _health(add, today):
    h = _load(ROOT / "knowledge" / "site" / "health_latest.json")
    if not h:
        add("health", "none", "بررسی سلامت فنی هنوز انجام نشده", "یک خزش فقط‌خواندنی می‌گوید لینک شکسته، noindex و مشکل نقشهٔ سایت کجاست.", 45, "/health", "۵ دقیقه")
        return
    sv = h.get("severity", {})
    if sv.get("H"):
        add("health", "H", f"{sv['H']} مشکل جدی سلامت فنی", "لینک شکسته، noindex یا خطای سرور؛ مستقیم روی دیده‌شدن اثر دارد.", 88 + min(sv["H"], 10) / 2, "/health", "۳۰ دقیقه")
    if sv.get("M"):
        add("health", "M", f"{sv['M']} مشکل متوسط سلامت فنی", "هر کدام جداگانه کم‌اثر است ولی روی هم اثر دارد.", 48, "/health", "۱ ساعت")


def _decay(add, today):
    from . import tracker
    r = tracker.decay(today=today)
    for it in r["items"][:5]:
        add("decay", it["id"], f"به‌روزرسانی مقالهٔ افت‌کرده: {it['title']}", "؛ ".join(it["reasons"]), 62 + min(it["score"], 28), "/refresh", "۳۰ دقیقه")


def _graph(add, today):
    g = _load(ROOT / "knowledge" / "graph" / "graph.json")
    if not g:
        add("graph", "none", "گراف لینک ساخته نشده", "مقاله‌های یتیم و خوشه‌های موضوعی را نشان می‌دهد.", 30, "/graph", "۲ دقیقه")
        return
    if g.get("oos_linked"):
        add("graph", "oos", f"{len(g['oos_linked'])} محصول ناموجود لینک دارد", "خواننده به صفحه‌ای می‌رسد که نمی‌تواند از آن بخرد.", 86, "/graph", "۱۵ دقیقه")
    if g.get("orphans"):
        add("graph", "orph", f"{len(g['orphans'])} مقالهٔ یتیم (بدون لینک ورودی)", "گوگل و خواننده کمتر پیدایشان می‌کنند؛ پیشنهاد لینک از گراف بسازید.", 52 + min(len(g["orphans"]), 10), "/graph", "۲۰ دقیقه")


def _gsc(add, today):
    from . import gsc
    d = gsc.load()
    if not d:
        add("gsc", "none", "خروجی Search Console وارد نشده", "بدون آن افت، فرصت صفحهٔ اول و کلمه‌های واقعی دیده نمی‌شود.", 50, "/gsc", "۱۰ دقیقه")
        return
    f = gsc.saved()[-1]
    if (_age_days(f) or 0) > 35:
        add("gsc", "old", f"خروجی Search Console {_age_days(f)} روز پیش است", "یک خروجی تازه با همان طول بازه بگیرید تا افت و اثر تغییرها دیده شود.", 44, "/gsc", "۱۰ دقیقه")
    a = gsc.analyze(d)
    for code, label, sc in (("near_page_one", "عبارت نزدیک صفحهٔ اول (جایگاه ۸ تا ۲۰)", 58), ("low_ctr_pages", "صفحه در صفحهٔ اول با CTR پایین (عنوان/متا ضعیف)", 56), ("quick_wins", "عبارت با جایگاه ۴ تا ۷ (برد سریع)", 50)):
        n = len(a.get(code) or [])
        if n:
            add("gsc", code, f"{n} {label}", "با بهبود عنوان، متا یا محتوا بیشترین بازده را دارد.", sc + min(n, 10) / 2, "/gsc", "۱ ساعت")


def _occasions(add, today):
    from . import occasions
    from .corpus import read_articles
    for r in occasions.calendar(read_articles() or None, today=today):
        if r["have"] == [] and r["days"] <= 90:
            add("occasion", r["name"] + str(r["date"]), f"مقاله برای مناسبت «{r['name']}» ندارید ({r['days']} روز دیگر)", f"انتشار تا {r['publish_jalali']}", 82 - r["days"] / 3, "/write", "۱ ساعت")
        elif r["state"] in ("soon", "late") and r["days"] <= 60 and r["have"]:
            add("occasion", "u" + r["name"] + str(r["date"]), f"مقالهٔ «{r['name']}» را تازه کنید ({r['days']} روز دیگر)", "پیش از مناسبت به‌روزرسانی کنید.", 60 - r["days"] / 4, "/refresh", "۳۰ دقیقه")


def _media(add, today):
    m = _load(ROOT / "knowledge" / "media" / "media.json")
    if m and m.get("no_alt"):
        add("media", "alt", f"{m['no_alt']} تصویر بدون Alt", "برای سئو تصویر و دسترس‌پذیری؛ پیشنهاد از متن مقاله ساخته می‌شود.", 40 + min(m["no_alt"], 20) / 2, "/media", "۱۰ دقیقه")
    c = _load(ROOT / "knowledge" / "media" / "cats.json")
    if c:
        n = sum(1 for x in c["items"] if {k for _, k in x["flags"]} & {"c_no_desc", "c_short", "c_dup"} and x["count"])
        if n:
            add("media", "cats", f"{n} دستهٔ محصول بدون توضیح کافی", "صفحهٔ دسته برای جست‌وجوی خرید مهم است.", 46, "/media", "۲۰ دقیقه")
    s = _load(ROOT / "knowledge" / "media" / "schema.json")
    if s:
        n = sum(1 for p in s["pages"] if any(sv in ("H", "M") for sv, _ in p["issues"]))
        if n:
            add("media", "schema", f"{n} صفحه مشکل Schema دارد", "قیمت/موجودی یا تاریخ ناقص یعنی کمتر شدن شانس نتیجهٔ غنی.", 47, "/media", "تنظیم Yoast/ووکامرس")
    elif not m:
        add("media", "none", "ممیزی تصاویر و دسته‌ها هنوز انجام نشده", "حجم، Alt، متن دسته‌ها و Schema را بررسی می‌کند.", 22, "/media", "۵ دقیقه")


def _jobs(add, today):
    from . import articlegen as ag
    for f in sorted(ag.DRAFTS.glob("a*/job.json")) if ag.DRAFTS.exists() else []:
        j = _load(f)
        if not j:
            continue
        link = f"/write?job={j['id']}"
        if j.get("wp") and j.get("dirty"):
            add("job", j["id"] + "d", f"تغییرات اعمال‌نشدهٔ مقاله: {j['title']}", "ویرایش‌های این برنامه هنوز در سایت نیست.", 66, link, "۵ دقیقه")
        elif j.get("wp") and j.get("state") != "published":
            add("job", j["id"] + "p", f"پیش‌نویس منتشرنشده: {j['title']}", "پیش‌نویس در سایت منتظر بازبینی و انتشار است.", 54, link, "۱۰ دقیقه")
        elif not j.get("wp") and j.get("text"):
            add("job", j["id"] + "w", f"مقالهٔ نوشته‌شده و ارسال‌نشده: {j['title']}", "متن آماده است؛ لینک، تصویر و ارسال به سایت مانده.", 42, link, "۱۵ دقیقه")


def _inbox(add, today):
    pa = [f for f in (ROOT / "inbox" / "articles").glob("*.md") if not f.name.startswith("_") and not f.with_suffix(".result.json").exists()]
    pp = [d for d in (ROOT / "inbox" / "products").glob("*") if d.is_dir() and not d.name.startswith("_") and not (d / "result.json").exists()]
    if pa or pp:
        add("inbox", "pending", f"در inbox ارسال‌نشده: {len(pp)} محصول، {len(pa)} مقاله", "پیش‌نمایش و ارسال از خانهٔ پنل.", 38, "/", "۵ دقیقه")


def _fresh(add, today):
    from .site import SITE
    s = SITE / "snapshot.json"
    a = _age_days(s)
    if a is None:
        add("fresh", "sync", "هنوز همگام‌سازی سایت انجام نشده", "همهٔ تحلیل‌ها به آن وابسته‌اند.", 90, "/", "۲ دقیقه")
    elif a > 7:
        add("fresh", "sync", f"همگام‌سازی سایت {a} روز پیش بوده", "موجودی محصولات و فهرست مقاله‌ها کهنه است؛ لینک به محصول ناموجود ممکن است.", 45 + min(a, 30) / 2, "/", "۲ دقیقه")


# ------------------------------------------------------------------ queue with state, manual tasks, notes
def queue(today=None, now=None):
    now = now or datetime.now()
    st = _state()
    T = collect(today)
    for m in st["manual"]:
        if m["kind"] == "task":
            T.append({"key": m["key"], "src": "mine", "title": m["title"], "why": m.get("note", ""), "score": PRIO.get(m.get("prio"), 50) + 5, "path": "/tasks", "effort": ""})
    out = []
    for t in T:
        s = st["status"].get(t["key"])
        if s:
            if s["s"] == "dismissed":
                continue
            if s["s"] == "snoozed" and datetime.fromisoformat(s["until"]) > now:
                continue
            if s["s"] == "done" and (now - datetime.fromisoformat(s["at"])).days < 14:
                continue
        out.append(t)
    out.sort(key=lambda t: (-t["score"], t["title"]))
    return out


def set_status(key, s, days=7, now=None):
    now = now or datetime.now()
    st = _state()
    if s == "reset":
        st["status"].pop(key, None)
    else:
        st["status"][key] = {"s": s, "at": now.isoformat(timespec="seconds"), "until": (now + timedelta(days=days)).isoformat(timespec="seconds")}
    _save(st)


def hidden():
    st = _state()
    return [{"key": k, **v} for k, v in st["status"].items()]


def add_note(kind, title, note="", prio="normal", now=None):
    if kind not in KINDS:
        raise ValueError("نوع نامعتبر است.")
    title = " ".join(str(title).split())
    if not title:
        raise ValueError("عنوان خالی است.")
    st = _state()
    m = {"key": "m" + hashlib.sha1(f"{datetime.now().isoformat()}{title}".encode()).hexdigest()[:9], "kind": kind, "title": title[:200], "note": str(note).strip()[:3000], "prio": prio if prio in PRIO else "normal",
         "created": (now or datetime.now()).isoformat(timespec="seconds"), "done": False}
    st["manual"].append(m)
    _save(st)
    return m


def notes(kind=None):
    return [m for m in _state()["manual"] if (kind is None or m["kind"] == kind)]


def finish_note(key, done=True):
    st = _state()
    for m in st["manual"]:
        if m["key"] == key:
            m["done"] = done
    _save(st)


def remove_note(key):
    st = _state()
    st["manual"] = [m for m in st["manual"] if m["key"] != key]
    _save(st)


# ------------------------------------------------------------------ hand-off: errors and suggestions for the developer (you give this file to Claude)
def log_error(route, text):
    LOGS.mkdir(exist_ok=True)
    with open(LOGS / "panel_errors.log", "a", encoding="utf-8") as f:
        f.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} {route}\n{text[-2500:]}\n")


SECRET = re.compile(r"(?i)((?:key|secret|token|password|passwd|authorization|api[_-]?key|cookie)\w*\s*[=:]\s*)\S+|sk-[A-Za-z0-9_\-]{8,}|(?<=//)[^/\s:@]+:[^/\s@]+@")


def redact(s):
    return SECRET.sub(lambda m: (m.group(1) + "***") if m.group(1) else "***", s or "")


def _tail(p, n):
    return p.read_text(encoding="utf-8", errors="replace")[-n:] if p.exists() else "(نیست)"


def bundle(now=None):
    """-> (path, text). One markdown file with your notes, the latest errors/log tails and versions - secrets removed. Give it to the developer (Claude)."""
    from . import panel
    now = now or datetime.now()
    N = notes()
    L = [f"# بستهٔ گزارش برای توسعه‌دهنده — {now:%Y-%m-%d %H:%M}", "", f"- نسخهٔ پنل: {panel.VERSION} · پایتون {sys.version.split()[0]} · {platform.platform()}", ""]
    for kind, title in (("bug", "خطاهایی که ثبت کرده‌ام"), ("idea", "پیشنهادها و کارهای تازه"), ("task", "کارهای من")):
        items = [m for m in N if m["kind"] == kind and not m["done"]]
        L += [f"## {title} ({len(items)})"] + ([f"- **{m['title']}**" + (f"\n  {m['note']}" if m["note"] else "") + f"  ({m['created'][:16]})" for m in items] or ["- (چیزی ثبت نشده)"]) + [""]
    L += ["## آخرین خطاهای پنل", "```", redact(_tail(LOGS / "panel_errors.log", 5000)), "```", "", "## آخرین گزارش روتین", redact(_tail(REP / "routine_latest.md", 3500)), "",
          "## انتهای logs/routine.log", "```", redact(_tail(LOGS / "routine.log", 3000)), "```", "", "## آخرین تغییرهایی که برنامه روی سایت داده (logs/actions.jsonl)", "```",
          redact("\n".join((LOGS / "actions.jsonl").read_text(encoding="utf-8").splitlines()[-30:]) if (LOGS / "actions.jsonl").exists() else "(نیست)"), "```"]
    text = "\n".join(L)
    REP.mkdir(exist_ok=True)
    p = REP / f"handoff_{now:%Y%m%d_%H%M}.md"
    p.write_text(text, encoding="utf-8")
    return p, text
