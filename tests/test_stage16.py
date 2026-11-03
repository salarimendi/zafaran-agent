"""Stage 5 tests: link graph, orphans, topic clusters, missing links, product/category links, out-of-stock products, suggestions in the link-plan format, panel page.
Run:  python -m unittest discover -s tests -v"""
import json
import sys
import threading
import unittest
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_stage13 import Base, art  # noqa: E402

from agent import corpus, linkgraph as lg, panel, proposals as pr  # noqa: E402
from agent.common import StepError  # noqa: E402

U = "https://zafaranmarket.com"
BODY = "## {h}\nمتن مقاله دربارهٔ {h} است. {extra}\n"


def mk(i, title, focus, extra=""):
    return (i, title, focus, BODY.format(h=focus, extra=extra))


ARTS = [
    mk(1, "خواص زعفران برای سلامت", "خواص زعفران", f"برای خرید می‌توانید [زعفران نگین]({U}/p/in/) و [چای زعفران]({U}/a4/) را ببینید و [یک ظرف نامرتبط]({U}/p/out/) هم هست."),
    mk(2, "فواید زعفران برای پوست", "خواص زعفران پوست", "در این مقاله دربارهٔ خواص زعفران می‌خوانید."),
    mk(3, "خواص زعفران در آشپزی", "خواص زعفران آشپزی", f"نکتهٔ مربوط به خواص زعفران را بخوانید و [مقالهٔ مادر]({U}/a1/) را ببینید. همچنین زعفران نگین مناسب است."),
    mk(4, "طرز تهیه چای زعفران", "چای زعفران", "چای را دم کنید."),
    mk(5, "دمنوش چای زعفران و عسل", "چای زعفران عسل", "این چای زعفران عالی است."),
    mk(6, "شله زرد نذری با زعفران", "شله زرد", "شله زرد را بپزید."),
    mk(7, "تاریخچهٔ برداشت گل", "برداشت گل", "برداشت در پاییز است."),
]


class Graph(Base):
    def setUp(self):
        super().setUp()
        t = Path(self.tmp.name)
        for f in (t / "arts").glob("*.md"):
            f.unlink()
        for a in ARTS:
            (t / "arts" / f"{a[0]}-x.md").write_text(art(*a), encoding="utf-8")
        snap = json.loads((t / "site" / "snapshot.json").read_text(encoding="utf-8"))
        snap["posts"] = [{"id": a[0], "status": "publish", "title": a[1], "slug": f"a{a[0]}", "url": f"{U}/a{a[0]}/", "categories": ["مقالات"], "modified": "2026-01-10"} for a in ARTS]
        snap["products"] = [
            {"id": 100, "status": "publish", "name": "زعفران نگین یک مثقال", "url": f"{U}/p/in/", "price": "1", "categories": ["زعفران نگین"], "stock_status": "instock", "manage_stock": True, "stock_quantity": 9},
            {"id": 101, "status": "publish", "name": "ظرف شیشه‌ای تمام‌شده", "url": f"{U}/p/out/", "price": "1", "categories": [], "stock_status": "outofstock", "manage_stock": True, "stock_quantity": 0}]
        (t / "site" / "snapshot.json").write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        self.old = (lg.GRAPH, lg.REP, pr.PROP)
        lg.GRAPH, lg.REP, pr.PROP = t / "graph", t / "rep", t / "prop"

    def tearDown(self):
        lg.GRAPH, lg.REP, pr.PROP = self.old
        super().tearDown()

    def test_graph_orphans_clusters_and_oos(self):
        msgs = []
        res = lg.build(msgs.append)
        by = {n["id"]: n for n in res["nodes"] if n["kind"] == "article"}
        self.assertEqual(by[1]["out"], 3)                                   # links to a product, an article and an out-of-stock product
        self.assertEqual(by[4]["inb"], 1)                                   # article 1 links to 4
        self.assertEqual(by[1]["inb"], 1)                                   # article 3 links to 1
        self.assertEqual({o["id"] for o in res["orphans"]}, {2, 3, 5, 6, 7})
        self.assertIn("article->product", res["by_type"])
        self.assertEqual([x["url"] for x in res["oos_linked"]], [f"{U}/p/out/"])   # a link to a product with zero stock is reported
        self.assertEqual(res["oos_linked"][0]["from"], ["خواص زعفران برای سلامت"])
        # clusters: the three «خواص زعفران» articles together, the two tea articles together, the others alone
        groups = [{c["pillar"]["id"]} | {s["id"] for s in c["supporters"]} for c in res["clusters"]]
        self.assertIn({1, 2, 3}, groups)
        self.assertIn({4, 5}, groups)
        pillar = next(c for c in res["clusters"] if c["pillar"]["id"] in (1, 2, 3) and len(c["supporters"]) == 2)
        self.assertEqual(pillar["pillar"]["id"], 1)                         # most linked-to member of the group becomes the parent page
        self.assertTrue(next(s for s in pillar["supporters"] if s["id"] == 3)["to_pillar"])
        self.assertFalse(next(s for s in pillar["supporters"] if s["id"] == 2)["to_pillar"])
        self.assertIn(6, {x["id"] for x in res["single"]})
        self.assertTrue(res["has_stock"])
        self.assertTrue((lg.REP / "link_graph.md").exists())
        self.assertTrue((lg.REP / "link_graph_nodes.csv").exists())
        self.assertEqual(lg.load()["edges"], res["edges"])
        self.assertTrue(any("مقالهٔ یتیم" in m for m in msgs))

    def test_suggestions_use_link_plan_format_and_never_oos(self):
        res = lg.build(lambda *_: None)
        path, rows, nophrase = lg.suggest(res, lambda *_: None)
        self.assertRegex(path.name, r"^links_\d{8}_\d{4}\.csv$")            # the existing panel page / apply flow reads this name
        data = pr.read_csv(path)
        self.assertEqual(list(data[0].keys())[:11], ["approve", "source_id", "source_title", "source_url", "anchor", "target_id", "target_title", "target_url", "sentence", "score", "source_modified"])
        self.assertTrue(all(r["approve"] == "" for r in data))              # nothing is pre-approved
        pairs = {(int(r["source_id"]), r["target_url"]) for r in data}
        self.assertIn((2, f"{U}/a1/"), pairs)                               # supporter 2 mentions «خواص زعفران» but does not link to the parent
        self.assertNotIn((3, f"{U}/a1/"), pairs)                            # 3 already links to the parent
        self.assertNotIn(f"{U}/p/out/", {r["target_url"] for r in data})   # never a product with zero stock
        r2 = next(r for r in data if int(r["source_id"]) == 2)
        self.assertIn(r2["anchor"], r2["sentence"])
        self.assertIn("صفحهٔ مادر", r2["why"])
        self.assertLessEqual(max(sum(1 for r in data if r["source_id"] == str(i)) for i in range(1, 8)), pr.MAX_NEW_PER_SOURCE)

    def test_old_snapshot_without_stock_links_to_no_product(self):
        t = Path(self.tmp.name)
        snap = json.loads((t / "site" / "snapshot.json").read_text(encoding="utf-8"))
        for p in snap["products"]:
            for k in ("stock_status", "manage_stock", "stock_quantity"):
                p.pop(k)
        (t / "site" / "snapshot.json").write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        msgs = []
        res = lg.build(msgs.append)
        self.assertFalse(res["has_stock"])
        self.assertTrue(any("همگام‌سازی" in m for m in msgs))
        _, rows, _ = lg.suggest(res, lambda *_: None)
        self.assertFalse([r for r in rows if r[7].startswith(f"{U}/p/")])
        self.assertTrue(all(not c["products"] for c in res["clusters"]))

    def test_needs_data(self):
        for f in (Path(self.tmp.name) / "arts").glob("*.md"):
            f.unlink()
        with self.assertRaises(StepError):
            lg.build(lambda *_: None)
        with self.assertRaises(StepError):
            lg.suggest(None)

    def test_panel_page_and_actions(self):
        srv = panel.make_server(0)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            get = lambda p: urllib.request.urlopen(f"http://127.0.0.1:{port}{p}", timeout=20).read().decode()
            post = lambda p, **f: urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}{p}", data=urllib.parse.urlencode({"t": panel.TOKEN, **f}).encode()), timeout=30)
            self.assertIn("ساخت/به‌روزرسانی گراف", get("/graph"))
            self.assertIn("گراف لینک", get("/"))                             # in the navigation
            h = post("/graphbuild", do="build").read().decode()
            self.assertIn("لینک به محصول ناموجود", h)
            self.assertIn("صفحهٔ مادر", h)
            self.assertIn("مقاله‌های یتیم", h)
            r = post("/graphbuild", do="links")
            self.assertTrue(r.geturl().endswith("/links"))                  # lands on the existing tick -> preview -> apply page
            self.assertIn("صفحهٔ مادر", r.read().decode())                  # the reason column is shown there
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
