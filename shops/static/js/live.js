// Live dashboard. Every second it asks the shop:
//   /status -> totals for the big counters (counted over ALL events), mock flag, SHOP_PAY_TO check
//   /events -> recent events, GROUPED into one row per purchase
//
// One purchase = all events with the same path + resource (full URL) + request_id:
//   payment_required -> verified -> settled  (or failed)
// The row's badge moves along:  402 BILL -> VERIFIED -> PAID / FAILED.
// A bill nobody paid within 60 seconds shows EXPIRED.
// Rows are kept in a Map and UPDATED IN PLACE, so a row doesn't jump or re-animate when
// its status changes; only the badge flashes.

(function () {
  const POLL_MS = 1000;
  const MAX_ROWS = 50;
  const EVENT_LIMIT = 500;
  const EXPIRE_SECONDS = 60;
  const $ = (id) => document.getElementById(id);

  const rows = new Map(); // purchase key -> { tr, cells, status }
  let firstLoad = true;
  let lastEarned = null;
  let clockSkew = 0; // server time - browser time, in seconds

  const STATUS = {
    bill: { label: "402 BILL", cls: "badge-402" },
    verified: { label: "VERIFIED", cls: "badge-verified" },
    paid: { label: "PAID", cls: "badge-ok" },
    failed: { label: "FAILED", cls: "badge-bad" },
    expired: { label: "EXPIRED", cls: "badge-expired" },
  };
  const STEP = { payment_required: "bill", verified: "verified", settled: "paid", failed: "failed" };

  function purchaseKey(ev) {
    const d = ev.data || {};
    // Events from before request ids existed: each one is its own row.
    if (!d.request_id) return `legacy|${ev.ts}|${ev.type}|${d.path}`;
    return `${d.path}|${d.resource}|${d.request_id}`;
  }

  // Fold the event list (oldest first) into one summary per purchase.
  function groupPurchases(events) {
    const groups = new Map();
    for (const ev of events) {
      const step = STEP[ev.type];
      if (!step) continue; // "request" events are not shown
      const d = ev.data || {};
      const key = purchaseKey(ev);
      let g = groups.get(key);
      if (!g) {
        g = { key, startTs: ev.ts, path: d.path, resource: d.resource, rid: d.request_id };
        groups.set(key, g);
      }
      g.step = step; // the latest event decides the status
      g.lastTs = ev.ts;
      if (d.amount_usd != null) g.amount = d.amount_usd;
      if (d.payer) g.payer = d.payer;
      if (d.tx) g.tx = d.tx;
      g.reason = step === "failed" ? d.reason : null;
    }
    return [...groups.values()];
  }

  function statusOf(g) {
    const now = Date.now() / 1000 + clockSkew;
    if (g.step === "bill" && now - g.startTs > EXPIRE_SECONDS) return "expired";
    return g.step;
  }

  function symbolOf(resource) {
    try { return new URL(resource).searchParams.get("symbol") || ""; } catch { return ""; }
  }

  function td(className) {
    const el = document.createElement("td");
    if (className) el.className = className;
    return el;
  }

  function createRow(g) {
    const tr = document.createElement("tr");
    const cells = {
      time: td("mono"), endpoint: td("mono"), amount: td("num mono"),
      payer: td("mono hide-sm"), tx: td("mono hide-sm"), status: td(),
    };
    Object.values(cells).forEach((c) => tr.appendChild(c));
    cells.time.textContent = new Date(g.startTs * 1000).toLocaleTimeString();
    const sym = symbolOf(g.resource);
    cells.endpoint.textContent = (g.path || "—") + (sym ? ` · ${sym}` : "");
    if (g.rid) {
      const rid = document.createElement("span");
      rid.className = "rid";
      rid.textContent = `id ${g.rid}`;
      cells.endpoint.appendChild(rid);
    }
    const badge = document.createElement("span");
    const why = document.createElement("span");
    why.className = "muted mono why";
    cells.status.append(badge, why);
    return { tr, cells, badge, why, status: null };
  }

  function updateRow(row, g) {
    const c = row.cells;
    c.amount.textContent = usd(g.amount);
    c.payer.textContent = shortHex(g.payer);
    c.payer.title = g.payer || "";
    c.tx.textContent = shortHex(g.tx);
    c.tx.title = g.tx || "";

    const status = statusOf(g);
    if (status !== row.status) {
      const s = STATUS[status];
      row.badge.className = "badge " + s.cls;
      row.badge.textContent = s.label;
      if (row.status !== null) { // flash only on a real change, not on first draw
        void row.badge.offsetWidth; // restart the CSS animation
        row.badge.classList.add("flash");
      }
      row.status = status;
    }
    row.why.textContent = g.reason || "";
    row.badge.title = g.reason || "";
  }

  function renderFeed(events) {
    const groups = groupPurchases(events)
      .sort((a, b) => b.startTs - a.startTs) // newest purchase first
      .slice(0, MAX_ROWS);
    if (!groups.length) return;

    const body = $("feed-body");
    const keep = new Set();
    groups.forEach((g, i) => {
      keep.add(g.key);
      let row = rows.get(g.key);
      if (!row) {
        row = createRow(g);
        rows.set(g.key, row);
        if (!firstLoad) row.tr.classList.add("new"); // slide in, but not the whole table on load
      }
      updateRow(row, g);
      // Put the row in position i (moves it only if it's not already there).
      if (body.children[i] !== row.tr) body.insertBefore(row.tr, body.children[i] || null);
    });
    // Drop rows that fell off the end, and the "waiting…" placeholder.
    const keepTrs = new Set([...keep].map((k) => rows.get(k).tr));
    [...body.children].forEach((tr) => { if (!keepTrs.has(tr)) tr.remove(); });
    for (const k of [...rows.keys()]) if (!keep.has(k)) rows.delete(k);
    firstLoad = false;
  }

  function renderStatus(st) {
    const t = st.totals;
    $("c-requests").textContent = t.requests;
    $("c-402").textContent = t.payment_required;
    $("c-settled").textContent = t.settled;
    $("c-failed").textContent = t.failed;
    // Optional banners: skip if this HTML doesn't have them (e.g. an older template).
    const mock = $("mock-badge");
    if (mock) mock.hidden = !st.mock_facilitator;
    const payto = $("payto-warning");
    if (payto) payto.hidden = !st.pay_to_problem;
    if (st.server_time) clockSkew = st.server_time - Date.now() / 1000;

    const earned = $("earned");
    earned.textContent = usd(t.earned_usd);
    if (lastEarned !== null && t.earned_usd > lastEarned) {
      earned.classList.add("bump");
      setTimeout(() => earned.classList.remove("bump"), 250);
    }
    lastEarned = t.earned_usd;
  }

  function setOnline(ok) {
    $("pulse").classList.toggle("offline", !ok);
    $("pulse-text").textContent = ok ? "live" : "shop offline, retrying…";
  }

  async function getJson(url) {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error(`${url} -> HTTP ${r.status}`);
    return r.json();
  }

  async function tick() {
    let st, ev;
    try {
      [st, ev] = await Promise.all([getJson("/status"), getJson(`/events?limit=${EVENT_LIMIT}`)]);
    } catch (err) {
      // Only a failed/broken fetch means "offline".
      console.warn("Live page: shop unreachable:", err);
      setOnline(false);
      setTimeout(tick, POLL_MS);
      return;
    }
    try {
      setOnline(true);
      renderStatus(st); // first, so clockSkew is fresh for the EXPIRED check
      renderFeed(ev);
    } catch (err) {
      // A bug in OUR drawing code is not "shop offline": show it in the console.
      console.error("Live page: render error:", err);
    } finally {
      setTimeout(tick, POLL_MS); // schedule after finishing, so slow polls never pile up
    }
  }

  tick();
})();
