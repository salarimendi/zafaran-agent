"""Read the exported Markdown articles (knowledge/articles) and small text helpers."""
import re

import yaml

from .config import ROOT

ART = ROOT / "knowledge" / "articles"


def read_articles():
    out = []
    for f in sorted(ART.glob("*.md")):
        t = f.read_text(encoding="utf-8")
        if not t.startswith("---"):
            continue
        end = t.find("\n---", 3)
        if end == -1:
            continue
        try:
            fm = yaml.safe_load(t[3:end]) or {}
        except yaml.YAMLError:
            continue
        fm["file"], fm["body"] = f.name, t[end + 4:].strip()
        if not fm.get("id"):
            m = re.match(r"(\d+)", f.name)
            fm["id"] = int(m.group(1)) if m else None
        out.append(fm)
    return out


def strip_md(body):
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", body)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    return re.sub(r"[#>*`|]|^\s*[-\d.]+\s", " ", s, flags=re.M)


def paragraphs(body):
    return [p.strip() for p in body.split("\n\n")
            if len(p.strip()) > 40 and not p.lstrip().startswith(("#", "-", "|", ">", "!", "```", "1."))]


def h2_list(body):
    return re.findall(r"^##\s+(.+?)\s*$", body, flags=re.M)
