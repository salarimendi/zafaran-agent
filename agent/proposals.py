"""Proposal -> review -> apply workflows (no AI needed; an AI can later FILL the proposal files).
 1. link_plan   : finds natural internal-link spots (exact phrases already in the text) -> proposals/links_*.csv
 2. apply_links : inserts the approved links (backup first, preview, confirmation)
 3. seo_todo    : lists weak articles + writes a ready prompt for a chat model (later: an API fills it)
 4. apply_seo   : applies a CSV of new focus keyword / SEO title / meta description to Yoast fields."""
import csv
import io
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from .common import StepError, confirm, ensure, log_action, norm, num
from .config import ROOT, load_config
from .corpus import h2_list, paragraphs, read_articles
from .wp_client import WPClient

PROP = ROOT / "proposals"
BACKUP = ROOT / "backups"
WORD = "\\u0600-\\u06FF\\u200cA-Za-z0-9"
YES = {"y", "yes", "1", "x", "true", "بله", "✓"}
MAX_NEW_PER_SOURCE, PER_TARGET = 3, 3


# ---------- helpers ----------
def read_csv(path):
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1256")  # Excel "CSV" on Persian Windows
    first = text.splitlines()[0] if text else ""
    delim = ";" if first.count(";") > first.count(",") else ","
    return list(csv.DictReader(io.StringIO(text), delimiter=delim))


def write_csv(path, header, rows):
    PROP.mkdir(exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def latest(pattern):
    fs = sorted(PROP.glob(pattern))
    return fs[-1] if fs else None


def boundary(anchor):
    return re.compile(rf"(?<![{WORD}]){re.escape(anchor)}(?![{WORD}])")


def bare(h):
    return h.lower().removeprefix("www.")


def _path(u):
    return unquote(urlparse(u).path).rstrip("/")


def search_text(body):
    """Body text where an anchor may be placed: no headings/images/tables/code; existing links masked."""
    out = []
    for ln in body.split("\n"):
        s = ln.strip()
        if not s or s.startswith(("#", "!", "|", ">", "```")):
            continue
        out.append(re.sub(r"\[[^\]]*\]\([^)]*\)", lambda m: " " * len(m.group(0)), ln))
    return "\n".join(out).replace("**", "  ").replace("*", " ")


def sentence_of(text, start, end):
    a = max(text.rfind(".", 0, start), text.rfind("؟", 0, start), text.rfind("!", 0, start), text.rfind("\n", 0, start))
    cands = [i for i in (text.find(".", end), text.find("؟", end), text.find("!", end), text.find("\n", end)) if i != -1]
    b = min(cands) if cands else len(text)
    return " ".join(text[a + 1:b + 1].split())[:260]


def clean_title(t):
    t = re.split(r"\s[|–—-]\s", t)[0].strip().rstrip("؟?").strip()
    t = re.sub(r"^(معرفی|آشنایی با)\s+", "", t)
    return re.sub(r"\s+چیست$", "", t).strip()


def anchors_of(a):
    cands = []
    if a.get("focus_keyword"):
        cands.append((a["focus_keyword"].strip(), 10))
    cands.append((clean_title(a.get("title", "")), 8))
    cands += [(t, 5) for t in (a.get("tags") or [])]
    seen, out = set(), []
    for t, w in cands:
        k = norm(t)
        if k and k not in seen and (len(t.split()) >= 2 or len(t) >= 8):
            seen.add(k)
            out.append((t, w))
    return out


# ---------- 1. link plan ----------
def link_plan():
    arts = read_articles()
    if len(arts) < 5:
        print("Export the articles first (menu 3).")
        return
    host = bare(urlparse(arts[0].get("url", "")).netloc)
    by_path = {_path(a["url"]): a for a in arts if a.get("url")}
    outl, inbound = defaultdict(set), Counter()
    for a in arts:
        for u in re.findall(r"\]\((https?://[^)\s]+|/[^)\s]*)\)", a["body"]):
            pu = urlparse(u)
            t = by_path.get(_path(u)) if bare(pu.netloc) in ("", host) else None
            if t and t is not a:
                outl[a["file"]].add(t["file"])
                inbound[t["file"]] += 1
    text = {a["file"]: search_text(a["body"]) for a in arts}
    targets = [a for a in arts if not inbound[a["file"]]]
    used, rows, nocand = Counter(), [], []
    ranked = []
    for t in targets:
        cands = []
        for anc, w in anchors_of(t):
            pat = boundary(anc)
            df = sum(1 for a in arts if pat.search(text[a["file"]]))
            if df > 0.25 * len(arts):
                continue  # too generic to be a good anchor
            for s in arts:
                if s is t or t["file"] in outl[s["file"]]:
                    continue
                m = pat.search(text[s["file"]])
                if m:
                    shared = set(s.get("categories") or []) & set(t.get("categories") or [])
                    sc = w + 2 * len(anc.split()) + (3 if shared else 0)
                    cands.append((sc, s, anc, sentence_of(text[s["file"]], m.start(), m.end())))
        best = {}
        for c in sorted(cands, key=lambda x: -x[0]):
            best.setdefault(c[1]["file"], c)
        ranked.append((t, sorted(best.values(), key=lambda x: -x[0])))
    for t, cands in sorted(ranked, key=lambda x: len(x[1])):
        picked = 0
        for sc, s, anc, sent in cands:
            if picked >= PER_TARGET:
                break
            if used[s["file"]] >= MAX_NEW_PER_SOURCE:
                continue
            used[s["file"]] += 1
            picked += 1
            rows.append(["", s["id"], s["title"], s["url"], anc, t["id"], t["title"], t["url"], sent, sc, s.get("modified", "")])
        if not picked:
            nocand.append([t["id"], t["title"], t["url"], " | ".join(a for a, _ in anchors_of(t))])
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    p = PROP / f"links_{ts}.csv"
    write_csv(p, ["approve", "source_id", "source_title", "source_url", "anchor", "target_id", "target_title",
                  "target_url", "sentence", "score", "source_modified"], rows)
    write_csv(PROP / f"links_no_candidates_{ts}.csv", ["target_id", "title", "url", "anchors_tried"], nocand)
    print(f"{len(arts)} articles, {len(targets)} without inbound links.")
    print(f"{len(rows)} link proposals for {len({r[5] for r in rows})} of them; {len(nocand)} have no natural phrase anywhere.")
    print(f"\nOpen in Excel: {p}\nWrite y in the 'approve' column for links that look right (read the 'sentence' column),")
    print("then save as 'CSV UTF-8' and run menu 11. Targets with no natural phrase: links_no_candidates_*.csv")


# ---------- 2. apply links ----------
TAG_RE = re.compile(r"(<[^>]*>)")
BLOCKED = {"a", "h1", "h2", "h3", "h4", "h5", "h6", "script", "style", "figcaption", "button", "code", "pre"}


def insert_link(raw, anchor, url):
    parts, blocked, pat = TAG_RE.split(raw), 0, boundary(anchor)
    for i, p in enumerate(parts):
        if i % 2:
            m = re.match(r"<(/?)([a-zA-Z0-9]+)", p)
            if m:
                name = m.group(2).lower()
                if name in BLOCKED:
                    if m.group(1):
                        blocked = max(blocked - 1, 0)
                    elif not p.rstrip().endswith("/>"):
                        blocked += 1
            continue
        if blocked:
            continue
        m = pat.search(p)
        if m:
            snip = p[max(0, m.start() - 45):m.start()] + "[[" + m.group(0) + "]]" + p[m.end():m.end() + 45]
            parts[i] = p[:m.start()] + f'<a href="{url}">{m.group(0)}</a>' + p[m.end():]
            return "".join(parts), " ".join(snip.split())
    return None, None


def apply_links(go, path=None):
    f = Path(path) if path else latest("links_2*.csv")
    if not f or not f.exists():
        print("No links_*.csv in proposals. Run menu 10 first.")
        return
    rows = [r for r in read_csv(f) if str(r.get("approve", "")).strip().lower() in YES]
    print(f"{f.name}: {len(rows)} approved link(s).")
    if not rows:
        print("Nothing approved. Write y in the 'approve' column.")
        return
    c = WPClient(load_config())
    c.detect_mode()
    by_src = defaultdict(list)
    for r in rows:
        by_src[num(r["source_id"])].append(r)
    jobs = []
    for sid, rs in by_src.items():
        try:
            cur = ensure(c.get(f"wp/v2/posts/{sid}", params={"context": "edit",
                         "_fields": "id,status,modified,content,title"}), f"read post {sid}")
        except StepError as e:
            print(f"! post {sid}: {e}")
            continue
        raw = cur["content"]["raw"]
        print(f"\n== {rs[0]['source_title']} (id {sid}, {cur.get('status')})")
        done = 0
        for r in rs:
            if r.get("source_modified") and r["source_modified"] != cur.get("modified"):
                print(f"   SKIP '{r['anchor']}': article changed since export ({cur.get('modified')}) - re-export (menu 3) and re-plan")
                continue
            if f'href="{r["target_url"]}"' in raw:
                print(f"   SKIP '{r['anchor']}': already links to the target")
                continue
            new, snip = insert_link(raw, r["anchor"], r["target_url"])
            if new is None:
                print(f"   SKIP '{r['anchor']}': phrase not found as plain text in the post's HTML (inside heading/link/odd markup)")
                continue
            raw, done = new, done + 1
            print(f"   + ...{snip}...  ->  {r['target_title']}")
        if done:
            jobs.append((sid, cur, raw, done))
    total = sum(j[3] for j in jobs)
    if not go:
        print(f"\nPreview only: {total} link(s) in {len(jobs)} article(s) would be inserted. Use menu 11 to apply.")
        return
    if not jobs or not confirm(total, f"link(s) in {len(jobs)} article(s) (originals are backed up first)"):
        print("Cancelled.")
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    (BACKUP / "posts").mkdir(parents=True, exist_ok=True)
    for sid, cur, raw, done in jobs:
        (BACKUP / "posts" / f"{sid}-{ts}.html").write_text(cur["content"]["raw"], encoding="utf-8")
        try:
            ensure(c.request("POST", f"wp/v2/posts/{sid}", auth="wp", json={"content": raw}), f"update post {sid}")
            chk = ensure(c.get(f"wp/v2/posts/{sid}", params={"context": "edit", "_fields": "id,content"}), "verify")
            ok = chk["content"]["raw"].count("<a href=") >= cur["content"]["raw"].count("<a href=") + done
            print(f"   OK post {sid}: {done} link(s) added, verified={ok}")
            log_action("links_added", {"id": sid, "count": done})
        except StepError as e:
            print(f"   FAILED post {sid}: {e}")
    print(f"\nBackups of the original HTML: backups\\posts\\*-{ts}.html (WordPress also keeps revisions).")


# ---------- 3. SEO to-do ----------
def weak(a):
    fk, meta = a.get("focus_keyword") or "", a.get("meta_description") or ""
    title = a.get("seo_title") or a.get("title", "")
    return (not fk or not meta or not 110 <= len(meta) <= 160 or len(title) > 60
            or (fk and (norm(fk) not in norm(title) or norm(fk) not in norm(meta))))


def seo_todo():
    arts = read_articles()
    todo = [a for a in arts if weak(a)]
    if not todo:
        print("No weak articles (or export first: menu 3).")
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    existing = sorted({a["focus_keyword"] for a in arts if a.get("focus_keyword")})
    write_csv(PROP / f"seo_todo_{ts}.csv",
              ["id", "title", "url", "focus_keyword", "seo_title", "meta_description", "words"],
              [[a["id"], a["title"], a["url"], a.get("focus_keyword", ""), a.get("seo_title", ""),
                a.get("meta_description", ""), a.get("words", "")] for a in todo])
    head = ("برای هر مقالهٔ زیر کلمه کلیدی کانونی، عنوان سئو و متا دیسکریپشن فارسی بنویس. قواعد:\n"
            "- کلمه کلیدی ۲ تا ۴ کلمه، دقیقاً با موضوع مقاله، و با کلمه‌های کلیدی موجود (فهرست پایین) تکراری نباشد.\n"
            "- عنوان سئو حداکثر ۶۰ کاراکتر و شامل کلمه کلیدی؛ متا ۱۱۰ تا ۱۶۰ کاراکتر، شامل کلمه کلیدی، جذاب و بدون اغراق.\n"
            "- اگر مقاله از قبل کلمه کلیدی یا متای خوب دارد، همان را نگه دار (فقط اصلاح لازم).\n"
            "- خروجی فقط CSV با ستون‌های دقیق زیر باشد، همهٔ مقدارها داخل گیومه:\n"
            "id,new_focus_keyword,new_seo_title,new_meta_description\n\n"
            "کلمه‌های کلیدی موجود (تکرار نشوند): " + "، ".join(existing) + "\n")
    n = 0
    for i in range(0, len(todo), 15):
        n += 1
        L = [head]
        for a in todo[i:i + 15]:
            intro = (paragraphs(a["body"]) or [""])[0][:350]
            L += [f"\n### id={a['id']} | عنوان: {a['title']}",
                  f"کلمه کلیدی فعلی: {a.get('focus_keyword', '-')} | متای فعلی: {a.get('meta_description', '-')}",
                  "تیترها: " + " / ".join(h2_list(a["body"])[:6]), f"مقدمه: {intro}"]
        (PROP / f"seo_prompt_{ts}_{n}.md").write_text("\n".join(L), encoding="utf-8")
    print(f"{len(todo)} of {len(arts)} articles need SEO work.\nList: proposals\\seo_todo_{ts}.csv")
    print(f"{n} prompt file(s): proposals\\seo_prompt_{ts}_*.md -> paste each into a chat model,")
    print("save its CSV answer as proposals\\seo_answer.csv (several answers: append rows under one header), then menu 13.")
    print("(When the API is connected later, this step happens automatically.)")


# ---------- 4. apply SEO ----------
def apply_seo(go, path=None):
    f = Path(path) if path else PROP / "seo_answer.csv"
    if not f.exists():
        print(f"{f} not found. Run menu 12 and save the model's CSV answer there.")
        return
    arts = {str(a["id"]): a for a in read_articles()}
    rows, plan = read_csv(f), []
    newkw = Counter()
    for r in rows:
        if r.get("new_focus_keyword"):
            newkw[norm(r["new_focus_keyword"])] += 1
    print(f"{f.name}: {len(rows)} row(s)\n")
    for r in rows:
        pid = num(r.get("id"))
        a = arts.get(pid)
        if not a:
            print(f"! id {pid}: not in exported articles - skipped")
            continue
        new = {"_yoast_wpseo_focuskw": (r.get("new_focus_keyword") or "").strip(),
               "_yoast_wpseo_title": (r.get("new_seo_title") or "").strip(),
               "_yoast_wpseo_metadesc": (r.get("new_meta_description") or "").strip()}
        old = {"_yoast_wpseo_focuskw": a.get("focus_keyword") or "", "_yoast_wpseo_title": a.get("seo_title") or "",
               "_yoast_wpseo_metadesc": a.get("meta_description") or ""}
        change = {k: v for k, v in new.items() if v and v != old[k]}
        if not change:
            continue
        fk = new["_yoast_wpseo_focuskw"] or old["_yoast_wpseo_focuskw"]
        ttl = new["_yoast_wpseo_title"] or old["_yoast_wpseo_title"] or a["title"]
        meta = new["_yoast_wpseo_metadesc"] or old["_yoast_wpseo_metadesc"]
        warn, block = [], None
        if len(ttl) > 60:
            warn.append(f"SEO title {len(ttl)} chars (>60)")
        if meta and not 110 <= len(meta) <= 160:
            warn.append(f"meta {len(meta)} chars (aim 110-160)")
        if fk and norm(fk) not in norm(ttl):
            warn.append("focus keyword not in SEO title")
        if fk and meta and norm(fk) not in norm(meta):
            warn.append("focus keyword not in meta")
        if fk:
            others = [x for x in arts.values() if x is not a and norm(x.get("focus_keyword") or "") == norm(fk)]
            if others:
                block = f"focus keyword already used by article id {others[0]['id']}"
            elif newkw[norm(fk)] > 1 and r.get("new_focus_keyword"):
                block = "same new focus keyword used twice in this file"
        print(f"== id {pid}: {a['title']}")
        for k, v in change.items():
            print(f"   {k.replace('_yoast_wpseo_', '')}: '{old[k]}' -> '{v}'")
        for w in warn:
            print(f"   WARN: {w}")
        if block:
            print(f"   BLOCKED: {block}")
            continue
        plan.append((pid, a, change, old))
    if not go:
        print(f"\nPreview only: {len(plan)} article(s) would be updated. Use menu 13 to apply.")
        return
    if not plan or not confirm(len(plan), "SEO update(s) (old values are saved first)"):
        print("Cancelled.")
        return
    c = WPClient(load_config())
    c.detect_mode()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    (BACKUP / "seo").mkdir(parents=True, exist_ok=True)
    (BACKUP / "seo" / f"{ts}.json").write_text(
        json.dumps({p[0]: p[3] for p in plan}, ensure_ascii=False, indent=1), encoding="utf-8")
    for pid, a, change, old in plan:
        try:
            res = ensure(c.request("POST", f"wp/v2/posts/{pid}", auth="wp", json={"meta": change}), f"update post {pid}")
            got = res.get("meta") or {}
            ok = all(got.get(k) == v for k, v in change.items())
            print(f"   OK id {pid}: saved={ok}")
            log_action("seo_update", {"id": pid, "fields": list(change)})
        except StepError as e:
            print(f"   FAILED id {pid}: {e}")
    print(f"\nOld values saved in backups\\seo\\{ts}.json")
