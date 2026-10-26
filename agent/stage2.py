"""Products + articles -> WordPress as DRAFT. Dry run by default. Never creates categories/tags."""
import json
from pathlib import Path
from urllib.parse import urlparse

import yaml

from . import images as img
from . import seo
from .common import (StepError, as_list, confirm, ensure, log_action, md, num, slug_error)
from .config import ROOT, load_config
from .site import expected_url, resolve_term, slug_in_use
from .wp_client import WPClient

INBOX_P = ROOT / "inbox" / "products"
INBOX_A = ROOT / "inbox" / "articles"
BUILD = ROOT / "build"
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def key_of(s):
    import re
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "item"


def site_host(cfg):
    return urlparse(cfg.site_url).netloc


def yoast_meta(focus, seo_title, desc):
    m = {}
    if focus:
        m["_yoast_wpseo_focuskw"] = focus
    if seo_title:
        m["_yoast_wpseo_title"] = seo_title
    if desc:
        m["_yoast_wpseo_metadesc"] = desc
    return m


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
    if kind == "product":
        return ([("product_cat", v) for v in as_list(d.get("categories") or d.get("category"))]
                + [("product_tag", v) for v in as_list(d.get("tags"), split=True)])
    return ([("category", v) for v in as_list(d.get("categories") or d.get("category"))]
            + [("post_tag", v) for v in as_list(d.get("tags"), split=True)])


def online_checks(c, kind, d, report):
    """Terms must exist, slug must be free. Returns (term_ids, ok). report=True prints findings (dry run)."""
    ids, ok = {}, True
    for tax, v in term_specs(kind, d):
        try:
            ids.setdefault(tax, []).append(resolve_term(c, tax, v))
            if report:
                print(f"    {tax} '{v}': exists")
        except StepError as e:
            ok = False
            print(f"    !! {e}")
    used = slug_in_use(c, kind, d["slug"])
    if used:
        ok = False
        print(f"    !! slug '{d['slug']}' is already used by: {used}")
    elif report:
        print(f"    slug '{d['slug']}': free")
    if report:
        u = expected_url(kind, d["slug"])
        print(f"    URL after publishing: {u or '(run sync.bat once to learn the permalink pattern)'}")
    return ids, ok


# ---------- products ----------
def load_product(folder: Path):
    f = folder / "product.yaml"
    if not f.exists():
        raise StepError("product.yaml not found")
    d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    if not d.get("name"):
        raise StepError("'name' is required in product.yaml")
    if not num(d.get("price")):
        raise StepError("'price' is required in product.yaml")
    if slug_error(d.get("slug")):
        raise StepError(slug_error(d.get("slug")))
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMG_EXT)
    if not files:
        raise StepError("no images in the folder")
    return d, files


def run_products(go, only=None):
    cfg = load_config()
    if only:
        p = Path(only)
        folders = [p if p.is_absolute() or p.exists() else INBOX_P / only]
    else:
        folders = [p for p in sorted(INBOX_P.glob("*"))
                   if p.is_dir() and not p.name.startswith("_") and not (p / "result.json").exists()]
    if not folders:
        print(f"No pending product folders in {INBOX_P} (folders starting with _ and already-sent ones are skipped).")
        return
    print(f"{'SENDING' if go else 'DRY RUN'}: {len(folders)} product folder(s)\n")
    plans = []
    for folder in folders:
        print(f"== {folder.name}")
        try:
            d, files = load_product(folder)
            imgs = prepare_images(files, key_of(d["slug"]), d.get("image_alt") or d["name"])
            desc_html = md(d.get("description", ""))
            print(f"    name={d['name']} price={num(d['price'])} sku={d.get('sku', '-')} slug={d['slug']}")
            for w in seo.lint("product", d["name"], d.get("meta_description", ""), d.get("focus_keyword", ""),
                              desc_html, site_host(cfg)):
                print(f"    SEO: {w}")
            plans.append((folder, d, imgs, desc_html))
        except Exception as e:
            print(f"    SKIPPED: {e}")
    c = None
    if plans:
        try:
            c = WPClient(cfg)
            c.detect_mode()
        except Exception as e:
            print(f"(online checks unavailable: {e})")
    if not go:
        if c:
            print("\nOnline checks (read-only):")
            for folder, d, _i, _h in plans:
                print(f"  [{folder.name}]")
                try:
                    online_checks(c, "product", d, True)
                except Exception as e:
                    print(f"    (checks failed: {e})")
        print("\nDry run finished. Nothing was sent. Run run_send.bat to send as DRAFT.")
        return
    if not plans or not confirm(len(plans), "product(s) as DRAFT"):
        print("Cancelled.")
        return
    for folder, d, imgs, desc_html in plans:
        print(f"\n>> {folder.name}")
        ids = []
        try:
            if d.get("sku"):
                ex = ensure(c.get("wc/v3/products", auth="wc", params={"sku": str(d["sku"])}), "check SKU")
                if ex:
                    raise StepError(f"SKU {d['sku']} already exists (product id {ex[0]['id']}) - skipped")
            term_ids, ok = online_checks(c, "product", d, False)
            if not ok:
                raise StepError("fix the problems above (nothing was uploaded)")
            for p, a in imgs:  # images last: nothing is orphaned if the checks above fail
                ids.append(upload_image(c, p, a, d["name"]))
            ym = yoast_meta(d.get("focus_keyword"), d.get("seo_title"), d.get("meta_description"))
            payload = {
                "name": d["name"], "slug": d["slug"], "type": "simple", "status": "draft",
                "regular_price": num(d["price"]),
                "description": desc_html,
                "short_description": md(d.get("short_description", "")),
                "images": [{"id": i} for i in ids],
                "meta_data": [{"key": k, "value": v} for k, v in ym.items()],
            }
            if num(d.get("sale_price")):
                payload["sale_price"] = num(d["sale_price"])
            if d.get("sku"):
                payload["sku"] = str(d["sku"])
            if num(d.get("stock_quantity")):
                payload.update(manage_stock=True, stock_quantity=int(float(num(d["stock_quantity"]))))
            if term_ids.get("product_cat"):
                payload["categories"] = [{"id": i} for i in term_ids["product_cat"]]
            if term_ids.get("product_tag"):
                payload["tags"] = [{"id": i} for i in term_ids["product_tag"]]
            if d.get("attributes"):
                payload["attributes"] = [{"name": k, "visible": True, "variation": False, "options": [str(v)]}
                                         for k, v in d["attributes"].items()]
            res = ensure(c.request("POST", "wc/v3/products", auth="wc", json=payload), "create product")
            saved = {m.get("key") for m in res.get("meta_data", [])}
            print(f"   OK product id={res['id']} (draft, slug={res.get('slug')})")
            print("   Yoast fields:", "saved" if set(ym) <= saved else f"NOT confirmed {sorted(set(ym) - saved)} - check in editor")
            print(f"   URL after publishing: {expected_url('product', d['slug']) or 'see site after publish'}")
            (folder / "result.json").write_text(json.dumps({"id": res["id"], "slug": res.get("slug")}), encoding="utf-8")
            log_action("product_draft", {"id": res["id"], "name": d["name"], "slug": d["slug"]})
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


def run_articles(go, only=None):
    cfg = load_config()
    if only:
        p = Path(only)
        files = [p if p.is_absolute() or p.exists() else INBOX_A / only]
    else:
        files = [p for p in sorted(INBOX_A.glob("*.md"))
                 if not p.name.startswith("_") and not p.with_suffix(".result.json").exists()]
    if not files:
        print(f"No pending articles in {INBOX_A} (files starting with _ and already-sent ones are skipped).")
        return
    print(f"{'SENDING' if go else 'DRY RUN'}: {len(files)} article(s)\n")
    plans = []
    for f in files:
        print(f"== {f.name}")
        try:
            m, body = load_article(f)
            body_html = md(body)
            imgs = []
            if m.get("image"):
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
            plans.append((f, m, body_html, imgs))
        except Exception as e:
            print(f"    SKIPPED: {e}")
    c = None
    if plans:
        try:
            c = WPClient(cfg)
            c.detect_mode()
        except Exception as e:
            print(f"(online checks unavailable: {e})")
    if not go:
        if c:
            print("\nOnline checks (read-only):")
            for f, m, _b, _i in plans:
                print(f"  [{f.name}]")
                try:
                    online_checks(c, "article", m, True)
                except Exception as e:
                    print(f"    (checks failed: {e})")
        print("\nDry run finished. Nothing was sent. Run run_send.bat to send as DRAFT.")
        return
    if not plans or not confirm(len(plans), "article(s) as DRAFT"):
        print("Cancelled.")
        return
    for f, m, body_html, imgs in plans:
        print(f"\n>> {f.name}")
        try:
            term_ids, ok = online_checks(c, "article", m, False)
            if not ok:
                raise StepError("fix the problems above (nothing was uploaded)")
            payload = {"title": m["title"], "slug": m["slug"], "content": body_html, "status": "draft"}
            if m.get("excerpt"):
                payload["excerpt"] = m["excerpt"]
            if term_ids.get("category"):
                payload["categories"] = term_ids["category"]
            if term_ids.get("post_tag"):
                payload["tags"] = term_ids["post_tag"]
            if imgs:
                payload["featured_media"] = upload_image(c, imgs[0][0], imgs[0][1], m["title"])
            ym = yoast_meta(m.get("focus_keyword"), m.get("seo_title"), m.get("meta_description"))
            if ym:
                payload["meta"] = ym
            res = ensure(c.request("POST", "wp/v2/posts", auth="wp", json=payload), "create post")
            got = res.get("meta") or {}
            print(f"   OK post id={res['id']} (draft, slug={res.get('slug')})")
            print("   Yoast fields:", "saved" if all(got.get(k) == v for k, v in ym.items()) else "NOT confirmed - check in the editor")
            print(f"   URL after publishing: {expected_url('post', m['slug']) or 'see site after publish'}")
            f.with_suffix(".result.json").write_text(json.dumps({"id": res["id"], "slug": res.get("slug")}), encoding="utf-8")
            log_action("article_draft", {"id": res["id"], "title": m["title"], "slug": m["slug"]})
        except StepError as e:
            print(f"   FAILED: {e}")
