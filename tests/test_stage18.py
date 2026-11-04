"""Stage 7 tests: content decay, impact tracking, task queue, notes + hand-off bundle (secrets removed), opening an EXISTING article for AI editing, version diff,
refresh plan, panel pages. No internet. Run:  python -m unittest discover -s tests -v"""
import json
import sys
import threading
import unittest
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_stage13 import art  # noqa: E402
from test_stage15 import Fx, WP2  # noqa: E402

from agent import articlegen as ag, gsc, panel, tasks as tk, tracker as tr  # noqa: E402
from agent.common import StepError  # noqa: E402

U = "https://zafaranmarket.com"


def page(i, clicks, impr, pos):
    return {"key": f"{U}/a{i}/", "clicks": clicks, "impr": impr, "ctr": clicks / impr if impr else 0, "pos": pos}


def snap_file(name, frm, to, pages):
    days = (date.fromisoformat(to) - date.fromisoformat(frm)).days + 1
    gsc.GSC.mkdir(parents=True, exist_ok=True)
    (gsc.GSC / f"gsc_{name}.json").write_text(json.dumps({"imported": "x", "files": [], "queries": [], "pages": pages, "from": frm, "to": to, "days": days}, ensure_ascii=False), encoding="utf-8")


class T7(Fx):
    def setUp(self):
        super().setUp()
        t = Path(self.tmp.name)
        self.keep = (tr.ACTIONS, tk.TASKS, tk.STATE, tk.REP, tk.LOGS, tk.ROOT)
        tr.ACTIONS, tk.TASKS, tk.STATE, tk.REP, tk.LOGS, tk.ROOT = t / "actions.jsonl", t / "tasks", t / "tasks" / "state.json", t / "rep", t / "logs", t
        for d in ("inbox/articles", "inbox/products"):
            (t / d).mkdir(parents=True, exist_ok=True)
        snap = json.loads((t / "site" / "snapshot.json").read_text(encoding="utf-8"))
        snap["posts"] = [{"id": i, "status": "publish", "title": f"مقالهٔ {i}", "slug": f"a{i}", "url": f"{U}/a{i}/", "categories": ["مقالات"], "modified": m}
                         for i, m in ((1, "2026-02-01T10:00:00"), (2, "2026-02-01T10:00:00"), (3, "2024-01-05T10:00:00"))]
        (t / "site" / "snapshot.json").write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        tr.ACTIONS, tk.TASKS, tk.STATE, tk.REP, tk.LOGS, tk.ROOT = self.keep
        super().tearDown()


class Decay(T7):
    def test_decay_between_two_same_length_exports(self):
        snap_file("20260301_000000", "2026-01-01", "2026-01-28", [page(1, 40, 800, 5.0), page(2, 20, 400, 4.0), page(3, 3, 60, 9)])
        snap_file("20260401_000000", "2026-03-01", "2026-03-28", [page(1, 12, 450, 8.5), page(2, 22, 410, 4.1), page(3, 2, 90, 9)])
        r = tr.decay(today=date(2026, 4, 5))
        ids = [x["id"] for x in r["items"]]
        self.assertEqual(ids[0], 1)                                         # biggest loss first
        self.assertNotIn(2, ids)                                            # grew a little: not a decay
        a1 = r["items"][0]
        self.assertTrue(any("کلیک 40" in x for x in a1["reasons"]), a1)
        self.assertTrue(any("جایگاه 5.0" in x for x in a1["reasons"]))
        self.assertIn(3, ids)                                               # old (2024) and still shown -> refresh candidate
        self.assertTrue(any("روز از آخرین ویرایش" in x for x in next(x for x in r["items"] if x["id"] == 3)["reasons"]))
        self.assertEqual(r["note"], "")

    def test_notes_when_data_is_not_comparable(self):
        snap_file("20260301_000000", "2026-01-01", "2026-01-28", [page(1, 40, 800, 5.0)])
        r = tr.decay(today=date(2026, 4, 5))
        self.assertIn("دو خروجی", r["note"])
        snap_file("20260401_000000", "2026-03-01", "2026-03-10", [page(1, 1, 80, 9.0)])        # 10 days against 28
        r = tr.decay(today=date(2026, 4, 5))
        self.assertIn("هم‌اندازه نیستند", r["note"])
        self.assertFalse([x for x in r["items"] if x["id"] == 1 and any("کلیک" in y for y in x["reasons"])])


class Impact(T7):
    def log(self, *rows):
        tr.ACTIONS.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    def test_before_after_relative_to_whole_site(self):
        snap_file("a_before", "2026-02-01", "2026-02-28", [page(1, 28, 700, 7.0), page(2, 280, 3000, 3.0), page(3, 1, 20, 9)])
        snap_file("b_after", "2026-03-11", "2026-04-07", [page(1, 56, 900, 5.0), page(2, 280, 3000, 3.0), page(3, 1, 20, 9)])
        self.log({"time": "2026-03-10T10:00:00", "kind": "article_update", "id": 1, "job": "x"},
                 {"time": "2026-03-10T11:00:00", "kind": "seo_update", "id": 2, "fields": ["title"]},       # same as the whole site: no change
                 {"time": "2026-03-10T11:30:00", "kind": "links_added", "id": 3, "count": 1},                # tiny numbers
                 {"time": "2026-04-05T09:00:00", "kind": "article_publish", "id": 1},                        # no export after it yet
                 {"time": "2026-03-10T12:00:00", "kind": "term_created", "id": 9})                           # not a page change: ignored
        rows = {(r["id"], r["time"][:10]): r for r in tr.impact()}
        r1 = rows[(1, "2026-03-10")]
        self.assertEqual(r1["verdict"], "better")
        self.assertEqual((r1["before"]["clicks_day"], r1["after"]["clicks_day"]), (1.0, 2.0))
        self.assertGreater(r1["rel"], 1.25)
        self.assertEqual(rows[(2, "2026-03-10")]["verdict"], "same")
        self.assertEqual(rows[(3, "2026-03-10")]["verdict"], "tiny")
        self.assertEqual(rows[(1, "2026-04-05")]["verdict"], "no_data")
        self.assertEqual(len(rows), 4)
        self.assertEqual(tr.impact()[0]["time"][:10], "2026-04-05")      # newest first

    def test_worse_and_no_exports(self):
        self.assertEqual(tr.impact(), [])
        self.log({"time": "2026-03-10T10:00:00", "kind": "article_update", "id": 1})
        self.assertEqual(tr.impact()[0]["verdict"], "no_data")             # no export at all
        snap_file("a_before", "2026-02-01", "2026-02-28", [page(1, 60, 900, 4.0), page(2, 100, 2000, 3.0)])
        snap_file("b_after", "2026-03-11", "2026-04-07", [page(1, 20, 500, 8.0), page(2, 100, 2000, 3.0)])
        v = tr.impact()[0]
        self.assertEqual(v["verdict"], "worse")


class Queue(T7):
    def put(self, rel, data):
        f = tk.ROOT / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_sources_priority_and_state(self):
        self.put("knowledge/site/health_latest.json", {"severity": {"H": 3, "M": 5}, "codes": {}, "rows": []})
        self.put("knowledge/graph/graph.json", {"oos_linked": [{"title": "x", "url": "u", "from": ["a"]}], "orphans": [{"id": 1}] * 4})
        self.put("knowledge/media/media.json", {"no_alt": 12, "items": [], "counts": {}, "total": 20, "has_size": True})
        j = ag.new_job("مقالهٔ نیمه‌کاره")
        j.update(text="متن", wp={"id": 5, "link": "", "status": "draft"}, state="draft", dirty=True)
        ag.save(j)
        q = tk.queue(today=date(2026, 4, 5))
        titles = [t["title"] for t in q]
        self.assertTrue(any("3 مشکل جدی" in t for t in titles))
        self.assertTrue(any("ناموجود" in t for t in titles))
        self.assertTrue(any("4 مقالهٔ یتیم" in t for t in titles))
        self.assertTrue(any("12 تصویر بدون Alt" in t for t in titles))
        self.assertTrue(any("تغییرات اعمال‌نشدهٔ مقاله" in t for t in titles))
        self.assertEqual([t["score"] for t in q], sorted((t["score"] for t in q), reverse=True))      # sorted by priority
        self.assertGreaterEqual(q[0]["score"], 88)
        health = next(t for t in q if "مشکل جدی" in t["title"])
        self.assertEqual(health["path"], "/health")
        # state: done hides for 14 days, snooze hides until the date, dismissed forever, reset brings it back
        now = datetime(2026, 4, 5, 10, 0)
        tk.set_status(health["key"], "done", now=now)
        self.assertNotIn(health["key"], [t["key"] for t in tk.queue(now=now + timedelta(days=3))])
        self.assertIn(health["key"], [t["key"] for t in tk.queue(now=now + timedelta(days=15))])
        oos = next(t for t in q if "ناموجود" in t["title"])
        tk.set_status(oos["key"], "snoozed", 7, now=now)
        self.assertNotIn(oos["key"], [t["key"] for t in tk.queue(now=now + timedelta(days=2))])
        self.assertIn(oos["key"], [t["key"] for t in tk.queue(now=now + timedelta(days=8))])
        orph = next(t for t in q if "یتیم" in t["title"])
        tk.set_status(orph["key"], "dismissed", now=now)
        self.assertNotIn(orph["key"], [t["key"] for t in tk.queue(now=now + timedelta(days=90))])
        tk.set_status(orph["key"], "reset")
        self.assertIn(orph["key"], [t["key"] for t in tk.queue(now=now)])

    def test_one_broken_source_does_not_hide_the_rest(self):
        self.put("knowledge/site/health_latest.json", {"severity": {"H": 1}, "codes": {}, "rows": []})
        orig = tk._graph
        tk._graph = lambda add, today: 1 / 0
        try:
            q = tk.queue(today=date(2026, 4, 5))
        finally:
            tk._graph = orig
        self.assertTrue(any("1 مشکل جدی" in t["title"] for t in q))
        self.assertTrue(any("خوانده نشد" in t["title"] for t in q))        # the failure itself is a visible task

    def test_stale_sync_and_gsc_tasks(self):
        s = Path(self.tmp.name) / "site" / "snapshot.json"
        import os
        old = (datetime.now() - timedelta(days=12)).timestamp()
        os.utime(s, (old, old))
        q = tk.queue()
        self.assertTrue(any("همگام‌سازی سایت 12 روز" in t["title"] for t in q))
        self.assertTrue(any("Search Console وارد نشده" in t["title"] for t in q))


class Notes(T7):
    def test_notes_tasks_and_bundle_without_secrets(self):
        tk.add_note("task", "تماس با طراح", "قالب را عوض کند", "high")
        tk.add_note("bug", "خطا در ارسال", "ERROR: ValueError پس از کلیک")
        tk.add_note("idea", "گزارش هفتگی ایمیلی")
        with self.assertRaises(ValueError):
            tk.add_note("bug", "   ")
        with self.assertRaises(ValueError):
            tk.add_note("other", "x")
        q = tk.queue()
        mine = next(t for t in q if t["src"] == "mine")
        self.assertEqual(mine["title"], "تماس با طراح")
        self.assertEqual(mine["score"], 85)                                 # «مهم» outranks normal
        self.assertEqual(len([t for t in q if t["src"] == "mine"]), 1)      # bugs and ideas are not tasks in the queue
        tk.log_error("panel", "ERROR: boom WP_APP_PASSWORD=abcd1234secret api_key: sk-ABCDEF123456789 https://user:pass123@example.com/x")
        tk.LOGS.mkdir(exist_ok=True)
        (tk.LOGS / "actions.jsonl").write_text(json.dumps({"kind": "article_update", "token": "sk-ZZZZZZZZZZZZ"}) + "\n", encoding="utf-8")
        p, text = tk.bundle(now=datetime(2026, 4, 5, 12, 0))
        self.assertTrue(p.exists())
        self.assertEqual(p.name, "handoff_20260405_1200.md")
        for part in ("خطا در ارسال", "گزارش هفتگی ایمیلی", "تماس با طراح", "ERROR: boom", "نسخهٔ پنل", "خطاهایی که ثبت کرده‌ام (1)"):
            self.assertIn(part, text)
        for secret in ("abcd1234secret", "sk-ABCDEF123456789", "pass123", "sk-ZZZZZZZZZZZZ"):
            self.assertNotIn(secret, text)
        self.assertIn("***", text)
        # finishing and deleting
        k = tk.notes("bug")[0]["key"]
        tk.finish_note(k)
        self.assertTrue(tk.notes("bug")[0]["done"])
        self.assertIn("خطاهایی که ثبت کرده‌ام (0)", tk.bundle()[1])
        tk.remove_note(k)
        self.assertEqual(tk.notes("bug"), [])


LONG = " ".join(["واژه"] * 45)
RAW = f"<h2>انتخاب زعفران</h2><p>متن قدیمی دربارهٔ انتخاب زعفران. {LONG}</p><h3>رنگ</h3><p>رنگ مهم است.</p><h2>نگهداری</h2><p>در ظرف دربسته. {LONG}</p>"


class Existing(T7):
    def setUp(self):
        super().setUp()
        import agent.corpus as corpus
        (corpus.ART / "700-x.md").write_text(art(700, "مقالهٔ قدیمی زعفران", "انتخاب زعفران", "## انتخاب زعفران\nمتن"), encoding="utf-8")
        self.wp.posts[700] = {"id": 700, "slug": "old-post", "status": "publish", "link": f"{U}/old-post/", "content": RAW, "title": "مقالهٔ قدیمی زعفران", "excerpt": "خلاصهٔ قدیمی", "mod": "2026-01-01T00:00:00", "meta": {}}

    def test_open_existing_article_then_edit_and_update_safely(self):
        out = self.run_out(lambda: ag.import_existing(700, False))
        self.assertIn("Preview only", out)
        self.assertIsNone(ag.find_job_for_post(700))                        # preview creates nothing
        j = ag.import_existing(700, True)
        j = ag.load(j["id"])
        self.assertEqual((j["wp"]["id"], j["state"], j["imported"], j["dirty"]), (700, "published", True, False))
        self.assertEqual((j["brief"]["slug"], j["brief"]["k"], j["brief"]["cat"], j["brief"]["x"]), ("old-post", "انتخاب زعفران", "مقالات", "خلاصهٔ قدیمی"))
        self.assertEqual([(h["h"], h["s"]) for h in j["brief"]["h"]], [("انتخاب زعفران", ["رنگ"]), ("نگهداری", [])])
        self.assertIn("## انتخاب زعفران", j["text"])
        self.assertIn("### رنگ", j["text"])
        self.assertEqual(j["text"], j["base_text"])
        self.assertEqual(ag.import_existing(700, True)["id"], j["id"])      # opening it again returns the same file, no duplicates
        self.assertEqual(len(list(ag.DRAFTS.glob("a*/job.json"))), 1)
        self.assertFalse([c for c in self.wp.calls if c[0] == "POST"])      # nothing was written to the site
        # the diff box: nothing changed yet, then a rewrite of one section
        self.assertTrue(ag.text_diff(j["base_text"], j["text"])["same"])
        self.replies["rewrite"] = lambda u: "".join(f"## x\n\nنسخهٔ تازهٔ {s['h']} " + " ".join(["واژه"] * 50) + "\n\n" for s in u["sections"])
        self.run_out(lambda: ag.rewrite(ag.load(j["id"]), True, "بهتر کن", "pick", ["نگهداری"]))
        j = ag.load(j["id"])
        d = ag.text_diff(j["base_text"], j["text"])
        self.assertFalse(d["same"])
        self.assertTrue(any(s == "-" and "در ظرف دربسته" in x for s, x in d["changes"]))
        self.assertTrue(any(s == "+" and "نسخهٔ تازهٔ نگهداری" in x for s, x in d["changes"]))
        self.assertEqual((d["heads_added"], d["heads_removed"]), ([], []))
        # update on the site: preview, ticks, backup; excerpt, slug and status are not damaged
        j["links"] = []
        ag.save(j)
        out = self.run_out(lambda: ag.update_post(ag.load(j["id"]), False))
        self.assertIn("وضعیت: منتشرشده (زنده)", out)
        self.run_out(lambda: ag.update_post(ag.load(j["id"]), True, overwrite=False, live_ok=True))
        up = self.wp.posts[700]["updates"][-1]
        self.assertNotIn("slug", up)
        self.assertNotIn("status", up)
        self.assertEqual(up["excerpt"], "خلاصهٔ قدیمی")
        self.assertIn("نسخهٔ تازهٔ نگهداری", up["content"])
        self.assertIn("متن قدیمی دربارهٔ انتخاب زعفران", up["content"])      # the untouched section survived
        self.assertTrue(list(ag.BACKUP.glob("*_before_update_*.html")))

    def test_empty_excerpt_never_erases_the_site_excerpt(self):
        j = ag.import_existing(700, True)
        j = ag.load(j["id"])
        j["brief"]["x"], j["links"] = "", []
        ag.save(j)
        self.run_out(lambda: ag.update_post(j, True, overwrite=True, live_ok=True))
        self.assertNotIn("excerpt", self.wp.posts[700]["updates"][-1])

    def test_special_structure_needs_a_tick(self):
        self.wp.posts[700]["content"] = RAW + '<!-- wp:acf/slider {"x":1} --><div class="slider">x</div>[gallery ids="1,2"]<iframe src="x"></iframe>'
        w = ag.fidelity_warnings(self.wp.posts[700]["content"])
        self.assertTrue(any("acf/slider" in x for x in w))
        self.assertTrue(any("شورت‌کد" in x for x in w))
        self.assertTrue(any("iframe" in x for x in w))
        self.assertTrue(any("div" in x for x in w))
        self.assertEqual(ag.fidelity_warnings(RAW), [])
        with self.assertRaises(StepError):
            self.run_out(lambda: ag.import_existing(700, True))
            ag.import_existing(700, True)
        j = ag.import_existing(700, True, ok_fidelity=True)
        self.assertTrue(ag.load(j["id"])["fidelity"])

    def test_refresh_plan(self):
        j = ag.load(ag.import_existing(700, True)["id"])
        self.replies["refresh_plan"] = {"issues": ["بخش نگهداری کم‌عمق است"], "actions": ["یک بخش سوالات متداول اضافه کن"], "instruction": "بخش نگهداری را عمیق‌تر کن و FAQ اضافه کن"}
        n = self.tasks.count("refresh_plan")
        self.run_out(lambda: ag.refresh_plan(j, False, "افت کلیک"))
        self.assertEqual(self.tasks.count("refresh_plan"), n)
        self.run_out(lambda: ag.refresh_plan(ag.load(j["id"]), True, "افت کلیک"))
        p = ag.load(j["id"])["refresh_plan"]
        self.assertEqual(p["instruction"], "بخش نگهداری را عمیق‌تر کن و FAQ اضافه کن")
        self.assertEqual(self.users["refresh_plan"]["why"], "افت کلیک")
        self.assertTrue(self.users["refresh_plan"]["outline"])
        self.replies["refresh_plan"] = {"issues": [], "actions": [], "instruction": ""}
        with self.assertRaises(ValueError):
            self.run_out(lambda: ag.refresh_plan(ag.load(j["id"]), True))
            ag.refresh_plan(ag.load(j["id"]), True)

    def test_diff_of_headings(self):
        d = ag.text_diff("## الف\n\nمتن\n\n## ب\n\nدیگر", "## الف\n\nمتن\n\n## ج\n\nتازه\n\n### د\n\nریز")
        self.assertEqual((d["heads_added"], d["heads_removed"]), (["ج", "د"], ["ب"]))
        self.assertEqual(d["words"], (6, 9))


class PanelStage7(Existing):
    def setUp(self):
        super().setUp()
        self.srv = panel.make_server(0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def get(self, p):
        return urllib.request.urlopen(f"http://127.0.0.1:{self.port}{p}", timeout=20).read().decode()

    def post(self, p, **f):
        items = []
        for k, v in {"t": panel.TOKEN, **f}.items():
            items += [(k, x) for x in (v if isinstance(v, list) else [v])]
        r = urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{self.port}{p}", data=urllib.parse.urlencode(items).encode()), timeout=60)
        return r.geturl(), r.read().decode()

    def test_tasks_page_notes_and_bundle(self):
        h = self.get("/tasks")
        self.assertIn("صف کار و گزارش خطا", self.get("/"))                  # in the navigation
        self.assertIn("کارهای پیشنهادی", h)
        self.post("/tasksact", do="note_add", kind="bug", title="خطای آزمایشی", note="جزئیات", prio="normal")
        _, h = self.post("/tasksact", do="bundle")
        self.assertIn("خطای آزمایشی", h)
        self.assertIn("بستهٔ گزارش برای توسعه‌دهنده", h)
        self.assertTrue(list(tk.REP.glob("handoff_*.md")))
        q = tk.queue()
        key = q[0]["key"]
        _, h = self.post("/tasksact", do="dismissed", key=key)
        self.assertNotIn(key, [t["key"] for t in tk.queue()])
        _, h = self.post("/tasksact", do="note_add", kind="bug", title="  ")
        self.assertIn("عنوان خالی است", h)

    def test_panel_error_is_kept_for_the_bundle(self):
        self.post("/refreshact", do="import_go", id="abc")
        _, text = tk.bundle()
        self.assertIn("شناسه نامعتبر", text)

    def test_refresh_page_and_open_existing(self):
        snap_file("20260301_000000", "2026-01-01", "2026-01-28", [page(1, 40, 800, 5.0)])
        snap_file("20260401_000000", "2026-03-01", "2026-03-28", [page(1, 12, 450, 8.5)])
        h = self.get("/refresh")
        self.assertIn("مقالهٔ 1", h)
        self.assertIn("باز کردن برای ویرایش", h)
        url, h = self.post("/refreshact", do="import_prev", id="700")
        self.assertIn("Preview only", h)
        url, h = self.post("/refreshact", do="import_go", id="700", why="افت")
        self.assertIn("/write?job=a", url)                                  # lands in the editing page of the new file
        self.assertIn("پیشنهاد AI برای به‌روزرسانی این مقاله", h)
        self.assertIn("تفاوت با نسخهٔ زمان باز کردن پرونده", h)
        self.assertIn("هنوز تغییری نداده‌اید", h)
        self.assertIn("post.php?post=700&action=edit", h)                   # the edit link of the existing article, at the top


if __name__ == "__main__":
    unittest.main()
