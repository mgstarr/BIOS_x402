// Defender page. Talks only to /defend/api/* (relative "api/..." because the page is /defend/).
const $ = (id) => document.getElementById(id);
const ROOT = $("defend").dataset;
const DECISIONS = { blocked: "BLOCKED", allowed: "ALLOWED", paid_unprotected: "PAID (guard OFF)" };

// The 8 checks, in the guard's own order. id -> icon, chip name, plain explanation.
const CHECKS = [
  ["origin", "🌐", "origin", "Right site", "The bill comes from the website the AI meant to visit."],
  ["asset", "🪙", "token", "Real money", "Paid in real USDC, not a made-up token."],
  ["payee", "👤", "receiver", "Right receiver", "Money goes to the shop's known wallet, not a lookalike."],
  ["price", "🏷️", "price", "Fair price", "No more than 3× what the item normally costs."],
  ["mandate", "📋", "mandate", "On task", "The owner's task allows this purchase and its budget."],
  ["cap", "🔒", "cap", "Under the cap", "No single payment over 50¢."],
  ["expiry", "⏱️", "expiry", "Expires soon", "The permission to pay runs out within 5 minutes."],
  ["duplicate", "🔁", "duplicate", "Not a repeat", "We haven't just paid for this exact item."],
];
const CHECK_BY_ID = Object.fromEntries(CHECKS.map((c) => [c[0], c]));
const NAME_TO_ID = { "spending cap": "cap" };

// Plain names for shop paths ("/evil/report" -> "Analyst report").
const ITEMS = {
  "/api/price": "Coin price", "/api/sentiment": "Mood score", "/api/report": "Analyst report",
  "/evil/overcharge": "Coin price", "/evil/drain": "Coin price", "/evil/fake-token": "Coin price",
  "/evil/long-expiry": "Coin price", "/evil/pay-again": "Coin price", "/evil/news": "News",
  "/evil/report": "Analyst report", "/evil/custom": "Custom bill",
  "/compromised/wrong-wallet": "Coin price", "/compromised/lookalike": "Coin price",
};

// Fallback names for the progress line when the attacker engine's list isn't available.
let ATTACKS = [
  ["overcharge", "Overcharge"], ["drain", "Drain"], ["wrong-wallet", "Wrong wallet"],
  ["lookalike", "Lookalike wallet"], ["fake-token", "Fake token"], ["long-expiry", "Long expiry"],
  ["pay-again", "Pay again"], ["injection", "Prompt injection"],
  ["honest-price", "Honest price"], ["honest-sentiment", "Honest sentiment"],
].map(([id, name]) => ({ id, name, url: null }));

const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const short = (a) => (a && a.length > 12 ? a.slice(0, 6) + "…" + a.slice(-4) : a || "?");
const shortAddrs = (s) => String(s || "").replace(/0x[0-9a-fA-F]{40}/g, short);

// 0.01 -> "1¢", 0.95 -> "95¢", 92.68 -> "$92.68"
function money(usd) {
  const n = Number(usd || 0);
  if (n >= 1) return "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return parseFloat((n * 100).toFixed(2)) + "¢";
}

async function api(path, opts, timeoutMs = 10000) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetch("api/" + path, { ...opts, signal: ctl.signal });
    return await r.json();
  } catch (e) {
    return { error: e.name === "AbortError" ? `no answer after ${timeoutMs / 1000} s` : String(e) };
  } finally {
    clearTimeout(timer);
  }
}
const post = (path, body, t) => api(path, { method: "POST", headers: { "Content-Type": "application/json" },
                                            body: JSON.stringify(body || {}) }, t);
const showSim = (x) => { if (x && x.simulated) $("sim").hidden = false; };

// ---------- 1) guard switch + reset ----------
function setSwitch(on) {
  const b = $("switch");
  b.className = "guard " + (on ? "on" : "off");
  b.setAttribute("aria-pressed", on);
  $("switch-label").textContent = on ? "Guard ON" : "Guard OFF";
  $("switch-sub").textContent = on ? "Every payment is checked" : "Payments go through unchecked";
}
async function syncMode() {
  const m = await api("mode");
  if (!m.error) setSwitch(m.enabled);
  showSim(m);
}
async function toggle() {
  const b = $("switch");
  b.disabled = true;
  const r = await post("mode", { enabled: !b.classList.contains("on") });
  if (!r.error) setSwitch(r.enabled);
  b.disabled = suiteRunning;
}
async function reset() {
  const b = $("reset");
  b.disabled = true;
  b.textContent = "Resetting…";
  await post("reset");
  $("feed").replaceChildren();
  await Promise.all([pollFeed(), pollStats(), syncMode()]);
  b.disabled = false;
  b.textContent = "Reset";
}

// ---------- 2) Guard ON vs Guard OFF ----------
function fact(value, label, cls) {
  return `<div class="fact"><div class="v ${cls}">${esc(value)}</div><div class="k">${esc(label)}</div></div>`;
}
function side(title, cls, s, total) {
  const blockedCls = s.blocked === total ? "good" : s.blocked === 0 ? "bad" : "warn";
  const honestCls = s.honest_allowed === s.honest_total ? "good" : "bad";
  return `<div class="side ${cls}"><h3>${cls === "on" ? "🛡️" : "⚠️"} ${title}</h3><div class="facts">
    ${fact(money(s.lost_usd), "money lost", s.lost_usd > 0 ? "bad" : "good")}
    ${fact(`${s.blocked}/${total}`, "attacks blocked", blockedCls)}
    ${fact(`${s.honest_allowed}/${s.honest_total}`, "honest payments allowed", honestCls)}
  </div></div>`;
}
function renderScore(r) {
  if (!r || !r.guard_on) return;
  const on = r.guard_on, off = r.guard_off;
  const results = on.results || [];
  const total = results.length ? results.filter((x) => !String(x.attack_id).startsWith("honest")).length : 8;
  const max = Math.max(on.lost_usd || 0, off.lost_usd || 0) || 1;
  const notes = [];
  if (off.no_payment) notes.push(`With the guard OFF, ${off.no_payment} attack(s) moved no real money (e.g. paid in a fake token).`);
  const errs = (on.errors || 0) + (off.errors || 0);
  if (errs) notes.push(`${errs} run(s) hit an error.`);
  if (r.simulated) notes.push("No blockchain running: money moves are simulated.");
  if (r.ts) notes.push("Last run " + new Date(r.ts * 1000).toLocaleTimeString() + ".");
  $("score").innerHTML = `<div class="sides">${side("Guard ON", "on", on, total)}${side("Guard OFF", "off", off, total)}</div>
    <div class="bars">
      <span class="cap">Money lost to attackers</span>
      <span class="bl" style="color:var(--green)">Guard ON</span><div class="track"><div class="fill on" data-w="${(on.lost_usd || 0) / max * 100}"></div></div><span class="bv">${money(on.lost_usd)}</span>
      <span class="bl" style="color:var(--red)">Guard OFF</span><div class="track"><div class="fill off" data-w="${(off.lost_usd || 0) / max * 100}"></div></div><span class="bv">${money(off.lost_usd)}</span>
    </div>${notes.length ? `<p class="note">${esc(notes.join(" "))}</p>` : ""}`;
  requestAnimationFrame(() => requestAnimationFrame(() =>
    document.querySelectorAll(".bars .fill").forEach((f) => { f.style.width = f.dataset.w + "%"; })));
  showSim(r);
}

let suiteRunning = false;
let suiteSince = 0;
function attackFor(e) {
  const ctx = (e.data || {}).context || {};
  const url = ctx.intended_url || (e.data || {}).resource;
  return ATTACKS.findIndex((a) => a.url && a.url === url);
}
function progressFrom(events) {
  const fresh = events.filter((e) => e.type in DECISIONS && e.ts > suiteSince);
  const nAtk = ATTACKS.filter((a) => !a.id.startsWith("honest")).length;
  const nAll = ATTACKS.length;
  let text = "Guard ON run · starting…", step = 0;
  for (let i = fresh.length - 1; i >= 0; i--) {
    const idx = attackFor(fresh[i]);
    if (idx < 0) continue;
    const off = fresh[i].type === "paid_unprotected";
    const a = ATTACKS[idx];
    const what = a.id.startsWith("honest") ? `honest purchase ${idx - nAtk + 1} of ${nAll - nAtk}`
                                           : `attack ${idx + 1} of ${nAtk}`;
    text = `Guard ${off ? "OFF" : "ON"} run · Running ${what}: ${a.name}…`;
    step = (off ? nAll : 0) + idx + 1;
    break;
  }
  $("ptext").textContent = text;
  $("pfill").style.width = (step / (2 * nAll)) * 100 + "%";
}

async function runSuite() {
  const btn = $("run");
  suiteRunning = true;
  btn.disabled = true; $("switch").disabled = true; $("reset").disabled = true;
  btn.textContent = "Running…";
  suiteSince = lastEventTs;
  $("progress").hidden = false;
  $("ptext").textContent = "Guard ON run · starting…";
  $("pfill").style.width = "0";
  let r;
  try {
    r = await post("suite", {}, 150000);
  } finally {
    suiteRunning = false;
    btn.disabled = false; $("switch").disabled = false; $("reset").disabled = false;
    btn.textContent = "Run full test";
    $("progress").hidden = true;
  }
  if (r.error) {
    $("score").innerHTML = `<p class="muted empty">The test could not finish: ${esc(r.error)}</p>`;
  } else {
    r.ts = r.ts || Date.now() / 1000;
    renderScore(r);
  }
  syncMode();
}

// ---------- 3) stat tiles ----------
async function pollStats() {
  const s = await api("stats");
  if (s.error) return;
  $("t-checked").textContent = s.checked ?? 0;
  $("t-blocked").textContent = s.blocked ?? 0;
  $("t-allowed").textContent = s.allowed ?? 0;
  $("t-unprotected").textContent = s.unprotected ?? 0;
  $("t-saved").textContent = money(s.saved_usd ?? 0);
  if (typeof s.enabled === "boolean" && !$("switch").disabled) setSwitch(s.enabled);
  showSim(s);
}

// ---------- 4) live feed ----------
function itemName(path) {
  const p = String(path || "").split("?")[0];
  if (ITEMS[p]) return ITEMS[p];
  const last = p.split("/").filter(Boolean).pop() || "item";
  return last.replace(/[-_]/g, " ").replace(/^./, (c) => c.toUpperCase());
}
function payeeName(addr) {
  const a = String(addr || "").toLowerCase();
  if (a === ROOT.ticktoll.toLowerCase()) return "TickToll";
  if (a === ROOT.hacker.toLowerCase()) return "Hacker " + short(addr);
  if (a === ROOT.lookalike.toLowerCase()) return "Lookalike " + short(addr);
  return short(addr);
}
function normChecks(d) {
  const byId = {};
  (d.checks || []).forEach((c) => { byId[c.id || NAME_TO_ID[c.name] || c.name] = c; });
  return CHECKS.map(([id]) => byId[id] || null);
}
function headline(e, d, checks) {
  const failed = checks.filter((c) => c && !c.ok);
  const reason = shortAddrs(d.reason);
  if (e.type === "blocked") return reason || "Blocked by the guard";
  if (e.type === "allowed") return failed.length ? `⚠ ${failed.length} check(s) failed` : "All 8 checks passed";
  return failed.length ? `Guard would have blocked: ${shortAddrs(failed[0].reason)}` : "Looked fine: all 8 checks pass";
}
function eventKey(e) {
  const d = e.data || {};
  return `${e.ts}|${e.type}|${d.resource || d.item || ""}`;
}
function card(e) {
  const d = e.data || {};
  const checks = normChecks(d);
  const amount = d.amount_usd ?? (d.amount_cents != null ? d.amount_cents / 100 : 0);
  const head = headline(e, d, checks);
  const chips = CHECKS.map(([id, icon, chip], i) => {
    const c = checks[i];
    if (!c) return `<span class="ck skip">${icon} ${chip} ?</span>`;
    const skipped = c.ok && /^skipped/i.test(c.reason || "");
    const cls = !c.ok ? "bad" : skipped ? "skip" : "ok";
    return `<span class="ck ${cls}" title="${esc(shortAddrs(c.reason))}">${c.ok ? "✓" : "✗"} ${icon} ${chip}</span>`;
  }).join("");
  const fails = checks.filter((c) => c && !c.ok)
    .map((c) => `<li><b>${esc(CHECK_BY_ID[c.id]?.[3] || c.name)}:</b> ${esc(shortAddrs(c.reason))}</li>`).join("");
  const el = document.createElement("div");
  el.className = "ev " + e.type;
  el.dataset.key = eventKey(e);
  el.innerHTML = `<div class="line">
      <span class="pill ${e.type}">${DECISIONS[e.type]}</span>
      <span class="what"><b>${esc(d.shop || "Shop")}</b> · ${esc(itemName(d.item || d.resource))}</span>
      <span class="amt">${money(amount)}</span>
      <span class="who">to ${esc(payeeName(d.payee))}</span>
      <span class="why" title="${esc(head)}">${esc(head)}</span>
      <span class="chev">›</span>
    </div>
    <div class="detail">
      <p class="full">${esc(head)} <span class="muted">· ${new Date(e.ts * 1000).toLocaleTimeString()}</span></p>
      <div class="chips">${chips}</div>${fails ? `<ul class="fails">${fails}</ul>` : ""}
    </div>`;
  return el;
}

let lastEventTs = 0;
let firstFeed = true;
async function pollFeed() {
  const ev = await api("events?limit=200");
  if (!Array.isArray(ev)) return;
  if (ev.length) lastEventTs = Math.max(lastEventTs, ...ev.map((e) => e.ts || 0));
  if (ev.some((e) => e.simulated)) $("sim").hidden = false;
  if (suiteRunning) progressFrom(ev);

  const items = ev.filter((e) => e.type in DECISIONS).slice(-60).reverse();  // newest first
  const feed = $("feed");
  const want = new Set(items.map(eventKey));
  [...feed.children].forEach((c) => { if (!want.has(c.dataset.key)) c.remove(); });
  const have = new Set([...feed.children].map((c) => c.dataset.key));
  const fresh = items.filter((e) => !have.has(eventKey(e))).map((e) => {
    const c = card(e);
    if (!firstFeed) c.classList.add("enter");
    return c;
  });
  if (fresh.length) feed.prepend(...fresh);
  firstFeed = false;
  $("empty").hidden = items.length > 0;
}

// ---------- 5) filters, 6) legend ----------
function setFilter(btn) {
  document.querySelectorAll(".filters .chip").forEach((b) => b.classList.toggle("on", b === btn));
  $("feed").dataset.filter = btn.dataset.filter;
}
$("legend").innerHTML = CHECKS.map(([, icon, , label, text]) =>
  `<li><span class="ic">${icon}</span><span><b>${label}</b> · ${text}</span></li>`).join("");

// ---------- wiring ----------
$("switch").addEventListener("click", toggle);
$("reset").addEventListener("click", reset);
$("run").addEventListener("click", runSuite);
document.querySelector(".filters").addEventListener("click", (e) => {
  const b = e.target.closest(".chip");
  if (b) setFilter(b);
});
$("feed").addEventListener("click", (e) => {
  const c = e.target.closest(".ev");
  if (c && !window.getSelection().toString()) c.classList.toggle("open");
});

api("attacks").then((list) => {
  if (Array.isArray(list) && list.length) ATTACKS = list.map((a) => ({ id: a.id, name: a.name, url: a.url }));
});
api("suite/last").then(renderScore);
syncMode();
pollFeed(); pollStats();
setInterval(pollFeed, 1000);
setInterval(pollStats, 1000);
