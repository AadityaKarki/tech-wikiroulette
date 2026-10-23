#!/usr/bin/env python3
"""Build the Tech Wikiroulette article pool from Wikipedia categories.

For each area in config.json, walk its seed categories breadth-first up to a
depth limit, skip blocked subcategories, keep main-namespace articles, then
drop redirects, disambiguation pages and stubs. Writes one JSON file per area
plus meta.json into the output folder.

Standard library only, so it runs anywhere Python 3.9+ does.

Usage:
    python crawler/crawl.py                    # all areas -> site/pool
    python crawler/crawl.py --areas ai,math    # just some areas
    python crawler/crawl.py --sample 50        # print random picks to hand-check
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "crawler" / "config.json"
DEFAULT_OUT = ROOT / "site" / "pool"
API = os.environ.get("WIKI_API", "https://en.wikipedia.org/w/api.php")


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


class Wiki:
    """Tiny, polite client for the MediaWiki Action API."""

    def __init__(self, user_agent: str, api: str = API, pause: float = 0.1):
        self.user_agent = user_agent
        self.api = api
        self.pause = pause
        self.calls = 0

    def get(self, **params) -> dict:
        params = {"format": "json", "formatversion": "2", "maxlag": "5", **params}
        url = self.api + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        delay = 2.0
        for attempt in range(8):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.load(resp)
                self.calls += 1
                err = data.get("error")
                if err and err.get("code") == "maxlag":
                    wait = float(err.get("lag", delay)) + 1
                    log(f"  server lagged, waiting {wait:.0f}s")
                    time.sleep(wait)
                    continue
                if err:
                    raise RuntimeError(f"API error {err.get('code')}: {err.get('info')}")
                time.sleep(self.pause)
                return data
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504):
                    wait = float(e.headers.get("Retry-After") or delay)
                    log(f"  HTTP {e.code}, retrying in {wait:.0f}s")
                    time.sleep(wait)
                    delay = min(delay * 2, 60)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError) as e:
                log(f"  network error ({e}), retrying in {delay:.0f}s")
                time.sleep(delay)
                delay = min(delay * 2, 60)
        raise RuntimeError(f"giving up after retries: {url}")

    def category_members(self, category: str):
        """Yield (pageid, ns, title) for every article and subcategory in a category."""
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": "Category:" + category,
            "cmtype": "page|subcat",
            "cmnamespace": "0|14",
            "cmprop": "ids|title",
            "cmlimit": "max",
        }
        while True:
            data = self.get(**params)
            for m in data.get("query", {}).get("categorymembers", []):
                yield m["pageid"], m["ns"], m["title"]
            cont = data.get("continue")
            if not cont:
                return
            params.update(cont)

    def page_info(self, pageids: list[int]) -> dict[int, dict]:
        """Length, redirect flag and disambiguation flag for up to 50 pages."""
        data = self.get(
            action="query",
            pageids="|".join(map(str, pageids)),
            prop="info|pageprops",
            ppprop="disambiguation",
        )
        out = {}
        for p in data.get("query", {}).get("pages", []):
            if p.get("missing") or "pageid" not in p:
                continue
            out[p["pageid"]] = {
                "title": p.get("title"),
                "length": p.get("length", 0),
                "redirect": bool(p.get("redirect")),
                "disambiguation": "disambiguation" in (p.get("pageprops") or {}),
            }
        return out


def strip_ns(title: str) -> str:
    return title.split(":", 1)[1] if title.startswith("Category:") else title


def crawl_area(wiki: Wiki, area: dict, cfg: dict) -> dict:
    blocked = [re.compile(p, re.I) for p in cfg["blocked_category_patterns"]]
    prefixes = tuple(cfg["drop_title_prefixes"])
    max_cats = cfg["max_categories_per_area"]

    seeds = [s["category"] for s in area["seeds"]]
    pages: dict[int, dict] = {}  # pageid -> {"title", "seed"}
    visited: set[str] = set()
    skipped_cats = 0

    for seed_idx, seed in enumerate(area["seeds"]):
        queue = deque([(seed["category"], 0)])
        found_before = len(pages)
        while queue:
            cat, depth = queue.popleft()
            if cat in visited:
                continue
            if len(visited) >= max_cats:
                log(f"  hit max_categories_per_area ({max_cats}); stopping this area's walk")
                queue.clear()
                break
            visited.add(cat)
            for pageid, ns, title in wiki.category_members(cat):
                if ns == 14:
                    sub = strip_ns(title)
                    if sub in visited:
                        continue
                    if any(rx.search(sub) for rx in blocked):
                        skipped_cats += 1
                        continue
                    if depth + 1 <= seed["depth"]:
                        queue.append((sub, depth + 1))
                elif ns == 0:
                    if title.startswith(prefixes):
                        continue
                    pages.setdefault(pageid, {"title": title, "seed": seed_idx})
        found = len(pages) - found_before
        log(f"  {seed['category']}: +{found} articles ({len(visited)} categories so far)")
        if found == 0:
            log(f"  WARNING: seed category '{seed['category']}' gave nothing new; check its name")

    # Drop redirects, disambiguation pages and stubs.
    ids = list(pages)
    kept = 0
    for i in range(0, len(ids), 50):
        batch = ids[i : i + 50]
        info = wiki.page_info(batch)
        for pid in batch:
            meta = info.get(pid)
            if (
                meta is None
                or meta["redirect"]
                or meta["disambiguation"]
                or meta["length"] < cfg["min_bytes"]
            ):
                del pages[pid]
            else:
                pages[pid]["title"] = meta["title"]
                kept += 1

    rows = sorted(([pid, p["title"], p["seed"]] for pid, p in pages.items()), key=lambda r: r[1])
    per_seed = [0] * len(seeds)
    for _, _, s in rows:
        per_seed[s] += 1
    log(f"  kept {kept} of {len(ids)} articles; skipped {skipped_cats} blocked subcategories")
    return {
        "area": area["id"],
        "name": area["name"],
        "seeds": seeds,
        "seed_counts": per_seed,
        "pages": rows,
    }


def write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--areas", help="comma-separated area ids (default: all)")
    ap.add_argument("--sample", type=int, default=0, help="print N random picks per area after crawling")
    args = ap.parse_args()

    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    wanted = set(args.areas.split(",")) if args.areas else None
    args.out.mkdir(parents=True, exist_ok=True)
    wiki = Wiki(cfg["user_agent"])

    meta_path = args.out / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"areas": {}}
    failures = []

    for area in cfg["areas"]:
        if wanted and area["id"] not in wanted:
            continue
        log(f"Crawling {area['name']} ...")
        try:
            result = crawl_area(wiki, area, cfg)
        except Exception as e:  # keep the previous pool for this area
            log(f"  FAILED: {e}")
            failures.append(area["id"])
            continue
        n = len(result["pages"])
        if n < cfg["min_pages_per_area"]:
            log(f"  only {n} articles (< {cfg['min_pages_per_area']}); keeping the previous file")
            failures.append(area["id"])
            continue
        write_json(args.out / f"{area['id']}.json", result)
        meta["areas"][area["id"]] = {
            "name": area["name"],
            "count": n,
            "seeds": result["seeds"],
            "seed_counts": result["seed_counts"],
            "crawled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if args.sample:
            print(f"\n== {area['name']}: {args.sample} random picks ==")
            for pid, title, s in random.sample(result["pages"], min(args.sample, n)):
                print(f"{title}  [{result['seeds'][s]}]  https://en.wikipedia.org/?curid={pid}")

    meta["order"] = [a["id"] for a in cfg["areas"] if a["id"] in meta["areas"]]
    meta["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_json(meta_path, meta)
    log(f"Done: {wiki.calls} API calls. Failed areas: {', '.join(failures) or 'none'}")
    return 1 if failures and len(failures) == len(wanted or cfg["areas"]) else 0


if __name__ == "__main__":
    sys.exit(main())
