"""Build knowledge/style/style_pack.md: statistics + exemplars + a ready prompt for any chat model."""
import re
import statistics
from collections import Counter

from .config import ROOT
from .corpus import h2_list, paragraphs, read_articles, strip_md

OUT = ROOT / "knowledge" / "style"
WORDS = ["شما", "ما", "می‌توانید", "توصیه", "حتماً", "نکته", "بهتر", "اصل"]
PROMPT = """تو سردبیر محتوای «زعفران مارکت» هستی. بر اساس آمار و نمونه‌های زیر، یک «راهنمای لحن و سبک نگارش» بنویس (حداکثر ۷۰۰ کلمه) شامل:
۱) لحن و نحوهٔ خطاب به مخاطب  ۲) ساختار معمول مقاله (مقدمه، تیترهای H2، جمع‌بندی)  ۳) طول جمله و پاراگراف
۴) عبارت‌ها و کلمات امضای برند  ۵) چیزهایی که نباید نوشت  ۶) الگوی پیشنهادی مقدمه و جمع‌بندی  ۷) قواعد سئویی که در مقاله‌ها رعایت شده.
فقط خود راهنما را بنویس و هیچ لینک یا آدرس اینترنتی در آن نیاور. بعد آن را در knowledge\\style\\style_guide.md ذخیره کنید."""


def _clean(text):
    """Plain text only: no links, images, URLs or leftover brackets."""
    t = strip_md(text)
    t = re.sub(r"https?://\S+", "", t)
    return " ".join(t.replace("[", " ").replace("]", " ").split())


def _excerpt(body, limit=1500):
    out, n = [], 0
    for p in paragraphs(body):
        c = _clean(p)
        if n + len(c) > limit and out:
            break
        out.append(c)
        n += len(c)
    return "\n\n".join(out)


def _sentences(text):
    return [s for s in re.split(r"(?<=[.!؟?])\s+|\n+", text) if len(s.split()) >= 3]


def build():
    arts = read_articles()
    if not arts:
        print("No exported articles. Run export_articles.bat first.")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    words = [a.get("words", 0) for a in arts]
    med = statistics.median(words)
    all_p = [p for a in arts for p in paragraphs(a["body"])]
    text = " ".join(strip_md(p) for p in all_p)
    sents = _sentences(text)
    tot = max(len(text.split()), 1)
    tokens = re.findall(r"[\u0600-\u06FF\u200c]+", text)
    tri = Counter(" ".join(t) for t in zip(tokens, tokens[1:], tokens[2:]))
    tc = Counter(tokens)
    marks = [f"«{w}»: {tc[w] * 1000 / tot:.1f}" for w in WORDS] + \
            [f"«{m}»: {text.count(m) * 1000 / tot:.1f}" for m in ("؟", "!")]
    n_lists = sum(1 for a in arts if re.search(r"^\s*[-*]\s", a["body"], flags=re.M))
    sig = [(p, n) for p, n in tri.most_common(80) if n >= 6][:25]
    h2s = [h for a in arts for h in h2_list(a["body"])]
    step = max(len(h2s) // 25, 1)
    intros = sorted((paragraphs(a["body"])[0] for a in arts if paragraphs(a["body"])), key=len)
    pick = [_clean(p)[:400].rsplit(" ", 1)[0] for p in intros[:: max(len(intros) // 6, 1)][:6]]
    good = sorted([a for a in arts if a.get("focus_keyword") and a.get("meta_description")],
                  key=lambda a: abs(a.get("words", 0) - med))[:3]
    L = [PROMPT, "", "---", "", f"# آمار ({len(arts)} مقاله، حدود {sum(words)} کلمه)", "",
         f"- میانگین طول مقاله: {int(sum(words) / len(words))} کلمه (میانه {int(med)})",
         f"- میانگین طول جمله: {len(text.split()) / max(len(sents), 1):.1f} کلمه",
         f"- میانگین طول پاراگراف: {len(text.split()) / max(len(all_p), 1):.0f} کلمه",
         f"- میانگین تعداد H2 در هر مقاله: {len(h2s) / len(arts):.1f}",
         f"- مقاله‌های دارای لیست: {n_lists} از {len(arts)}",
         "- بسامد در هر ۱۰۰۰ کلمه: " + "، ".join(marks),
         "", "## عبارت‌های سه‌کلمه‌ای پرتکرار (امضای نوشتاری)"] + [f"- {p} ({n})" for p, n in sig]
    L += ["", "## نمونه تیترهای H2"] + [f"- {h}" for h in h2s[::step][:25]]
    L += ["", "## نمونه مقدمه‌ها"] + [f"> {p[:400]}" for p in pick]
    L += ["", "## نمونه مقاله‌های کامل‌تر (ابتدای متن)"]
    for a in good:
        L += ["", f"### {a.get('title')} ({a.get('words')} کلمه) - کلمه کلیدی: {a.get('focus_keyword')}",
              "تیترها: " + " / ".join(_clean(h) for h in h2_list(a["body"])[:8]), "", _excerpt(a["body"])]
    (OUT / "style_pack.md").write_text(re.sub(r"https?://\S+", "", "\n".join(L)), encoding="utf-8")
    kb = (OUT / "style_pack.md").stat().st_size // 1024
    print(f"Wrote {OUT}\\style_pack.md ({kb} KB). Paste its whole content into a chat model; save the answer as style_guide.md.")
