# Tech Wikiroulette

Spin for a random technical Wikipedia article. Each spin opens one article in a new tab, drawn from four areas:

| Area | Seed categories |
| --- | --- |
| ML / AI | Machine learning, Artificial neural networks, Natural language processing, Computer vision, Artificial intelligence |
| Computer science | Algorithms, Data structures, Theory of computation, Distributed computing, Computer networking |
| Math & stats | Statistics, Probability theory, Linear algebra, Mathematical optimization, Graph theory, Information theory |
| Security & blockchain | Cryptography, Computer security, Blockchains, Cryptocurrencies |

## How it works

1. **Weekly crawl.** A GitHub Action runs `crawler/crawl.py` every Monday. It walks each seed category breadth-first, 1–3 levels deep, using Wikipedia's Action API (`list=categorymembers`). It skips subcategories that match a blocklist (people, companies, films, "by country", and so on). Each article is filed under the seed that reaches it in the fewest steps, so the Subfield picker stays meaningful even when seeds overlap.
2. **Filters.** Articles are kept only if they are in the main namespace, aren't redirects or disambiguation pages, and are at least 3,000 bytes long. Titles starting with "List of", "Outline of" and similar are dropped.
3. **Pool files.** The crawler writes `site/pool/<area>.json` (`[pageid, title, seedIndex]` rows) and `site/pool/meta.json`, then commits them.
4. **Spin.** The static page loads the pool files and picks an enabled area at random with equal odds, then an article you haven't seen yet. It opens `https://en.wikipedia.org/?curid=<pageid>` in a new tab. A spin makes no API call. Page IDs keep working after an article is renamed.

Your seen list, history and settings stay in your browser's localStorage.

## Using the page

- **Drawers:** turn areas on or off.
- **Subfield:** spin only within one seed category, e.g. Graph theory.
- **Give each subfield equal odds:** stops a large subtree, such as Cryptocurrencies, from crowding out smaller ones.
- **S key:** spin from the keyboard.

## Running the crawler yourself

Python 3.9+ with no dependencies:

```bash
python crawler/crawl.py                  # all areas -> site/pool
python crawler/crawl.py --areas ai       # one area
python crawler/crawl.py --sample 50      # also print 50 random picks per area to hand-check
```

Then serve the site locally:

```bash
python -m http.server -d site 8000
```

Tune what gets in through `crawler/config.json`: seed categories, depth per seed, the subcategory blocklist, minimum article size, and a safety cap on categories per area. If an area comes back with fewer than `min_pages_per_area` articles, its previous file is kept.

From the Actions tab you can run **Crawl and deploy** by hand, optionally for some areas only. Each run uploads a `crawl-sample` artifact with 20 random picks per area for a quick precision check.

## Tests

```bash
python tests/test_crawler.py   # crawler against a fake Wikipedia API (stdlib only)
python tests/test_site.py      # page end to end in headless Chromium (needs: pip install playwright)
```

Article text and titles come from [Wikipedia](https://en.wikipedia.org/) (CC BY-SA). The call numbers on the page are Dewey-style, for flavor.
