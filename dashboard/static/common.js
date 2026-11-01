// Helpers shared by every page, and the status strip in the top bar.

const $ = (id) => document.getElementById(id);

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  try {
    const r = await fetch("/api/" + path, opts);
    return await r.json();
  } catch (e) {
    return { error: "dashboard not reachable" };
  }
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// 0.01 -> "1¢", 0.955 -> "95.5¢", 1.5 -> "$1.50"
function money(usd) {
  if (usd == null) return "–";
  if (Math.abs(usd) >= 1) return "$" + usd.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return (+(usd * 100).toFixed(2)) + "¢";
}

function dollars(usd) {
  if (usd == null) return "–";
  return "$" + usd.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function shortAddr(a) {
  return a && a.length > 14 ? a.slice(0, 6) + "…" + a.slice(-4) : (a || "–");
}

function clock(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function ago(ts) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return s + " s ago";
  if (s < 3600) return Math.round(s / 60) + " min ago";
  return Math.round(s / 3600) + " h ago";
}

// Names for the wallets in this demo, so pages say "TickToll" instead of 0x3C44…
const WALLETS = {
  "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc": "TickToll",
  "0x90f79bf6eb2c4f870365e785982e1f101e93b906": "Attacker",
  "0x3c44cddd1b2c3d4e5f60718293a4b5c6d74293bc": "Lookalike of TickToll",
  "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266": "AI agent",
};
function walletName(a) {
  return WALLETS[(a || "").toLowerCase()] || shortAddr(a);
}

function shopName(url) {
  if (!url) return "–";
  if (url.includes(":5001")) return "TickToll";
  if (url.includes(":5002")) return "EvilData";
  return url.replace(/^https?:\/\//, "").split("/")[0];
}

// "Get the price from http://localhost:5002/evil/x?symbol=ETH" -> "Get the price from EvilData /evil/x"
function tidyUrls(text) {
  return String(text || "").replace(/https?:\/\/[^\s/]+(\/[^\s?]*)?(\?\S*)?/g,
    (m, path) => shopName(m) + (path && path !== "/" ? " " + path : ""));
}

async function refreshStatus() {
  const s = await api("status");
  const g = $("st-guard");
  if (s.guard == null) { g.textContent = "offline"; g.className = ""; }
  else { g.textContent = s.guard ? "on" : "off"; g.className = s.guard ? "on" : "off"; }
  $("st-agent").textContent = s.agent_usd == null ? "–" : dollars(s.agent_usd);
  if (window.onStatus) window.onStatus(s);
}
refreshStatus();
setInterval(refreshStatus, 3000);

// --- before vs after: the headline number for a demo ---------------------------------------
// raceSummary(el, rounds): round 1 vs the latest round, for attacker success and guard accuracy.
// Survives a service restart (the rounds_log itself is persisted server-side), so this keeps
// telling the true story even if the stack gets restarted right before showing it to someone.
function raceSummary(el, rounds) {
  if (rounds.length < 2) { el.innerHTML = ""; return; }
  const first = rounds[0], last = rounds[rounds.length - 1];
  const tile = (label, a, b, lowerIsBetter) => {
    const pct = (v) => (v == null ? "–" : Math.round(v * 100) + "%");
    const improved = a != null && b != null && (lowerIsBetter ? b < a : b > a);
    return `<div class="figure"><div class="label">${esc(label)}</div>
      <div class="value">${pct(a)} <span class="muted" style="font-size:.6em">&rarr;</span>
        <span class="${improved ? "gold-text" : ""}">${pct(b)}</span></div>
      <div class="sub">round 1 vs round ${last.round}, ${rounds.length} rounds played</div></div>`;
  };
  el.innerHTML = tile("Attacker success rate", first.attacker_success_rate, last.attacker_success_rate, true)
    + tile("Guard accuracy", first.guard_accuracy, last.guard_accuracy, false);
}

// --- the learning race chart -------------------------------------------------------------
// lineChart(el, rounds, series): one 0-100% axis, x = training round. series: [{name, cls, values}]
// (values 0..1 or null). Thin lines, markers, a legend, a direct label at each line's end, and a
// hover crosshair with the exact numbers for that round. A table view sits under it for screen readers.
function lineChart(el, rounds, series) {
  if (!rounds.length) { el.innerHTML = '<p class="empty">No training yet. Start a round above.</p>'; return; }
  const W = 560, H = 220, L = 40, R = 110, T = 14, B = 30;
  const x = (i) => L + (rounds.length === 1 ? (W - L - R) / 2 : (i * (W - L - R)) / (rounds.length - 1));
  const y = (v) => T + (1 - v) * (H - T - B);
  const grid = [0, 0.25, 0.5, 0.75, 1].map((v) =>
    `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text class="axis" x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${v * 100}%</text>`).join("");
  const ticks = rounds.map((r, i) => (rounds.length <= 12 || i % Math.ceil(rounds.length / 12) === 0)
    ? `<text class="axis" x="${x(i)}" y="${H - 10}" text-anchor="middle">${r}</text>` : "").join("");
  const lines = series.map((s) => {
    const pts = s.values.map((v, i) => (v == null ? null : [x(i), y(v)])).filter(Boolean);
    const last = pts[pts.length - 1], lastV = [...s.values].reverse().find((v) => v != null);
    return `<g class="series ${s.cls}"><polyline points="${pts.map((p) => p.join(",")).join(" ")}"/>
      ${pts.map((p) => `<circle cx="${p[0]}" cy="${p[1]}" r="4"/>`).join("")}
      ${last ? `<text class="end-label" x="${last[0] + 8}" y="${last[1] + 4}">${esc(s.name)} ${Math.round(lastV * 100)}%</text>` : ""}</g>`;
  }).join("");
  el.innerHTML = `<div class="legend">${series.map((s) => `<span class="key ${s.cls}"><i></i>${esc(s.name)}</span>`).join("")}</div>
    <div class="chart-wrap"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(series.map((s) => s.name).join(" and "))} by training round">
      ${grid}${ticks}<text class="axis" x="${(W - R + L) / 2}" y="${H}" text-anchor="middle">training round</text>
      ${lines}<line class="crosshair" y1="${T}" y2="${H - B}" style="display:none"/></svg>
      <div class="tip" style="display:none"></div></div>
    <table class="sr-only"><thead><tr><th>Round</th>${series.map((s) => `<th>${esc(s.name)}</th>`).join("")}</tr></thead>
      <tbody>${rounds.map((r, i) => `<tr><td>${r}</td>${series.map((s) => `<td>${s.values[i] == null ? "–" : Math.round(s.values[i] * 100) + "%"}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  const svg = el.querySelector("svg"), tip = el.querySelector(".tip"), cross = el.querySelector(".crosshair");
  svg.addEventListener("mousemove", (e) => {
    const box = svg.getBoundingClientRect(), sx = ((e.clientX - box.left) / box.width) * W;
    let i = 0;
    rounds.forEach((_, j) => { if (Math.abs(x(j) - sx) < Math.abs(x(i) - sx)) i = j; });
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.style.display = "";
    tip.style.display = "";
    tip.style.left = `${(x(i) / W) * 100}%`;
    tip.innerHTML = `<b>Round ${rounds[i]}</b>` + series.map((s) =>
      `<div><span class="key ${s.cls}"><i></i>${esc(s.name)}</span> ${s.values[i] == null ? "–" : Math.round(s.values[i] * 100) + "%"}</div>`).join("");
  });
  svg.addEventListener("mouseleave", () => { tip.style.display = "none"; cross.style.display = "none"; });
}

// Plain names for the attacker's tactic ids, e.g. "look:7/5" -> "Look-alike wallet, 7+5 characters".
function tacticName(t) {
  const [kind, arg] = t.split(":");
  const names = { cat: () => ({ overcharge: "Overcharge", drain: "Big drain", "pay-again": "Pay me twice", "long-expiry": "Forever cheque",
    "wrong-wallet": "Inside job", lookalike: "Lookalike (catalogue)", "fake-token": "Fake money", injection: "Mind trick" }[arg] || arg),
    price: () => `New shop at ${money(+arg)}`, expiry: () => `Cheque lasting ${arg} s`, look: () => `Look-alike wallet, ${arg.replace("/", "+")} chars`,
    fresh: () => `Fresh wallet at ${money(+arg)}`, inject: () => `Hidden order, ${money(+arg)} item`, injshop: () => `Hidden order to a new shop` };
  return (names[kind] || (() => t))();
}
