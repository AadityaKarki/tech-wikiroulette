"""Crawler tests against a small fake MediaWiki API. Run: python tests/test_crawler.py"""

import json
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "crawler"))
import crawl  # noqa: E402

# category -> list of members (pageid, ns, title)
GRAPH = {
    "Machine learning": [
        (1, 0, "Backpropagation"),
        (2, 0, "List of machine learning concepts"),
        (100, 14, "Category:Deep learning"),
        (101, 14, "Category:Machine learning researchers"),
        (102, 14, "Category:Machine learning"),  # cycle back to itself
    ],
    "Deep learning": [
        (3, 0, "Transformer (deep learning architecture)"),
        (4, 0, "Dropout (redirect)"),
        (5, 0, "Attention (disambiguation)"),
        (6, 0, "Tiny stub"),
        (103, 14, "Category:Neural nets"),
    ],
    "Neural nets": [(7, 0, "Perceptron"), (104, 14, "Category:Too deep")],
    "Too deep": [(8, 0, "Should not appear")],
    "Machine learning researchers": [(9, 0, "Some Person")],
    "Computer vision": [(3, 0, "Transformer (deep learning architecture)"), (10, 0, "Image segmentation")],
}
INFO = {
    1: {"length": 50000}, 2: {"length": 9000}, 3: {"length": 80000},
    4: {"length": 50, "redirect": True}, 5: {"length": 4000, "pageprops": {"disambiguation": ""}},
    6: {"length": 100}, 7: {"length": 20000}, 8: {"length": 20000}, 9: {"length": 20000},
    10: {"length": 30000},
}
PAGE_SIZE = 2  # force continuation
state = {"calls": 0, "lagged": False}


class FakeAPI(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
        state["calls"] += 1
        assert self.headers.get("User-Agent", "").startswith("TechWikiroulette"), "missing User-Agent"
        if not state["lagged"]:
            state["lagged"] = True
            return self.send({"error": {"code": "maxlag", "info": "lagged", "lag": 0}})
        if q.get("list") == "categorymembers":
            cat = q["cmtitle"].split(":", 1)[1]
            members = GRAPH.get(cat, [])
            start = int(q.get("cmcontinue", 0))
            chunk = members[start : start + PAGE_SIZE]
            body = {"query": {"categorymembers": [{"pageid": p, "ns": ns, "title": t} for p, ns, t in chunk]}}
            if start + PAGE_SIZE < len(members):
                body["continue"] = {"cmcontinue": str(start + PAGE_SIZE), "continue": "-||"}
            return self.send(body)
        if "pageids" in q:
            ids = [int(x) for x in q["pageids"].split("|")]
            assert len(ids) <= 50
            titles = {p: t for members in GRAPH.values() for p, ns, t in members if ns == 0}
            pages = []
            for i in ids:
                meta = INFO.get(i)
                if meta is None:
                    pages.append({"pageid": i, "missing": True})
                    continue
                page = {"pageid": i, "ns": 0, "title": titles[i], "length": meta["length"]}
                if meta.get("redirect"):
                    page["redirect"] = True
                if "pageprops" in meta:
                    page["pageprops"] = meta["pageprops"]
                pages.append(page)
            return self.send({"query": {"pages": pages}})
        self.send({"error": {"code": "badrequest", "info": str(q)}})

    def send(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


CFG = {
    "user_agent": "TechWikiroulette/test",
    "min_bytes": 3000,
    "min_pages_per_area": 2,
    "max_categories_per_area": 100,
    "drop_title_prefixes": ["List of "],
    "blocked_category_patterns": ["researchers\\b"],
    "areas": [
        {
            "id": "ai",
            "name": "ML / AI",
            "seeds": [
                {"category": "Machine learning", "depth": 2},
                {"category": "Computer vision", "depth": 1},
                {"category": "Missing category", "depth": 1},
            ],
        }
    ],
}


class CrawlerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeAPI)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/w/api.php"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_crawl_area(self):
        wiki = crawl.Wiki("TechWikiroulette/test", api=self.url, pause=0)
        result = crawl.crawl_area(wiki, CFG["areas"][0], CFG)
        got = {pid: (title, seed) for pid, title, seed in result["pages"]}
        self.assertEqual(
            got,
            {
                1: ("Backpropagation", 0),
                3: ("Transformer (deep learning architecture)", 0),
                7: ("Perceptron", 0),
                10: ("Image segmentation", 1),
            },
        )
        self.assertEqual(result["seed_counts"], [3, 1, 0])
        self.assertTrue(state["lagged"])

    def test_cli_writes_pool_and_meta(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text(json.dumps(CFG))
            out = Path(tmp) / "pool"
            proc = subprocess.run(
                [sys.executable, str(ROOT / "crawler" / "crawl.py"), "--config", str(cfg_path), "--out", str(out), "--sample", "2"],
                env={"WIKI_API": self.url, "PATH": "/usr/bin:/bin"},
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            pool = json.loads((out / "ai.json").read_text())
            meta = json.loads((out / "meta.json").read_text())
            self.assertEqual(len(pool["pages"]), 4)
            self.assertEqual(meta["areas"]["ai"]["count"], 4)
            self.assertEqual(meta["order"], ["ai"])
            self.assertIn("curid=", proc.stdout)

    def test_too_small_area_keeps_previous_file(self):
        cfg = json.loads(json.dumps(CFG))
        cfg["min_pages_per_area"] = 50
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text(json.dumps(cfg))
            out = Path(tmp) / "pool"
            out.mkdir()
            (out / "ai.json").write_text('{"previous": true}')
            proc = subprocess.run(
                [sys.executable, str(ROOT / "crawler" / "crawl.py"), "--config", str(cfg_path), "--out", str(out)],
                env={"WIKI_API": self.url, "PATH": "/usr/bin:/bin"},
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(json.loads((out / "ai.json").read_text()), {"previous": True})


if __name__ == "__main__":
    unittest.main(verbosity=2)
