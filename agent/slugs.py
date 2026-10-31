"""Find non-Latin slugs and fix them: AI suggests Latin slugs (cheap), YOU review/edit, then apply with backup."""
import json
import re
from datetime import datetime
from pathlib import Path

from . import llm
from . import proposals as pr
from .common import StepError, confirm, ensure, log_action, slug_error
from .config import ROOT, load_config
from .site import expected_url, load_snapshot, slug_in_use
from .wp_client import WPClient

KINDS = {"post": ("posts", "title"), "page": ("pages", "title"), "product": ("products", "name")}
SYS = ("برای هر عنوان فارسی یک slug لاتین کوتاه برای آدرس سایت بنویس: ۲ تا ۶ کلمهٔ انگلیسی (برای اصطلاح‌های خاص مثل سرگل یا نگین "
       "املای رایج لاتین مثل sargol و negin)، فقط حروف کوچک a-z، عدد و خط تیره، بدون کلمات اضافی، و معنی عنوان را برساند. "
       "فقط یک آرایهٔ JSON با اشیای {\"id\",\"s\"} بده و هیچ توضیح دیگری ننویس.")


def non_latin(s):
    return bool(s) and bool(re.search(r"[^a-z0-9-]", s))


def scan():
    snap = load_snapshot()
    if not snap:
        return None
    items = []
    for kind, (lst, tk) in KINDS.items():
        for it in snap.get(lst, []):
            if non_latin(it.get("slug")) and it.get("status") in ("publish", "draft", "pending", "private", "future"):
                items.append({"kind": kind, "id": it["id"], "title": it[tk], "slug": it["slug"], "url": it.get("url", ""), "status": it["status"]})
    return items


def bad_term_slugs():
    snap = load_snapshot() or {}
    return [(t, x["name"], x["slug"], x.get("link", "")) for t in ("category", "post_tag", "product_cat", "product_tag")
            for x in snap.get(t, []) if non_latin(x.get("slug"))]


def _norm_slug(s):
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", str(s).lower())).strip("-")[:70]


def ai_slugs(go, limit=None):
    items = scan()
    if items is None:
        print("Run 'sync' first (step 2).")
        return
    if limit:
        items = items[:limit]
    if not items:
        print("All slugs are Latin. Nothing to do.")
        return
    c = llm.cfg()
    batches = [items[i:i + 30] for i in range(0, len(items), 30)]

    def msg(b):
        return [{"role": "system", "content": SYS},
                {"role": "user", "content": "\n".join(json.dumps({"id": f"{x['kind']}{x['id']}", "t": x["title"]}, ensure_ascii=False, separators=(",", ":")) for x in b)}]
    msgs = [msg(b) for b in batches]
    from .ai import _brief
    _brief(c, len(batches), sum(llm.est_tokens(sum(len(m["content"]) for m in ms)) for ms in msgs),
           sum(min(c["max_out"], len(b) * 24 + 40) for b in batches))
    print(f"{len(items)} item(s) with a non-Latin slug.")
    if not go:
        print("Estimate only.")
        return
    if not confirm(len(batches), "AI call(s)", to="to the AI provider (spends credit; NOTHING is sent to your website)"):
        print("Cancelled.")
        return
    snap = load_snapshot()
    taken = {it["slug"] for lst in ("posts", "pages", "products") for it in snap.get(lst, []) if it.get("slug")}
    by = {f"{x['kind']}{x['id']}": x for x in items}
    rows, spent = [], 0.0
    for b, ms in zip(batches, msgs):
        try:
            text, info = llm.chat(ms, max_out=min(c["max_out"], len(b) * 24 + 40), task="slugs")
            spent += info["cost"]
            for r in llm.parse_json_list(text):
                x = by.get(str(r.get("id")))
                s = _norm_slug(r.get("s", ""))
                if not x or not s:
                    continue
                note, base, n = "", s, 2
                while s in taken:
                    s, n = f"{base}-{n}", n + 1
                    note = "suffix added to stay unique"
                taken.add(s)
                rows.append([x["id"], x["kind"], x["title"], x["slug"], s, x["url"], note])
        except llm.LLMError as e:
            print(f"  batch failed: {e}")
            break
    f = pr.PROP / f"slugs_{datetime.now():%Y%m%d_%H%M}.csv"
    pr.write_csv(f, ["id", "kind", "title", "old_slug", "new_slug", "url", "ai_notes"], rows)
    print(f"\n{len(rows)} suggestion(s) saved. Cost: ${spent:.4f}. Review/edit them in the panel (Addresses page). Nothing was changed on the site.")


def apply_slugs(go, path=None):
    f = Path(path) if path else (sorted(pr.PROP.glob("slugs_2*.csv")) or [None])[-1]
    if not f or not f.exists():
        print("No slug proposals. Run the AI suggestion first.")
        return
    c = WPClient(load_config())
    c.detect_mode()
    plan = []
    for r in pr.read_csv(f):
        kind, pid, new = r.get("kind"), pr.num(r.get("id")), (r.get("new_slug") or "").strip()
        route, auth = (f"wc/v3/products/{pid}", "wc") if kind == "product" else (f"wp/v2/{'pages' if kind == 'page' else 'posts'}/{pid}", "wp")
        try:
            if kind not in KINDS or not pid:
                raise StepError("bad row")
            if slug_error(new):
                raise StepError(slug_error(new))
            cur = ensure(c.get(route, auth=auth), f"read {kind} {pid}")
            old = cur.get("slug", "")
            if old == new:
                print(f"= {kind} {pid}: already '{new}'")
                continue
            used = [u for u in slug_in_use(c, "product" if kind == "product" else "post", new) if u[0] != int(pid)]
            if used:
                raise StepError(f"slug '{new}' already used by {used}")
            print(f"+ {kind} {pid}: '{old}' -> '{new}'   (new URL: {expected_url('product' if kind == 'product' else 'post', new) or new})")
            plan.append((kind, pid, route, auth, old, new))
        except StepError as e:
            print(f"! {kind} {pid}: {e}")
    if not go:
        print(f"\nPreview only: {len(plan)} slug(s) would change.")
        return
    if not plan or not confirm(len(plan), "slug change(s) (old slugs are saved first)"):
        print("Cancelled.")
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    (pr.BACKUP / "slugs").mkdir(parents=True, exist_ok=True)
    (pr.BACKUP / "slugs" / f"{ts}.json").write_text(json.dumps([[p[0], p[1], p[4], p[5]] for p in plan], ensure_ascii=False, indent=1), encoding="utf-8")
    done = 0
    for kind, pid, route, auth, old, new in plan:
        try:
            res = ensure(c.request("POST", route, auth=auth, json={"slug": new}), f"update {kind} {pid}")
            ok = res.get("slug") == new
            done += ok
            print(f"   OK {kind} {pid}: now '{res.get('slug')}' ({'as requested' if ok else 'WordPress changed it'})")
            log_action("slug_changed", {"kind": kind, "id": pid, "old": old, "new": res.get("slug")})
        except StepError as e:
            print(f"   FAILED {kind} {pid}: {e}")
    print(f"\n{done} changed. Old slugs saved in backups\\slugs\\{ts}.json.")
    print("WordPress normally redirects an old post/page/product URL to the new one (301): open one OLD address in your browser to confirm.")
    try:
        from .export import export_articles
        from .site import sync
        print("\nRefreshing local data...")
        sync()
        export_articles()
    except Exception as e:
        print(f"(refresh failed: {e}; run steps 2 and 3)")
