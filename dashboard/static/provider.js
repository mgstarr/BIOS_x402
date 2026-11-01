// Service provider page: TickToll's shelf of APIs (try and buy), incoming requests and invoices.

const STATUS = {
  paid: ["Paid", "ok"],
  declined: ["Declined", "bad"],
  "awaiting payment": ["Awaiting payment", "warn"],
  hijacked: ["Paid to another wallet", "bad"],
  verifying: ["Verifying", "warn"],
  requested: ["Requested", "plain"],
};
const PARAM_NAMES = { symbol: "Choose a coin", city: "Choose a city", ticker: "Choose a company",
                      pair: "Choose a currency pair", topic: "Choose a topic" };
const COINS = { BTC: "Bitcoin", ETH: "Ether", SOL: "Solana" };

let services = [], orders = [], selectedOrder = null;
let shelf = "All", current = null, choice = null;

// --- the shelf ---------------------------------------------------------------------------
function drawShelves() {
  const cats = ["All", ...new Set(services.map((s) => s.category))];
  $("shelves").innerHTML = cats.map((c) =>
    `<button class="chip ${c === shelf ? "on" : ""}" data-shelf="${esc(c)}">${esc(c)}</button>`).join("");
}

function drawCards() {
  const q = $("search").value.trim().toLowerCase();
  const shown = services.filter((s) => (shelf === "All" || s.category === shelf) &&
    (!q || [s.name, s.about, s.category, ...s.options].join(" ").toLowerCase().includes(q)));
  $("cards").innerHTML = shown.map((s) => `
    <button class="card ${current && current.path === s.path ? "on" : ""}" data-path="${esc(s.path)}">
      <span class="cat">${esc(s.category)}</span>
      <h3>${esc(s.name)}</h3>
      <p>${esc(s.about)}</p>
      <div class="foot"><span class="price">${money(s.price_usd)} <small>per answer</small></span>
        <span class="go">Try it</span></div>
    </button>`).join("") || '<p class="empty">Nothing matches that search.</p>';
}

$("shelves").addEventListener("click", (e) => {
  const b = e.target.closest("[data-shelf]");
  if (b) { shelf = b.dataset.shelf; drawShelves(); drawCards(); }
});
$("search").addEventListener("input", drawCards);
$("cards").addEventListener("click", (e) => {
  const b = e.target.closest("[data-path]");
  if (b) openTry(services.find((s) => s.path === b.dataset.path));
});

// --- try it ------------------------------------------------------------------------------
function progress(n) {
  document.querySelectorAll("#progress span").forEach((s) => s.classList.toggle("done", +s.dataset.s <= n));
}

function optionLabel(s, o) {
  return s.param === "symbol" ? `${COINS[o] || o} (${o})` : o;
}

function openTry(s) {
  current = s;
  choice = s.options.includes("ETH") ? "ETH" : s.options[0];
  $("try-panel").classList.remove("hidden");
  $("try-title").textContent = `${s.name} · ${money(s.price_usd)} per answer`;
  $("try-about").textContent = s.about;
  $("param-label").textContent = PARAM_NAMES[s.param] || "Choose";
  drawOptions();
  $("try-out").innerHTML = '<p class="muted">Pick an option, then see the bill or buy straight away.</p>';
  progress(1);
  drawCards();
  $("try-panel").scrollIntoView({ behavior: "smooth", block: "start" });
}

function drawOptions() {
  $("options").innerHTML = current.options.map((o) =>
    `<button class="chip ${o === choice ? "on" : ""}" data-opt="${esc(o)}">${esc(optionLabel(current, o))}</button>`).join("");
}

$("options").addEventListener("click", (e) => {
  const b = e.target.closest("[data-opt]");
  if (b) { choice = b.dataset.opt; drawOptions(); progress(1); }
});
$("try-close").onclick = () => { $("try-panel").classList.add("hidden"); current = null; drawCards(); };

function billHtml(b) {
  return `<div class="bill-card">
    <span class="label">The bill (HTTP 402, before any payment)</span>
    <h2 style="margin-top:4px">${money(b.amount_usd)} for ${esc(current.name)}</h2>
    <dl class="kv">
      <dt>Pay to</dt><dd>${esc(walletName(b.pay_to))} <span class="mono muted">${esc(shortAddr(b.pay_to))}</span></dd>
      <dt>Paid in</dt><dd>USDC (${esc(b.amount_units)} units, 6 decimals)</dd>
      <dt>Network</dt><dd>Base Sepolia (${esc(b.network)})</dd>
      <dt>Offer valid for</dt><dd>${b.valid_for_s} seconds</dd>
      <dt>Address asked</dt><dd class="mono">${esc(b.url)}</dd>
    </dl>
  </div>`;
}

$("see-bill").onclick = async () => {
  $("try-out").innerHTML = '<p class="muted">Asking TickToll for the price…</p>';
  const b = await api("bill", { path: current.path, value: choice });
  if (b.error) { $("try-out").innerHTML = `<p class="error-line">${esc(b.error)}</p>`; return; }
  progress(2);
  $("try-out").innerHTML = billHtml(b) +
    '<p class="small muted" style="margin-top:10px">Nothing has been paid yet. Press "Pay and get the data" to buy.</p>';
};

$("pay").onclick = async () => {
  const btn = $("pay");
  btn.disabled = true;
  btn.textContent = "Paying…";
  progress(3);
  $("try-out").innerHTML = '<p class="muted">Signing a USDC payment, asking the guard, settling on the chain…</p>';
  const r = await api("buy", { path: current.path, value: choice });
  btn.disabled = false;
  btn.textContent = "Pay and get the data";
  if (r.blocked) {
    $("try-out").innerHTML = `<div class="bill-card"><span class="label">Payment stopped by the guard</span>
      <h2 style="margin-top:4px">${esc(r.block_reason)}</h2>
      <p class="small muted" style="margin-top:6px">Nothing was paid. The guard's rules are on the Guard page.</p></div>`;
    return;
  }
  if (r.error) { $("try-out").innerHTML = `<p class="error-line">${esc(r.error)}</p>`; return; }
  progress(4);
  const p = r.payments[r.payments.length - 1];
  $("try-out").innerHTML = `<div class="data-view">${render(current.path, r.data)}</div>${p ? receiptHtml(p) : ""}`;
  poll();
};

function receiptHtml(p) {
  const c = p.chain || {};
  return `<div class="receipt"><span class="label">Receipt</span>
    <dl class="kv">
      <dt>Paid</dt><dd>${money(p.amount_usd)} USDC to ${esc(walletName(p.payTo))}</dd>
      <dt>Transaction</dt><dd class="mono">${esc(p.tx)}</dd>
      <dt>Block</dt><dd>${c.block ? `#${c.block.toLocaleString()}, ${c.success ? "confirmed" : "failed"}, gas ${c.gas_used.toLocaleString()}` : "–"}</dd>
    </dl></div>`;
}

// --- showing the data like a person would want to read it --------------------------------
const pct = (v) => `<span class="${v >= 0 ? "up" : "down"}">${v >= 0 ? "+" : ""}${v.toFixed(2)}%</span>`;
const big = (n) => n >= 1e12 ? (n / 1e12).toFixed(2) + " trillion" : n >= 1e9 ? (n / 1e9).toFixed(2) + " billion"
  : n >= 1e6 ? (n / 1e6).toFixed(1) + " million" : n.toLocaleString();
const usd = (n, cur = "USD") => (cur === "INR" ? "₹" : "$") + n.toLocaleString("en-US", { maximumFractionDigits: 2 });
const kv = (rows) => `<dl class="kv">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;

function render(path, d) {
  switch (path) {
    case "/api/price":
      return `<span class="label">${esc(d.name)} (${esc(d.symbol)})</span>
        <div class="data-hero"><span class="big">${usd(d.price_usd)}</span>${pct(d.change_24h_pct)} <span class="muted">in 24 hours</span></div>
        ${kv([["24h range", `${usd(d.low_24h_usd)} to ${usd(d.high_24h_usd)}`], ["Traded today", "$" + big(d.volume_24h_usd)],
              ["Market value", "$" + big(d.market_cap_usd)]])}`;
    case "/api/sentiment":
      return `<span class="label">Market mood for ${esc(d.symbol)}</span>
        <div class="data-hero"><span class="big" style="text-transform:capitalize">${esc(d.label)}</span>
          <span class="muted">score ${d.score} from -1 to 1</span></div>
        ${kv([["Positive", d.breakdown_pct.positive + "%"], ["Neutral", d.breakdown_pct.neutral + "%"],
              ["Negative", d.breakdown_pct.negative + "%"], ["Mentions today", d.mentions_24h.toLocaleString()],
              ["People talk about", d.top_topics.map(esc).join(", ")]])}`;
    case "/api/report":
      return `<span class="label">${esc(d.title)}</span>
        <div class="data-hero"><span class="big" style="font-size:1.5rem">Rating: ${esc(d.rating)}</span>
          <span class="muted">risk ${esc(d.risk)}</span></div>
        <p style="margin-top:10px">${esc(d.summary)}</p>
        <ul class="headlines">${(d.risks || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
    case "/api/weather":
      return `<span class="label">${esc(d.city)}, ${esc(d.country)}</span>
        <div class="data-hero"><span class="big">${d.temp_c}°C</span><b>${esc(d.condition)}</b>
          <span class="muted">feels like ${d.feels_like_c}°C</span></div>
        ${kv([["Humidity", d.humidity_pct + "%"], ["Wind", d.wind_kmh + " km/h"]])}
        <div class="forecast">${d.forecast.map(([day, cond, hi, lo]) =>
          `<div><b>${esc(day)}</b>${esc(cond)}<div class="muted">${hi}° / ${lo}°</div></div>`).join("")}</div>`;
    case "/api/temperature": {
      const lo = Math.min(...d.hourly_c) - 1, hi = Math.max(...d.hourly_c);
      return `<span class="label">${esc(d.city)} now</span>
        <div class="data-hero"><span class="big">${d.temp_c}°C</span><span class="muted">feels like ${d.feels_like_c}°C</span></div>
        <div class="label" style="margin-top:14px">Next 6 hours</div>
        <div class="sparkline">${d.hourly_c.map((t) =>
          `<div style="height:${12 + ((t - lo) / (hi - lo || 1)) * 40}px"><span>${t}°</span></div>`).join("")}</div>`;
    }
    case "/api/air":
      return `<span class="label">Air in ${esc(d.city)}</span>
        <div class="data-hero"><span class="big">${d.aqi}</span><b style="text-transform:capitalize">${esc(d.level)}</b>
          <span class="muted">air quality index</span></div>
        <div class="meter"><i style="left:${Math.min(d.aqi / 300, 1) * 100}%"></i></div>
        ${kv([["Fine dust (PM2.5)", d.pm25 + " µg/m³"], ["What it means", d.aqi <= 50 ? "Fine for everyone"
          : d.aqi <= 100 ? "Fine for most people" : "Limit time outdoors"]])}`;
    case "/api/stock":
      return `<span class="label">${esc(d.name)} · ${esc(d.exchange)}: ${esc(d.ticker)}</span>
        <div class="data-hero"><span class="big">${usd(d.price, d.currency)}</span>${pct(d.change_pct)} <span class="muted">today</span></div>
        ${kv([["Day range", `${usd(d.day_low, d.currency)} to ${usd(d.day_high, d.currency)}`],
              ["Shares traded", big(d.volume)], ["Company value", (d.currency === "INR" ? "₹" : "$") + big(d.market_cap)],
              ["Price / earnings", d.pe_ratio]])}`;
    case "/api/fx": {
      const [a, b] = d.pair.split("-");
      return `<span class="label">Exchange rate</span>
        <div class="data-hero"><span class="big">1 ${esc(a)} = ${d.rate} ${esc(b)}</span>${pct(d.change_pct)}</div>
        ${kv([["100 " + a, (100 * d.rate).toLocaleString("en-US", { maximumFractionDigits: 2 }) + " " + b],
              ["1 " + b, (1 / d.rate).toFixed(4) + " " + a]])}`;
    }
    case "/api/news":
      return `<span class="label">Top ${esc(d.topic)} headlines</span>
        <ul class="headlines">${d.headlines.map((h) => `<li>${esc(h)}</li>`).join("")}</ul>`;
    default:
      return kv(Object.entries(d).map(([k, v]) => [k, esc(JSON.stringify(v))]));
  }
}

// --- requests and invoices ---------------------------------------------------------------
function invoiceNo(o) {
  return "TT-" + o.id.slice(0, 6).toUpperCase();
}

function drawOrders() {
  if (!orders.length) {
    $("orders").innerHTML = '<tr><td colspan="5" class="empty">No requests yet. Try an API above.</td></tr>';
    return;
  }
  $("orders").innerHTML = orders.slice(0, 30).map((o) => {
    const unpaid = o.status === "awaiting payment" && Date.now() / 1000 - o.ts > 60;
    const [label, cls] = unpaid ? ["Not paid", "plain"] : (STATUS[o.status] || [o.status, "plain"]);
    let note = o.status === "declined" && o.reason ? `<div class="small muted">${esc(o.reason.slice(0, 60))}</div>` : "";
    if (o.status === "hijacked") note = `<div class="small muted">sent to ${esc(walletName(o.pay_to))}</div>`;
    return `<tr class="${o.status === "paid" ? "clickable" : ""} ${o.id === selectedOrder ? "selected" : ""}" data-id="${o.id}">
      <td class="time">${clock(o.ts)}</td>
      <td>${esc(o.product)} <span class="muted">${esc(o.coin)}</span></td>
      <td>${o.payer ? esc(walletName(o.payer)) : '<span class="muted">not paid yet</span>'}</td>
      <td class="num">${money(o.amount_usd)}</td>
      <td><span class="tag ${cls}">${label}</span>${note}</td>
    </tr>`;
  }).join("");
}

function drawInvoice() {
  const o = orders.find((x) => x.id === selectedOrder);
  if (!o) return;
  const when = new Date((o.paid_ts || o.ts) * 1000);
  $("invoice").innerHTML = `<div class="invoice">
    <div class="inv-top">
      <div><span class="label">Invoice</span><h2>${invoiceNo(o)}</h2></div>
      <div style="text-align:right"><span class="label">Date</span>
        <div>${when.toLocaleDateString()} ${when.toLocaleTimeString()}</div></div>
    </div>
    <dl>
      <dt>From</dt><dd>TickToll</dd>
      <dt>Billed to</dt><dd>${esc(walletName(o.payer))}<div class="mono muted">${esc(o.payer)}</div></dd>
      <dt>Paid with</dt><dd>USDC on Base Sepolia (local fork)</dd>
      <dt>Transaction</dt><dd class="mono">${esc(o.tx)}</dd>
    </dl>
    <table class="lines"><tbody>
      <tr><td>${esc(o.product)}, ${esc(o.coin)}<div class="small muted">1 request</div></td>
          <td class="num">${money(o.amount_usd)}</td></tr>
    </tbody></table>
    <div class="total"><span>Total paid</span><span>${dollars(o.amount_usd)}</span></div>
    <span class="stamp">Paid in full</span>
  </div>`;
}

$("orders").addEventListener("click", (e) => {
  const tr = e.target.closest("tr.clickable");
  if (!tr) return;
  selectedOrder = tr.dataset.id;
  drawOrders();
  drawInvoice();
});

async function poll() {
  const r = await api("provider");
  if (r.error) {
    $("orders").innerHTML = `<tr><td colspan="5" class="empty">Shop: ${esc(r.error)}</td></tr>`;
    return;
  }
  if (!services.length) { services = r.services; drawShelves(); drawCards(); }
  $("wallet").textContent = shortAddr(r.wallet);
  $("wallet").title = r.wallet;
  const t = r.totals || {};
  $("f-revenue").textContent = dollars(r.revenue_usd);
  $("f-paid").textContent = r.orders.filter((o) => o.status === "paid").length;
  $("f-requests").textContent = t.requests || 0;
  const hijacked = r.orders.filter((o) => o.status === "hijacked").length;
  $("f-declined").textContent = [t.failed ? `${t.failed} declined` : "", hijacked ? `${hijacked} paid elsewhere` : ""]
    .filter(Boolean).join(", ");
  $("f-balance").textContent = r.wallet_usd == null ? "–" : dollars(r.wallet_usd);
  $("mock-note").textContent = r.mock ? "Test mode: payments are simulated" : "";
  orders = r.orders;
  if (!selectedOrder) {
    const firstPaid = orders.find((o) => o.status === "paid");
    if (firstPaid) selectedOrder = firstPaid.id;
  }
  drawOrders();
  drawInvoice();
}

poll();
setInterval(poll, 2000);
