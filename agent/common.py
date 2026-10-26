import html
import json
import re
from datetime import datetime

import markdown

from .config import ROOT
from .wp_client import _json

LOGS = ROOT / "logs"
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


class StepError(Exception):
    pass


def ensure(r, what):
    if r.status_code in (200, 201):
        return _json(r)
    d = _json(r)
    code = d.get("code") if isinstance(d, dict) else None
    msg = d.get("message") if isinstance(d, dict) else None
    raise StepError(f"{what}: HTTP {r.status_code} {code or ''} {msg or 'non-JSON reply (server/firewall block?)'}")


def log_action(kind, data):
    LOGS.mkdir(exist_ok=True)
    with open(LOGS / "actions.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), "kind": kind, **data},
                           ensure_ascii=False) + "\n")


def md(text):
    return markdown.markdown(text or "", extensions=["extra", "sane_lists"])


def num(v):
    if v in (None, ""):
        return ""
    return re.sub(r"[^\d.]", "", str(v).translate(DIGITS))


def norm(s):
    """Case/entity/Persian-Arabic-letter/ZWNJ insensitive form for comparing names."""
    s = html.unescape(str(s)).strip().lower().replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ")
    return " ".join(s.split())


def as_list(v, split=False):
    if v in (None, ""):
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if split:
        return [t.strip() for t in re.split(r"[,،]", str(v)) if t.strip()]
    return [str(v).strip()]


def slug_error(slug):
    if not slug:
        return "'slug' is required (Latin letters/digits/hyphens, e.g. negin-saffron-1-mesghal)"
    if not SLUG_RE.match(str(slug)):
        return f"slug '{slug}' is invalid: use only lowercase Latin letters, digits and single hyphens"
    return None


def confirm(n, what="item(s)"):
    return input(f"\nSend {n} {what} to the site? Type y to continue: ").strip().lower() == "y"
