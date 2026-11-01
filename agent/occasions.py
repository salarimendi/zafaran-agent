"""Stage 3 - fixed-occasion content calendar (Nowruz, Yalda, Ramadan, harvest season ...). NO AI, read-only.
Jalali <-> Gregorian and the tabular Hijri calendar are implemented here (no extra library). Hijri dates computed
this way can differ by 1-2 days from the official Iranian announcement, so lunar occasions are marked approximate."""
import math
from datetime import date, datetime, timedelta

import yaml

from .common import norm
from .config import ROOT
from .corpus import h2_list

KW = ROOT / "knowledge" / "keywords"
OCC_FILE = KW / "occasions.yaml"
REP = ROOT / "reports"

JMONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
HMONTHS = ["محرم", "صفر", "ربیع‌الاول", "ربیع‌الثانی", "جمادی‌الاول", "جمادی‌الثانی", "رجب", "شعبان", "رمضان", "شوال", "ذی‌القعده", "ذی‌الحجه"]

# ------------------------------------------------------------------ Jalali (33-year cycle algorithm, same as jalaali-js)
_BREAKS = [-61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210, 1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178]


def _div(a, b):
    """Division truncating toward zero (the reference algorithm was written for that; Python's // floors)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def _mod(a, b):
    return a - _div(a, b) * b


def _jal_cal(jy):
    """-> (leap, gy, march) : leap = years since last leap year (0 = leap), march = day of March of 1 Farvardin."""
    gy = jy + 621
    leap_j, jp, jump = -14, _BREAKS[0], 0
    for j in range(1, len(_BREAKS)):
        jm = _BREAKS[j]
        jump = jm - jp
        if jy < jm:
            break
        leap_j += jump // 33 * 8 + (jump % 33) // 4
        jp = jm
    n = jy - jp
    leap_j += n // 33 * 8 + ((n % 33) + 3) // 4
    if jump % 33 == 4 and jump - n == 4:
        leap_j += 1
    leap_g = gy // 4 - (gy // 100 + 1) * 3 // 4 - 150
    march = 20 + leap_j - leap_g
    if jump - n < 6:
        n = n - jump + (jump + 4) // 33 * 33
    leap = _mod(_mod(n + 1, 33) - 1, 4)
    if leap == -1:
        leap = 4
    return leap, gy, march


def _g2d(gy, gm, gd):
    d = (gy + _div(gm - 8, 6) + 100100) * 1461 // 4 + (153 * ((gm + 9) % 12) + 2) // 5 + gd - 34840408
    return d - (gy + 100100 + _div(gm - 8, 6)) // 100 * 3 // 4 + 752


def _d2g(jdn):
    j = 4 * jdn + 139361631
    j += (4 * jdn + 183187720) // 146097 * 3 // 4 * 4 - 3908
    i = (j % 1461) // 4 * 5 + 308
    gd = (i % 153) // 5 + 1
    gm = (i // 153) % 12 + 1
    gy = j // 1461 - 100100 + _div(8 - gm, 6)
    return gy, gm, gd


def _j2d(jy, jm, jd):
    _, gy, march = _jal_cal(jy)
    return _g2d(gy, 3, march) + (jm - 1) * 31 - jm // 7 * (jm - 7) + jd - 1


def jalali_to_gregorian(jy, jm, jd):
    return date(*_d2g(_j2d(jy, jm, jd)))


def gregorian_to_jalali(d):
    jdn = _g2d(d.year, d.month, d.day)
    gy = _d2g(jdn)[0]
    jy = gy - 621
    leap, _, march = _jal_cal(jy)
    k = jdn - _g2d(gy, 3, march)
    if k >= 0:
        if k <= 185:
            return jy, 1 + k // 31, k % 31 + 1
        k -= 186
    else:
        jy -= 1
        k += 179
        if leap == 1:
            k += 1
    return jy, 7 + k // 30, k % 30 + 1


def jalali_str(d):
    jy, jm, jd = gregorian_to_jalali(d)
    return f"{jd} {JMONTHS[jm - 1]} {jy}"


# ------------------------------------------------------------------ Hijri (tabular / civil, approximate)
def hijri_to_gregorian(hy, hm, hd):
    jd = hd + math.ceil(29.5 * (hm - 1)) + (hy - 1) * 354 + (3 + 11 * hy) // 30 + 1948440 - 1  # JDN of the civil epoch
    return date.fromordinal(jd - 1721425)  # JDN -> proleptic Gregorian ordinal


def gregorian_year_to_hijri(gy):
    return max(1, int((gy - 622) * 33 / 32.0))


# ------------------------------------------------------------------ occasions file
DEFAULT_OCCASIONS = """# مناسبت‌های ثابتِ تقویم محتوا. این فایل را می‌توانید ویرایش کنید (مناسبت اضافه/حذف کنید).
# calendar: jalali (روز ثابت شمسی) | hijri (قمری؛ تاریخ میلادی تقریبی است و ۱ تا ۲ روز جابه‌جایی دارد)
# month/day: ماه و روز در همان تقویم   ·   lead_days: چند روز قبل از مناسبت باید محتوا منتشر شده باشد
# keywords: اگر یکی از این کلمه‌ها در عنوان/کلمه کلیدی/تیترهای یک مقالهٔ منتشرشده باشد، «مقاله دارد» حساب می‌شود
# topics: ایده‌های پیشنهادی (قالب ثابت؛ بدون AI). ادعای درمانی و پزشکی ننویسید. (داخل یک ایده از «:» استفاده نکنید.)
- name: نوروز و سفرهٔ هفت‌سین
  calendar: jalali
  month: 1
  day: 1
  lead_days: 50
  keywords: [نوروز, عید, هفت سین, هفت‌سین, سال نو]
  topics:
    - راهنمای خرید زعفران برای عید و مهمانی‌های نوروز
    - زعفران به‌عنوان هدیهٔ عید؛ بسته‌بندی و انتخاب
    - طرز تهیهٔ غذاها و شیرینی‌های نوروزی با زعفران
- name: مهرگان
  calendar: jalali
  month: 7
  day: 16
  lead_days: 14
  keywords: [مهرگان]
  topics:
    - جشن مهرگان و رسم‌های ایرانی؛ زعفران در آشپزی پاییزه
- name: فصل برداشت زعفران
  calendar: jalali
  month: 8
  day: 1
  lead_days: 30
  keywords: [برداشت, گل زعفران, فصل برداشت, تازه]
  topics:
    - زعفران نو در برابر زعفران کهنه؛ چه تفاوتی دارند؟
    - مراحل برداشت و خشک‌کردن زعفران، از گل تا نخ
    - زمان مناسب خرید زعفران نو
- name: شب یلدا
  calendar: jalali
  month: 9
  day: 30
  lead_days: 35
  keywords: [یلدا, شب چله, چله]
  topics:
    - ایده‌های شب یلدا با زعفران (نوشیدنی و شیرینی)
    - هدیهٔ یلدایی؛ سبد هدیه با زعفران
- name: ماه رمضان
  calendar: hijri
  month: 9
  day: 1
  lead_days: 28
  keywords: [رمضان, ماه رمضان, افطار, سحر, سحری, ماه مبارک]
  topics:
    - زعفران در سفرهٔ افطار: دمنوش، شله‌زرد و زولبیا
    - راهنمای خرید زعفران پیش از ماه رمضان
- name: عید فطر
  calendar: hijri
  month: 10
  day: 1
  lead_days: 14
  keywords: [عید فطر, فطر]
  topics:
    - شیرینی‌ها و دسرهای عید فطر با زعفران
- name: ماه محرم و نذری
  calendar: hijri
  month: 1
  day: 1
  lead_days: 21
  keywords: [محرم, نذری, شله زرد, شله‌زرد, عاشورا]
  topics:
    - طرز تهیهٔ شله‌زرد نذری و مقدار زعفران لازم
    - خرید عمدهٔ زعفران برای نذری
- name: روز پدر
  calendar: hijri
  month: 7
  day: 13
  lead_days: 21
  keywords: [روز پدر]
  topics:
    - ایدهٔ هدیه برای روز پدر
- name: روز مادر
  calendar: hijri
  month: 6
  day: 20
  lead_days: 21
  keywords: [روز مادر]
  topics:
    - ایدهٔ هدیه برای روز مادر
"""


def _topic(t):
    """A topic line that contains «: » is read by YAML as a mapping; turn it back into plain text instead of showing a dict."""
    if isinstance(t, dict):
        return "؛ ".join(f"{k}: {v}" if v else str(k) for k, v in t.items())
    return str(t)


def load_occasions():
    KW.mkdir(parents=True, exist_ok=True)
    if not OCC_FILE.exists():
        OCC_FILE.write_text(DEFAULT_OCCASIONS, encoding="utf-8")
    try:
        data = yaml.safe_load(OCC_FILE.read_text(encoding="utf-8")) or []
    except yaml.YAMLError as e:
        raise ValueError(f"فایل knowledge\\keywords\\occasions.yaml خطای YAML دارد: {e}")
    out = []
    for o in data if isinstance(data, list) else []:
        try:
            cal, m, d = str(o["calendar"]), int(o["month"]), int(o["day"])
            if cal not in ("jalali", "hijri") or not 1 <= m <= 12 or not 1 <= d <= 31:
                continue
            out.append({"name": str(o["name"]), "calendar": cal, "month": m, "day": d, "lead": int(o.get("lead_days", 21)),
                        "keywords": [str(k) for k in o.get("keywords", [])], "topics": [_topic(t) for t in (o.get("topics") or [])]})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def next_dates(o, today, horizon_days=400):
    """All dates of occasion `o` from today until today+horizon (a lunar occasion can fall twice in a Gregorian year)."""
    res, end = [], today + timedelta(days=horizon_days)
    if o["calendar"] == "jalali":
        jy = gregorian_to_jalali(today)[0]
        for y in (jy, jy + 1, jy + 2):
            try:
                d = jalali_to_gregorian(y, o["month"], o["day"])
            except (ValueError, OverflowError):
                continue
            if today <= d <= end:
                res.append(d)
    else:
        hy = gregorian_year_to_hijri(today.year)
        for y in range(hy - 1, hy + 3):
            try:
                d = hijri_to_gregorian(y, o["month"], min(o["day"], 30))
            except (ValueError, OverflowError):
                continue
            if today <= d <= end:
                res.append(d)
    return sorted(res)


def _articles(arts):
    out = []
    for a in arts or []:
        if str(a.get("status", "publish")) != "publish":
            continue
        txt = " ".join([str(a.get("title", "")), str(a.get("focus_keyword", "") or ""), str(a.get("seo_title", "") or "")]
                       + h2_list(a.get("body") or ""))
        out.append((a, norm(txt)))
    return out


def coverage(o, arts):
    """Published articles that already mention this occasion (code only; title/focus keyword/headings)."""
    ks = [norm(k) for k in o["keywords"] if norm(k)]
    hits = []
    for a, t in _articles(arts):
        if any(k in t for k in ks):
            hits.append(a)
    return hits


def calendar(arts=None, today=None, horizon_days=400):
    """-> list of dict rows sorted by date. `arts` = exported articles (corpus.read_articles()); optional."""
    today = today or date.today()
    rows = []
    for o in load_occasions():
        hits = coverage(o, arts) if arts is not None else None
        for d in next_dates(o, today, horizon_days):
            pub = d - timedelta(days=o["lead"])
            days = (d - today).days
            if today > pub:
                state = "late"          # the ideal publish date has passed, the occasion has not
            elif (pub - today).days <= 21:
                state = "soon"          # publish window opens within 3 weeks
            else:
                state = "later"
            old = None
            if hits:
                mods = [str(a.get("modified", ""))[:10] for a in hits if a.get("modified")]
                if mods:
                    try:
                        old = (today - datetime.fromisoformat(max(mods)).date()).days
                    except ValueError:
                        old = None
            rows.append({"name": o["name"], "date": d, "jalali": jalali_str(d), "days": days, "publish_by": pub,
                         "publish_jalali": jalali_str(pub), "state": state, "approx": o["calendar"] == "hijri", "topics": o["topics"],
                         "have": [{"id": a.get("id"), "title": a.get("title"), "url": a.get("url", ""), "modified": str(a.get("modified", ""))[:10]}
                                  for a in (hits or [])] if hits is not None else None,
                         "stale_days": old})
    return sorted(rows, key=lambda r: r["date"])


def calendar_text(rows):
    ST = {"late": "⚠ زمان ایدهٔ انتشار گذشته", "soon": "← به‌زودی شروع کنید", "later": "هنوز زود است"}
    L = []
    for r in rows:
        L.append(f"{r['date']}  {r['jalali']}{' (تقریبی)' if r['approx'] else ''}  |  {r['name']}  |  تا مناسبت {r['days']} روز  |  انتشار تا {r['publish_by']}  |  {ST[r['state']]}")
        if r["have"] is None:
            L.append("    (خروجی مقاله‌ها گرفته نشده؛ پوشش بررسی نشد)")
        elif not r["have"]:
            L.append("    مقاله‌ای دربارهٔ این مناسبت ندارید.")
        else:
            L.append(f"    مقاله‌های موجود: {len(r['have'])}" + (f" (آخرین ویرایش {r['stale_days']} روز پیش)" if r["stale_days"] is not None else ""))
        L += [f"    · {t}" for t in r["topics"][:3]]
    return "\n".join(L)


def run(log=print):
    """CLI/menu entry: prints the calendar and writes reports/content_calendar.csv."""
    import csv
    from .corpus import read_articles
    arts = read_articles() or None
    rows = calendar(arts)
    log(calendar_text(rows))
    REP.mkdir(exist_ok=True)
    f = REP / "content_calendar.csv"
    with open(f, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["occasion", "date", "jalali", "approx", "days_left", "publish_by", "articles_have", "topics"])
        for r in rows:
            w.writerow([r["name"], r["date"], r["jalali"], "yes" if r["approx"] else "", r["days"], r["publish_by"],
                        "" if r["have"] is None else len(r["have"]), " | ".join(r["topics"])])
    log(f"\nفایل کامل: {f}")
    return rows
