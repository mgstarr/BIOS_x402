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
