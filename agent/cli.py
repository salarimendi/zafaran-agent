"""Stage 1: read-only connection check. Writes NOTHING to the site."""
import sys
from datetime import datetime

import requests

from .config import ROOT, load_config
from .wp_client import WPClient, _json, short_body

OK, WARN, FAIL = "OK  ", "WARN", "FAIL"
results: list[tuple[str, str, str]] = []

HTACCESS_HINT = (
    "Host may be stripping the Authorization header. Add to .htaccess (above the WordPress block):\n"
    "      RewriteEngine On\n"
    "      RewriteRule .* - [E=HTTP_AUTHORIZATION:%{HTTP:Authorization}]"
)


def add(status: str, name: str, detail: str = ""):
    results.append((status, name, detail))
    print(f"[{status}] {name}" + (f" - {detail}" if detail else ""))


def check_root(c: WPClient):
    try:
        r = c.detect_mode()
    except requests.RequestException as e:
        add(FAIL, "Site reachable", f"{type(e).__name__}: {e}")
        return None
    data = _json(r)
    if not r.ok or "namespaces" not in data:
        add(FAIL, "REST API root", f"HTTP {r.status_code}: {short_body(r)}")
        if r.status_code in (403, 406):
            print("      Likely blocked by host firewall / security plugin / Cloudflare.")
        return None
    mode = "pretty permalinks (/wp-json/)" if c.mode == "pretty" else "plain (?rest_route=)"
    add(OK, "REST API root", f"{data.get('name', '')} | {mode}")
    return data.get("namespaces", [])


def check_auth(c: WPClient):
    r = c.get("wp/v2/users/me", params={"context": "edit"})
    d = _json(r)
    if r.status_code == 200:
        caps = d.get("capabilities", {})
        roles = ",".join(d.get("roles", []))
        need = ["edit_posts", "publish_posts", "upload_files", "edit_others_posts", "manage_categories"]
        have = [k for k in need if caps.get(k)]
        lack = [k for k in need if not caps.get(k)]
        add(OK, "Auth (Application Password)", f"user={d.get('slug')} roles={roles}")
        add(OK if "upload_files" in have and "edit_posts" in have else WARN,
            "Capabilities", f"have={have} missing={lack}")
        if "administrator" in d.get("roles", []):
            add(WARN, "User role", "administrator - prefer Editor for the agent")
        return True
    server_level = r.status_code == 403 and not d
    if server_level:
        add(WARN, "Users endpoint (/wp/v2/users/me)",
            "blocked at server level (HTML 403). Common anti-enumeration rule; trying another authenticated endpoint...")
    else:
        add(FAIL, "Auth (Application Password)",
            f"HTTP {r.status_code} {d.get('code', '')} {d.get('message', short_body(r))}")
        if r.status_code == 401:
            print("      " + HTACCESS_HINT)
            print("      Also re-check: username (not email), full password, Application Passwords not disabled by a security plugin.")
        return False
    # Fallback: an endpoint that REQUIRES login but is not the users endpoint.
    r2 = c.get("wp/v2/posts", params={"status": "draft", "per_page": 1, "context": "edit"})
    if r2.status_code == 200:
        add(OK, "Auth via posts endpoint", "authenticated request accepted")
        old = c.mode
        c.mode = "rest_route"
        try:
            r3 = c.get("wp/v2/users/me", params={"context": "edit"})
            d3 = _json(r3)
            if r3.status_code == 200 and isinstance(d3, dict):
                caps = d3.get("capabilities", {})
                need = ["edit_posts", "publish_posts", "upload_files", "edit_others_posts", "manage_categories"]
                add(OK, "User/capabilities (via ?rest_route=)",
                    f"roles={','.join(d3.get('roles', []))} missing={[k for k in need if not caps.get(k)]}")
        except requests.RequestException:
            pass
        c.mode = old
        return True
    d2 = _json(r2)
    add(FAIL, "Auth via posts endpoint",
        f"HTTP {r2.status_code} {d2.get('code', '') or 'HTML/server-level'} - run diagnose.bat and paste the output")
    return False


def count(c: WPClient, route: str, auth: str = "wp", **params):
    r = c.get(route, auth=auth, params={"per_page": 1, **params})
    if r.status_code != 200:
        return None, r
    return int(r.headers.get("X-WP-Total", "0")), r


def check_content(c: WPClient):
    for label, route, extra in (("Posts (all statuses)", "wp/v2/posts", {"status": "any"}),
                                ("Pages", "wp/v2/pages", {"status": "any"}),
                                ("Categories", "wp/v2/categories", {}),
                                ("Media library", "wp/v2/media", {})):
        n, r = count(c, route, **extra)
        add(OK if n is not None else WARN, label, str(n) if n is not None else f"HTTP {r.status_code}")


def check_yoast(c: WPClient, namespaces):
    ns = "yoast/v1" in (namespaces or [])
    add(OK if ns else WARN, "Yoast REST namespace", "yoast/v1 present" if ns else "yoast/v1 not found")
    r = c.get("wp/v2/posts", params={"per_page": 1, "context": "edit",
                                     "_fields": "id,meta,yoast_head_json"})
    if r.status_code != 200 or not _json(r):
        add(WARN, "Yoast fields on a post", "no post to inspect or request failed")
        return
    post = _json(r)[0]
    head = post.get("yoast_head_json")
    add(OK if head else WARN, "Yoast read-only SEO data (yoast_head_json)", "available" if head else "missing")
    meta = post.get("meta") or {}
    ymeta = [k for k in meta if k.startswith("_yoast_wpseo")] if isinstance(meta, dict) else []
    if ymeta:
        add(OK, "Yoast meta writable via REST", ", ".join(ymeta))
    else:
        add(WARN, "Yoast meta writable via REST",
            "NOT exposed (expected). Stage 3 adds a tiny bridge plugin for focus keyword / meta description.")


def check_woo(c: WPClient, namespaces):
    if "wc/v3" not in (namespaces or []):
        add(WARN, "WooCommerce REST namespace", "wc/v3 not found")
        return
    add(OK, "WooCommerce REST namespace", "wc/v3 present")
    if not (c.cfg.wc_key and c.cfg.wc_secret):
        add(WARN, "WooCommerce keys", "WC_CONSUMER_KEY/SECRET not set in .env")
        return
    n, r = count(c, "wc/v3/products", auth="wc")
    if n is None and r.status_code == 401:
        c.wc_auth_mode = "query"  # some hosts strip Basic auth; try keys in query string
        n, r = count(c, "wc/v3/products", auth="wc")
    if n is None:
        add(FAIL, "WooCommerce keys", f"HTTP {r.status_code} {_json(r).get('message', short_body(r))}")
        if r.status_code == 403:
            print("      Key is valid but its WordPress user lacks WooCommerce permissions.")
            print("      Fix: make that user a Shop Manager, or create a new key (WooCommerce > Settings > Advanced > REST API)")
            print("      for a Shop Manager user with Read/Write permission.")
        return
    add(OK, "WooCommerce products", f"{n} (auth via {c.wc_auth_mode})")
    n2, _ = count(c, "wc/v3/products/categories", auth="wc")
    add(OK if n2 is not None else WARN, "WooCommerce categories", str(n2))


def save_report():
    d = ROOT / "reports"
    d.mkdir(exist_ok=True)
    p = d / f"connection_report_{datetime.now():%Y%m%d_%H%M}.txt"
    lines = [f"{s} | {n} | {t}" for s, n, t in results]
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def check():
    results.clear()
    cfg = load_config()
    if not cfg.site_url.startswith("https://"):
        print("WARNING: site URL is not https - Application Passwords normally need HTTPS.")
    c = WPClient(cfg)
    print(f"Checking {cfg.site_url} (read-only, nothing is changed)\n")
    ns = check_root(c)
    if ns is not None and check_auth(c):
        check_content(c)
        check_yoast(c, ns)
        check_woo(c, ns)
    fails = sum(1 for s, *_ in results if s == FAIL)
    warns = sum(1 for s, *_ in results if s == WARN)
    p = save_report()
    print(f"\nSummary: {fails} FAIL, {warns} WARN. Report saved: {p}")
    print("Copy the lines above (or the report file) and paste them to Claude.")


BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _kind(r):
    if "json" in r.headers.get("Content-Type", ""):
        j = _json(r)
        return "WP-JSON " + (str(j.get("code", "")) if isinstance(j, dict) else "")
    return "HTML/server-level"


def diagnose():
    cfg = load_config()
    c = WPClient(cfg)
    c.detect_mode()
    print(f"Diagnosing {cfg.site_url} (read-only)\n")
    probes = [
        ("users/me, no login", "wp/v2/users/me", None, None, {}),
        ("users/me, login", "wp/v2/users/me", "wp", None, {}),
        ("users/me?context=edit, login", "wp/v2/users/me", "wp", None, {"context": "edit"}),
        ("users/me, login, browser UA", "wp/v2/users/me", "wp", BROWSER_UA, {}),
        ("drafts list, login", "wp/v2/posts", "wp", None, {"status": "draft", "per_page": 1}),
        ("drafts list, login, browser UA", "wp/v2/posts", "wp", BROWSER_UA, {"status": "draft", "per_page": 1}),
        ("posts list, no login", "wp/v2/posts", None, None, {"per_page": 1}),
    ]
    last_html = None
    for label, route, auth, ua, params in probes:
        try:
            r = c.request("GET", route, auth=auth, params=params,
                          headers={"User-Agent": ua} if ua else None)
            print(f"{label:34} -> HTTP {r.status_code}  {_kind(r)}  server={r.headers.get('Server', '?')}")
            if r.status_code == 403 and "json" not in r.headers.get("Content-Type", ""):
                last_html = short_body(r, 300)
        except requests.RequestException as e:
            print(f"{label:34} -> ERROR {type(e).__name__}")
    old = c.mode
    c.mode = "rest_route"
    try:
        r = c.request("GET", "wp/v2/users/me", auth="wp")
        print(f"{'users/me, login, ?rest_route=':34} -> HTTP {r.status_code}  {_kind(r)}")
    except requests.RequestException as e:
        print(f"{'users/me, login, ?rest_route=':34} -> ERROR {type(e).__name__}")
    c.mode = old
    if last_html:
        print("\nBlock page text:", last_html)
    print("\nPaste this whole output to Claude. (No passwords are printed.)")


def main():
    import argparse
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="agent")
    ap.add_argument("cmd", choices=["check", "diagnose", "products", "articles", "sync", "newterms", "export", "audit", "style", "linkplan", "applylinks", "seotodo", "applyseo", "panel", "routine"])
    ap.add_argument("path", nargs="?", help="optional: one product folder / one article file")
    ap.add_argument("--go", action="store_true", help="really send to the site (as DRAFT). Without it: dry run.")
    ap.add_argument("--all", action="store_true", help="export: include drafts/private, not only published")
    a = ap.parse_args()
    if a.cmd == "check":
        check()
    elif a.cmd == "diagnose":
        diagnose()
    elif a.cmd == "sync":
        from . import site
        site.sync()
    elif a.cmd in ("linkplan", "applylinks", "seotodo", "applyseo"):
        from . import proposals as pr
        {"linkplan": lambda: pr.link_plan(), "applylinks": lambda: pr.apply_links(a.go, a.path),
         "seotodo": lambda: pr.seo_todo(), "applyseo": lambda: pr.apply_seo(a.go, a.path)}[a.cmd]()
    elif a.cmd == "routine":
        from . import routine
        sys.exit(0 if routine.run() else 1)
    elif a.cmd == "panel":
        from . import panel
        panel.serve()
    elif a.cmd == "audit":
        from . import audit
        audit.audit()
    elif a.cmd == "style":
        from . import style
        style.build()
    elif a.cmd == "export":
        from . import export
        export.export_articles(a.all)
    elif a.cmd == "newterms":
        from . import site
        site.run_newterms(a.go)
    else:
        from . import stage2
        (stage2.run_products if a.cmd == "products" else stage2.run_articles)(a.go, a.path)


if __name__ == "__main__":
    main()
