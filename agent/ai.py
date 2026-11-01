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
    if not confirm(len(batches), "AI call(s)", to="to the AI provider (spends credit; cached ones are free; NOTHING is sent to your website)"):
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
    if not confirm(len(batches), "AI call(s)", to="to the AI provider (spends credit; NOTHING is sent to your website)"):
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


def ai_style(go):
    """One cheap call: turn knowledge/style/style_pack.md into a short knowledge/style/style_guide.md."""
    from . import style
    c = llm.cfg()
    f = style.OUT / "style_pack.md"
    if not f.exists():
        style.build()
    if not f.exists():
        return
    head, _, rest = f.read_text(encoding="utf-8").partition("\n---\n")
    msgs = [{"role": "system", "content": head.strip()}, {"role": "user", "content": rest.strip()[:9000]}]
    est_in = llm.est_tokens(sum(len(m["content"]) for m in msgs))
    print("Style pack -> style guide (1 call, input trimmed to 9000 characters).")
    _brief(c, 1, est_in, min(c["max_out"], 1300))
    if not go:
        print("Estimate only.")
        return
    if not confirm(1, "AI call", to="to the AI provider (spends credit; NOTHING is sent to your website)"):
        print("Cancelled.")
        return
    text, info = llm.chat(msgs, max_out=min(c["max_out"], 1300), task="style")
    out = style.OUT / "style_guide_ai.md"
    if out.exists():
        out.rename(style.OUT / f"style_guide_ai_prev_{datetime.now():%Y%m%d_%H%M%S}.md")
    out.write_text(text.strip() + "\n", encoding="utf-8")
    print(f"Saved {out} ({len(text)} chars). Cost: ${info['cost']:.4f}.")
    print("This is only a DRAFT: it does NOT replace your main guide (style_guide.md). In the panel (Tone page) you can compare and choose.")


GAP_SYS = ("تو استراتژیست محتوای سئوی فارسی برای یک فروشگاه زعفران هستی. هر خط یک خوشه از عبارت‌های جست‌وجو است که سایت صفحه‌ای برایشان ندارد: "
           "شماره|عبارت اصلی|مجموع نمایش|نیت(info/commercial/unknown)|چند عبارت نمونه. خوشه‌هایی که یک نیت جست‌وجو دارند را ادغام کن و برای هر گروه یک موضوع پیشنهاد بده: "
           "g = آرایهٔ شمارهٔ خوشه‌ها، type = article یا category یا product، t = عنوان پیشنهادی (حداکثر ۶۰ کاراکتر)، k = کلمه کلیدی کانونی (۲ تا ۴ کلمه). "
           "هر شماره فقط در یک گروه بیاید. ادعای درمانی و پزشکی ننویس. فقط یک آرایهٔ JSON از اشیای {\"g\":[..],\"type\":\"\",\"t\":\"\",\"k\":\"\"}؛ هیچ توضیح دیگری ننویس.")
OUT_PER_GROUP = 70


def _gap_input(cl):
    return "\n".join(f"{c['id']}|{c['label']}|{c['impr']}|{c['intent'] if c['intent'] in ('info', 'commercial') else 'unknown'}|" + ";".join(q["key"] for q in c["queries"][1:4])
                     for c in cl)


def _gap_problems(r, valid, used, focus):
    from . import keywords as kw
    p = []
    g = r.get("g")
    if not isinstance(g, list) or not g or not all(isinstance(x, int) and x in valid for x in g):
        p.append("شمارهٔ خوشه نامعتبر است")
    elif any(x in used for x in g):
        p.append("یک خوشه در دو گروه آمده است")
    if r.get("type") not in ("article", "category", "product"):
        p.append("type باید article یا category یا product باشد")
    t, k = str(r.get("t", "")).strip(), str(r.get("k", "")).strip()
    if not t or len(t) > 60:
        p.append(f"عنوان {len(t)} کاراکتر است (حداکثر ۶۰)")
    if not k or not 1 <= len(k.split()) <= 5:
        p.append("کلمه کلیدی باید ۱ تا ۵ کلمه باشد")
    elif k:
        kt = kw.topic_set(k)
        for fk in focus:
            if norm(fk) == norm(k) or (len(kt) >= 2 and kw.jacc(kt, kw.topic_set(fk)) >= 0.66):
                p.append(f"کلمه کلیدی شبیه کلمه کلیدی مقالهٔ موجود است: «{fk}» (هم‌نوع‌خواری)")
                break
    return p


def ai_gap(go):
    """ONE batched call: regroup the code-made gap clusters semantically and suggest topics. Nothing is sent to the website."""
    from . import keywords as kw
    c = llm.cfg()
    res = kw.load_analysis()
    if not res or not res.get("gap_clusters"):
        print("No content-gap clusters yet. Build the keyword analysis first (needs a Search Console import).")
        return
    n_max = max(5, min(30, (c["max_out"] - 60) // OUT_PER_GROUP))
    cl = res["gap_clusters"][:n_max]
    msgs = [{"role": "system", "content": GAP_SYS}, {"role": "user", "content": _gap_input(cl)}]
    est_in = llm.est_tokens(sum(len(m["content"]) for m in msgs))
    est_out = min(c["max_out"], len(cl) * OUT_PER_GROUP + 60)
    print(f"{len(cl)} gap cluster(s) (top by impressions; max {n_max} fit one answer at LLM_MAX_OUTPUT_TOKENS={c['max_out']}), 1 call.")
    _brief(c, 1, est_in, est_out)
    if not go:
        print("Estimate only (nothing sent).")
        return
    if not confirm(1, "AI call", to="to the AI provider (spends credit; NOTHING is sent to your website)"):
        print("Cancelled.")
        return
    text, info = llm.chat(msgs, max_out=est_out, task="gap")
    valid, used, items = {x["id"] for x in cl}, set(), []
    focus = [d["focus"] for d in kw.build_docs() if d["kind"] == "article" and d["focus"]]
    byid = {x["id"]: x for x in cl}
    for r in llm.parse_json_list(text):
        if not isinstance(r, dict):
            continue
        pb = _gap_problems(r, valid, used, focus)
        g = [x for x in r.get("g", []) if isinstance(x, int) and x in valid and x not in used] if isinstance(r.get("g"), list) else []
        if not g:
            continue
        used.update(g)
        qs = [q for x in g for q in byid[x]["queries"]]
        items.append({"g": g, "type": r.get("type", ""), "t": str(r.get("t", "")).strip(), "k": str(r.get("k", "")).strip(),
                      "impr": sum(byid[x]["impr"] for x in g), "queries": [q["key"] for q in qs[:6]], "notes": "؛ ".join(pb)})
    left = [x for x in valid if x not in used]
    out = kw.KW / "topics_ai.json"
    if out.exists():
        out.rename(kw.KW / f"topics_ai_prev_{datetime.now():%Y%m%d_%H%M%S}.json")
    out.write_text(json.dumps({"built": datetime.now().isoformat(timespec="seconds"), "cost": round(info["cost"], 6), "left_out": left,
                               "items": sorted(items, key=lambda x: -x["impr"])}, ensure_ascii=False), encoding="utf-8")
    clean = sum(1 for i in items if not i["notes"])
    print(f"\n{len(items)} topic suggestion(s) saved ({clean} pass all checks; the others carry a note). Cost this run: ${info['cost']:.4f}")
    print("This is only a list of IDEAS: nothing was written to the site. Review it in the panel (Keywords page).")
