"""Free, local SEO checks (no AI). Returns a list of warning strings."""
import re


def _text(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html or "").split())


def lint(kind: str, title: str, meta: str, focus: str, body_html: str, site_host: str = "") -> list[str]:
    w = []
    text = _text(body_html)
    words = len(text.split())
    if not focus:
        w.append("no focus keyword set")
    else:
        if focus not in title:
            w.append("focus keyword not in title")
        if not meta:
            w.append("meta description is empty")
        elif focus not in meta:
            w.append("focus keyword not in meta description")
        if focus not in " ".join(text.split()[:120]):
            w.append("focus keyword not in the first ~120 words")
        if kind == "article":
            h2 = re.findall(r"<h2[^>]*>(.*?)</h2>", body_html or "", flags=re.S)
            if not h2:
                w.append("no H2 headings")
            elif not any(focus in _text(h) for h in h2):
                w.append("focus keyword not in any H2")
    if len(title) > 60:
        w.append(f"title is {len(title)} chars (aim <= 60)")
    if meta and not (110 <= len(meta) <= 160):
        w.append(f"meta description is {len(meta)} chars (aim 110-160)")
    need = 600 if kind == "article" else 120
    if words < need:
        w.append(f"short text: {words} words (aim >= {need})")
    if kind == "article":
        links = re.findall(r'href="([^"]+)"', body_html or "")
        internal = [l for l in links if l.startswith("/") or (site_host and site_host in l)]
        if not internal:
            w.append("no internal links")
    return w
