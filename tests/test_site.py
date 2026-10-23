"""End-to-end tests for the spinner page with a generated fake pool.

Needs Playwright for Python with Chromium. Run: python tests/test_site.py [--shots DIR]
"""

import argparse
import json
import shutil
import sys
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent

AREAS = {
    "ai": ("ML / AI", ["Machine learning", "Computer vision"], 40),
    "cs": ("Computer science", ["Algorithms", "Data structures"], 30),
    "math": ("Math & stats", ["Statistics", "Graph theory"], 20),
    "sec": ("Security & blockchain", ["Cryptography", "Tiny seed"], 3),
}


def build_site(tmp: Path) -> Path:
    site = tmp / "site"
    shutil.copytree(ROOT / "site", site, ignore=shutil.ignore_patterns("pool"))
    pool = site / "pool"
    pool.mkdir()
    meta = {"areas": {}, "order": list(AREAS), "generated_at": "2026-10-26T03:20:00Z"}
    pid = 1000
    for area, (name, seeds, n) in AREAS.items():
        rows = []
        for i in range(n):
            pid += 1
            seed = 0 if area == "sec" else i % 2  # sec: all in seed 0, "Tiny seed" empty
            rows.append([pid, f"{name} article {i}", seed])
        counts = [sum(1 for r in rows if r[2] == s) for s in range(len(seeds))]
        (pool / f"{area}.json").write_text(json.dumps({"area": area, "name": name, "seeds": seeds, "seed_counts": counts, "pages": rows}))
        meta["areas"][area] = {"name": name, "count": n, "seeds": seeds, "seed_counts": counts, "crawled_at": meta["generated_at"]}
    (pool / "meta.json").write_text(json.dumps(meta))
    return site


def serve(directory: Path):
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(Quiet, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/"


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok -", msg)


def run(shots: Path | None):
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        site = build_site(tmp)
        server, url = serve(site)
        empty = tmp / "empty"
        shutil.copytree(ROOT / "site", empty, ignore=shutil.ignore_patterns("pool"))
        server2, url_empty = serve(empty)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                ctx = browser.new_context()
                ctx.route("https://en.wikipedia.org/**", lambda r: r.fulfill(status=200, body="<title>wiki</title>", content_type="text/html"))
                ctx.route("https://fonts.googleapis.com/**", lambda r: r.abort())
                errors = []
                page = ctx.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(url)
                page.wait_for_function("!document.getElementById('spin').disabled")

                print("Loading")
                check(page.locator(".area").count() == 4, "four drawers render")
                check("93 articles across 4 drawers" in page.inner_text("#status"), "status shows total")
                check("2026-10-26" in page.inner_text("#built"), "footer shows build date")

                print("Spin opens Wikipedia in a new tab")
                with ctx.expect_page() as newp:
                    page.click("#spin")
                tab = newp.value
                tab.wait_for_load_state()
                check(tab.url.startswith("https://en.wikipedia.org/?curid="), f"new tab at {tab.url}")
                tab.close()
                check(page.locator("#history-list li").count() == 1, "history has one entry")
                check(page.inner_text("#seen") == "Seen 1 of 93", "seen counter is 1 of 93")
                check(page.locator("#status a").count() == 1, "status links the opened article")

                print("Keyboard shortcut")
                with ctx.expect_page() as newp:
                    page.keyboard.press("s")
                newp.value.close()
                check(page.locator("#history-list li").count() == 2, "S key spins")

                print("Drawers filter the draw")
                page.click("#reset-seen")
                for a in ("ai", "cs", "math"):
                    page.click(f".area[data-area={a}]")
                check(page.locator(".area[aria-pressed=true]").count() == 1, "only Security is on")
                page.click(".area[data-area=sec]")
                check("at least one drawer" in page.inner_text("#status"), "can't turn off the last drawer")
                for _ in range(4):  # sec has 3 pages: the 4th spin must reset
                    with ctx.expect_page() as newp:
                        page.click("#spin")
                    newp.value.close()
                titles = page.locator("#history-list li .title a").all_inner_texts()[:4]
                check(all(t.startswith("Security & blockchain") for t in titles), "spins only from the enabled drawer")
                check("started over" in page.inner_text("#status"), "exhausted drawer resets with a note")
                check(len(set(titles[1:4])) == 3, "no repeats before the reset")

                print("Subfield filter")
                for a in ("ai", "cs"):
                    page.click(f".area[data-area={a}]")
                options = page.locator("#seed option").all_inner_texts()
                check("Tiny seed (0)" not in " ".join(options) and not any(o.startswith("Tiny seed") for o in options), "empty subfields are hidden")
                page.select_option("#seed", "cs:1")
                check(page.is_disabled("#balance"), "balance is disabled with a subfield chosen")
                with ctx.expect_page() as newp:
                    page.click("#spin")
                newp.value.close()
                crumb = page.inner_text("#status .crumb")
                check("Data structures" in crumb, f"subfield respected ({crumb.strip()})")

                print("Persistence")
                page.reload()
                page.wait_for_function("!document.getElementById('spin').disabled")
                check(page.locator("#history-list li").count() == 7, "history survives reload")
                check(page.input_value("#seed") == "cs:1", "subfield setting survives reload")
                page.click("#clear-history")
                check(page.locator("#history-list li").count() == 0 and page.is_visible("#history-empty"), "clear history empties the list")
                page.click("#reset-seen")
                check(page.inner_text("#seen").startswith("Seen 0"), "reset seen zeroes the counter")

                print("Pick logic (balance)")
                res = page.evaluate("""() => {
                  const mk = (seeds, counts) => { const pages = []; let id = 1;
                    counts.forEach((c, s) => { for (let i = 0; i < c; i++) pages.push([id++, 't', s]); });
                    const bySeed = seeds.map(() => []); pages.forEach((p, i) => bySeed[p[2]].push(i));
                    return { name: 'x', seeds, pages, bySeed }; };
                  const pools = { a: mk(['big', 'small'], [990, 10]) };
                  const tally = (balance) => { let small = 0;
                    for (let i = 0; i < 4000; i++) {
                      const r = TWR.pick({ pools, enabled: new Set(['a']), seedFilter: '', balance, seen: {} });
                      if (r.page[2] === 1) small++; }
                    return small / 4000; };
                  return [tally(false), tally(true)];
                }""")
                check(res[0] < 0.05, f"without balance the small subfield is rare ({res[0]:.3f})")
                check(0.4 < res[1] < 0.6, f"with balance each subfield gets about half ({res[1]:.3f})")

                check(not errors, f"no page errors {errors}")

                print("Missing pool")
                page2 = ctx.new_page()
                page2.goto(url_empty)
                page2.wait_for_function("document.getElementById('status').classList.contains('error')")
                check("hasn't been built yet" in page2.inner_text("#status"), "explains that the pool is missing")
                check(page2.is_disabled("#spin"), "spin stays disabled")

                if shots:
                    shots.mkdir(parents=True, exist_ok=True)
                    page.select_option("#seed", "")
                    for _ in range(4):
                        with ctx.expect_page() as newp:
                            page.click("#spin")
                        newp.value.close()
                    page.set_viewport_size({"width": 1100, "height": 1000})
                    page.screenshot(path=str(shots / "desktop-light.png"), full_page=True)
                    page.emulate_media(color_scheme="dark")
                    page.screenshot(path=str(shots / "desktop-dark.png"), full_page=True)
                    page.emulate_media(color_scheme="light")
                    page.set_viewport_size({"width": 390, "height": 900})
                    page.screenshot(path=str(shots / "phone-light.png"), full_page=True)
                    w = page.evaluate("document.documentElement.scrollWidth")
                    check(w <= 390, f"no horizontal scroll on phone (scrollWidth {w})")
                browser.close()
        finally:
            server.shutdown()
            server2.shutdown()
    print("\nAll site tests passed.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", type=Path)
    run(ap.parse_args().shots)
