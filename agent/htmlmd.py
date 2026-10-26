"""Small dependency-free HTML -> Markdown converter (enough for blog articles)."""
import re
from html.parser import HTMLParser

SKIP = {"script", "style", "noscript", "svg", "iframe", "form", "button", "select"}
BLOCK = {"div", "section", "article", "figure", "figcaption", "header", "footer", "main", "aside"}


class _MD(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.pre = [], 0, False
        self.lists, self.hrefs = [], []
        self.cell, self.row, self.rows, self.in_table = None, [], [], False

    def w(self, s):
        (self.cell if self.cell is not None else self.out).append(s)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if re.fullmatch(r"h[1-6]", tag):
            self.w("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "p" or tag in BLOCK:
            self.w("\n\n")
        elif tag == "br":
            self.w("\n")
        elif tag in ("strong", "b"):
            self.w("**")
        elif tag in ("em", "i"):
            self.w("*")
        elif tag == "a":
            self.hrefs.append(a.get("href"))
            self.w("[" if a.get("href") else "")
        elif tag in ("ul", "ol"):
            if not self.lists:
                self.w("\n")
            self.lists.append([tag, 0])
        elif tag == "li":
            depth = max(len(self.lists) - 1, 0)
            if self.lists and self.lists[-1][0] == "ol":
                self.lists[-1][1] += 1
                mark = f"{self.lists[-1][1]}. "
            else:
                mark = "- "
            self.w("\n" + "  " * depth + mark)
        elif tag == "blockquote":
            self.w("\n\n> ")
        elif tag == "img":
            alt, src = (a.get("alt") or "").strip(), a.get("src") or a.get("data-src") or ""
            if src:
                self.w(f"\n\n![{alt}]({src})\n\n")
        elif tag == "table":
            self.in_table, self.rows = True, []
        elif tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.cell = []
        elif tag == "pre":
            self.pre = True
            self.w("\n\n```\n")
        elif tag == "code" and not self.pre:
            self.w("`")
        elif tag == "hr":
            self.w("\n\n---\n\n")

    def handle_endtag(self, tag):
        if tag in SKIP:
            self.skip = max(self.skip - 1, 0)
            return
        if self.skip:
            return
        if re.fullmatch(r"h[1-6]", tag) or tag == "p" or tag in BLOCK or tag == "blockquote":
            self.w("\n\n")
        elif tag in ("strong", "b"):
            self.w("**")
        elif tag in ("em", "i"):
            self.w("*")
        elif tag == "a":
            href = self.hrefs.pop() if self.hrefs else None
            if href:
                self.w(f"]({href})")
        elif tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
            if not self.lists:
                self.w("\n")
        elif tag in ("td", "th"):
            if self.cell is not None:
                self.row.append(" ".join("".join(self.cell).split()).replace("|", "/"))
            self.cell = None
        elif tag == "tr":
            if self.row:
                self.rows.append(self.row)
        elif tag == "table":
            self.in_table = False
            if self.rows:
                n = max(len(r) for r in self.rows)
                lines = ["| " + " | ".join(r + [""] * (n - len(r))) + " |" for r in self.rows]
                lines.insert(1, "|" + " --- |" * n)
                self.w("\n\n" + "\n".join(lines) + "\n\n")
        elif tag == "pre":
            self.pre = False
            self.w("\n```\n\n")
        elif tag == "code" and not self.pre:
            self.w("`")

    def handle_data(self, data):
        if self.skip:
            return
        self.w(data if self.pre else re.sub(r"\s+", " ", data))


def html_to_md(html: str) -> str:
    p = _MD()
    p.feed(html or "")
    p.close()
    text = "".join(p.out)
    lines = []
    for ln in text.split("\n"):
        lines.append(ln.rstrip() if re.match(r"^\s*([-*]|\d+\.)\s", ln) else ln.strip())
    text = "\n".join(lines)
    text = re.sub(r"\*\*\s*\*\*", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"
