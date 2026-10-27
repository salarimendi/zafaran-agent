"""Scheduled, READ-ONLY routine: check -> sync -> export -> audit -> report. Never changes the site."""
import contextlib
import io
import json
from datetime import datetime

from .config import ROOT

REP, LOGS = ROOT / "reports", ROOT / "logs"


def _step(name, fn, log):
    buf, ok, ret = io.StringIO(), True, None
    with contextlib.redirect_stdout(buf):
        try:
            ret = fn()
        except SystemExit as e:
            ok = False
            print(e)
        except Exception as e:
            ok = False
            print(f"ERROR {type(e).__name__}: {e}")
    out = buf.getvalue()
    log.write(f"\n===== {name}: {'OK' if ok else 'FAILED'} =====\n{out[-3000:]}\n")
    return ok, ret


def _diff(prev, new):
    L = []
    for kind, key, label in (("posts", "title", "مقاله"), ("products", "name", "محصول"), ("pages", "title", "صفحه")):
        pm = {i["id"]: i for i in (prev or {}).get(kind, [])}
        nm = {i["id"]: i for i in new.get(kind, [])}
        for i in nm:
            if i not in pm:
                L.append(f"- {label} جدید ({nm[i].get('status')}): {nm[i][key]}")
            elif pm[i].get("status") != nm[i].get("status"):
                L.append(f"- {label} «{nm[i][key]}»: وضعیت {pm[i].get('status')} ← {nm[i].get('status')}")
            elif kind == "posts" and pm[i].get("modified") != nm[i].get("modified"):
                L.append(f"- مقاله ویرایش شد: {nm[i][key]}")
            elif kind == "products" and pm[i].get("price") != nm[i].get("price"):
                L.append(f"- قیمت «{nm[i][key]}»: {pm[i].get('price')} ← {nm[i].get('price')}")
        L += [f"- {label} حذف شد: {pm[i][key]}" for i in pm if i not in nm]
    return L


def run():
    from . import audit, cli, export, site
    LOGS.mkdir(exist_ok=True)
    REP.mkdir(exist_ok=True)
    now = datetime.now()
    steps, ok_all = [], True
    with open(LOGS / "routine.log", "a", encoding="utf-8") as log:
        log.write(f"\n\n######## routine {now:%Y-%m-%d %H:%M} ########")
        ok, _ = _step("check", cli.check, log)
        conn_ok = ok and not any(s == cli.FAIL for s, _, _ in cli.results)
        steps.append(("بررسی اتصال", conn_ok))
        prev = new = None
        audit_res = None
        if conn_ok:
            prev = site.load_snapshot()
            for name, label, fn in (("sync", "همگام‌سازی سایت", site.sync), ("export", "خروجی مقاله‌ها", export.export_articles)):
                ok, _ = _step(name, fn, log)
                steps.append((label, ok))
            ok, audit_res = _step("audit", audit.audit, log)
            steps.append(("گزارش سئو", ok and audit_res is not None))
            new = site.load_snapshot()
        ok_all = all(ok for _, ok in steps)
    # compare with the previous run
    last = REP / "last_audit.json"
    old_codes = json.loads(last.read_text(encoding="utf-8")).get("codes", {}) if last.exists() else None
    L = [f"# گزارش روتین - {now:%Y-%m-%d %H:%M}", "", "**وضعیت: " + ("سالم" if ok_all else "مشکل دارد (جزئیات: logs\\routine.log)") + "**", "",
         "## مراحل"] + [f"- {n}: {'✓' if ok else '✗ ناموفق'}" for n, ok in steps]
    if new:
        d = _diff(prev, new)
        L += ["", "## تغییرات سایت نسبت به اجرای قبلی"] + (d[:30] if prev else ["(اجرای اول؛ مبنای مقایسه ذخیره شد)"])
        if prev and not d:
            L.append("- تغییری نبود")
        if len(d) > 30:
            L.append(f"- ... و {len(d) - 30} مورد دیگر")
    if audit_res:
        L += ["", f"## سئو ({audit_res['n']} مقاله)"]
        for code, n in sorted(audit_res["codes"].items(), key=lambda x: -x[1]):
            delta = "" if old_codes is None else f" ({n - old_codes.get(code, 0):+d})"
            L.append(f"- {code}: {n}{delta}")
        for code in (old_codes or {}):
            if code not in audit_res["codes"]:
                L.append(f"- {code}: 0 ({-old_codes[code]:+d}) ✓ رفع شد")
        L += ["", "بدترین مقاله‌ها:"] + [f"- ({sc}) {t} [id {i}]" for i, t, sc in audit_res["worst"]]
        last.write_text(json.dumps({"codes": audit_res["codes"], "time": now.isoformat(timespec="seconds")}), encoding="utf-8")
    pend_p = [d for d in (ROOT / "inbox" / "products").glob("*") if d.is_dir() and not d.name.startswith("_") and not (d / "result.json").exists()]
    pend_a = [f for f in (ROOT / "inbox" / "articles").glob("*.md") if not f.name.startswith("_") and not f.with_suffix(".result.json").exists()]
    L += ["", "## منتظر شما", f"- محصول در inbox ارسال‌نشده: {len(pend_p)}", f"- مقاله در inbox ارسال‌نشده: {len(pend_a)}"]
    (REP / f"routine_{now:%Y%m%d_%H%M}.md").write_text("\n".join(L), encoding="utf-8")
    (REP / "routine_latest.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    return ok_all
