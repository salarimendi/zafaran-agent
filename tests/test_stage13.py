"""Stage 3 tests (keywords, gaps, cannibalization, calendar, AI step with a fake model, panel page).
Run:  python -m unittest discover -s tests -v      (needs no internet, no .env, touches no real site data)."""
import builtins
import csv
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["WP_SITE_URL"] = "https://zafaranmarket.com"

from agent import ai, audit, cli, corpus, export, gsc, health, keywords as kw, llm, occasions as oc, panel, routine, site  # noqa: E402


def art(i, title, focus, body="", status="publish", modified="2026-01-10T10:00:00"):
    fm = (f"---\nid: {i}\ntitle: {json.dumps(title, ensure_ascii=False)}\nslug: a{i}\nurl: https://zafaranmarket.com/a{i}/\nstatus: {status}\n"
          f"modified: '{modified}'\ncategories: [مقالات]\ntags: []\nfocus_keyword: {json.dumps(focus, ensure_ascii=False)}\nseo_title: ''\nmeta_description: ''\n---\n\n")
    return fm + body


ARTICLES = [
    (1, "خواص زعفران برای پوست", "خواص زعفران", "## خواص زعفران\nمتن"),
    (2, "طرز تهیه چای زعفران", "چای زعفران", "## چای زعفران\nمتن"),
    (3, "تشخیص زعفران اصل از تقلبی", "تشخیص زعفران اصل", "## تشخیص\nمتن"),
    (4, "راهنمای خرید زعفران نگین", "خرید زعفران نگین", "## نگین\nمتن"),
    (5, "زعفران نگین چیست", "زعفران نگین", "## نگین\nمتن"),
    (6, "شله زرد نذری با زعفران", "شله زرد", "## نذری\nمتن"),
    (7, "فواید زعفران برای سلامت", "خواص زعفران", "## فواید\nمتن"),
    (8, "پیش‌نویس", "پیش نویس", "x", "draft"),
]
QUERIES = [  # query, clicks, impressions, position
    ("خواص زعفران", 40, 900, 5.0), ("قیمت زعفران نگین", 30, 500, 4.2), ("زعفران نگین", 12, 300, 9.0), ("چای زعفران", 8, 200, 6.0),
    ("ست هدیه زعفران", 0, 120, 14.0), ("جعبه کادویی زعفران", 0, 90, 16.0), ("هدیه زعفران عید", 0, 60, 18.0),
    ("کاشت پیاز زعفران", 1, 150, 12.0), ("قیمت پیاز زعفران", 0, 80, 15.0), ("خرید پیاز زعفران", 0, 40, 20.0),
    ("دمنوش زعفران", 0, 70, 11.0), ("زعفران مارکت", 50, 100, 1.2), ("نادر نادر", 0, 3, 30.0), ("شله زرد", 2, 110, 7.0),
]


def csv_bytes(header, rows):
    s = io.StringIO()
    w = csv.writer(s)
    w.writerow(header)
    w.writerows(rows)
    return s.getvalue().encode("utf-8-sig")


def gsc_zip(scale=1, start="2026-09-01", days=28):
    from datetime import datetime, timedelta
    d0 = datetime.fromisoformat(start)
    q = [(k, int(c * scale), int(i * scale), f"{(c / i * 100 if i else 0):.1f}%", p) for k, c, i, p in QUERIES]
    dates = [((d0 + timedelta(days=n)).strftime("%Y-%m-%d"), 5, 100, "5%", 8.0) for n in range(days)]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Queries.csv", csv_bytes(["Top queries", "Clicks", "Impressions", "CTR", "Position"], q))
        z.writestr("Pages.csv", csv_bytes(["Top pages", "Clicks", "Impressions", "CTR", "Position"], [("https://zafaranmarket.com/a1/", 40, 900, "4%", 5.0)]))
        z.writestr("Dates.csv", csv_bytes(["Date", "Clicks", "Impressions", "CTR", "Position"], dates))
    return buf.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.saved = {m: dict(vars(m)) for m in (kw, oc, gsc, corpus, site)}
        for mod, names in ((kw, {"KW": t / "kw", "ANALYSIS": t / "kw" / "analysis.json", "MANUAL": t / "kw" / "manual.txt", "REP": t / "rep"}),
                           (oc, {"KW": t / "kw", "OCC_FILE": t / "kw" / "occasions.yaml", "REP": t / "rep"}),
                           (gsc, {"GSC": t / "gsc", "INBOX": t / "inbox", "REP": t / "rep"}),
                           (corpus, {"ART": t / "arts"}), (site, {"SITE": t / "site"})):
            for k, v in names.items():
                setattr(mod, k, v)
        (t / "arts").mkdir()
        (t / "site").mkdir()
        for a in ARTICLES:
            (t / "arts" / f"{a[0]}-x.md").write_text(art(*a), encoding="utf-8")
        snap = {"fetched_at": "x", "site": "https://zafaranmarket.com",
                "posts": [{"id": a[0], "status": a[4] if len(a) > 4 else "publish", "title": a[1], "slug": f"a{a[0]}", "url": f"https://zafaranmarket.com/a{a[0]}/",
                           "categories": ["مقالات"], "modified": "2026-01-10"} for a in ARTICLES],
                "pages": [{"id": 90, "status": "publish", "title": "تماس با ما", "slug": "contact", "url": "https://zafaranmarket.com/contact/"}],
                "products": [{"id": 100, "status": "publish", "name": "زعفران سرگل ۱ گرمی", "sku": "s1", "slug": "p1", "url": "https://zafaranmarket.com/p/p1/", "price": "1", "categories": ["زعفران سرگل"]},
                             {"id": 101, "status": "publish", "name": "زعفران نگین ۱ مثقال", "sku": "s2", "slug": "p2", "url": "https://zafaranmarket.com/p/p2/", "price": "1", "categories": ["زعفران نگین"]}],
                "product_cat": [{"id": 1, "name": "زعفران سرگل", "slug": "sar", "parent": 0, "count": 3, "link": "https://zafaranmarket.com/c/sar/"},
                                {"id": 2, "name": "زعفران نگین", "slug": "neg", "parent": 0, "count": 2, "link": "https://zafaranmarket.com/c/neg/"},
                                {"id": 3, "name": "ظروف زعفران", "slug": "zof", "parent": 0, "count": 2, "link": "https://zafaranmarket.com/c/zof/"}],
                "category": [{"id": 5, "name": "مقالات", "slug": "m", "parent": 0, "count": 7, "link": ""}], "product_tag": [], "post_tag": []}
        (t / "site" / "snapshot.json").write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        for m, d in self.saved.items():
            for k, v in d.items():
                setattr(m, k, v)
        self.tmp.cleanup()

    def import_gsc(self, scale=1, start="2026-09-01"):
        return gsc.import_files([("export.zip", gsc_zip(scale, start))])


class TextAndDates(unittest.TestCase):
    def test_tokens(self):
        self.assertEqual(kw.tokens("زعفران‌ها و نگین"), ["زعفران", "نگین"])
        self.assertEqual(kw.tokens("زعفرانها"), ["زعفران"])
        self.assertEqual(kw.tokens("قيمت زعفران ۱۰۰"), ["قیمت", "زعفران"])  # Arabic ي, Persian digits dropped
        self.assertEqual(kw.intent_of("قیمت زعفران نگین"), "commercial")
        self.assertEqual(kw.intent_of("خواص زعفران"), "info")
        self.assertEqual(kw.intent_of("زعفران مارکت", brand=True), "brand")

    def test_jalali(self):
        J = oc.jalali_to_gregorian
        self.assertEqual(J(1403, 1, 1), date(2024, 3, 20))
        self.assertEqual(J(1405, 1, 1), date(2026, 3, 21))
        self.assertEqual(J(1405, 9, 30), date(2026, 12, 21))   # Yalda
        self.assertEqual(oc.gregorian_to_jalali(date(2026, 11, 1)), (1405, 8, 10))
        self.assertEqual(oc.gregorian_to_jalali(date(2025, 3, 20)), (1403, 12, 30))  # leap year end
        d = date(2000, 3, 1)
        while d < date(2050, 1, 1):  # round trip for 50 years
            self.assertEqual(J(*oc.gregorian_to_jalali(d)), d)
            d = date.fromordinal(d.toordinal() + 7)

    def test_hijri_approx(self):
        H = oc.hijri_to_gregorian
        for got, want in ((H(1447, 9, 1), date(2026, 2, 18)), (H(1448, 9, 1), date(2027, 2, 8)), (H(1448, 1, 1), date(2026, 6, 17))):
            self.assertLessEqual(abs((got - want).days), 2)


class Calendar(Base):
    def test_default_topics_are_plain_text(self):
        for o in oc.load_occasions():
            self.assertTrue(o["topics"] and all(isinstance(t, str) and not t.startswith("{") for t in o["topics"]), o["name"])
        oc.OCC_FILE.write_text("- {name: x, calendar: jalali, month: 1, day: 1, topics: [{'عنوان: دو': 'ایده'}]}\n", encoding="utf-8")
        self.assertIn("عنوان: دو", oc.load_occasions()[0]["topics"][0])

    def test_calendar_states(self):
        rows = oc.calendar(None, today=date(2026, 11, 1))
        by = {r["name"]: r for r in rows}
        y = by["شب یلدا"]
        self.assertEqual(y["date"], date(2026, 12, 21))
        self.assertEqual(y["state"], "soon")
        self.assertEqual(y["publish_by"], date(2026, 11, 16))
        self.assertEqual(by["نوروز و سفرهٔ هفت‌سین"]["date"], date(2027, 3, 21))
        self.assertTrue(by["ماه رمضان"]["approx"])
        self.assertEqual(rows, sorted(rows, key=lambda r: r["date"]))
        self.assertTrue(all(r["have"] is None for r in rows))  # no articles passed -> coverage unknown

    def test_late_state_and_coverage(self):
        arts = corpus.read_articles()
        rows = oc.calendar(arts, today=date(2027, 2, 1))
        by = {}
        for r in rows:
            by.setdefault(r["name"], r)  # first (nearest) occurrence of each occasion
        self.assertEqual(by["ماه رمضان"]["state"], "late")        # window opened Jan 11, occasion Feb 8
        self.assertEqual(by["ماه محرم و نذری"]["have"][0]["id"], 6)  # «شله زرد نذری» article counts as coverage
        self.assertEqual(by["شب یلدا"]["have"], [])

    def test_occasions_file_created_and_editable(self):
        oc.load_occasions()
        self.assertTrue(oc.OCC_FILE.exists())
        oc.OCC_FILE.write_text("- {name: تست, calendar: jalali, month: 2, day: 5, lead_days: 10, keywords: [x], topics: [t]}\n- {broken: true}\n", encoding="utf-8")
        self.assertEqual([o["name"] for o in oc.load_occasions()], ["تست"])
        oc.OCC_FILE.write_text("- [unclosed", encoding="utf-8")
        with self.assertRaises(ValueError):
            oc.load_occasions()

    def test_run_writes_csv(self):
        out = []
        oc.run(out.append)
        self.assertTrue((Path(self.tmp.name) / "rep" / "content_calendar.csv").exists())


class Analysis(Base):
    def test_requires_snapshot(self):
        (Path(self.tmp.name) / "site" / "snapshot.json").unlink()
        with self.assertRaises(ValueError):
            kw.build()

    def test_build_without_gsc(self):
        res = kw.build(10, lambda *_: None)
        self.assertFalse(res["has_gsc"])
        self.assertEqual(res["gap_clusters"], [])
        self.assertIn("خواص زعفران", [r["key"] for r in res["bank"]])
        self.assertIn("ظروف زعفران", [c["name"] for c in res["cats_without_article"]])

    def test_full_build(self):
        self.import_gsc()
        res = kw.build(10, lambda *_: None)
        st = {r["key"]: r for r in res["bank"]}
        self.assertEqual(st["خواص زعفران"]["status"], "covered")
        self.assertEqual(st["قیمت زعفران نگین"]["target"]["kind"], "product_cat")   # buying intent -> category page
        self.assertEqual(st["زعفران مارکت"]["status"], "brand")
        self.assertNotIn("نادر نادر", [c["label"] for c in res["gap_clusters"]])    # below min impressions
        self.assertEqual(st["دمنوش زعفران"]["status"], "gap")
        labels = {c["label"]: c for c in res["gap_clusters"]}
        gift = next(c for c in res["gap_clusters"] if "هدیه" in c["label"])
        self.assertEqual(gift["n"], 2)                                                 # queries sharing «هدیه» grouped; «جعبه کادویی» left for the AI to regroup
        bulb = next(c for c in res["gap_clusters"] if "پیاز" in c["label"])
        self.assertEqual(bulb["n"], 3)
        self.assertEqual([c["impr"] for c in res["gap_clusters"]], sorted((c["impr"] for c in res["gap_clusters"]), reverse=True))
        # cannibalization
        exact = [g for g in res["cannibal_focus"] if g["exact"]]
        self.assertEqual({p["id"] for p in exact[0]["pages"]}, {1, 7})
        similar = [g for g in res["cannibal_focus"] if not g["exact"]]
        self.assertTrue(any({p["id"] for p in g["pages"]} >= {4, 5} for g in similar))
        self.assertTrue(any(c["query"] == "زعفران نگین" for c in res["cannibal_queries"]))
        # different sub-intents must NOT be called rivals: «خواص زعفران» vs the article about detecting real saffron
        ben = next(c for c in res["cannibal_queries"] if c["query"] == "خواص زعفران")
        self.assertEqual({p["id"] for p in ben["pages"]}, {1, 7})
        self.assertFalse(any(c["query"] == "دمنوش زعفران" for c in res["cannibal_queries"]))
        # draft article is not a page
        self.assertNotIn(8, [p["id"] for g in res["cannibal_focus"] for p in g["pages"]])
        # files
        rep = Path(self.tmp.name) / "rep"
        for f in ("keywords_bank.csv", "content_gaps.csv", "keywords_cannibal.csv"):
            self.assertTrue((rep / f).exists(), f)
        self.assertEqual(kw.load_analysis()["built"], res["built"])
        self.assertTrue(kw.summary_lines(res))

    def test_manual_keywords(self):
        kw.save_manual("# comment\nظرف نقره زعفران\n\nزعفران سرگل\n")
        res = kw.build(10, lambda *_: None)
        self.assertEqual(kw.read_manual(), ["ظرف نقره زعفران", "زعفران سرگل"])
        self.assertIn("ظرف نقره زعفران", [r["key"] for r in res["gap_manual"]])
        self.assertNotIn("زعفران سرگل", [r["key"] for r in res["gap_manual"]])  # a product category covers it

    def test_rising(self):
        import time
        self.import_gsc(scale=1, start="2026-07-01")
        time.sleep(1.1)  # export files are named by the second
        self.import_gsc(scale=1.0, start="2026-09-01")
        files = gsc.saved()
        self.assertEqual(len(files), 2)
        cur, prev = gsc.load(files[-1]), gsc.load(files[-2])
        # make one query explode in the current export
        for q in cur["queries"]:
            if q["key"] == "دمنوش زعفران":
                q["impr"] = 400
        r = kw.rising(cur, prev)
        self.assertEqual(r["rows"][0]["key"], "دمنوش زعفران")
        self.assertEqual(kw.rising(cur, None)["rows"], [])
        prev["days"] = 90
        self.assertIn("هم‌اندازه", kw.rising(cur, prev)["note"])


class FakeAI(Base):
    def setUp(self):
        super().setUp()
        for k, v in {"LLM_BASE_URL": "https://x.test/v1", "LLM_API_KEY": "k", "LLM_MODEL": "m", "LLM_PRICE_IN_PER_M": "1", "LLM_PRICE_OUT_PER_M": "2",
                     "LLM_BUDGET_USD": "1", "LLM_MAX_OUTPUT_TOKENS": "1500"}.items():
            os.environ[k] = v
        self._load = llm.load_dotenv
        llm.load_dotenv = lambda *a, **k: None   # do not read a real .env
        self._input, self._chat = builtins.input, llm.chat
        self.calls = []

    def tearDown(self):
        builtins.input, llm.chat, llm.load_dotenv = self._input, self._chat, self._load
        for k in [k for k in os.environ if k.startswith("LLM_")]:
            del os.environ[k]
        super().tearDown()

    def test_estimate_does_not_call_model(self):
        self.import_gsc()
        kw.build(10, lambda *_: None)
        llm.chat = lambda *a, **k: self.calls.append(1)
        buf = io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(buf):
            ai.ai_gap(False)
        self.assertEqual(self.calls, [])
        self.assertIn("Estimated tokens", buf.getvalue())

    def test_ai_gap_validates_and_saves(self):
        self.import_gsc()
        res = kw.build(10, lambda *_: None)
        ids = [c["id"] for c in res["gap_clusters"]]
        reply = json.dumps([{"g": ids[:2], "type": "article", "t": "راهنمای هدیه دادن زعفران", "k": "هدیه زعفران"},
                            {"g": [ids[2]], "type": "article", "t": "خواص زعفران برای پوست و مو", "k": "خواص زعفران"},   # duplicates an existing focus keyword
                            {"g": [ids[0]], "type": "article", "t": "دوباره", "k": "x y"},                                # cluster already used
                            {"g": [999], "type": "banana", "t": "", "k": ""}], ensure_ascii=False)   # garbage must be dropped, not crash

        def fake(msgs, max_out=None, task="x", use_cache=True):
            self.calls.append(task)
            return reply, {"cached": False, "tin": 100, "tout": 50, "cost": 0.0005, "sec": 0.1}
        llm.chat = fake
        builtins.input = lambda *a: "y"
        ai.ai_gap(True)
        self.assertEqual(self.calls, ["gap"])  # exactly ONE call
        d = json.loads((kw.KW / "topics_ai.json").read_text(encoding="utf-8"))
        self.assertEqual(len(d["items"]), 2)
        ok = next(i for i in d["items"] if i["k"] == "هدیه زعفران")
        self.assertEqual(ok["notes"], "")
        bad = next(i for i in d["items"] if i["k"] == "خواص زعفران")
        self.assertIn("هم‌نوع‌خواری", bad["notes"])
        # cancelling must not call the model
        self.calls.clear()
        builtins.input = lambda *a: "n"
        ai.ai_gap(True)
        self.assertEqual(self.calls, [])


class RoutineReport(Base):
    def test_weekly_report_has_stage3_sections(self):
        t = Path(self.tmp.name)
        self.import_gsc()
        keep = {(m, n): getattr(m, n) for m, n in ((routine, "REP"), (routine, "LOGS"), (cli, "check"), (site, "sync"), (export, "export_articles"),
                                                       (audit, "audit"), (health, "run"), (health, "delta"), (oc, "calendar"))}
        try:
            routine.REP, routine.LOGS = t / "rep", t / "logs"
            cli.check, cli.results = (lambda: None), []
            site.sync = export.export_articles = lambda *a, **k: None
            audit.audit = lambda: {"n": 1, "codes": {}, "worst": []}
            health.run = lambda *a, **k: {"severity": {"H": 0}, "codes": {}}
            health.delta = lambda: {}
            real_cal = oc.calendar
            oc.calendar = lambda arts=None, **k: real_cal(arts, today=date(2026, 11, 1))  # fixed "today" so the test never depends on the date
            ok = routine.run()
        finally:
            for (m, n), v in keep.items():
                setattr(m, n, v)
        rep = (t / "rep" / "routine_latest.md").read_text(encoding="utf-8")
        self.assertTrue(ok)
        self.assertIn("## کلمات کلیدی و شکاف محتوا", rep)
        self.assertIn("خوشه‌های شکاف محتوا", rep)
        self.assertIn("## مناسبت‌های پیش‌رو", rep)


class PanelPages(Base):
    def setUp(self):
        super().setUp()
        self.srv = panel.make_server(0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=20) as r:
            return r.status, r.read().decode("utf-8")

    def post(self, path, **f):
        data = urllib.parse.urlencode({"t": panel.TOKEN, **f}).encode()
        with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data), timeout=60) as r:
            return r.status, r.read().decode("utf-8")

    def test_pages(self):
        s, h = self.get("/keywords")
        self.assertEqual(s, 200)
        self.assertIn("تقویم محتوا", h)
        self.assertIn("شب یلدا", h)
        s, h = self.post("/kwbuild", min="10")
        self.assertEqual(s, 200)
        self.assertIn("بانک", h)
        self.import_gsc()
        s, h = self.post("/kwbuild", min="10")
        self.assertIn("شکاف محتوا", h)
        self.assertIn("هدیه", h)
        self.assertIn("هم‌نوع‌خواری", h)
        for st in ("gap", "weak", "covered", "mismatch", "all"):
            self.assertEqual(self.get(f"/keywords?st={st}")[0], 200)
        s, h = self.post("/kwsave", manual="ظرف نقره زعفران")
        self.assertIn("ذخیره شد", h)
        s, h = self.post("/run", action="ai_gap_est")
        self.assertIn("Estimated tokens", h.replace("<", " ")) if "LLM" not in h else None
        s, h = self.get("/")
        self.assertIn("کلمات کلیدی", h)
        self.assertEqual(self.get("/roadmap")[0], 200)

    def test_bad_token_rejected(self):
        data = urllib.parse.urlencode({"t": "wrong", "min": "10"}).encode()
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{self.port}/kwbuild", data=data), timeout=20)
        self.assertEqual(e.exception.code, 403)

    def test_html_is_escaped(self):
        kw.save_manual("<script>alert(1)</script>")
        kw.build(10, lambda *_: None)
        s, h = self.get("/keywords?st=all")
        self.assertNotIn("<script>alert(1)</script>", h)


if __name__ == "__main__":
    unittest.main()
