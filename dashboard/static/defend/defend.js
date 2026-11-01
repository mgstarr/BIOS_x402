const $ = (id) => document.getElementById(id);
const short = (a) => (a && a.length > 12 ? a.slice(0, 6) + "..." + a.slice(-4) : a || "?");

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

function setSwitch(on) {
  $("switch").className = "switch " + (on ? "on" : "off");
  $("switch-label").textContent = on ? "Guard ON" : "Guard OFF";
}

async function toggle() {
  const on = $("switch").classList.contains("on");
  const r = await api("mode", { method: "POST", headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({ enabled: !on }) });
  if (!r.error) setSwitch(r.enabled);
}

function cents(d) {
  if (d.amount_cents != null) return d.amount_cents;
  if (d.amount_usd != null) return Math.round(d.amount_usd * 100);
  return 0;
}

function row(e) {
  const d = e.data || {};
  const el = document.createElement("div");
  el.className = "row";
  const checks = (d.checks || []).map((c) =>
    `<span class="${c.ok ? "pass" : "fail"}">${c.ok ? "✓" : "✗"} ${c.name}: ${c.reason || ""}</span>`).join("");
  el.innerHTML = `<div class="main"><span>${d.shop || "shop"}</span><span class="grow">${d.item || ""}</span>
    <span>${cents(d)}c</span><span>${short(d.payee || d.payTo)}</span>
    <span class="pill ${e.type}">${e.type.toUpperCase()}</span></div><div class="checks">${checks}</div>`;
  return el;
}

async function pollFeed() {
  const ev = await api("events");
  if (!Array.isArray(ev)) return;
  const items = ev.filter((e) => e.type === "allowed" || e.type === "blocked").slice(-15).reverse();
  const feed = $("feed");
  feed.replaceChildren(...items.map(row));
  if (!items.length) feed.innerHTML = '<div class="empty">Waiting for payments...</div>';
  if (ev.some((e) => e.simulated)) $("sim").hidden = false;
}

async function pollStats() {
  const s = await api("stats");
  if (s.error) return;
  $("t-checked").textContent = s.checked ?? 0;
  $("t-blocked").textContent = s.blocked ?? 0;
  $("t-allowed").textContent = s.allowed ?? 0;
  $("t-saved").textContent = "$" + Number(s.saved_usd ?? s.money_saved ?? 0).toFixed(2);
  if (s.simulated) $("sim").hidden = false;
}

function card(title, cls, s) {
  return `<div class="card ${cls}"><h3>${title}</h3>
    <div>Attacks blocked: ${s.blocked}</div><div>Attacks stolen: ${s.stolen ?? 0}</div>
    <div>Money lost: $${Number(s.lost_usd).toFixed(2)}</div>${s.errors ? `<div>Errors: ${s.errors}</div>` : ""}
    <div>Honest allowed: ${s.honest_allowed}/${s.honest_total ?? s.honest_allowed}</div></div>`;
}

async function runSuite() {
  const btn = $("run");
  btn.disabled = true;
  btn.textContent = "Running...";
  let r;
  try {
    r = await api("suite", { method: "POST" }, 150000);
  } finally {
    btn.disabled = false;
    btn.textContent = "Run full test";
  }
  if (r.error) { $("score").innerHTML = `<div class="empty">Suite failed: ${r.error}</div>`; return; }
  $("score").innerHTML = card("Guard ON", "on", r.guard_on) + card("Guard OFF", "off", r.guard_off);
  if (r.simulated) $("sim").hidden = false;
  const m = await api("mode");
  if (!m.error) setSwitch(m.enabled);
}

$("switch").addEventListener("click", toggle);
$("run").addEventListener("click", runSuite);
api("mode").then((m) => { if (!m.error) setSwitch(m.enabled); });
setInterval(pollFeed, 1000);
setInterval(pollStats, 1000);
pollFeed(); pollStats();
