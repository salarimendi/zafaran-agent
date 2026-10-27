"""Products + articles -> WordPress. New items are DRAFT. Dry run by default.
Never creates categories/tags. Never touches an existing item unless `update: true` is written in its file."""
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import yaml

from . import images as img
from . import seo
from .common import StepError, as_list, confirm, ensure, log_action, md, num, slug_error
from .config import ROOT, load_config
from .site import expected_url, resolve_term, similar_names, slug_in_use
from .wp_client import WPClient

INBOX_P = ROOT / "inbox" / "products"
INBOX_A = ROOT / "inbox" / "articles"
BUILD = ROOT / "build"
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
SKIP_HINT = ("To change it: add `update: true`. To create a NEW item: use a new slug (and sku) and delete the result file.")


def key_of(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "item"


def site_host(cfg):
    return urlparse(cfg.site_url).netloc


def read_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def yoast_meta(focus, seo_title, desc):
    m = {}
    if focus:
        m["_yoast_wpseo_focuskw"] = focus
    if seo_title:
        m["_yoast_wpseo_title"] = seo_title
    if desc:
        m["_yoast_wpseo_metadesc"] = desc
    return m


def plain(s):
    return " ".join(re.sub(r"<[^>]+>", " ", s or "").split())


def upload_image(c, path: Path, alt: str, title: str):
    headers = {"Content-Disposition": f'attachment; filename="{path.name}"', "Content-Type": "image/webp"}
    r = c.request("POST", "wp/v2/media", auth="wp", data=path.read_bytes(), headers=headers, timeout=180)
    mid = ensure(r, f"upload {path.name}")["id"]
    ensure(c.request("POST", f"wp/v2/media/{mid}", auth="wp", json={"alt_text": alt, "title": title}),
           f"set alt text for media {mid}")
    return mid


def prepare_images(files, key, base_alt):
    out = []
    for i, f in enumerate(files, 1):
        dest = BUILD / key / f"{key}-{i}.webp"
        before, after, size = img.to_webp(f, dest)
        print(f"    image {f.name}: {before}KB -> {after}KB WebP {size[0]}x{size[1]}")
        out.append((dest, base_alt if i == 1 else f"{base_alt} - تصویر {i}"))
    return out


def term_specs(kind, d):
    cat, tag = ("product_cat", "product_tag") if kind == "product" else ("category", "post_tag")
    return ([(cat, v) for v in as_list(d.get("categories") or d.get("category"))]
            + [(tag, v) for v in as_list(d.get("tags"), split=True)])


def online_checks(c, kind, d, report, self_id=None):
    """Terms must exist; slug/SKU must be free (except for the item being updated). -> (term_ids, ok)"""
    ids, ok = {}, True
    for tax, v in term_specs(kind, d):
        try:
            ids.setdefault(tax, []).append(resolve_term(c, tax, v))
            if report:
                print(f"    {tax} '{v}': exists")
        except StepError as e:
            ok = False
            print(f"    !! {e}")
    used = [u for u in slug_in_use(c, kind, d["slug"]) if u[0] != self_id]
    if used:
        ok = False
        print(f"    !! slug '{d['slug']}' already used by {used}  (to modify an existing item use `update: true`)")
    elif report:
        print(f"    slug '{d['slug']}': free")
    if kind == "product" and d.get("sku"):
        ex = [i for i in ensure(c.get("wc/v3/products", auth="wc", params={"sku": str(d["sku"])}), "check SKU")
              if i["id"] != self_id]
        if ex:
            ok = False
            print(f"    !! SKU '{d['sku']}' already used by product id {ex[0]['id']} ({ex[0].get('status')}) "
                  f"- will NOT be created (to modify it use `update: true`)")
        elif report:
            print(f"    sku '{d['sku']}': free")
    for i, name, r in similar_names(kind, d.get("name") or d.get("title"), self_id):
        print(f"    WARN: very similar existing {kind} id {i}: '{name}' (similarity {r}) - make sure this is not a duplicate")
    if report:
        u = expected_url(kind, d["slug"])
        print(f"    URL after publishing: {u or '(run sync.bat once to learn the permalink pattern)'}")
    return ids, ok


def show_diff(lines, what):
    if lines:
        print(f"    CHANGES to {what}:")
        for ln in lines:
            print(f"      - {ln}")
    else:
        print(f"    no differences from the live {what}")


def cmp_lines(rows):
    out = []
    for label, old, new, long in rows:
        if new is None:
            continue
        if long:
            if plain(old) != plain(new):
                out.append(f"{label}: changed ({len(plain(old))} -> {len(plain(new))} chars)")
        elif str(old or "") != str(new):
            out.append(f"{label}: '{old}' -> '{new}'")
    return out


# ---------- products ----------
def load_product(folder: Path):
    f = folder / "product.yaml"
    if not f.exists():
        raise StepError("product.yaml not found")
    d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    for k in ("name",):
        if not d.get(k):
            raise StepError(f"'{k}' is required in product.yaml")
    if not num(d.get("price")):
        raise StepError("'price' is required in product.yaml")
    if slug_error(d.get("slug")):
        raise StepError(slug_error(d.get("slug")))
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMG_EXT)
    upd = bool(d.get("update"))
    if (not upd or d.get("replace_images")) and not files:
        raise StepError("no images in the folder")
    return d, files


def product_payload(d, desc_html, term_ids, image_ids, update):
    ym = yoast_meta(d.get("focus_keyword"), d.get("seo_title"), d.get("meta_description"))
    p = {"name": d["name"], "slug": d["slug"], "regular_price": num(d["price"]),
         "description": desc_html, "short_description": md(d.get("short_description", "")),
         "meta_data": [{"key": k, "value": v} for k, v in ym.items()]}
    if not update:
        p.update(type="simple", status="draft")
    if image_ids:
        p["images"] = [{"id": i} for i in image_ids]
    if num(d.get("sale_price")):
        p["sale_price"] = num(d["sale_price"])
    if d.get("sku"):
        p["sku"] = str(d["sku"])
    if num(d.get("stock_quantity")):
        p.update(manage_stock=True, stock_quantity=int(float(num(d["stock_quantity"]))))
    if term_ids.get("product_cat"):
        p["categories"] = [{"id": i} for i in term_ids["product_cat"]]
    if term_ids.get("product_tag"):
        p["tags"] = [{"id": i} for i in term_ids["product_tag"]]
    if d.get("attributes"):
        p["attributes"] = [{"name": k, "visible": True, "variation": False, "options": [str(v)]}
                           for k, v in d["attributes"].items()]
    return p, ym


def product_diff(cur, p, ym):
    curm = {m.get("key"): m.get("value") for m in cur.get("meta_data", [])}
    rows = [("name", cur.get("name"), p.get("name"), False), ("slug", cur.get("slug"), p.get("slug"), False),
            ("price", cur.get("regular_price"), p.get("regular_price"), False),
            ("sale_price", cur.get("sale_price"), p.get("sale_price"), False),
            ("sku", cur.get("sku"), p.get("sku"), False),
            ("description", cur.get("description"), p.get("description"), True),
            ("short_description", cur.get("short_description"), p.get("short_description"), True)]
    L = cmp_lines(rows)
    for key, ids in (("categories", "categories"), ("tags", "tags")):
        if key in p:
            old, new = {x["id"] for x in cur.get(key, [])}, {x["id"] for x in p[key]}
            if old != new:
                L.append(f"{key}: ids {sorted(old)} -> {sorted(new)}")
    for k, v in ym.items():
        if curm.get(k) != v:
            L.append(f"{k}: '{curm.get(k)}' -> '{v}'")
    if "images" in p:
        L.append(f"images: will be REPLACED by {len(p['images'])} new image(s)")
    return L


def run_products(go, only=None):
    cfg = load_config()
    if only:
        pth = Path(only)
        folders = [pth if pth.is_absolute() or pth.exists() else INBOX_P / only]
    else:
        folders = [p for p in sorted(INBOX_P.glob("*")) if p.is_dir() and not p.name.startswith("_")]
    if not folders:
        print(f"No product folders in {INBOX_P}.")
        return
    print(f"{'SENDING' if go else 'DRY RUN'}: {len(folders)} product folder(s)\n")
    plans = []
    for folder in folders:
        print(f"== {folder.name}")
        try:
            d, files = load_product(folder)
            res = read_json(folder / "result.json")
            pid = None
            if res and not d.get("update"):
                print(f"    already sent as product id {res.get('id')} (slug {res.get('slug')}) - SKIPPED. {SKIP_HINT}")
                continue
            if d.get("update"):
                pid = d.get("id") or (res or {}).get("id")
                if not pid:
                    raise StepError("`update: true` needs result.json or `id:` of the existing product")
                print(f"    UPDATE MODE for existing product id {pid}")
            imgs = prepare_images(files, key_of(d["slug"]), d.get("image_alt") or d["name"]) if (not pid or d.get("replace_images")) else []
            desc_html = md(d.get("description", ""))
            print(f"    name={d['name']} price={num(d['price'])} sku={d.get('sku', '-')} slug={d['slug']}")
            for w in seo.lint("product", d["name"], d.get("meta_description", ""), d.get("focus_keyword", ""),
                              desc_html, site_host(cfg)):
                print(f"    SEO: {w}")
            plans.append((folder, d, imgs, desc_html, pid))
        except Exception as e:
            print(f"    SKIPPED: {e}")
    if not plans:
        print("\nNothing to do.")
        return
    c = None
    try:
        c = WPClient(cfg)
        c.detect_mode()
    except Exception as e:
        print(f"(online checks unavailable: {e})")
    if not go:
        if c:
            print("\nOnline checks (read-only):")
            for folder, d, imgs, desc_html, pid in plans:
                print(f"  [{folder.name}]")
                try:
                    tids, _ = online_checks(c, "product", d, True, self_id=pid)
                    if pid:
                        cur = ensure(c.get(f"wc/v3/products/{pid}", auth="wc"), "read existing product")
                        p, ym = product_payload(d, desc_html, tids, [0] if imgs else [], True)
                        if imgs:
                            p["images"] = [{"id": 0}] * len(imgs)
                        show_diff(product_diff(cur, p, ym), "product")
                except Exception as e:
                    print(f"    (checks failed: {e})")
        print("\nDry run finished. Nothing was sent. Run run_send.bat to send.")
        return
    n_new = sum(1 for p in plans if not p[4])
    if not confirm(len(plans), f"product(s) ({n_new} new as DRAFT, {len(plans) - n_new} UPDATE of existing)"):
        print("Cancelled.")
        return
    for folder, d, imgs, desc_html, pid in plans:
        print(f"\n>> {folder.name}")
        ids = []
        try:
            term_ids, ok = online_checks(c, "product", d, False, self_id=pid)
            if not ok:
                raise StepError("fix the problems above (nothing was changed or uploaded)")
            for p_, a in imgs:
                ids.append(upload_image(c, p_, a, d["name"]))
            payload, ym = product_payload(d, desc_html, term_ids, ids, bool(pid))
            if pid:
                cur = ensure(c.get(f"wc/v3/products/{pid}", auth="wc"), "read existing product")
                show_diff(product_diff(cur, payload, ym), "product")
                res = ensure(c.request("POST", f"wc/v3/products/{pid}", auth="wc", json=payload), "update product")
                print(f"   OK product id={res['id']} UPDATED (status unchanged: {res.get('status')}, slug={res.get('slug')})")
                log_action("product_update", {"id": res["id"], "name": d["name"], "slug": d["slug"]})
            else:
                res = ensure(c.request("POST", "wc/v3/products", auth="wc", json=payload), "create product")
                print(f"   OK product id={res['id']} (draft, slug={res.get('slug')})")
                (folder / "result.json").write_text(json.dumps({"id": res["id"], "slug": res.get("slug")}), encoding="utf-8")
                log_action("product_draft", {"id": res["id"], "name": d["name"], "slug": d["slug"]})
            saved = {m.get("key") for m in res.get("meta_data", [])}
            print("   Yoast fields:", "saved" if set(ym) <= saved else f"NOT confirmed {sorted(set(ym) - saved)} - check in editor")
        except StepError as e:
            print(f"   FAILED: {e}")
            if ids:
                print(f"   NOTE: media ids already uploaded and unused: {ids} (delete them in the Media library)")
                log_action("orphan_media", {"ids": ids, "product": d.get("name")})


# ---------- articles ----------
def load_article(f: Path):
    t = f.read_text(encoding="utf-8").lstrip("\ufeff")
    if not t.startswith("---"):
        raise StepError("article must start with a --- front-matter block")
    end = t.find("\n---", 3)
    if end == -1:
        raise StepError("front-matter block is not closed with ---")
    meta = yaml.safe_load(t[3:end]) or {}
    if not meta.get("title"):
        raise StepError("'title' is required")
    if slug_error(meta.get("slug")):
        raise StepError(slug_error(meta.get("slug")))
    return meta, t[end + 4:].strip()


def article_payload(m, body_html, term_ids, update):
    ym = yoast_meta(m.get("focus_keyword"), m.get("seo_title"), m.get("meta_description"))
    p = {"title": m["title"], "slug": m["slug"]}
    if not update:
        p["status"] = "draft"
    if not update or m.get("replace_content"):
        p["content"] = body_html
    if m.get("excerpt"):
        p["excerpt"] = m["excerpt"]
    if term_ids.get("category"):
        p["categories"] = term_ids["category"]
    if term_ids.get("post_tag"):
        p["tags"] = term_ids["post_tag"]
    if ym:
        p["meta"] = ym
    return p, ym


def article_diff(cur, p, ym):
    L = cmp_lines([("title", (cur.get("title") or {}).get("raw"), p.get("title"), False),
                   ("slug", cur.get("slug"), p.get("slug"), False),
                   ("excerpt", (cur.get("excerpt") or {}).get("raw"), p.get("excerpt"), False),
                   ("content", (cur.get("content") or {}).get("raw"), p.get("content"), True)])
    for key in ("categories", "tags"):
        if key in p and set(cur.get(key, [])) != set(p[key]):
            L.append(f"{key}: ids {sorted(cur.get(key, []))} -> {sorted(p[key])}")
    curm = cur.get("meta") if isinstance(cur.get("meta"), dict) else {}
    for k, v in ym.items():
        if curm.get(k) != v:
            L.append(f"{k}: '{curm.get(k)}' -> '{v}'")
    return L


def run_articles(go, only=None):
    cfg = load_config()
    if only:
        pth = Path(only)
        files = [pth if pth.is_absolute() or pth.exists() else INBOX_A / only]
    else:
        files = [p for p in sorted(INBOX_A.glob("*.md")) if not p.name.startswith("_")]
    if not files:
        print(f"No articles in {INBOX_A}.")
        return
    print(f"{'SENDING' if go else 'DRY RUN'}: {len(files)} article(s)\n")
    plans = []
    for f in files:
        print(f"== {f.name}")
        try:
            m, body = load_article(f)
            res = read_json(f.with_suffix(".result.json"))
            pid = None
            if res and not m.get("update"):
                print(f"    already sent as post id {res.get('id')} (slug {res.get('slug')}) - SKIPPED. {SKIP_HINT}")
                continue
            if m.get("update"):
                pid = m.get("id") or (res or {}).get("id")
                if not pid:
                    raise StepError("`update: true` needs the .result.json or `id:` of the existing post")
                print(f"    UPDATE MODE for existing post id {pid} "
                      f"({'content WILL be replaced' if m.get('replace_content') else 'content untouched; set replace_content: true to replace'})")
            body_html = md(body)
            imgs = []
            if m.get("image") and (not pid or m.get("replace_image")):
                src = f.parent / str(m["image"])
                if not src.exists():
                    raise StepError(f"featured image not found: {src}")
                imgs = prepare_images([src], key_of(m["slug"]), m.get("image_alt") or m.get("focus_keyword") or m["title"])
            print(f"    title={m['title']} slug={m['slug']} words={len(body.split())}")
            if not as_list(m.get("categories") or m.get("category")):
                print("    NOTE: no category given - WordPress will use its default category")
            for w in seo.lint("article", m["title"], m.get("meta_description", ""), m.get("focus_keyword", ""),
                              body_html, site_host(cfg)):
                print(f"    SEO: {w}")
            plans.append((f, m, body_html, imgs, pid))
        except Exception as e:
            print(f"    SKIPPED: {e}")
    if not plans:
        print("\nNothing to do.")
        return
    c = None
    try:
        c = WPClient(cfg)
        c.detect_mode()
    except Exception as e:
        print(f"(online checks unavailable: {e})")
    if not go:
        if c:
            print("\nOnline checks (read-only):")
            for f, m, body_html, imgs, pid in plans:
                print(f"  [{f.name}]")
                try:
                    tids, _ = online_checks(c, "article", m, True, self_id=pid)
                    if pid:
                        cur = ensure(c.get(f"wp/v2/posts/{pid}", params={"context": "edit"}), "read existing post")
                        p, ym = article_payload(m, body_html, tids, True)
                        show_diff(article_diff(cur, p, ym), "post")
                except Exception as e:
                    print(f"    (checks failed: {e})")
        print("\nDry run finished. Nothing was sent. Run run_send.bat to send.")
        return
    n_new = sum(1 for p in plans if not p[4])
    if not confirm(len(plans), f"article(s) ({n_new} new as DRAFT, {len(plans) - n_new} UPDATE of existing)"):
        print("Cancelled.")
        return
    for f, m, body_html, imgs, pid in plans:
        print(f"\n>> {f.name}")
        try:
            term_ids, ok = online_checks(c, "article", m, False, self_id=pid)
            if not ok:
                raise StepError("fix the problems above (nothing was changed or uploaded)")
            payload, ym = article_payload(m, body_html, term_ids, bool(pid))
            if imgs:
                payload["featured_media"] = upload_image(c, imgs[0][0], imgs[0][1], m["title"])
            if pid:
                cur = ensure(c.get(f"wp/v2/posts/{pid}", params={"context": "edit"}), "read existing post")
                show_diff(article_diff(cur, payload, ym), "post")
                res = ensure(c.request("POST", f"wp/v2/posts/{pid}", auth="wp", json=payload), "update post")
                print(f"   OK post id={res['id']} UPDATED (status unchanged: {res.get('status')}, slug={res.get('slug')})")
                log_action("article_update", {"id": res["id"], "title": m["title"], "slug": m["slug"]})
            else:
                res = ensure(c.request("POST", "wp/v2/posts", auth="wp", json=payload), "create post")
                print(f"   OK post id={res['id']} (draft, slug={res.get('slug')})")
                f.with_suffix(".result.json").write_text(json.dumps({"id": res["id"], "slug": res.get("slug")}), encoding="utf-8")
                log_action("article_draft", {"id": res["id"], "title": m["title"], "slug": m["slug"]})
            got = res.get("meta") or {}
            print("   Yoast fields:", "saved" if all(got.get(k) == v for k, v in ym.items()) else "NOT confirmed - check in the editor")
        except StepError as e:
            print(f"   FAILED: {e}")
