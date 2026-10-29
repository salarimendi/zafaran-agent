"""AI tasks. Token-saving rules: AI sees only a few cheap facts per item (title, 4 headings, 300-char intro),
items are batched, output is compact JSON, local code validates, and only FAILED rows get a (small) repair call."""
import json
from datetime import datetime
from pathlib import Path

from . import llm
from . import proposals as pr
from .common import confirm, norm
from .corpus import h2_list, paragraphs, strip_md
from .llm import LLMError

SEO_SYS = ("تو متخصص سئوی فارسی هستی. برای هر مقاله این‌ها را بنویس: k = کلمه کلیدی کانونی (۲ تا ۴ کلمه، دقیقاً موضوع مقاله)، "
           "t = عنوان سئو (حداکثر ۶۰ کاراکتر، شامل k)، m = متا دیسکریپشن (۱۱۰ تا ۱۶۰ کاراکتر، شامل k، جذاب و بدون اغراق). "
           "اگر در cur مقدار مناسبی هست همان را نگه دار. فقط یک آرایهٔ JSON با اشیای {\"id\",\"k\",\"t\",\"m\"} بده؛ هیچ توضیح دیگری ننویس.")
FIX_SYS = ("هر مورد مشکلاتی (problems) دارد. همان فیلدهای k و t و m را طوری اصلاح کن که مشکل‌ها برطرف شود و قواعد قبلی "
           "(k در t و m باشد؛ t حداکثر ۶۰ و m بین ۱۱۰ تا ۱۶۰ کاراکتر) رعایت شود. فقط آرایهٔ JSON با {\"id\",\"k\",\"t\",\"m\"}.")
JUDGE_SYS = ("برای هر خط بسنج لینک‌دادن عبارت (anchor) داخل جمله به مقالهٔ مقصد، برای خواننده طبیعی و مفید است یا نه. "
             "قالب هر خط: شماره|anchor|جمله|عنوان مقصد. امتیاز ۱ تا ۵ (۵ = کاملاً مرتبط و طبیعی، ۱ = بی‌ربط یا اجباری). "
             "فقط یک آرایهٔ JSON از اعداد، به همان ترتیب خطوط.")
BATCH_SEO, BATCH_JUDGE, OUT_PER_ITEM = 8, 20, 170


def _brief(c, calls, est_in, est_out):
    cost = llm.cost_of(c, est_in, est_out)
    print(f"{calls} API call(s). Estimated tokens: in ~{est_in}, out <= {est_out}"
          + (f", cost <= ${cost:.4f}" if c["pin"] or c["pout"] else " (set prices in .env to see cost)"))
    if c["budget"]:
        print(f"Budget: ${c['budget']} / month, already spent ${llm.spent_this_month():.4f}")


def _item(a):
    intro = strip_md((paragraphs(a["body"]) or [""])[0])[:300]
    o = {"id": a["id"], "title": a["title"], "h2": h2_list(a["body"])[:4], "intro": " ".join(intro.split())}
    cur = {k: v for k, v in (("k", a.get("focus_keyword")), ("t", a.get("seo_title")), ("m", a.get("meta_description"))) if v}
    if cur:
        o["cur"] = cur
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def _problems(r, others):
    k, t, m, p = r["k"], r["t"], r["m"], []
    if len(t) > 60:
        p.append(f"t is {len(t)} chars (max 60)")
    if not 110 <= len(m) <= 160:
        p.append(f"m is {len(m)} chars (need 110-160)")
    if norm(k) not in norm(t):
        p.append("k not inside t")
    if norm(k) not in norm(m):
        p.append("k not inside m")
    if norm(k) in others:
        p.append(f"k already used by another article; choose a different keyword (not: {k})")
    return p


def ai_seo(go, limit=None):
    c = llm.cfg()
    arts = pr.read_articles()
    todo = [a for a in arts if pr.weak(a)]
    if limit:
        todo = todo[:limit]
    if not todo:
        print("No weak articles (export first, or all good).")
        return
    batches = [todo[i:i + BATCH_SEO] for i in range(0, len(todo), BATCH_SEO)]
    msgs = [[{"role": "system", "content": SEO_SYS}, {"role": "user", "content": "\n".join(_item(a) for a in b)}] for b in batches]
    est_in = sum(llm.est_tokens(sum(len(m["content"]) for m in ms)) for ms in msgs)
    est_out = sum(min(c["max_out"], len(b) * OUT_PER_ITEM + 40) for b in batches)
    print(f"{len(todo)} weak article(s) in {len(batches)} batch(es) of up to {BATCH_SEO}.")
    _brief(c, len(batches), est_in, est_out)
    if not go:
        print("Estimate only (nothing sent). Use the menu/panel button to run.")
        return
    if not confirm(len(batches), "AI call(s) (cached answers cost nothing)"):
        print("Cancelled.")
        return
    existing = {norm(a["focus_keyword"]): a["id"] for a in arts if a.get("focus_keyword")}
    by_id, rows, spent = {str(a["id"]): a for a in todo}, {}, 0.0
    for b, ms in zip(batches, msgs):
        try:
            text, info = llm.chat(ms, max_out=min(c["max_out"], len(b) * OUT_PER_ITEM + 40), task="seo")
            spent += info["cost"]
            for r in llm.parse_json_list(text):
                if str(r.get("id")) in by_id and all(r.get(x) for x in ("k", "t", "m")):
                    rows[str(r["id"])] = {x: str(r[x]).strip() for x in ("k", "t", "m")}
        except LLMError as e:
            print(f"  batch failed: {e}")
            if "Budget" in str(e) or "cap" in str(e) or "HTTP 40" in str(e):
                break
    # local validation; only failing rows get one small repair call
    taken = set()
    bad = {}
    for i, r in rows.items():
        others = {k for k, v in existing.items() if str(v) != i} | taken
        pb = _problems(r, others)
        if pb:
            bad[i] = pb
        else:
            taken.add(norm(r["k"]))
    if bad:
        print(f"{len(bad)} row(s) failed local checks; one repair call...")
        items = [{"id": by_id[i]["id"], "title": by_id[i]["title"], **rows[i], "problems": bad[i]} for i in bad]
        try:
            text, info = llm.chat([{"role": "system", "content": FIX_SYS},
                                   {"role": "user", "content": "\n".join(json.dumps(x, ensure_ascii=False, separators=(",", ":")) for x in items)}],
                                  max_out=min(c["max_out"], len(items) * OUT_PER_ITEM + 40), task="seo_fix")
            spent += info["cost"]
            for r in llm.parse_json_list(text):
                i = str(r.get("id"))
                if i in bad and all(r.get(x) for x in ("k", "t", "m")):
                    rows[i] = {x: str(r[x]).strip() for x in ("k", "t", "m")}
        except LLMError as e:
            print(f"  repair failed: {e}")
    out = []
    for i, r in rows.items():
        others = {k for k, v in existing.items() if str(v) != i}
        notes = "; ".join(_problems(r, others))
        out.append([by_id[i]["id"], r["k"], r["t"], r["m"], notes])
    f = pr.PROP / "seo_answer.csv"
    if f.exists():
        f.rename(pr.PROP / f"seo_answer_prev_{datetime.now():%Y%m%d_%H%M%S}.csv")
    pr.write_csv(f, ["id", "new_focus_keyword", "new_seo_title", "new_meta_description", "ai_notes"], out)
    clean = sum(1 for r in out if not r[4])
    print(f"\n{len(out)} suggestion(s) written to {f} ({clean} pass all checks; others have notes). Cost this run: ${spent:.4f}")
    print("Review with 'Apply SEO answer' (preview first). Nothing was changed on the site.")


def ai_judge(go, path=None, limit=None):
    c = llm.cfg()
    f = Path(path) if path else next((x for x in sorted(pr.PROP.glob("links_2*.csv"), reverse=True) if not x.stem.endswith("_ai")), None)
    if not f:
        print("No link plan. Run 'link plan' first.")
        return
    rows = pr.read_csv(f)
    if limit:
        rows = rows[:limit]
    batches = [rows[i:i + BATCH_JUDGE] for i in range(0, len(rows), BATCH_JUDGE)]

    def msg(b):
        return [{"role": "system", "content": JUDGE_SYS},
                {"role": "user", "content": "\n".join(f"{i + 1}|{r['anchor']}|{r['sentence'][:200]}|{r['target_title']}" for i, r in enumerate(b))}]
    msgs = [msg(b) for b in batches]
    est_in = sum(llm.est_tokens(sum(len(m["content"]) for m in ms)) for ms in msgs)
    est_out = sum(min(c["max_out"], len(b) * 4 + 20) for b in batches)
    print(f"{f.name}: {len(rows)} proposal(s) in {len(batches)} batch(es).")
    _brief(c, len(batches), est_in, est_out)
    if not go:
        print("Estimate only (nothing sent).")
        return
    if not confirm(len(batches), "AI call(s)"):
        print("Cancelled.")
        return
    spent = 0.0
    for b, ms in zip(batches, msgs):
        try:
            text, info = llm.chat(ms, max_out=len(b) * 4 + 20, task="judge")
            spent += info["cost"]
            sc = llm.parse_json_list(text)
            if len(sc) != len(b):
                raise LLMError(f"expected {len(b)} scores, got {len(sc)}")
            for r, s in zip(b, sc):
                r["ai_score"] = int(s)
        except (LLMError, ValueError, TypeError) as e:
            print(f"  batch failed: {e}")
            if "Budget" in str(e) or "cap" in str(e) or "HTTP 40" in str(e):
                break
    hdr = list(rows[0].keys())
    if "ai_score" not in hdr:
        hdr.append("ai_score")
    for r in rows:
        r.setdefault("ai_score", "")
        if r["ai_score"] != "" and int(r["ai_score"]) >= 4 and not r.get("approve"):
            r["approve"] = "y"  # pre-ticked suggestion; you still review and confirm
    out = f.with_name(f.stem + "_ai.csv")
    pr.write_csv(out, hdr, [[r.get(k, "") for k in hdr] for r in rows])
    scored = [int(r["ai_score"]) for r in rows if r["ai_score"] != ""]
    print(f"\nScored {len(scored)} of {len(rows)}: " + ", ".join(f"{s}★={scored.count(s)}" for s in (5, 4, 3, 2, 1)) + f". Cost: ${spent:.4f}")
    print(f"Saved {out.name}: score>=4 are pre-ticked. Open the links page and review before applying.")
