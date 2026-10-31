// Small helpers shared by every page. Plain JS, no libraries.

// x402 headers are base64 of JSON. These convert both ways (UTF-8 safe).
function b64ToJson(b64) {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes));
}
function jsonToB64(obj) {
  const bytes = new TextEncoder().encode(JSON.stringify(obj));
  let bin = "";
  bytes.forEach((b) => { bin += String.fromCharCode(b); });
  return btoa(bin);
}

// 0x1234567890abcdef... -> 0x1234…abcd
function shortHex(value) {
  if (!value) return "—";
  return value.length > 12 ? value.slice(0, 6) + "…" + value.slice(-4) : value;
}

function usd(n, digits = 2) {
  return "$" + Number(n || 0).toFixed(digits);
}

function randomHex(bytes) {
  const arr = new Uint8Array(bytes);
  crypto.getRandomValues(arr);
  return "0x" + Array.from(arr, (b) => b.toString(16).padStart(2, "0")).join("");
}

function pretty(obj) {
  return JSON.stringify(obj, null, 2);
}

// ---- Plain-English helpers for the website (names come from window.SITE, set in base.html) ----

const SITE = window.SITE || { products: {}, coins: {} };

function productName(path) {
  return (SITE.products[path] || {}).name || "Data";
}
function coinName(symbol) {
  return SITE.coins[symbol] || symbol || "";
}
function coinLabel(symbol) {
  return symbol ? `${coinName(symbol)} (${symbol})` : "";
}

// Dollars -> cents text: 0.01 -> "1¢", 0.000001 -> "0.0001¢".
function cents(dollars) {
  const c = Number(dollars || 0) * 100;
  if (Number.isInteger(Math.round(c * 1e6) / 1e6) && c >= 1) return `${Math.round(c)}¢`;
  return `${parseFloat(c.toPrecision(3))}¢`;
}
// USDC "atomic units" (1 USDC = 1,000,000) -> cents text.
function atomicCents(atomic) {
  return cents(Number(atomic) / 1e6);
}

// Why a payment was refused, in plain English. short = for tables; sentence = after "Refused ✗:".
const REASONS = {
  amount_too_low: ["Paid less than the bill", "the payment was less than the bill"],
  authorization_wrong_recipient: ["Tried to pay a different wallet", "the money was going to a different wallet, not TickToll"],
  authorization_mismatch: ["Signed amount didn't match the bill", "the signed amount didn't match the bill"],
  amount_mismatch: ["Amount didn't match the bill", "the amount didn't match the bill"],
  pay_to_mismatch: ["Wrong receiver", "the payment was addressed to someone else"],
  asset_mismatch: ["Wrong currency", "it was paid in the wrong currency"],
  wrong_scheme_or_network: ["Wrong payment network", "it used the wrong payment network"],
  missing_signature: ["Not signed", "the payment wasn't signed"],
  malformed_authorization: ["Unreadable payment", "the payment slip couldn't be read"],
  malformed_payment_signature: ["Unreadable payment", "the payment slip couldn't be read"],
  unsupported_x402_version: ["Unsupported payment format", "it used a payment format we don't support"],
  invalid_signature: ["Signature not valid", "the signature wasn't valid"],
  insufficient_funds: ["Not enough money in wallet", "the wallet didn't have enough money"],
};
function reasonText(code, form = "short") {
  if (!code) return "";
  if (String(code).startsWith("facilitator_error")) {
    return form === "short" ? "Payment checker unreachable" : "the payment checker could not be reached";
  }
  const r = REASONS[code];
  if (r) return form === "short" ? r[0] : r[1];
  return form === "short" ? "Payment refused" : "the payment was not accepted";
}

// Tiny DOM builder: el("p", "muted", "text") -> <p class="muted">text</p>. Always uses
// textContent, so data can never inject HTML.
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

const money = (n) => Number(n).toLocaleString("en-US", { style: "currency", currency: "USD" });
const MOOD = { bullish: "Mostly positive", neutral: "Mixed: neither positive nor negative", bearish: "Mostly negative" };
const RATING = { accumulate: "Buy gradually", hold: "Hold (wait and see)", reduce: "Sell some" };

// The data the AI bought, as a friendly card (not JSON).
function dataCard(path, symbol, data) {
  const card = el("div", "data-card");
  const name = coinName(symbol);
  card.appendChild(el("div", "dc-label", `${productName(path)} · ${coinLabel(symbol)}`));

  if (path === "/api/price") {
    card.appendChild(el("div", "dc-big", `${name}: ${money(data.price_usd)}`));
    const line = el("p", "dc-line");
    const ch = Number(data.change_24h_pct);
    line.appendChild(el("span", ch >= 0 ? "up" : "down",
      `${ch >= 0 ? "up" : "down"} ${Math.abs(ch).toFixed(1)}% today`));
    line.append(` · today's range ${money(data.low_24h_usd)} to ${money(data.high_24h_usd)}`);
    card.appendChild(line);
  } else if (path === "/api/sentiment") {
    card.appendChild(el("div", "dc-big", `${name}: ${MOOD[data.label] || data.label}`));
    const b = data.breakdown_pct || {};
    const bar = el("div", "mood-bar");
    [["pos", b.positive], ["neu", b.neutral], ["neg", b.negative]].forEach(([c, v]) => {
      const s = el("span", c); s.style.width = `${v || 0}%`; bar.appendChild(s);
    });
    card.appendChild(bar);
    const lg = el("div", "mood-legend");
    [["var(--accent)", "positive", b.positive], ["var(--grey)", "neutral", b.neutral], ["var(--bad)", "negative", b.negative]]
      .forEach(([col, label, v]) => {
        const item = el("span"); const dot = el("i"); dot.style.background = col;
        item.append(dot, `${Math.round(v || 0)}% ${label}`); lg.appendChild(item);
      });
    card.appendChild(lg);
    card.appendChild(el("p", "dc-line",
      `${Number(data.mentions_24h || 0).toLocaleString("en-US")} posts in the last 24 hours. Talked about: ${(data.top_topics || []).join(", ")}.`));
  } else if (path === "/api/report") {
    card.appendChild(el("div", "dc-big", data.title || name));
    card.appendChild(el("p", "dc-line", data.summary || ""));
    card.appendChild(el("p", "dc-line",
      `Analyst view: ${RATING[data.rating] || data.rating} · Risk: ${data.risk}`));
    if (data.risks && data.risks.length) {
      const ul = el("ul");
      data.risks.forEach((r) => ul.appendChild(el("li", null, `Risk: ${r}`)));
      card.appendChild(ul);
    }
  } else {
    card.appendChild(el("pre", null, pretty(data)));
  }
  return card;
}

// A "Show technical details" toggle (closed by default) holding labelled JSON blocks.
// blocks = [[title, objectOrString], ...]
function techDetails(blocks) {
  const d = el("details", "tech");
  d.appendChild(el("summary", null, "Show technical details"));
  const body = el("div", "tech-body");
  blocks.forEach(([title, value]) => {
    if (value == null) return;
    body.appendChild(el("h4", null, title));
    body.appendChild(el("pre", typeof value === "string" ? "b64" : null,
      typeof value === "string" ? value : pretty(value)));
  });
  d.appendChild(body);
  return d;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function postJson(url, body) {
  const resp = await fetch(url, {
    method: "POST", cache: "no-store",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
  return data;
}
