"""Stage 4 tests: article engine (brief, writing, QC, links, draft, images, publish, auto) with a fake AI and a fake WordPress,
plus the panel pages and the new 'select all' ticks. Needs no internet and touches no real data.
Run:  python -m unittest discover -s tests -v"""
import builtins
import json
import os
import re
import sys
import threading
import unittest
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_stage13 import Base  # noqa: E402

from agent import articlegen as ag, common, llm, panel  # noqa: E402
from agent.common import StepError  # noqa: E402

ORD = ["اول", "دوم", "سوم", "چهارم", "پنجم", "ششم", "هفتم", "هشتم", "نهم", "دهم", "یازدهم", "دوازدهم", "سیزدهم", "چهاردهم", "پانزدهم", "شانزدهم"]
K = "هدیه زعفران"
META = "در این راهنما بخوانید هدیه زعفران را چگونه انتخاب کنید، چه بسته‌بندی بهتر است و در مناسبت‌ها چطور هدیه‌ای ماندگار و خوشایند بدهید."
HEADS = ["چرا هدیه زعفران انتخاب خوبی است؟", "انواع زعفران مناسب هدیه", "بسته‌بندی و ظرف مناسب", "زمان و مناسبت‌های هدیه دادن", "نکاتی برای خرید زعفران نگین", "جمع‌بندی"]
_n = [0]


def paragraph(topic):
    _n[0] += 1
    return " ".join(f"این بخش دربارهٔ {topic} است و نکتهٔ {ORD[(_n[0] + i) % len(ORD)]} شمارهٔ {ORD[(_n[0] * 3 + i) % len(ORD)]} را با مثال {ORD[(_n[0] * 5 + i) % len(ORD)]} روشن می‌کند." for i in range(4))


class Resp:
    def __init__(self, data, code=200):
        self._d, self.status_code, self.headers, self.text = data, code, {"X-WP-TotalPages": "1"}, json.dumps(data, ensure_ascii=False)

    def json(self):
        return self._d


class FakeWP:
    def __init__(self, strip_script=False):
        self.terms = {"wp/v2/categories": [{"id": 5, "name": "مقالات", "slug": "m", "parent": 0, "count": 7}],
                      "wp/v2/tags": [{"id": 9, "name": "زعفران", "slug": "saffron", "parent": 0, "count": 5}]}
        self.posts, self.media, self.calls, self.strip = {}, {}, [], strip_script

    def get(self, route, auth="wp", **kw):
        return self.request("GET", route, auth, **kw)

    def request(self, method, route, auth="wp", **kw):
        self.calls.append((method, route))
        p = kw.get("params") or {}
        if route in self.terms and method == "GET":
            return Resp(self.terms[route])
        if route in self.terms and method == "POST":
            d = {"id": 100 + len(self.terms[route]), "parent": 0, "count": 0, **kw["json"]}
            self.terms[route].append(d)
            return Resp(d, 201)
        if route in ("wp/v2/posts", "wp/v2/pages") and method == "GET":
            return Resp([x for x in self.posts.values() if x["slug"] == p.get("slug")] if route.endswith("posts") else [])
        if route == "wp/v2/posts" and method == "POST":
            j = kw["json"]
            pid = 500 + len(self.posts)
            self.posts[pid] = {"id": pid, "slug": j["slug"], "status": j["status"], "content": j["content"], "meta": j.get("meta", {}), "payload": j, "link": f"https://zafaranmarket.com/{j['slug']}/"}
            return Resp({"id": pid, "link": self.posts[pid]["link"], "status": "draft", "meta": j.get("meta", {})}, 201)
        if route == "wp/v2/media" and method == "POST":
            mid = 700 + len(self.media)
            self.media[mid] = {"bytes": len(kw["data"]), "headers": kw["headers"]}
            return Resp({"id": mid, "source_url": f"https://zafaranmarket.com/wp-content/uploads/{mid}.webp"}, 201)
        m = re.fullmatch(r"wp/v2/media/(\d+)", route)
        if m and method == "POST":
            self.media[int(m.group(1))].update(kw["json"])
            return Resp({"id": int(m.group(1))})
        m = re.fullmatch(r"wp/v2/posts/(\d+)", route)
        if m:
            post = self.posts[int(m.group(1))]
            if method == "POST":
                j = kw["json"]
                if "content" in j:
                    post["content"] = re.sub(r'<script type="application/ld\+json">.*?</script>', "", j["content"], flags=re.S) if self.strip else j["content"]
                post["status"] = j.get("status", post["status"])
                if "featured_media" in j:
                    post["featured"] = j["featured_media"]
            return Resp({"id": post["id"], "status": post["status"], "link": post["link"], "content": {"raw": post["content"]}})
        raise AssertionError(f"unexpected call {method} {route}")


def brief_reply(**over):
    r = {"cat": "NEW:هدیه و سوغات|gift", "slug": "saffron-gift-guide", "tags": ["زعفران", "ناموجود"], "k": K, "t": "راهنمای هدیه زعفران | انتخاب و بسته‌بندی", "m": META,
         "x": "راهنمای کوتاه انتخاب هدیه زعفران.", "h": [{"h": h, "n": "یادداشت"} for h in HEADS],
         "img": [{"w": "cover", "kind": "photo", "p": "عکس سبد هدیه زعفران", "f": "saffron-gift-box", "a": "سبد هدیه زعفران", "d": ""},
                 {"w": 2, "kind": "photo", "p": "عکس نخ زعفران", "f": "saffron-threads", "a": "نخ‌های زعفران", "d": ""},
                 {"w": 4, "kind": "infographic", "p": "اینفوگرافیک مناسبت‌ها", "f": "gift-occasions", "a": "مناسبت‌های هدیه دادن", "d": "نوروز، یلدا"}]}
    r.update(over)
    return r


class Engine(Base):
    def setUp(self):
        super().setUp()
        t = Path(self.tmp.name)
        snap = json.loads((t / "site" / "snapshot.json").read_text(encoding="utf-8"))
        snap["post_tag"] = [{"id": 9, "name": "زعفران", "slug": "saffron", "count": 5, "link": ""}]
        snap["category"] = [{"id": 5, "name": "مقالات", "slug": "m", "parent": 0, "count": 7, "link": ""}]
        (t / "site" / "snapshot.json").write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        self.old = {n: getattr(ag, n) for n in ("DRAFTS", "IMG_IN", "BUILD", "BACKUP", "STYLE_GUIDE", "get_client")} | {"LOGS": common.LOGS}
        ag.DRAFTS, ag.IMG_IN, ag.BUILD, ag.BACKUP, ag.STYLE_GUIDE = t / "drafts", t / "imgs", t / "build", t / "bk", t / "style_guide.md"
        common.LOGS = t / "logs"
        self.wp = FakeWP()
        ag.get_client = lambda: self.wp
        for k, v in {"LLM_BASE_URL": "https://x.test/v1", "LLM_API_KEY": "k", "LLM_MODEL": "m", "LLM_PRICE_IN_PER_M": "1", "LLM_PRICE_OUT_PER_M": "2",
                     "LLM_BUDGET_USD": "1", "LLM_MAX_OUTPUT_TOKENS": "1500"}.items():
            os.environ[k] = v
        self._l, self._input, self._chat = llm.load_dotenv, builtins.input, llm.chat
        llm.load_dotenv = lambda *a, **k: None
        builtins.input = lambda *a: "y"
        self.tasks, self.reply, self.raw, self.last = [], brief_reply(), None, None

        def fake(msgs, max_out=None, task="x", use_cache=True):
            self.tasks.append(task)
            self.last = json.loads(msgs[1]["content"])
            if task == "brief":
                text = self.raw if self.raw is not None else json.dumps([self.reply], ensure_ascii=False)
            else:
                u = json.loads(msgs[1]["content"])
                text = (paragraph(u["title"]) + "\n\n" + paragraph(u["k"]) + "\n\n" if u["intro"] else "")
                for s in u["sections"]:
                    text += f"## {s['h']}\n\n{paragraph(s['h'])}\n\n{paragraph('خرید زعفران نگین') if 'نگین' in s['h'] else paragraph(s['h'])}\n\n"
            return text, {"cached": False, "tin": 100, "tout": 100, "cost": 0.001, "sec": 0.1}
        llm.chat = fake

    def tearDown(self):
        for n, v in self.old.items():
            if n == "LOGS":
                common.LOGS = v
            else:
                setattr(ag, n, v)
        llm.load_dotenv, builtins.input, llm.chat = self._l, self._input, self._chat
        for k in [k for k in os.environ if k.startswith("LLM_")]:
            del os.environ[k]
        super().tearDown()

    def briefed(self, title="راهنمای هدیه زعفران", existing_only=False):
        j = ag.new_job(title, "")
        ag.make_brief(j, True, existing_only)
        return ag.load(j["id"])

    def written(self, **kw):
        j = self.briefed(**kw)
        ag.write(j, True)
        j = ag.load(j["id"])
        j["brief"]["schema"] = True                                      # the default is off now; these tests exercise the schema path
        ag.save(j)
        return ag.load(j["id"])


class Brief(Engine):
    def test_brief_is_normalised_and_checked(self):
        j = self.briefed()
        b = j["brief"]
        self.assertEqual(j["state"], "brief")
        self.assertEqual(b["new_cat"], {"name": "هدیه و سوغات", "slug": "gift"})
        self.assertEqual(b["tags"], ["زعفران"])                         # unknown tag dropped, never invented
        self.assertEqual([i["w"] for i in b["img"]], ["cover", 2, 4])
        self.assertEqual([i["n"] for i in b["img"]], [1, 2, 3])
        self.assertEqual(ag.brief_warnings(b), [])
        self.assertEqual(self.wp.calls, [])                              # the brief never touches the site

    def test_existing_only_blocks_new_category(self):
        self.reply = brief_reply(cat="NEW:چیز تازه|x")
        b = self.briefed(existing_only=True)["brief"]
        self.assertIsNone(b["new_cat"])
        self.assertTrue(any("دسته" in w for w in ag.brief_warnings(b)))

    def test_warnings(self):
        b = self.briefed()["brief"]
        b.update(slug="نامه", t="x" * 70, m="کوتاه", k="خواص زعفران")
        w = " | ".join(ag.brief_warnings(b))
        for part in ("slug", "عنوان سئو", "متا", "هم‌نوع‌خواری"):
            self.assertIn(part, w)
        b["slug"] = "a1"
        self.assertIn("قبلاً", " ".join(ag.brief_warnings(b)))

    def test_idea_becomes_better_title_and_uses_real_searches(self):
        self.import_gsc()
        from agent import keywords as kw
        kw.build(10, lambda *_: None)
        self.reply = brief_reply(title="هدیهٔ کادویی زعفران برای روز مادر")
        j = self.briefed("هدیه برای روز مادر")
        self.assertEqual(j["title"], "هدیهٔ کادویی زعفران برای روز مادر")
        self.assertEqual(j["idea"], "هدیه برای روز مادر")                  # what you typed is kept as the idea
        self.assertEqual(self.last["idea"], "هدیه برای روز مادر")
        self.assertIn("ست هدیه زعفران", self.last["q"])                   # real searches that share a word with the idea are given to the AI
        self.assertNotIn("title", j["brief"])
        self.reply = brief_reply(title="x")                                # absurd title: ignored, the earlier one stays
        ag.make_brief(j, True)
        self.assertEqual(ag.load(j["id"])["title"], "هدیهٔ کادویی زعفران برای روز مادر")
        self.assertEqual(self.last["idea"], "هدیه برای روز مادر")         # the next brief starts from the idea again, not from the AI title

    def test_image_prompts_carry_logo_and_site(self):
        b = self.briefed()["brief"]
        for im in b["img"]:
            self.assertIn("logo-160-57.png", im["p"])
            self.assertIn("زعفران مارکت", im["p"])
        info = [i for i in b["img"] if i["kind"] == "infographic"][0]
        self.assertIn("zafaranmarket.com", info["p"])
        self.assertIn("بالای اینفوگرافیک", info["p"])
        self.assertEqual(b["img"][0]["p"].count("logo-160-57.png"), 1)

    def test_at_least_six_headings(self):
        self.reply = brief_reply(h=[{"h": h, "n": ""} for h in HEADS[:5]])
        b = self.briefed()["brief"]
        self.assertTrue(any("۶" in w for w in ag.brief_warnings(b)))
        self.assertIn("حداقل ۶ تیتر", ag.BRIEF_SYS)
        b["h"].append({"h": "یک تیتر دیگر", "n": "", "on": True})
        self.assertFalse(any("۶" in w for w in ag.brief_warnings(b)))

    def test_estimate_only_and_cancel(self):
        j = ag.new_job("x")
        ag.make_brief(j, False)
        builtins.input = lambda *a: "n"
        ag.make_brief(j, True)
        self.assertEqual(self.tasks, [])

    def test_form_edit(self):
        j = self.briefed()
        f = {"title": ["عنوان تازه"], "cat": ["مقالات"], "tag": ["زعفران"], "new_tags": ["هدیه|gift-tag\n"], "b_slug": ["New-Slug"], "b_k": [K], "b_t": ["t"], "b_m": [META], "b_x": ["x"],
             "h_0": ["تیتر یک"], "hon_0": ["on"], "h_1": ["تیتر دو"], "ion_1": ["on"], "if_1": ["Cover-File"], "ia_1": ["alt"], "ip_1": ["p"], "iw_1": ["cover"], "schema": ["on"]}
        ag.parse_brief_form(f, j)
        b = j["brief"]
        self.assertEqual((j["title"], b["cat"], b["slug"], b["new_cat"]), ("عنوان تازه", "مقالات", "new-slug", None))
        self.assertEqual(b["new_tags"], [{"name": "هدیه", "slug": "gift-tag"}])
        self.assertEqual([h["h"] for h in b["h"]], ["تیتر یک", "تیتر دو"])
        self.assertFalse(b["h"][1]["on"])                                # unticked = removed from the outline
        self.assertEqual([i["on"] for i in b["img"]], [True, False, False])
        self.assertEqual(b["img"][0]["f"], "cover-file")


class RealWorldReplies(Engine):
    """The exact failure seen on the real site: ```json fence + an object (not a list) + a category that is called «blog» in English."""
    def setUp(self):
        super().setUp()
        t = Path(self.tmp.name)
        snap = json.loads((t / "site" / "snapshot.json").read_text(encoding="utf-8"))
        snap["category"] = [{"id": 5, "name": "blog", "slug": "blog", "parent": 0, "count": 7, "link": ""}, {"id": 6, "name": "آموزش", "slug": "learn", "parent": 0, "count": 2, "link": ""}]
        snap["post_tag"] = [{"id": 9, "name": "زعفران", "slug": "saffron", "count": 5, "link": ""}, {"id": 10, "name": "هدیه", "slug": "gift", "count": 2, "link": ""}]
        (t / "site" / "snapshot.json").write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")

    def test_fenced_object_and_numbered_choices(self):
        r = brief_reply(cat=1, tags=[1, 2, 7])
        self.raw = "```json\n" + json.dumps(r, ensure_ascii=False, indent=2) + "\n```"
        b = self.briefed("هدیه برای روز مادر")["brief"]
        self.assertEqual(b["cat"], "blog")                               # chosen by NUMBER, the English name is no problem
        self.assertEqual(b["tags"], ["زعفران", "هدیه"])                  # 7 does not exist -> dropped
        self.assertEqual(len(b["h"]), 6)

    def test_persian_name_or_slug_still_resolves_and_never_invents(self):
        self.raw = json.dumps(brief_reply(cat="blog"), ensure_ascii=False)
        self.assertEqual(self.briefed()["brief"]["cat"], "blog")
        self.raw = json.dumps(brief_reply(cat="بلاگ"), ensure_ascii=False)
        b = self.briefed(existing_only=True)["brief"]
        self.assertEqual((b["cat"], b["new_cat"]), ("", None))           # unknown name: left empty (you choose), nothing invented
        self.assertTrue(any("دسته" in w for w in ag.brief_warnings(b)))

    def test_cut_off_reply_gives_a_useful_error(self):
        self.raw = '```json\n{\n  "cat": 1,\n  "slug": "x",\n  "tags": [\n    "زعفران",\n'
        j = ag.new_job("x")
        with self.assertRaises(llm.LLMError) as e:
            ag.make_brief(j, True)
        self.assertIn("LLM_MAX_OUTPUT_TOKENS", str(e.exception))
        self.assertEqual(ag.load(j["id"])["state"], "new")


class FinalStep(Engine):
    def ready(self):
        j = self.written()
        j["brief"]["cat"], j["brief"]["new_cat"] = "مقالات", None
        ag.save(j)
        return ag.load(j["id"])

    def put(self, j, nums):
        from PIL import Image
        d = ag.img_dir(j)
        d.mkdir(parents=True, exist_ok=True)
        for n in nums:
            Image.new("RGB", (900, 600), (10, 90, 160)).save(d / f"{n:02d}.jpg")

    def test_everything_prepared_locally_then_one_send(self):
        j = self.ready()
        self.put(j, [1, 2, 3])
        ag.create_draft(j, False)
        self.assertEqual([c for c in self.wp.calls if c[0] == "POST"], [])   # preview converts locally, sends nothing
        self.assertEqual(self.wp.media, {})
        ag.create_draft(ag.load(j["id"]), True)
        j = ag.load(j["id"])
        p = self.wp.posts[j["wp"]["id"]]
        self.assertEqual(p["status"], "draft")
        self.assertEqual(len(self.wp.media), 3)
        self.assertEqual(p["payload"]["featured_media"], 700)
        self.assertEqual(p["content"].count("<figure"), 2)               # 02 and 03 inside the text, 01 is the featured image
        self.assertNotIn("<!--IMG", p["content"])
        self.assertEqual(p["content"].count("contentUrl"), 3)
        self.assertEqual(p["payload"]["categories"], [5])
        self.assertEqual([c for c in self.wp.calls if c == ("POST", "wp/v2/posts")], [("POST", "wp/v2/posts")])   # exactly one post call

    def test_draft_may_leave_empty_slots_but_publish_may_not(self):
        j = self.ready()
        self.put(j, [1])
        with self.assertRaises(ValueError):
            ag.create_draft(j, True, status="publish")                   # slots 02/03 have no file
        self.assertEqual(self.wp.posts, {})
        ag.create_draft(j, True)                                         # as a draft it is fine; placeholders stay invisible
        self.assertIn("<!--IMG:2-->", self.wp.posts[500]["content"])

    def test_direct_publish_when_everything_is_ready(self):
        j = self.ready()
        self.put(j, [1, 2, 3])
        with self.assertRaises(ValueError):
            ag.create_draft(j, True, status="publish")                   # undecided internal-link suggestions block a direct publish
        j["links"] = []                                                  # (decided: applied or rejected)
        ag.save(j)
        j = ag.load(j["id"])
        ag.create_draft(j, True, status="publish")
        j = ag.load(j["id"])
        self.assertEqual((j["state"], self.wp.posts[j["wp"]["id"]]["status"]), ("published", "publish"))

    def test_publish_blocked_by_serious_qc(self):
        j = self.ready()
        j["text"] += "\n\nاین زعفران قطعاً سرطان را درمان می‌کند."
        for im in j["brief"]["img"]:
            im["on"] = False
        ag.save(j)
        with self.assertRaises(ValueError) as e:
            ag.create_draft(ag.load(j["id"]), True, status="publish")
        self.assertIn("پزشکی", str(e.exception))
        self.assertEqual(self.wp.posts, {})

    def test_panel_direct_publish_needs_its_own_tick(self):
        from agent import panel
        srv = panel.make_server(0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            j = self.ready()
            for im in j["brief"]["img"]:
                im["on"] = False
            ag.save(j)
            data = urllib.parse.urlencode({"t": panel.TOKEN, "job": j["id"], "do": "draft_make", "status": "publish", "confirm": "1"}).encode()
            with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/writeact", data=data), timeout=30) as r:
                h = r.read().decode("utf-8")
            self.assertIn("مستقیم منتشر شود", h)
            self.assertEqual(self.wp.posts, {})
        finally:
            srv.shutdown()
            srv.server_close()


class Writing(Engine):
    def test_write_in_steps_and_resume(self):
        j = self.briefed()
        ag.write(j, False)
        self.assertEqual(self.tasks, ["brief"])
        ag.write(j, True)
        self.assertEqual(self.tasks.count("write"), 3)                   # intro+2 sections, 2, 2
        j = ag.load(j["id"])
        self.assertEqual(j["state"], "written")
        for h in HEADS:
            self.assertIn(f"## {h}", j["text"])
        self.assertIn("[[IMG:2]]", j["text"])
        self.assertLess(j["text"].index("## " + HEADS[1]), j["text"].index("[[IMG:2]]"))   # image 2 goes after heading 2
        self.assertLess(j["text"].index("[[IMG:2]]"), j["text"].index("## " + HEADS[2]))
        self.assertNotIn("[[IMG:1]]", j["text"])                         # cover is not inside the text
        n = len(self.tasks)
        ag.write(j, True)
        self.assertEqual(len(self.tasks), n)                             # nothing left: no new calls, no cost
        j["parts"].pop(HEADS[3])
        ag.write(j, True, redo=True)
        self.assertEqual(self.tasks.count("write"), 4)                   # only the missing batch is written again

    def test_qc(self):
        j = self.written()
        q = ag.qc(j)
        self.assertFalse([x for s, x in q if s == "H"], q)
        j["text"] += "\n\nاین مصرف قطعاً سرطان را درمان می‌کند و ۱۰ درصد بهتر است. [منبع](https://example.com)"
        q = dict((x[:12], s) for s, x in ag.qc(j))
        txt = " | ".join(x for s, x in ag.qc(j))
        self.assertIn("ادعای پزشکی", txt)
        self.assertIn("عدد/آمار", txt)
        self.assertIn("لینک یا آدرس", txt)
        j["text"] = j["text"].replace("## " + HEADS[2], "## تیتر دیگر")
        self.assertIn("در متن نیست", " ".join(x for s, x in ag.qc(j)))
        self.assertEqual(ag.qc({"text": ""})[0][0], "H")

    def test_fix_text(self):
        t = ag.fix_text("# عنوان\r\nمتن كتاب ي\n## تیتر\n\n\n\nبعد  \n```md\n### ریز\nx")
        self.assertNotIn("# عنوان", t.replace("## عنوان", ""))
        self.assertIn("کتاب ی", t)
        self.assertNotIn("```", t)
        self.assertNotIn("\n\n\n", t)

    def test_links_are_suggested_automatically_and_block_publish_until_decided(self):
        j = self.written()
        self.assertTrue(j["links"])                                       # suggested right after writing, nothing applied yet
        self.assertNotIn("](https://", j["text"])
        self.assertTrue(ag.links_pending(j))
        self.assertTrue(any("لینک داخلی" in x for x in ag.send_problems(j, "publish")))
        self.assertFalse(any("لینک داخلی" in x for x in ag.send_problems(j, "draft")))
        ag.apply_links(j, {0})
        self.assertFalse(ag.links_pending(ag.load(j["id"])))
        self.assertIn("](https://", ag.load(j["id"])["text"])

    def test_link_finder_tolerates_zwnj_and_arabic_letters(self):
        j = ag.new_job("x")
        j["text"] = "مقدمه\n\nما زعفران‌نگین درجه یک داریم و كتاب نمی‌فروشیم.\n\nیک متن دیگر"
        L = ag.suggest_links(j)
        self.assertTrue(any(x["phrase"] == "زعفران‌نگین" for x in L))
        j["links"] = L
        n = ag.apply_links(j, {i for i, x in enumerate(L) if x["phrase"] == "زعفران‌نگین"})
        self.assertEqual(n, 1)
        self.assertIn("[زعفران‌نگین](https://", j["text"])

    def test_internal_links(self):
        j = self.written()
        L = ag.suggest_links(j)
        self.assertTrue(L)
        self.assertTrue(all(x["url"].startswith("https://zafaranmarket.com/") for x in L))
        self.assertEqual(len({x["url"] for x in L}), len(L))             # each target once
        n = ag.apply_links(ag.load(j["id"]), {0})
        self.assertEqual(n, 1)
        t = ag.load(j["id"])["text"]
        self.assertEqual(len(re.findall(r"\]\(https://zafaranmarket\.com/", t)), 1)
        self.assertFalse(re.search(r"(?m)^##.*\]\(", t))                 # never inside a heading
        self.assertEqual(ag.apply_links(ag.load(j["id"]), {0}), 0)       # links list was cleared: no double apply


class Site(Engine):
    def prep(self):
        j = self.written()
        j["brief"]["cat"], j["brief"]["new_cat"] = "", {"name": "هدیه و سوغات", "slug": "gift"}
        ag.save(j)
        return ag.load(j["id"])

    def test_draft_needs_tick_for_new_terms(self):
        j = self.prep()
        ag.create_draft(j, False)
        self.assertEqual([c for c in self.wp.calls if c[0] == "POST"], [])
        with self.assertRaises(StepError):
            ag.create_draft(j, True)                                     # new category not ticked -> refused
        self.assertEqual(self.wp.posts, {})
        ag.create_draft(j, True, ok_terms=set(ag.new_term_keys(j)))
        j = ag.load(j["id"])
        p = self.wp.posts[j["wp"]["id"]]
        self.assertEqual(p["status"], "draft")
        self.assertEqual(p["payload"]["categories"], [101])
        self.assertEqual(p["payload"]["tags"], [9])
        self.assertEqual(p["meta"]["_yoast_wpseo_focuskw"], K)
        self.assertIn("<!--IMG:2-->", p["content"])
        self.assertIn("<h2>", p["content"])
        self.assertEqual(j["state"], "draft")
        self.assertEqual([c for c in self.wp.terms["wp/v2/categories"] if c["name"] == "هدیه و سوغات"][0]["slug"], "gift")
        ag.create_draft(j, True)                                         # second call must not create another post
        self.assertEqual(len(self.wp.posts), 1)

    def test_slug_in_use_blocks(self):
        j = self.prep()
        j["brief"]["slug"] = "a1"
        ag.save(j)
        with self.assertRaises(ValueError):
            ag.create_draft(ag.load(j["id"]), True, ok_terms=set(ag.new_term_keys(j)))

    def make_draft(self):
        j = self.prep()
        ag.create_draft(j, True, ok_terms=set(ag.new_term_keys(j)))
        return ag.load(j["id"])

    def put_images(self, j, nums):
        from PIL import Image
        d = ag.img_dir(j)
        d.mkdir(parents=True, exist_ok=True)
        for n in nums:
            Image.new("RGB", (900, 600), (200, 30, 30)).save(d / (f"{n:02d}.jpg" if n % 2 else f"{n:02d}.png"))

    def test_images_attach_publish(self):
        j = self.make_draft()
        pid = j["wp"]["id"]
        ag.attach_images(j, True)                                        # no files yet: nothing happens
        self.assertEqual(self.wp.media, {})
        self.put_images(j, [1, 2])
        ag.attach_images(j, False)                                       # preview: converts, uploads nothing
        self.assertEqual(self.wp.media, {})
        self.assertTrue((ag.BUILD / j["id"] / "saffron-gift-box.webp").exists())     # SEO file name from the brief, webp
        j = ag.load(j["id"])
        ag.attach_images(j, True)
        j = ag.load(j["id"])
        post = self.wp.posts[pid]
        self.assertEqual(post["featured"], 700)                          # image 01 = featured image
        self.assertIn('<figure class="wp-block-image', post["content"])
        self.assertIn('alt="نخ‌های زعفران"', post["content"])
        self.assertIn('width="900" height="600"', post["content"])
        self.assertNotIn("<!--IMG:2-->", post["content"])
        self.assertIn("<!--IMG:3-->", post["content"])                   # image 3 is still empty
        self.assertEqual(post["content"].count("application/ld+json"), 1)
        self.assertIn('"@type":"ImageObject"', post["content"])
        self.assertEqual(len(list(ag.BACKUP.glob("*.html"))), 1)         # the draft was backed up first
        self.assertTrue(all(h["headers"]["Content-Type"] == "image/webp" and h["alt_text"] for h in self.wp.media.values()))
        self.put_images(j, [3])
        ag.attach_images(ag.load(j["id"]), True)
        self.assertEqual(len(self.wp.media), 3)                          # 01 and 02 were not uploaded twice
        content = self.wp.posts[pid]["content"]
        self.assertEqual(content.count("application/ld+json"), 1)        # schema rebuilt, not duplicated
        self.assertEqual(content.count("<figure"), 2)                    # 02 and 03 in text (01 is the featured image)
        self.assertEqual(content.count('"@type":"ImageObject"'), 3)     # one script holding all three images
        self.assertEqual(content.count("contentUrl"), 3)

    def test_publish_rules(self):
        j = self.make_draft()
        with self.assertRaises(ValueError):
            ag.publish(j, True)                                          # empty image slots block publishing
        j["brief"]["img"][0]["on"] = j["brief"]["img"][1]["on"] = j["brief"]["img"][2]["on"] = False   # "remove the image suggestion"
        ag.save(j)
        j = ag.load(j["id"])
        ag.publish(j, False)
        self.assertEqual(self.wp.posts[j["wp"]["id"]]["status"], "draft")   # preview publishes nothing
        ag.publish(j, True)
        self.assertEqual(self.wp.posts[j["wp"]["id"]]["status"], "publish")
        self.assertEqual(ag.load(j["id"])["state"], "published")
        self.assertNotIn("<!--IMG", ag.to_html(ag.load(j["id"])))        # removed slots leave nothing behind

    def test_schema_stripped_by_wordpress_is_reported(self):
        self.wp.strip = True
        j = self.make_draft()
        self.put_images(j, [1, 2])
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ag.attach_images(ag.load(j["id"]), True)
        self.assertIn("Schema", buf.getvalue())
        self.assertIn("حذف کرد", buf.getvalue())

    def test_auto_makes_drafts_without_new_terms(self):
        a, b = ag.new_job("مقالهٔ یک"), ag.new_job("مقالهٔ دو")
        self.reply = brief_reply(cat="NEW:چیز تازه|x")
        ag.auto([a["id"], b["id"]], False)
        self.assertEqual(self.tasks, [])                                  # estimate only
        self.reply = brief_reply(cat="مقالات", slug="auto-one")
        ag.auto([a["id"]], True)
        self.reply = brief_reply(cat="NEW:چیز تازه|x", slug="auto-two")
        ag.auto([b["id"]], True)
        self.assertEqual(ag.load(a["id"])["state"], "draft")
        self.assertEqual(ag.load(b["id"])["state"], "written")           # no category and none may be created -> no draft; you pick one in the brief
        self.assertEqual([c["name"] for c in self.wp.terms["wp/v2/categories"]], ["مقالات"])
        self.assertEqual({p["status"] for p in self.wp.posts.values()}, {"draft"})


class PanelWrite(Engine):
    def setUp(self):
        super().setUp()
        from agent import panel
        self.srv = panel.make_server(0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=20) as r:
            return r.read().decode("utf-8")

    def post(self, path, **f):
        items = []
        for k, v in {"t": panel.TOKEN, **f}.items():
            items += [(k, x) for x in (v if isinstance(v, list) else [v])]
        with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=urllib.parse.urlencode(items).encode()), timeout=60) as r:
            return r.geturl(), r.read().decode("utf-8")

    def test_flow(self):
        self.assertIn("تولید مقاله", self.get("/write"))
        url, h = self.post("/write", title="راهنمای هدیه زعفران", note="")
        self.assertIn("/write?job=a", url)
        jid = url.split("job=")[1]
        self.assertIn("ساخت بریف با AI", h)
        # AI actions need their tick
        _, h = self.post("/writeact", job=jid, do="brief")
        self.assertIn("تیک مصرف اعتبار AI", h)
        self.assertEqual(self.tasks, [])
        _, h = self.post("/writeact", job=jid, do="brief_est", title="راهنمای هدیه زعفران")
        self.assertEqual(self.tasks, [])
        _, h = self.post("/writeact", job=jid, do="brief", confirm="1", title="راهنمای هدیه زعفران", existing_only="1")
        self.assertIn("فهرست تیترها", h)
        self.assertIn("انتخاب همه", h)                                    # select-all in the tick lists
        self.assertIn("اینفوگرافیک", h)
        self.assertIn("saffron-gift-guide", h)
        _, h = self.post("/writeact", job=jid, do="write", confirm="1")
        self.assertIn("کنترل کیفیت", h)
        self.assertEqual(self.tasks.count("write"), 3)
        _, h = self.post("/writeact", job=jid, do="links_find")
        _, h = self.post("/writeact", job=jid, do="draft_make")           # site tick missing
        self.assertIn("تیک تأیید تغییر روی سایت", h)
        self.assertEqual(self.wp.posts, {})
        _, h = self.post("/writeact", job=jid, do="save_text", text="# سرتیتر\n\nمتن ویرایش‌شده [[IMG:2]]")
        self.assertEqual(ag.load(jid)["text"], "## سرتیتر\n\nمتن ویرایش‌شده [[IMG:2]]")
        # ideas from the keyword/calendar pages become jobs
        url, _ = self.post("/writeq", idea=["ایدهٔ اول||یادداشت", "ایدهٔ دوم||"], source="calendar")
        self.assertTrue(url.endswith("/write"))
        titles = [j["title"] for j in ag.jobs()]
        self.assertIn("ایدهٔ اول", titles)
        self.assertIn("ایدهٔ دوم", titles)
        # delete needs a tick
        ids = [j["id"] for j in ag.jobs() if j["title"] == "ایدهٔ اول"]
        _, h = self.post("/writeact", job=ids[0], do="delete")
        self.assertTrue(ag.load(ids[0]))
        self.post("/writeact", job=ids[0], do="delete", confirm="1")
        self.assertIsNone(ag.load(ids[0]))

    def test_seo_title_is_not_replaced_by_the_csrf_token(self):
        """Real bug: the form field for the SEO title was called «t», the same name as the panel's hidden token, so the saved title became the token."""
        j = self.briefed()
        h = self.get("/write?job=" + j["id"])
        self.assertNotIn("name='t'", h)
        self.assertIn("name='b_t'", h)
        self.post("/writeact", job=j["id"], do="save_brief", title="عنوان", cat="مقالات", b_slug="my-slug", b_k=K, b_t="عنوان سئوی من | زعفران مارکت", b_m=META, b_x="x",
                  h_0="تیتر", hon_0="on")
        b = ag.load(j["id"])["brief"]
        self.assertEqual(b["t"], "عنوان سئوی من | زعفران مارکت")
        self.assertNotEqual(b["t"], panel.TOKEN)
        self.assertEqual((b["slug"], b["k"], b["m"]), ("my-slug", K, META))

    def test_links_skip_and_banner(self):
        j = self.written()
        h = self.get("/write?job=" + j["id"])
        self.assertIn("منتظر تصمیم شماست", h)
        self.assertIn("لینک داخلی هنوز اعمال یا رد نشده", h)
        self.post("/writeact", job=j["id"], do="links_skip")
        self.assertEqual(ag.load(j["id"])["links"], [])
        self.assertNotIn("منتظر تصمیم شماست", self.get("/write?job=" + j["id"]))

    def test_auto_needs_both_ticks(self):
        j = ag.new_job("x")
        _, h = self.post("/writeauto", j=[j["id"]], do="go", confirm="1")
        self.assertIn("هر دو تیک", h)
        self.assertEqual(self.tasks, [])

    def test_job_id_cannot_escape(self):
        self.assertIsNone(ag.load("../../etc/passwd"))
        self.assertIn("پیدا نشد", self.get("/write?job=../../x"))

    def test_style_page_and_select_all(self):
        h = self.get("/style")
        self.assertIn("الف) ساخت/به‌روزرسانی بستهٔ لحن", h)
        self.assertIn("ب) پیش‌نویس راهنمای لحن با AI", h)
        # the AI tick sits in the form of button B only
        a = h.index("الف) ساخت"), h.index("ب) پیش‌نویس")
        self.assertNotIn('name="confirm"', h[a[0]:a[1]])
        self.assertIn('name="confirm"', h[a[1]:])
        self.assertIn("function sa(", self.get("/links"))


if __name__ == "__main__":
    unittest.main()
