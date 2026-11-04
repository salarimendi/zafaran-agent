"""Stage 6 tests: media-library audit, Alt from article context, product-category texts (audit + one AI call + apply), Schema validation, panel page.
No internet; a fake WordPress client and a fake model. Run:  python -m unittest discover -s tests -v"""
import json
import sys
import threading
import unittest
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_stage13 import art  # noqa: E402
from test_stage14 import Engine, Resp  # noqa: E402

from agent import llm, media_seo as ms, panel  # noqa: E402
from agent.common import StepError  # noqa: E402

U = "https://zafaranmarket.com/wp-content/uploads/2026/11/"
KB = 1024


def im(i, name, alt="", mime="image/webp", size=50 * KB, w=1200):
    return {"id": i, "source_url": U + name, "alt_text": alt, "mime_type": mime, "media_details": {"filesize": size, "width": w, "height": 800}}


MEDIA = [im(1, "IMG_1234.jpg", "", "image/jpeg", 700 * KB, 3000), im(2, "saffron-threads.webp", "saffron threads"), im(3, "a1.webp", "زعفران"), im(4, "a2.webp", "زعفران"),
         im(5, "a3.webp", "زعفران"), im(6, "luxury-gift-box-300x200.webp", ""), im(7, "ok-image.webp", "رشته‌های زعفران نگین در ظرف شیشه‌ای")]
CATS = [{"id": 10, "name": "زعفران نگین", "slug": "negin", "parent": 0, "count": 5, "description": "", "image": None, "link": "https://zafaranmarket.com/c/negin/"},
        {"id": 11, "name": "ست هدیه", "slug": "gift", "parent": 0, "count": 3, "description": "<p>" + "ست هدیه برای مناسبت‌ها " * 15 + "</p>", "image": {"id": 1}, "link": "https://zafaranmarket.com/c/gift/"},
        {"id": 12, "name": "سرگل", "slug": "sargol", "parent": 0, "count": 2, "description": "<p>" + "توضیح تکراری دسته " * 20 + "</p>", "image": {"id": 1}, "link": ""},
        {"id": 13, "name": "پوشال", "slug": "push", "parent": 0, "count": 0, "description": "<p>" + "توضیح تکراری دسته " * 20 + "</p>", "image": {"id": 1}, "link": ""},
        {"id": 1, "name": "Uncategorized", "slug": "uncategorized", "parent": 0, "count": 0, "description": "", "image": None, "link": ""}]


class MC:
    def __init__(self):
        self.calls = []

    def get(self, route, auth="wp", params=None):
        self.calls.append(("GET", route))
        return Resp({"wp/v2/media": MEDIA, "wc/v3/products/categories": CATS}[route])

    def request(self, method, route, auth="wp", **kw):
        self.calls.append((method, route, kw.get("json")))
        return Resp({"id": 1})


class Fx6(Engine):
    def setUp(self):
        super().setUp()
        t = Path(self.tmp.name)
        self.old6 = (ms.MEDIA, ms.PROP, ms.BACKUP, ms.REP, ms.client)
        ms.MEDIA, ms.PROP, ms.BACKUP, ms.REP = t / "media", t / "prop", t / "bak", t / "rep"
        self.c = MC()
        ms.client = lambda: self.c
        (t / "arts" / "50-x.md").write_text(art(50, "هدیه زعفران", "هدیه زعفران", f"## بسته‌بندی هدیه\n\n![]({U}luxury-gift-box.webp)\n\nمتن.\n\n## رشته‌ها\n\n![رشته‌های زعفران نگین مرغوب]({U}saffron-threads.webp)\n"), encoding="utf-8")
        snap = json.loads((t / "site" / "snapshot.json").read_text(encoding="utf-8"))
        snap["site"] = "https://zafaranmarket.com"
        snap["products"] = [{"id": 1, "status": "publish", "name": "زعفران نگین یک مثقال", "url": "https://zafaranmarket.com/p/1/", "categories": ["زعفران نگین"], "stock_status": "instock", "manage_stock": False, "stock_quantity": None},
                            {"id": 2, "status": "publish", "name": "نگین ناموجود", "url": "https://zafaranmarket.com/p/2/", "categories": ["زعفران نگین"], "stock_status": "outofstock"}]
        (t / "site" / "snapshot.json").write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        self.base, self.users, self.reply = llm.chat, {}, {}

        def fake(msgs, max_out=None, task="x", use_cache=True):
            self.users[task] = json.loads(msgs[1]["content"])
            return json.dumps(self.reply, ensure_ascii=False), {"cached": False, "tin": 1, "tout": 1, "cost": 0.003, "sec": 0.1}
        llm.chat = fake

    def tearDown(self):
        llm.chat = self.base
        ms.MEDIA, ms.PROP, ms.BACKUP, ms.REP, ms.client = self.old6
        super().tearDown()


class MediaAudit(Fx6):
    def test_audit_findings(self):
        out = []
        res = ms.audit_media(out.append)
        by = {r["id"]: {c for _, c in r["flags"]} for r in res["items"]}
        self.assertEqual(by[1], {"m_no_alt", "m_huge", "m_dims", "m_format", "m_name"})
        self.assertEqual(by[2], {"m_alt_name"})
        self.assertEqual(by[3], {"m_alt_dup"})                              # same Alt on 3 images
        self.assertEqual(by[6], {"m_no_alt"})
        self.assertNotIn(7, by)                                             # a good image has no finding
        self.assertEqual(res["no_alt"], 2)
        self.assertTrue(res["has_size"])
        self.assertTrue((ms.REP / "media_audit.csv").exists())
        self.assertEqual(ms.load_media()["total"], 7)
        self.assertFalse([c for c in self.c.calls if c[0] != "GET"])        # read-only

    def test_alt_from_article_context_and_apply(self):
        ms.audit_media(lambda *_: None)
        rows = ms.build_alt(None, lambda *_: None)
        by = {r["id"]: r for r in rows}
        self.assertEqual(set(by), {2, 6})                                   # #1 is used in no article: nothing invented for it
        self.assertEqual(by[6]["new"], "بسته‌بندی هدیه - هدیه زعفران")       # heading of the section + title
        self.assertEqual(by[2]["new"], "رشته‌های زعفران نگین مرغوب")        # the Alt already written inside the article wins
        self.assertEqual(ms.stem(U + "luxury-gift-box-300x200.webp"), "luxury-gift-box")
        n_calls = len(self.c.calls)
        self.assertEqual(ms.apply_alt({"6"}, {}, False, lambda *_: None), 0)  # preview
        self.assertEqual(len(self.c.calls), n_calls)
        self.assertEqual(ms.apply_alt({"6", "2"}, {"6": "جعبهٔ هدیهٔ لوکس زعفران"}, True, lambda *_: None), 2)
        posts = {c[1]: c[2] for c in self.c.calls if c[0] == "POST"}
        self.assertEqual(posts["wp/v2/media/6"], {"alt_text": "جعبهٔ هدیهٔ لوکس زعفران"})   # the edited text, only alt_text
        self.assertEqual(posts["wp/v2/media/2"], {"alt_text": "رشته‌های زعفران نگین مرغوب"})
        self.assertTrue(list(ms.BACKUP.glob("media_alt_*.json")))
        self.assertEqual(ms.load_alt()["items"], [])
        with self.assertRaises(StepError):
            (ms.MEDIA / "media.json").unlink()
            ms.build_alt(None)


class Categories(Fx6):
    def test_audit_ai_draft_and_apply(self):
        res = ms.audit_cats(lambda *_: None)
        by = {x["id"]: {c for _, c in x["flags"]} for x in res["items"]}
        self.assertNotIn(1, by)                                             # «uncategorized» is ignored
        self.assertIn("c_no_desc", by[10])
        self.assertIn("c_dup", by[12])
        self.assertIn("c_dup", by[13])
        self.assertIn("c_empty", by[13])
        self.assertNotIn("c_no_desc", by[11])
        self.reply = {"10": "زعفران نگین برای کسانی است که کیفیت می‌خواهند و شفا می‌دهد. " + "متن دسته " * 30}
        items = ms.draft_cats(False, lambda *_: None)                       # estimate only
        self.assertEqual((items, "cat_desc" in self.users), ([], False))
        items = ms.draft_cats(True, lambda *_: None)
        sent = self.users["cat_desc"]["cats"]
        self.assertEqual([c["id"] for c in sent], ["10"])                   # only categories with products and without text
        self.assertEqual(sent[0]["products"], ["زعفران نگین یک مثقال"])     # real, in-stock products only
        self.assertEqual(len(items), 1)
        self.assertTrue(any("پزشکی" in f for f in items[0]["flags"]))      # the sensitive word is flagged for you
        n = len(self.c.calls)
        ms.apply_cats({"10"}, {}, False, lambda *_: None)
        self.assertEqual(len(self.c.calls), n)
        self.assertEqual(ms.apply_cats({"10"}, {"10": "متن ویرایش‌شدهٔ من\n\nپاراگراف دوم"}, True, lambda *_: None), 1)
        call = [c for c in self.c.calls if c[0] == "POST"][-1]
        self.assertEqual(call[1], "wc/v3/products/categories/10")
        self.assertEqual(call[2], {"description": "<p>متن ویرایش‌شدهٔ من</p><p>پاراگراف دوم</p>"})
        self.assertTrue(list(ms.BACKUP.glob("cat_desc_*.json")))
        self.assertEqual(ms.load_cat_drafts()["items"], [])

    def test_ai_text_with_links_is_cleaned(self):
        ms.audit_cats(lambda *_: None)
        self.reply = {"10": "دستهٔ نگین https://evil.example.com مناسب هدیه است. " + "کلمه " * 50}
        self.assertNotIn("evil", ms.draft_cats(True, lambda *_: None)[0]["new"])


GOOD_PRODUCT = {"@context": "https://schema.org", "@graph": [{"@type": "Product", "name": "x", "image": "i.jpg", "offers": {"@type": "Offer", "price": "1", "priceCurrency": "IRR", "availability": "InStock"}},
                                                             {"@type": "BreadcrumbList", "itemListElement": [{"position": 1}]}]}


def page(*blocks):
    return "<html>" + "".join(f'<script type="application/ld+json">{b if isinstance(b, str) else json.dumps(b)}</script>' for b in blocks) + "</html>"


class Schema(unittest.TestCase):
    def test_check_page(self):
        self.assertEqual(ms.check_page("product", "u", page(GOOD_PRODUCT)), [])
        self.assertEqual(ms.check_page("product", "u", "<html></html>"), [("M", "هیچ Schema (JSON-LD) ندارد")])
        self.assertEqual(ms.check_page("product", "u", page("{broken"))[0][0], "H")
        bad = {"@type": "Product", "name": "x", "offers": {"@type": "Offer", "price": "1"}}
        msg = " ".join(m for _, m in ms.check_page("product", "u", page(bad)))
        self.assertIn("image", msg)
        self.assertIn("offers.priceCurrency", msg)
        self.assertIn("offers.availability", msg)
        self.assertTrue(any("Product ندارد" in m for _, m in ms.check_page("product", "u", page({"@type": "WebPage"}))))
        art_ = ms.check_page("article", "u", page({"@type": "Article", "headline": "h"}))
        self.assertIn("datePublished", " ".join(m for _, m in art_))
        self.assertTrue(ms.check_page("article", "u", page({"@type": "WebPage"})))
        self.assertEqual(ms.check_page("article", "u", page({"@type": ["BlogPosting"], "headline": "h", "datePublished": "d", "author": {"name": "a"}, "image": "i"})), [])
        two = ms.check_page("product", "u", page(GOOD_PRODUCT, GOOD_PRODUCT))
        self.assertTrue(any("بیش از یک Product" in m for _, m in two))
        agg = {"@type": "Product", "name": "x", "image": "i", "offers": {"@type": "AggregateOffer", "lowPrice": "1", "priceCurrency": "IRR", "availability": "InStock"}}
        self.assertEqual(ms.check_page("product", "u", page(agg)), [])


class SchemaRun(Fx6):
    def test_samples_pages_and_reads_only(self):
        seen = []

        def fetch(url):
            seen.append(url)
            return page(GOOD_PRODUCT) if "/p/1/" in url else ("" if "/p/2/" in url else "<html></html>")
        import os
        os.environ["HEALTH_DELAY"] = "0"
        res = ms.validate_schema(12, fetch, lambda *_: None)
        kinds = [p["kind"] for p in res["pages"]]
        self.assertEqual(kinds[0], "home")
        self.assertIn("article", kinds)
        self.assertEqual(kinds.count("product"), 2)
        bad = {p["url"]: p["issues"] for p in res["pages"]}
        self.assertEqual(bad["https://zafaranmarket.com/p/1/"], [])
        self.assertEqual(bad["https://zafaranmarket.com/p/2/"], [("H", "صفحه باز نشد")])
        self.assertTrue(bad["https://zafaranmarket.com/a50/"])           # the article page has no Schema in the fake
        self.assertEqual(ms.load_schema()["pages"][0]["kind"], "home")
        self.assertFalse(self.c.calls)                                      # does not touch WordPress at all


class PanelMedia(Fx6):
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
        return urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{self.port}{p}", data=urllib.parse.urlencode(items).encode()), timeout=30).read().decode()

    def test_page_flow_and_ticks(self):
        self.assertIn("ممیزی تصاویر", self.get("/media"))
        self.assertIn("تصاویر و دسته‌ها", self.get("/"))
        h = self.post("/mediaact", do="audit_media")
        self.assertIn("بدون Alt", h)
        h = self.post("/mediaact", do="alt_build")
        self.assertIn("بسته‌بندی هدیه - هدیه زعفران", h)                     # editable proposal in the page
        n = len([c for c in self.c.calls if c[0] == "POST"])
        h = self.post("/mediaact", do="alt_go", alt=["6"])
        self.assertIn("تیک تأیید را بزنید", h)                              # no tick -> nothing sent
        self.assertEqual(len([c for c in self.c.calls if c[0] == "POST"]), n)
        self.post("/mediaact", do="alt_go", alt=["6"], alt_6="Alt من", confirm="1")
        self.assertEqual([c for c in self.c.calls if c[0] == "POST"][-1][2], {"alt_text": "Alt من"})
        self.post("/mediaact", do="cats_audit")
        self.reply = {"10": "متن دسته " * 40}
        h = self.post("/mediaact", do="cats_ai", confirm="1")
        self.assertIn("متن دسته", h)
        h = self.post("/mediaact", do="cats_go", cat=["10"])
        self.assertIn("تیک تأیید را بزنید", h)


if __name__ == "__main__":
    unittest.main()
