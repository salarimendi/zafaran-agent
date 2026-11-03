"""Stage 4.1 tests: H3 headings + stronger prompts, AI revision of an existing article (SEO, outline, rewrite, internal links),
updating the post that is already on the site, the edit link, uniqueness check, and the panel's closed-connection handling.
Needs no internet and touches no real data.   Run:  python -m unittest discover -s tests -v"""
import contextlib
import io
import json
import re
import sys
import threading
import unittest
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_stage13 import art  # noqa: E402
from test_stage14 import Engine, FakeWP, HEADS, K, META, Resp, brief_reply, paragraph  # noqa: E402

from agent import articlegen as ag, llm, panel  # noqa: E402
from agent.common import StepError  # noqa: E402

SUBS = {HEADS[1]: ["نگین و سرگل", "پوشال و دختر پیچ"], HEADS[2]: ["ظرف شیشه‌ای", "جعبهٔ کادویی"]}


class WP2(FakeWP):
    """FakeWP that also understands reading and updating an existing post (title, excerpt, meta, terms, modified time)."""
    def request(self, method, route, auth="wp", **kw):
        m = re.fullmatch(r"wp/v2/posts/(\d+)", route)
        if not m:
            return super().request(method, route, auth, **kw)
        self.calls.append((method, route))
        post = self.posts[int(m.group(1))]
        if method == "POST":
            j = kw["json"]
            post.setdefault("updates", []).append(j)
            for k in ("content", "title", "excerpt"):
                if k in j:
                    post[k] = j[k]
            if "status" in j:
                post["status"] = j["status"]
            post["mod"] = f"2026-11-03T10:0{len(post['updates'])}:00"
        return Resp({"id": post["id"], "status": post["status"], "link": post["link"], "content": {"raw": post["content"]}, "title": {"raw": post.get("title", "")},
                     "excerpt": {"raw": post.get("excerpt", "")}, "modified_gmt": post.get("mod", "2026-11-03T09:00:00"), "meta": post.get("meta", {})})


class Fx(Engine):
    def setUp(self):
        super().setUp()
        self.wp = WP2()
        ag.get_client = lambda: self.wp
        self.base, self.replies, self.users = llm.chat, {}, {}

        def fake(msgs, max_out=None, task="x", use_cache=True):
            u = json.loads(msgs[1]["content"])
            self.tasks.append(task)
            self.users[task] = u
            self.last = u
            if task == "write" and any(s.get("s") for s in u["sections"]):
                text = (paragraph(u["title"]) + "\n\n" + paragraph(u["k"]) + "\n\n") if u["intro"] else ""
                for s in u["sections"]:
                    text += f"## {s['h']}\n\n{paragraph(s['h'])}\n\n" + "".join(f"### {x}\n\n{paragraph(x)}\n\n" for x in s["s"])
                return text, {"cached": False, "tin": 1, "tout": 1, "cost": 0.001, "sec": 0.1}
            if task in self.replies:
                r = self.replies[task]
                r = r(u) if callable(r) else r
                return (r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)), {"cached": False, "tin": 1, "tout": 1, "cost": 0.002, "sec": 0.1}
            self.tasks.pop()
            return self.base(msgs, max_out=max_out, task=task, use_cache=use_cache)
        llm.chat = fake

    def tearDown(self):
        llm.chat = self.base
        super().tearDown()

    def run_out(self, fn):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn()
        return buf.getvalue()

    def with_h3(self):
        self.reply = brief_reply(h=[{"h": h, "n": "یادداشت", "s": SUBS.get(h, [])} for h in HEADS])
        return self.written()

    def sent(self):
        j = self.with_h3()
        j["brief"]["cat"], j["brief"]["new_cat"], j["links"] = "مقالات", None, []
        ag.save(j)
        ag.create_draft(ag.load(j["id"]), True)
        return ag.load(j["id"])


class PromptsAndH3(Fx):
    def test_every_prompt_demands_uniqueness_and_quality(self):
        for name in ("BRIEF_SYS", "WRITE_SYS", "SEO_SYS", "OUTLINE_SYS", "REWRITE_SYS"):
            p = getattr(ag, name)
            for part in ("یونیک", "E-E-A-T", "نیت جست‌وجو", "کلیشه"):
                self.assertIn(part, p, f"{name} lacks «{part}»")
        for part in ("H3", "«### »"):
            self.assertIn(part, ag.WRITE_SYS)
        self.assertIn("H3", ag.BRIEF_SYS)
        self.assertIn("existing", ag.BRIEF_SYS)

    def test_brief_has_h3_and_gets_existing_titles(self):
        j = self.with_h3() if False else self.briefed()
        self.reply = brief_reply(h=[{"h": h, "n": "", "s": SUBS.get(h, []) + ["تکراری", "تکراری"]} for h in HEADS])
        ag.make_brief(j, True)
        b = ag.load(j["id"])["brief"]
        self.assertEqual(b["h"][1]["s"], SUBS[HEADS[1]] + ["تکراری"])      # de-duplicated
        self.assertIsInstance(self.users["brief"]["existing"], list)
        self.assertIn("avoid", b)

    def test_writer_gets_h3_and_text_has_them(self):
        j = self.with_h3()
        self.assertEqual([s["s"] for s in self.users["write"]["sections"]][0:1], [SUBS.get(self.users["write"]["sections"][0]["h"], [])])
        self.assertIn("### نگین و سرگل", j["text"])
        self.assertEqual(len(re.findall(r"(?m)^## ", j["text"])), 6)
        self.assertFalse([x for s, x in ag.qc(j) if "H3" in x])
        self.assertTrue(any(f"<h3>" in ag.to_html(j) for _ in [0]))

    def test_qc_flags_long_text_without_h3_and_planned_h3_missing(self):
        j = self.with_h3()
        j["text"] = re.sub(r"(?m)^### .*$", "", j["text"]) + "\n\n" + " ".join(["واژه"] * 800)
        msgs = " | ".join(x for s, x in ag.qc(j))
        self.assertIn("هیچ تیتر H3", msgs)
        self.assertIn("برنامه‌ریزی‌شده", msgs)

    def test_form_saves_h3(self):
        j = self.with_h3()
        f = {"title": ["x"], "cat": ["مقالات"], "b_slug": ["s-1"], "b_k": [K], "b_t": ["t"], "b_m": [META], "b_x": ["x"],
             "h_0": ["تیتر یک"], "hon_0": ["on"], "hs_0": ["الف\n\n ب \n"], "h_1": ["تیتر دو"], "hon_1": ["on"]}
        ag.parse_brief_form(f, j)
        self.assertEqual(j["brief"]["h"][0]["s"], ["الف", "ب"])
        self.assertEqual(j["brief"]["h"][1]["s"], [])


class WritingIsPaidOnce(Fx):
    def test_only_missing_parts_are_written_and_edits_survive(self):
        j = self.with_h3()
        n = self.tasks.count("write")
        j["text"] = j["text"].replace("## " + HEADS[0], "## " + HEADS[0], 1) + "\n\nجملهٔ دستی من."
        j["parts"].pop(HEADS[3])                                           # one section failed
        ag.save(j)
        ag.write(ag.load(j["id"]), True)
        self.assertEqual(self.tasks.count("write") - n, 1)                 # one call only
        self.assertFalse(self.users["write"]["intro"])                     # the intro is NOT written again
        self.assertIn("جملهٔ دستی من.", ag.load(j["id"])["text"])          # a manual edit is never lost by re-assembling

    def test_plan_batches(self):
        b = {"h": [{"h": f"h{i}", "on": True} for i in range(5)]}
        self.assertEqual([(x["intro"], len(x["secs"])) for x in ag.plan_batches(b)], [(True, 2), (False, 2), (False, 1)])
        self.assertEqual([(x["intro"], [s["h"] for s in x["secs"]]) for x in ag.plan_batches(b, {"intro": "x", "h0": "a", "h1": "a", "h3": "a"})], [(False, ["h2", "h4"])])
        self.assertEqual(ag.plan_batches(b, {"intro": "x", **{f"h{i}": "a" for i in range(5)}}), [])


class ReviseSeo(Fx):
    def test_only_chosen_fields_change_slug_never(self):
        j = self.with_h3()
        self.replies["seo_revise"] = {"k": "هدیه کادویی زعفران", "t": "هدیه کادویی زعفران | راهنمای خرید", "m": "m" * 120, "x": "خلاصهٔ تازه", "title": "عنوان تازهٔ مقاله برای هدیه"}
        old = dict(j["brief"])
        out = self.run_out(lambda: ag.revise_seo(j, False, ("k", "t")))
        self.assertEqual(self.tasks.count("seo_revise"), 0)               # estimate only
        self.assertIn("Estimate only", out)
        j = ag.load(j["id"])
        self.run_out(lambda: ag.revise_seo(j, True, ("k", "t"), "جنبهٔ خرید"))
        b = ag.load(j["id"])["brief"]
        self.assertEqual((b["k"], b["t"]), ("هدیه کادویی زعفران", "هدیه کادویی زعفران | راهنمای خرید"))
        self.assertEqual((b["m"], b["x"], b["slug"]), (old["m"], old["x"], old["slug"]))      # not asked -> untouched; slug never
        self.assertEqual(ag.load(j["id"])["title"], j["title"])
        self.assertEqual(self.users["seo_revise"]["want"], ["k", "t"])
        self.assertEqual(self.users["seo_revise"]["note"], "جنبهٔ خرید")
        self.assertIn("outline", self.users["seo_revise"])
        self.assertNotIn("slug", ag.SEO_FIELDS)
        # undo
        self.assertEqual(ag.undo_last(ag.load(j["id"]))["why"].split()[0], "سئو")
        self.assertEqual(ag.load(j["id"])["brief"]["k"], old["k"])

    def test_title_and_nothing_chosen(self):
        j = self.with_h3()
        self.replies["seo_revise"] = {"title": "عنوان تازهٔ مقاله برای هدیه", "k": "x y"}
        self.run_out(lambda: ag.revise_seo(j, True, ("title",)))
        j = ag.load(j["id"])
        self.assertEqual((j["title"], j["brief"]["k"]), ("عنوان تازهٔ مقاله برای هدیه", K))
        with self.assertRaises(ValueError):
            ag.revise_seo(j, True, ())

    def test_marks_dirty_only_when_on_site(self):
        j = self.with_h3()
        self.replies["seo_revise"] = {"t": "عنوان سئوی دیگر"}
        self.run_out(lambda: ag.revise_seo(j, True, ("t",)))
        self.assertFalse(ag.load(j["id"]).get("dirty"))
        s = self.sent()
        self.assertFalse(s["dirty"])
        self.run_out(lambda: ag.revise_seo(s, True, ("t",)))
        self.assertTrue(ag.load(s["id"])["dirty"])


class ReviseOutline(Fx):
    def test_new_outline_keeps_unchanged_sections_and_writes_only_the_rest(self):
        j = self.with_h3()
        j["text"] = j["text"] + "\n\nجملهٔ دستی من."                        # lands in the last section (HEADS[5])
        ag.save(j)
        before_intro = ag.load(j["id"])["parts"]["intro"]
        new = [{"h": HEADS[0], "n": "", "s": []}, {"h": HEADS[1], "n": "", "s": ["نگین و سرگل", "پوشال", "چه چیزی بخریم؟"]},
               {"h": "سوالات متداول دربارهٔ هدیه زعفران", "n": "FAQ", "s": ["آیا زعفران هدیهٔ مناسبی است؟", "چگونه نگهداری شود؟"]}, {"h": HEADS[5], "n": "", "s": []}]
        self.replies["outline"] = {"h": new + [{"h": "تیتر پنجم", "n": "", "s": []}], "why": "بهتر شد"}
        out = self.run_out(lambda: ag.revise_outline(ag.load(j["id"]), True, "FAQ اضافه کن"))
        j2 = ag.load(j["id"])
        self.assertEqual([h["h"] for h in j2["brief"]["h"]], [h["h"] for h in new] + ["تیتر پنجم"])
        self.assertEqual(j2["brief"]["h"][2]["s"], ["آیا زعفران هدیهٔ مناسبی است؟", "چگونه نگهداری شود؟"])
        self.assertIn(HEADS[0], j2["parts"])                               # unchanged: text kept
        self.assertNotIn(HEADS[1], j2["parts"])                            # H3 changed: must be rewritten
        self.assertIn("جدید یا تغییرکرده", out)
        self.assertIn(HEADS[2], out)                                       # reported as removed
        self.assertIn(HEADS[2], j2["text"])                                # the text itself is untouched until you write
        n = self.tasks.count("write")
        ag.write(j2, True)
        j3 = ag.load(j["id"])
        self.assertEqual(self.tasks.count("write") - n, 2)                 # 3 missing sections (HEADS[1], FAQ, تیتر پنجم) -> 2 calls
        self.assertEqual(j3["parts"]["intro"], before_intro)               # intro not paid again
        heads = re.findall(r"(?m)^## (.+)$", j3["text"])
        self.assertEqual(heads, [h["h"] for h in new] + ["تیتر پنجم"])
        self.assertIn("جملهٔ دستی من.", j3["text"])                        # manual edit in an unchanged section survived
        self.assertIn("### چه چیزی بخریم؟", j3["text"])
        self.assertNotIn(HEADS[2], j3["text"])
        self.assertTrue(all(i["w"] == "cover" or i["w"] <= 5 for i in j3["brief"]["img"]))
        # undo goes back step by step: first the writing, then the outline change -> the old outline and text
        ag.undo_last(j3)
        ag.undo_last(ag.load(j["id"]))
        self.assertEqual([h["h"] for h in ag.load(j["id"])["brief"]["h"]], HEADS)
        self.assertIn(HEADS[2], ag.load(j["id"])["text"])

    def test_bad_reply_changes_nothing(self):
        j = self.with_h3()
        self.replies["outline"] = {"h": [{"h": "فقط یکی"}]}
        with self.assertRaises(ValueError):
            self.run_out(lambda: ag.revise_outline(j, True, ""))
            ag.revise_outline(j, True, "")
        self.assertEqual([h["h"] for h in ag.load(j["id"])["brief"]["h"]], HEADS)


class Rewrite(Fx):
    def fake_rewrite(self, u):
        t = ""
        if u["intro"]:
            t += "مقدمهٔ تازه " + " ".join(["واژه"] * 30) + "\n\n"
        for s in u["sections"]:
            t += f"## عنوان دیگر\n\nبازنویسی‌شدهٔ {s['h']} با [لینک بد](https://evil.example.com/x) و https://evil.example.com " + " ".join(["واژه"] * 60) + "\n\n"
        return t

    def test_pick_changes_only_chosen_section_and_keeps_links_and_images(self):
        j = self.with_h3()
        j["links"] = [L for L in ag.suggest_links(j, 3)]
        self.assertTrue(j["links"])
        ag.apply_links(j, {0})
        j = ag.load(j["id"])
        link_urls = ag.md_links(j["text"])
        _, old = ag.split_text(j["text"])
        self.replies["rewrite"] = self.fake_rewrite
        out = self.run_out(lambda: ag.rewrite(j, False, "یونیک‌تر کن", "pick", [HEADS[1]]))
        self.assertEqual(self.tasks.count("rewrite"), 0)
        self.run_out(lambda: ag.rewrite(ag.load(j["id"]), True, "یونیک‌تر کن", "pick", [HEADS[1]]))
        j2 = ag.load(j["id"])
        _, new = ag.split_text(j2["text"])
        self.assertEqual([h for h, _ in new], [h for h, _ in old])          # headings are never renamed by a rewrite
        for (h, ob), (_, nb) in zip(old, new):
            if h == HEADS[1]:
                self.assertIn("بازنویسی‌شدهٔ", nb)
                self.assertIn("[[IMG:2]]", nb)                              # the image slot after this section is kept
                self.assertNotIn("evil.example.com", nb)                    # AI links are stripped, anchor text stays
                self.assertIn("لینک بد", nb)
            else:
                self.assertEqual(ob, nb)                                    # other sections byte-identical
        self.assertEqual(self.users["rewrite"]["instruction"], "یونیک‌تر کن")
        self.assertEqual([s["h"] for s in self.users["rewrite"]["sections"]], [HEADS[1]])
        self.assertEqual(self.users["rewrite"]["sections"][0]["s"], SUBS[HEADS[1]])
        self.assertFalse(self.users["rewrite"]["intro"])
        self.assertTrue(link_urls <= ag.md_links(j2["text"]) or "لینک‌های داخلی" in "")   # untouched sections keep their links
        self.assertEqual(ag.undo_last(j2)["why"][:5], "بازنو")
        self.assertEqual(ag.load(j["id"])["text"], j["text"])

    def test_scopes_and_cost_gate(self):
        j = self.with_h3()
        self.replies["rewrite"] = self.fake_rewrite
        self.run_out(lambda: ag.rewrite(j, True, "x", "all"))
        self.assertEqual(self.tasks.count("rewrite"), 3)                    # intro + 2 sections, then 2 + 2
        self.assertTrue(self.users["rewrite"]["sections"])
        n = self.tasks.count("rewrite")
        self.run_out(lambda: ag.rewrite(ag.load(j["id"]), True, "x", "intro"))
        self.assertEqual(self.tasks.count("rewrite") - n, 1)
        self.assertTrue(self.users["rewrite"]["intro"])
        self.assertEqual(self.users["rewrite"]["sections"], [])
        with self.assertRaises(ValueError):
            ag.rewrite(ag.load(j["id"]), True, "   ", "all")
        with self.assertRaises(ValueError):
            ag.rewrite(ag.load(j["id"]), True, "x", "pick", [])
        import builtins
        builtins.input = lambda *a: "n"                                     # not confirmed -> no call
        n = self.tasks.count("rewrite")
        self.run_out(lambda: ag.rewrite(ag.load(j["id"]), True, "x", "all"))
        self.assertEqual(self.tasks.count("rewrite"), n)


class AiLinks(Fx):
    def test_ai_choices_are_verified_by_code(self):
        j = self.with_h3()
        j["links"] = []
        ag.save(j)

        def reply(u):
            keys = list(u["pars"])
            p1, p2, p3 = keys[0], keys[1], keys[2]
            anchor = " ".join(u["pars"][p1].split()[2:5])
            return [{"p": p1, "a": anchor, "t": "1", "why": "مکمل"},
                    {"p": p2, "a": "عبارتی که در متن نیست", "t": "2"},       # anchor not in the paragraph -> dropped
                    {"p": p3, "a": " ".join(u["pars"][p3].split()[1:4]), "t": "1"},   # same target twice -> dropped
                    {"p": p3, "a": " ".join(u["pars"][p3].split()[1:4]), "t": "9999"}, "junk"]   # target does not exist -> dropped
        self.replies["links_ai"] = reply
        self.run_out(lambda: ag.ai_links(j, False))
        self.assertEqual(self.tasks.count("links_ai"), 0)
        out = self.run_out(lambda: ag.ai_links(ag.load(j["id"]), True, 6))
        j2 = ag.load(j["id"])
        self.assertEqual(len(j2["links"]), 1, out)
        L = j2["links"][0]
        self.assertEqual((L["src"], L["why"]), ("ai", "مکمل"))
        tg = self.users["links_ai"]["targets"]
        self.assertGreater(len(tg), 3)
        self.assertEqual(L["title"], tg["1"][0])
        self.assertTrue(L["url"].startswith("https://zafaranmarket.com/") or L["url"].startswith("http"))
        self.assertTrue(all("[[IMG" not in v and not v.startswith("#") for v in self.users["links_ai"]["pars"].values()))   # only body paragraphs are offered
        self.assertEqual(ag.apply_links(j2, {0}), 1)
        self.assertIn(f"]({L['url']})", ag.load(j["id"])["text"])
        # a target already linked is not offered again
        self.run_out(lambda: ag.ai_links(ag.load(j["id"]), True))
        self.assertIn(L["url"], self.users["links_ai"]["already"])
        self.assertNotIn(L["title"], [v[0] for v in self.users["links_ai"]["targets"].values()])

    def test_titles_sent_are_ranked_and_never_the_article_itself(self):
        j = self.with_h3()
        j["wp"] = {"id": 1}
        msgs, docs, *_ = ag._link_input(j, 6)
        self.assertTrue(docs)
        self.assertNotIn(1, [d["id"] for d in docs if d["kind"] == "article"])


class UpdateSite(Fx):
    def edited(self, j):
        self.replies["rewrite"] = lambda u: "".join(f"## x\n\nنسخهٔ ویرایش‌شدهٔ {s['h']} " + " ".join(["واژه"] * 60) + "\n\n" for s in u["sections"])
        self.run_out(lambda: ag.rewrite(ag.load(j["id"]), True, "بهتر کن", "pick", [HEADS[0]]))
        return ag.load(j["id"])

    def test_preview_sends_nothing_and_real_update_needs_ticks(self):
        j = self.edited(self.sent())
        pid = j["wp"]["id"]
        self.assertTrue(j["dirty"])
        n_post = len([c for c in self.wp.calls if c[0] == "POST"])
        out = self.run_out(lambda: ag.update_post(j, False))
        self.assertIn("آدرس (slug): بدون تغییر", out)
        self.assertIn("Preview only", out)
        self.assertIn("نمی‌دانم", out)                                      # created without a recorded edit time -> unknown
        self.assertEqual(len([c for c in self.wp.calls if c[0] == "POST"]), n_post)
        with self.assertRaises(StepError):
            self.run_out(lambda: ag.update_post(j, True))                  # unknown remote state -> needs the overwrite tick
            ag.update_post(j, True)
        out = self.run_out(lambda: ag.update_post(j, True, overwrite=True))
        p = self.wp.posts[pid]
        up = p["updates"][-1]
        self.assertNotIn("slug", up)                                       # the address is never sent
        self.assertNotIn("status", up)
        self.assertEqual(up["title"], j["title"])
        self.assertEqual(up["meta"]["_yoast_wpseo_focuskw"], j["brief"]["k"])
        self.assertEqual(up["categories"], [5])
        self.assertIn("نسخهٔ ویرایش‌شدهٔ", p["content"])
        self.assertTrue(list(ag.BACKUP.glob(f"{j['id']}_before_update_*.html")))
        j = ag.load(j["id"])
        self.assertFalse(j["dirty"])
        self.assertEqual(j["wp"]["modified"], "2026-11-03T10:01:00")
        last = [ln for ln in out.strip().splitlines() if ln.strip()][-1]
        self.assertEqual(last, f"https://zafaranmarket.com/wp-admin/post.php?post={pid}&action=edit")   # the edit link is the last thing printed
        # known state now: a second update needs no overwrite tick, until somebody edits the post in WordPress
        self.run_out(lambda: ag.update_post(j, True))
        p["mod"] = "2026-12-24T08:00:00"
        with self.assertRaises(StepError):
            ag.update_post(ag.load(j["id"]), True)
        out = self.run_out(lambda: ag.update_post(ag.load(j["id"]), False))
        self.assertIn("تغییر کرده", out)

    def test_published_post_needs_its_own_tick_and_the_publish_bar(self):
        j = self.edited(self.sent())
        pid = j["wp"]["id"]
        self.wp.posts[pid]["status"] = "publish"
        j["links"] = []                                                    # (the rewrite left fresh link suggestions; decided here)
        ag.save(j)
        with self.assertRaises(StepError):
            ag.update_post(ag.load(j["id"]), True, overwrite=True)
        j = ag.load(j["id"])
        j["links"] = [{"par": 0, "phrase": "x", "url": "https://zafaranmarket.com/a/", "title": "t", "kind": "article"}]
        with self.assertRaises(ValueError) as e:
            ag.update_post(j, True, overwrite=True, live_ok=True)
        self.assertIn("لینک داخلی", str(e.exception))
        j["links"] = []
        self.run_out(lambda: ag.update_post(j, True, overwrite=True, live_ok=True))
        self.assertEqual(self.wp.posts[pid]["status"], "publish")

    def test_new_category_needs_a_tick_here_too(self):
        j = self.edited(self.sent())
        j["brief"]["cat"], j["brief"]["new_cat"] = "", {"name": "دستهٔ تازه", "slug": "fresh"}
        ag.save(j)
        with self.assertRaises(StepError):
            ag.update_post(ag.load(j["id"]), True, overwrite=True)
        self.run_out(lambda: ag.update_post(ag.load(j["id"]), True, {"category|دستهٔ تازه"}, overwrite=True))
        self.assertEqual(self.wp.posts[j["wp"]["id"]]["updates"][-1]["categories"], [101])

    def test_requires_a_post_on_the_site(self):
        j = self.with_h3()
        with self.assertRaises(ValueError):
            ag.update_post(j, False)
        with self.assertRaises(ValueError):
            ag.pull_from_site(j, False)

    def test_pull_from_site(self):
        j = self.sent()
        old_text = j["text"]
        self.wp.posts[j["wp"]["id"]].update(content="<h2>تیتر دستی</h2><p>متن دستی در وردپرس.</p><h3>ریزتیتر دستی</h3><p>بدنه.</p>", title="عنوان دستی", mod="2026-12-01T00:00:00")
        out = self.run_out(lambda: ag.pull_from_site(ag.load(j["id"]), False))
        self.assertIn("Preview only", out)
        self.assertEqual(ag.load(j["id"])["text"], old_text)
        self.run_out(lambda: ag.pull_from_site(ag.load(j["id"]), True))
        j2 = ag.load(j["id"])
        self.assertIn("## تیتر دستی", j2["text"])
        self.assertIn("### ریزتیتر دستی", j2["text"])
        self.assertEqual(j2["title"], "عنوان دستی")
        self.assertEqual([(h["h"], h["s"]) for h in j2["brief"]["h"]], [("تیتر دستی", ["ریزتیتر دستی"])])
        self.assertEqual(j2["wp"]["modified"], "2026-12-01T00:00:00")
        self.assertFalse(j2["dirty"])
        ag.undo_last(j2)
        self.assertEqual(ag.load(j["id"])["text"], old_text)


class EditLink(Fx):
    def test_printed_last_after_draft_and_publish(self):
        j = self.with_h3()
        j["brief"]["cat"], j["brief"]["new_cat"], j["links"] = "مقالات", None, []
        for im in j["brief"]["img"]:
            im["on"] = False
        ag.save(j)
        out = self.run_out(lambda: ag.create_draft(ag.load(j["id"]), True))
        j = ag.load(j["id"])
        url = f"https://zafaranmarket.com/wp-admin/post.php?post={j['wp']['id']}&action=edit"
        self.assertEqual(ag.wp_edit_url(j), url)
        self.assertEqual(out.strip().splitlines()[-1], url)
        out = self.run_out(lambda: ag.publish(j, True))
        self.assertEqual(out.strip().splitlines()[-1], url)
        out = self.run_out(lambda: ag.create_draft(ag.load(j["id"]), True))      # already on the site: still shows where it is
        self.assertEqual(out.strip().splitlines()[-1], url)
        self.assertEqual(ag.wp_edit_url({"wp": None}), "")


class Uniqueness(Fx):
    def test_similar_to_an_existing_article_is_flagged(self):
        import agent.corpus as corpus
        j = self.with_h3()
        self.assertFalse([x for s, x in ag.qc(j) if "شباهت" in x])
        (corpus.ART / "9.md").write_text(art(9, "مقالهٔ مشابه", "هدیه", j["text"]), encoding="utf-8")
        q = ag.qc(j)
        hit = [(s, x) for s, x in q if "شباهت" in x]
        self.assertEqual(hit[0][0], "H")                                    # a copy blocks publishing
        self.assertIn("مقالهٔ مشابه", hit[0][1])
        self.assertTrue(any("شباهت" in x for x in ag.send_problems(j, "publish")))
        j["wp"] = {"id": 9}                                                 # the same article after it was sent: not compared with itself
        self.assertFalse([x for s, x in ag.qc(j) if "شباهت" in x])

    def test_internal_links_are_not_flagged_but_external_are(self):
        j = self.with_h3()
        j["links"] = []
        j["text"] += "\n\n[داخلی](https://zafaranmarket.com/a1/) و [نسبی](/blog/x/)"
        self.assertFalse([x for s, x in ag.qc(j) if "بیرونی" in x])
        j["text"] += " [بیرونی](https://example.org/p)"
        self.assertTrue([x for s, x in ag.qc(j) if "بیرونی" in x])


class PanelRevise(Fx):
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
            return r.read().decode("utf-8")

    def post(self, path, **f):
        items = []
        for k, v in {"t": panel.TOKEN, **f}.items():
            items += [(k, x) for x in (v if isinstance(v, list) else [v])]
        with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=urllib.parse.urlencode(items).encode()), timeout=60) as r:
            return r.geturl(), r.read().decode("utf-8")

    def test_revise_section_and_ticks(self):
        j = self.with_h3()
        h = self.get("/write?job=" + j["id"])
        for part in ("۷) ویرایش و بهبود با AI", "پرسیدن دوبارهٔ سئو", "طراحی مجدد تیترها", "بازنویسی با دستور دلخواه", "پیشنهاد لینک داخلی با AI", "name='hs_0'"):
            self.assertIn(part, h)
        self.assertNotIn("ت) به‌روزرسانی مقاله در سایت", h)                # not on the site yet: no update box
        self.assertNotIn("post.php", h)
        n = self.tasks.count("seo_revise")
        _, h = self.post("/writeact", job=j["id"], do="seo_ai", w=["k", "t"])
        self.assertIn("تیک مصرف اعتبار AI", h)                               # no tick -> no call
        self.replies["seo_revise"] = {"k": "کلید تازه زعفران", "t": "عنوان تازه"}
        _, h = self.post("/writeact", job=j["id"], do="seo_est", w=["k"])
        self.assertEqual(self.tasks.count("seo_revise"), n)
        _, h = self.post("/writeact", job=j["id"], do="seo_ai", w=["k", "t"], confirm="1", note="n")
        self.assertEqual(ag.load(j["id"])["brief"]["k"], "کلید تازه زعفران")
        self.assertIn("برگرداندن آخرین تغییر AI", h)
        self.post("/writeact", job=j["id"], do="undo")
        self.assertEqual(ag.load(j["id"])["brief"]["k"], K)
        self.replies["rewrite"] = lambda u: "".join(f"## x\n\nبازنویسی {s['h']} " + " ".join(["واژه"] * 50) + "\n\n" for s in u["sections"])
        _, h = self.post("/writeact", job=j["id"], do="rewrite_ai", confirm="1", instruction="بهتر کن", scope="pick", sec=[HEADS[2]])
        self.assertIn("بازنویسی انجام شد", h)
        self.assertEqual(self.users["rewrite"]["instruction"], "بهتر کن")
        _, h = self.post("/writeact", job=j["id"], do="rewrite_ai", confirm="1", instruction="", scope="all")
        self.assertIn("دستور ویرایش خالی", h)

    def test_site_box_edit_link_and_update_flow(self):
        j = self.sent()
        pid = j["wp"]["id"]
        h = self.get("/write?job=" + j["id"])
        edit = f"https://zafaranmarket.com/wp-admin/post.php?post={pid}&action=edit"
        self.assertIn(edit, h)                                              # visible at the top, always
        self.assertIn("ت) به‌روزرسانی مقاله در سایت", h)
        self.assertNotIn("هنوز در سایت اعمال نشده", h)
        _, h = self.post("/writeact", job=j["id"], do="save_text", text="## تیتر\n\nمتن تازه " + " ".join(["واژه"] * 5))
        self.assertIn("هنوز در سایت اعمال نشده", h)
        _, h = self.post("/writeact", job=j["id"], do="update_go", overwrite="1")
        self.assertIn("تیک تأیید تغییر روی سایت", h)                         # needs the site tick
        self.assertNotIn("updates", self.wp.posts[pid])
        _, h = self.post("/writeact", job=j["id"], do="update_prev")
        self.assertIn("Preview only", h)
        self.assertNotIn("updates", self.wp.posts[pid])

    def test_pull_needs_its_tick(self):
        j = self.sent()
        _, h = self.post("/writeact", job=j["id"], do="pull_go")
        self.assertIn("اجرا نشد", h)
        self.wp.posts[j["wp"]["id"]]["content"] = "<h2>تیتر</h2><p>متن</p>"
        self.post("/writeact", job=j["id"], do="pull_go", okpull="1")
        self.assertIn("## تیتر", ag.load(j["id"])["text"])

    def test_save_brief_keeps_h3(self):
        j = self.with_h3()
        self.post("/writeact", job=j["id"], do="save_brief", title="عنوان", cat="مقالات", b_slug="my-slug", b_k=K, b_t="عنوان سئو", b_m=META, b_x="x",
                  h_0="تیتر", hon_0="on", hs_0="الف\nب")
        self.assertEqual(ag.load(j["id"])["brief"]["h"][0]["s"], ["الف", "ب"])


class ClosedConnection(unittest.TestCase):
    """Real failure at the end of START.bat: the browser closed the page while the panel was answering -> ConnectionAbortedError (WinError 10053) traceback."""
    def test_send_swallows_a_closed_connection(self):
        class W:
            def write(self, b):
                raise ConnectionAbortedError(10053, "aborted")

            def flush(self):
                pass
        h = object.__new__(panel.Handler)
        h.wfile, h.request_version, h.requestline, h.command, h.close_connection = W(), "HTTP/1.1", "POST /writeact HTTP/1.1", "POST", False
        h.client_address = ("127.0.0.1", 1)
        h._send(200, "<html>x</html>")                                       # must not raise
        self.assertTrue(h.close_connection)
        h._redirect("/write")                                                # same for redirects

    def test_server_prints_no_traceback_for_it_but_still_reports_real_errors(self):
        srv = panel.make_server(0)
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                try:
                    raise ConnectionAbortedError(10053, "aborted")
                except ConnectionAbortedError:
                    srv.handle_error(None, ("127.0.0.1", 1))
            self.assertEqual(err.getvalue(), "")
            with contextlib.redirect_stderr(err):
                try:
                    raise ValueError("real bug")
                except ValueError:
                    srv.handle_error(None, ("127.0.0.1", 1))
            self.assertIn("real bug", err.getvalue())
        finally:
            srv.server_close()

    def test_panel_survives_a_client_that_hangs_up(self):
        import socket
        srv = panel.make_server(0)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            for _ in range(3):
                s = socket.create_connection(("127.0.0.1", port), timeout=5)
                body = f"t={panel.TOKEN}&do=x&job=nope"
                s.sendall((f"POST /writeact HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Length: {len(body)}\r\n\r\n{body}").encode())
                s.close()                                                    # hang up before reading the answer
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/ping", timeout=5) as r:
                self.assertIn("zafaran-panel", r.read().decode())
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()


class Fixes18(Fx):
    def test_schema_text_left_by_wordpress_is_cleaned_and_default_is_off(self):
        from agent import keywords as kw  # noqa
        self.assertFalse(ag.norm_brief({}, ag.site_context())["schema"])
        j = self.with_h3()
        j["brief"]["cat"], j["brief"]["new_cat"], j["links"], j["brief"]["schema"] = "مقالات", None, [], True
        from PIL import Image
        d = ag.img_dir(j)
        d.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (900, 600), (10, 90, 160)).save(d / "01.jpg")
        ag.save(j)
        orig = self.wp.request

        def strip_tag_keep_text(method, route, auth="wp", **kw):      # what the real site did: kses removed the tag, the JSON stayed as text
            if method == "POST" and "json" in kw and "content" in kw["json"]:
                kw["json"]["content"] = kw["json"]["content"].replace('<script type="application/ld+json">', "").replace("</script>", "")
            return orig(method, route, auth, **kw)
        self.wp.request = strip_tag_keep_text
        out = self.run_out(lambda: ag.create_draft(ag.load(j["id"]), True))
        self.assertIn("خودکار پاک شد", out)
        self.assertNotIn("@context", self.wp.posts[500]["content"])
        self.assertNotIn("schema.org", self.wp.posts[500]["content"])

    def test_tables_and_faq_get_inline_css(self):
        md = ("مقدمه\n\n| نوع پک | مناسب برای |\n|---|---|\n| لوکس | هدیه رسمی |\n| اقتصادی | روزمره |\n| ست | کامل |\n\n"
              "## سوالات متداول دربارهٔ هدیه\n\n### آیا مناسب است؟\n\nبله.\n\n### چگونه نگهداری شود؟\n\nدر ظرف دربسته.\n\n## جمع‌بندی\n\nپایان")
        h = ag.style_html(ag.md(md))
        self.assertIn('class="zaf-table"', h)
        self.assertIn("overflow-x:auto", h)
        self.assertEqual(h.count("<th style="), 2)
        self.assertEqual(h.count('background:#fdfaf5"'), 1 + 2)            # one zebra row in the table + the two FAQ cards
        self.assertEqual(h.count('class="zaf-faq"'), 2)
        self.assertIn("<h3 style=", h)
        self.assertNotIn("<style", h)
        self.assertNotIn("<script", h)
        self.assertIn("<h2>جمع‌بندی</h2>", h)                              # the section after the FAQ is untouched
        self.assertNotIn("zaf-faq", h.split("<h2>جمع‌بندی</h2>")[1])

    def test_out_of_stock_products_are_never_linked(self):
        import json as _j
        from agent import keywords as kw
        snap = kw.load_snapshot()
        snap["products"] = [
            {"id": 1, "status": "publish", "name": "زعفران نگین موجود", "url": "https://zafaranmarket.com/p/in/", "categories": [], "stock_status": "instock", "manage_stock": True, "stock_quantity": 5},
            {"id": 2, "status": "publish", "name": "زعفران نگین تمام‌شده", "url": "https://zafaranmarket.com/p/out/", "categories": [], "stock_status": "outofstock", "manage_stock": False, "stock_quantity": None},
            {"id": 3, "status": "publish", "name": "زعفران نگین صفر", "url": "https://zafaranmarket.com/p/zero/", "categories": [], "stock_status": "instock", "manage_stock": True, "stock_quantity": 0},
            {"id": 4, "status": "publish", "name": "زعفران نگین قدیمی", "url": "https://zafaranmarket.com/p/old/", "categories": []}]
        (Path(self.tmp.name) / "site" / "snapshot.json").write_text(_j.dumps(snap, ensure_ascii=False), encoding="utf-8")
        self.assertEqual([kw.in_stock(p) for p in snap["products"]], [True, False, False, False])
        j = self.with_h3()
        titles = [d["title"] for d in ag.ranked_docs(j, ("product",), 50)]
        self.assertEqual(titles, ["زعفران نگین موجود"])
        msgs, docs, *_ = ag._link_input(j, 6)
        self.assertNotIn("https://zafaranmarket.com/p/out/", [d["url"] for d in docs])
        self.assertIn("https://zafaranmarket.com/p/in/", [d["url"] for d in docs])
        j["links"] = []
        j["text"] += "\n\n[زعفران نگین تمام‌شده](https://zafaranmarket.com/p/out/)"
        q = ag.qc(j)
        self.assertTrue([x for s, x in q if s == "H" and "ناموجود" in x])
        self.assertTrue(any("ناموجود" in x for x in ag.send_problems(j, "publish")))
