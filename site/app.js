// Tech Wikiroulette: pick a random article from the local pool and open it on Wikipedia.
// No Wikipedia API calls happen here; the weekly crawler builds pool/<area>.json.
(() => {
  "use strict";

  const CALL_NUMBERS = { ai: "006.31", cs: "004.0", math: "519.5", sec: "005.8" };
  const HISTORY_MAX = 50;
  const KEY = { settings: "twr.settings", history: "twr.history", seen: (a) => "twr.seen." + a };

  // ---- storage helpers (storage can be unavailable; the app still works without it)
  const store = {
    get(key, fallback) {
      try {
        const v = localStorage.getItem(key);
        return v == null ? fallback : JSON.parse(v);
      } catch { return fallback; }
    },
    set(key, value) {
      try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* ignore */ }
    },
    remove(key) {
      try { localStorage.removeItem(key); } catch { /* ignore */ }
    },
  };

  const $ = (id) => document.getElementById(id);
  const els = {
    areas: $("areas"), seed: $("seed"), balance: $("balance"), spin: $("spin"),
    status: $("status"), list: $("history-list"), empty: $("history-empty"),
    seen: $("seen"), resetSeen: $("reset-seen"), clearHistory: $("clear-history"), built: $("built"),
  };

  const state = {
    meta: null,
    order: [],               // area ids in display order
    pools: {},               // id -> {name, seeds, pages: [[id,title,seed]], bySeed: [[indices]]}
    enabled: new Set(),
    seedFilter: "",          // "" or "<area>:<seedIndex>"
    balance: false,
    seen: {},                // area -> Set(pageid)
    history: store.get(KEY.history, []),
  };

  // ---- pure picking logic (exported on window for tests)
  function pickIndex(n, rand) { return Math.floor(rand() * n); }

  function candidates(pool, seen, seedIdx) {
    const idxs = seedIdx == null ? pool.pages.map((_, i) => i) : pool.bySeed[seedIdx] || [];
    return idxs.filter((i) => !seen.has(pool.pages[i][0]));
  }

  // Returns {area, page:[id,title,seed], reset:boolean} or null.
  function pick({ pools, enabled, seedFilter, balance, seen }, rand = Math.random) {
    let areas, forcedSeed = null;
    if (seedFilter) {
      const [a, s] = seedFilter.split(":");
      if (!pools[a]) return null;
      areas = [a];
      forcedSeed = Number(s);
    } else {
      areas = [...enabled].filter((a) => pools[a] && pools[a].pages.length);
    }
    if (!areas.length) return null;

    const area = areas[pickIndex(areas.length, rand)];
    const pool = pools[area];
    const seenSet = seen[area] || (seen[area] = new Set());

    let seedIdx = forcedSeed;
    if (seedIdx == null && balance) {
      const nonEmpty = pool.bySeed.map((ix, i) => (ix.length ? i : -1)).filter((i) => i >= 0);
      seedIdx = nonEmpty[pickIndex(nonEmpty.length, rand)];
    }

    let cands = candidates(pool, seenSet, seedIdx);
    let reset = false;
    if (!cands.length) {
      // Everything here has been seen: forget those pages and start over.
      const scope = seedIdx == null ? pool.pages : pool.bySeed[seedIdx].map((i) => pool.pages[i]);
      for (const p of scope) seenSet.delete(p[0]);
      cands = candidates(pool, seenSet, seedIdx);
      reset = true;
    }
    if (!cands.length) return null;
    const page = pool.pages[cands[pickIndex(cands.length, rand)]];
    return { area, page, reset };
  }

  function articleUrl(pageid) { return "https://en.wikipedia.org/?curid=" + pageid; }

  window.TWR = { pick, articleUrl };

  // ---- UI
  function fmt(n) { return n.toLocaleString("en-US"); }

  function saveSettings() {
    store.set(KEY.settings, { enabled: [...state.enabled], seedFilter: state.seedFilter, balance: state.balance });
  }

  function renderAreas() {
    els.areas.textContent = "";
    for (const id of state.order) {
      const info = state.meta.areas[id];
      const loaded = !!state.pools[id];
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "area";
      btn.disabled = !loaded;
      btn.setAttribute("aria-pressed", String(state.enabled.has(id) && loaded));
      btn.dataset.area = id;
      const call = document.createElement("span");
      call.className = "area-call";
      call.textContent = CALL_NUMBERS[id] || "000";
      const name = document.createElement("span");
      name.className = "area-name";
      name.textContent = info.name;
      const count = document.createElement("span");
      count.className = "area-count";
      count.textContent = loaded ? fmt(info.count) + " articles" : "unavailable";
      btn.append(call, name, count);
      btn.addEventListener("click", () => {
        if (state.enabled.has(id)) {
          if (state.enabled.size === 1) { setStatus("Keep at least one drawer open."); return; }
          state.enabled.delete(id);
        } else state.enabled.add(id);
        if (state.seedFilter && !state.enabled.has(state.seedFilter.split(":")[0])) state.seedFilter = "";
        saveSettings();
        renderAreas(); renderSeeds(); renderSeen();
      });
      els.areas.append(btn);
    }
  }

  function renderSeeds() {
    els.seed.textContent = "";
    const any = new Option("Any subfield", "");
    els.seed.append(any);
    for (const id of state.order) {
      if (!state.enabled.has(id) || !state.pools[id]) continue;
      const pool = state.pools[id];
      const group = document.createElement("optgroup");
      group.label = pool.name;
      pool.seeds.forEach((s, i) => {
        if (!pool.bySeed[i].length) return;
        group.append(new Option(`${s} (${fmt(pool.bySeed[i].length)})`, `${id}:${i}`));
      });
      els.seed.append(group);
    }
    els.seed.value = state.seedFilter;
    if (els.seed.value !== state.seedFilter) { state.seedFilter = ""; els.seed.value = ""; }
    els.balance.disabled = !!state.seedFilter;
  }

  function scopeTotals() {
    let total = 0, seen = 0;
    const areas = state.seedFilter ? [state.seedFilter.split(":")[0]] : [...state.enabled];
    for (const a of areas) {
      const pool = state.pools[a];
      if (!pool) continue;
      const s = state.seen[a] || new Set();
      if (state.seedFilter) {
        const idx = pool.bySeed[Number(state.seedFilter.split(":")[1])] || [];
        total += idx.length;
        seen += idx.filter((i) => s.has(pool.pages[i][0])).length;
      } else {
        total += pool.pages.length;
        seen += s.size;
      }
    }
    return { total, seen };
  }

  function renderSeen() {
    const { total, seen } = scopeTotals();
    els.seen.textContent = total ? `Seen ${fmt(seen)} of ${fmt(total)}` : "";
  }

  function timeLabel(ts) {
    const d = new Date(ts);
    const today = new Date();
    return d.toDateString() === today.toDateString()
      ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : d.toLocaleDateString([], { month: "short", day: "numeric" });
  }

  function renderHistory() {
    els.list.textContent = "";
    for (const h of state.history) {
      const li = document.createElement("li");
      const call = document.createElement("span");
      call.className = "call";
      call.textContent = CALL_NUMBERS[h.area] || "";
      const title = document.createElement("span");
      title.className = "title";
      const a = document.createElement("a");
      a.href = articleUrl(h.id);
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = h.title;
      const small = document.createElement("small");
      small.textContent = `${h.areaName} › ${h.seed}`;
      title.append(a, small);
      const time = document.createElement("time");
      time.dateTime = new Date(h.ts).toISOString();
      time.textContent = timeLabel(h.ts);
      li.append(call, title, time);
      els.list.append(li);
    }
    els.empty.hidden = state.history.length > 0;
  }

  function setStatus(text, { error = false } = {}) {
    els.status.classList.toggle("error", error);
    els.status.textContent = text;
  }

  function openInNewTab(url) {
    // An anchor click inside the user's gesture is the most reliable way past popup blockers.
    const a = document.createElement("a");
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener";
    document.body.append(a);
    a.click();
    a.remove();
  }

  function spin() {
    const result = pick(state);
    if (!result) { setStatus("No articles to pick from. Turn on a drawer above.", { error: true }); return; }
    const { area, page, reset } = result;
    const [id, title, seedIdx] = page;
    const pool = state.pools[area];
    const url = articleUrl(id);
    openInNewTab(url);

    state.seen[area].add(id);
    store.set(KEY.seen(area), [...state.seen[area]]);
    state.history.unshift({ id, title, area, areaName: pool.name, seed: pool.seeds[seedIdx], ts: Date.now() });
    state.history.length = Math.min(state.history.length, HISTORY_MAX);
    store.set(KEY.history, state.history);

    els.status.classList.remove("error");
    els.status.textContent = "";
    const link = document.createElement("a");
    link.href = url; link.target = "_blank"; link.rel = "noopener"; link.textContent = title;
    const crumb = document.createElement("span");
    crumb.className = "crumb";
    crumb.textContent = ` · ${pool.name} › ${pool.seeds[seedIdx]}`;
    els.status.append("Opened ", link, crumb);
    if (reset) els.status.append(" (you'd seen everything here, so we started over)");
    renderHistory(); renderSeen();
  }

  async function loadJSON(path) {
    const res = await fetch(path, { cache: "no-cache" });
    if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
    return res.json();
  }

  async function init() {
    try {
      state.meta = await loadJSON("pool/meta.json");
    } catch {
      setStatus("The article list hasn't been built yet. Run the \"Crawl and deploy\" workflow on GitHub, then reload.", { error: true });
      return;
    }
    state.order = (state.meta.order || Object.keys(state.meta.areas)).filter((a) => state.meta.areas[a]);

    const results = await Promise.allSettled(state.order.map((a) => loadJSON(`pool/${a}.json`)));
    results.forEach((r, i) => {
      const id = state.order[i];
      if (r.status !== "fulfilled") return;
      const pool = r.value;
      pool.bySeed = pool.seeds.map(() => []);
      pool.pages.forEach((p, idx) => { if (pool.bySeed[p[2]]) pool.bySeed[p[2]].push(idx); });
      state.pools[id] = pool;
      const valid = new Set(pool.pages.map((p) => p[0]));
      state.seen[id] = new Set(store.get(KEY.seen(id), []).filter((x) => valid.has(x)));
    });

    const saved = store.get(KEY.settings, {});
    const loadedIds = state.order.filter((a) => state.pools[a]);
    const want = (saved.enabled || loadedIds).filter((a) => state.pools[a]);
    state.enabled = new Set(want.length ? want : loadedIds);
    state.seedFilter = saved.seedFilter || "";
    state.balance = !!saved.balance;
    els.balance.checked = state.balance;

    if (state.meta.generated_at) {
      els.built.textContent = `Article list built ${state.meta.generated_at.slice(0, 10)}.`;
    }
    renderAreas(); renderSeeds(); renderSeen(); renderHistory();

    if (!loadedIds.length) {
      setStatus("Couldn't load any article lists. Try reloading the page.", { error: true });
      return;
    }
    const total = loadedIds.reduce((n, a) => n + state.pools[a].pages.length, 0);
    setStatus(`${fmt(total)} articles across ${loadedIds.length} drawers. Ready when you are.`);
    els.spin.disabled = false;
  }

  els.spin.addEventListener("click", spin);
  els.seed.addEventListener("change", () => {
    state.seedFilter = els.seed.value;
    els.balance.disabled = !!state.seedFilter;
    saveSettings(); renderSeen();
  });
  els.balance.addEventListener("change", () => { state.balance = els.balance.checked; saveSettings(); });
  els.resetSeen.addEventListener("click", () => {
    for (const a of Object.keys(state.seen)) { state.seen[a].clear(); store.remove(KEY.seen(a)); }
    renderSeen();
    setStatus("Seen list cleared. Every article is back in the draw.");
  });
  els.clearHistory.addEventListener("click", () => {
    state.history = [];
    store.remove(KEY.history);
    renderHistory();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "s" && e.key !== "S") return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const tag = (e.target && e.target.tagName) || "";
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
    if (els.spin.disabled) return;
    e.preventDefault();
    spin();
  });

  init();
})();
