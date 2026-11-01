// Service provider page: TickToll's services, incoming requests and invoices.

const STATUS = {
  paid: ["Paid", "ok"],
  declined: ["Declined", "bad"],
  "awaiting payment": ["Awaiting payment", "warn"],
  hijacked: ["Paid to another wallet", "bad"],
  verifying: ["Verifying", "warn"],
  requested: ["Requested", "plain"],
};
let orders = [];
let selected = null;
let servicesDrawn = false;

function invoiceNo(o) {
  return "TT-" + o.id.slice(0, 6).toUpperCase();
}

function drawServices(services) {
  $("services").innerHTML = services.map((s) => `
    <div class="service">
      <h3>${esc(s.name)}</h3>
      <p>${esc(s.about)}</p>
      <div class="price">${money(s.price_usd)} <small>per request</small></div>
      <div class="path mono muted">GET ${esc(s.path)}?symbol=ETH</div>
    </div>`).join("");
}

function drawOrders() {
  if (!orders.length) {
    $("orders").innerHTML = '<tr><td colspan="5" class="empty">No requests yet.</td></tr>';
    return;
  }
  $("orders").innerHTML = orders.slice(0, 30).map((o) => {
    const unpaid = o.status === "awaiting payment" && Date.now() / 1000 - o.ts > 60;
    const [label, cls] = unpaid ? ["Not paid", "plain"] : (STATUS[o.status] || [o.status, "plain"]);
    let note = o.status === "declined" && o.reason ? `<div class="small muted">${esc(o.reason.slice(0, 60))}</div>` : "";
    if (o.status === "hijacked") note = `<div class="small muted">sent to ${esc(walletName(o.pay_to))}</div>`;
    return `<tr class="${o.status === "paid" ? "clickable" : ""} ${o.id === selected ? "selected" : ""}" data-id="${o.id}">
      <td class="time">${clock(o.ts)}</td>
      <td>${esc(o.product)} <span class="muted">${esc(o.coin)}</span></td>
      <td>${o.payer ? esc(walletName(o.payer)) : '<span class="muted">not paid yet</span>'}</td>
      <td class="num">${money(o.amount_usd)}</td>
      <td><span class="tag ${cls}">${label}</span>${note}</td>
    </tr>`;
  }).join("");
}

function drawInvoice() {
  const o = orders.find((x) => x.id === selected);
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
      <tr><td>${esc(o.product)} for ${esc(o.coin)}<div class="small muted">1 request</div></td>
          <td class="num">${money(o.amount_usd)}</td></tr>
    </tbody></table>
    <div class="total"><span>Total paid</span><span>${dollars(o.amount_usd)}</span></div>
    <span class="stamp">Paid in full</span>
  </div>`;
}

$("orders").addEventListener("click", (e) => {
  const tr = e.target.closest("tr.clickable");
  if (!tr) return;
  selected = tr.dataset.id;
  drawOrders();
  drawInvoice();
});

async function poll() {
  const r = await api("provider");
  if (r.error) {
    $("orders").innerHTML = `<tr><td colspan="5" class="empty">Shop: ${esc(r.error)}</td></tr>`;
    return;
  }
  if (!servicesDrawn) { drawServices(r.services); servicesDrawn = true; }
  $("wallet").textContent = shortAddr(r.wallet);
  $("wallet").title = r.wallet;
  const t = r.totals || {};
  $("f-revenue").textContent = dollars(r.revenue_usd);
  $("f-paid").textContent = r.orders.filter((o) => o.status === "paid").length;
  $("f-requests").textContent = t.requests || 0;
  const hijacked = r.orders.filter((o) => o.status === "hijacked").length;
  $("f-declined").textContent = [t.failed ? `${t.failed} declined` : "", hijacked ? `${hijacked} paid to another wallet` : ""]
    .filter(Boolean).join(", ");
  $("f-balance").textContent = r.wallet_usd == null ? "–" : dollars(r.wallet_usd);
  $("mock-note").textContent = r.mock ? "Test mode: payments are simulated" : "";
  orders = r.orders;
  if (!selected) {
    const firstPaid = orders.find((o) => o.status === "paid");
    if (firstPaid) selected = firstPaid.id;
  }
  drawOrders();
  drawInvoice();
}

poll();
setInterval(poll, 2000);
