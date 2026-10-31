// "How it works" page: the three "Try it yourself" buttons.
//   See a real bill -> the browser asks for Coin price of ETH without paying (a real 402)
//   Make a payment  -> the server runs a full mock purchase (POST /try/simulate)
//   Try to cheat    -> the server sends a tampered payment (POST /try/attack)
// All of them go through the real paywall, so they also appear on Live payments.
// Results are shown in plain words; the JSON is inside a closed "Show technical details".

(function () {
  const $ = (id) => document.getElementById(id);
  const out = $("t-result");
  const buttons = [$("t-bill"), $("t-pay"), $("t-cheat")];
  const PATH = "/api/price";
  const SYMBOL = "ETH";

  function busy(on) {
    buttons.forEach((b) => {
      if (on) { b.dataset.was = b.disabled ? "1" : ""; b.disabled = true; }
      else b.disabled = b.dataset.was === "1";
    });
  }

  function head(cls, text) {
    return el("div", `result-head ${cls}`, text);
  }

  function compare(rows) {
    const box = el("div", "compare");
    rows.forEach(([k, v, bad]) => {
      const cell = el("div");
      cell.append(el("div", "k", k), el("div", bad ? "v bad" : "v", v));
      box.appendChild(cell);
    });
    return box;
  }

  async function run(fn) {
    busy(true);
    out.replaceChildren(el("p", "muted", "Working…"));
    try {
      out.replaceChildren(...(await fn()));
    } catch (err) {
      out.replaceChildren(head("bad", "Something went wrong"), el("p", "muted", err.message));
    } finally {
      busy(false);
    }
  }

  // 1) See a real bill
  $("t-bill").addEventListener("click", () => run(async () => {
    const resp = await fetch(window.LIVE_URL, { cache: "no-store" });
    const b64 = resp.headers.get("PAYMENT-REQUIRED");
    if (resp.status !== 402 || !b64) throw new Error(`Expected a bill, got HTTP ${resp.status}`);
    const bill = b64ToJson(b64);
    const req = bill.accepts[0];
    return [
      head("info", `We got a bill: ${atomicCents(req.amount)} for ${productName(PATH)} of ${coinLabel(SYMBOL)}`),
      el("p", null, `${SITE.brand} answered: "This costs ${atomicCents(req.amount)}. Pay it to our wallet ${shortHex(req.payTo)} within ${req.maxTimeoutSeconds} seconds." No data yet: first, pay.`),
      compare([
        ["Price", atomicCents(req.amount)],
        ["Pay to", `${SITE.brand} wallet ${shortHex(req.payTo)}`],
        ["Currency", "USDC (digital dollar)"],
        ["Valid for", `${req.maxTimeoutSeconds} seconds`],
      ]),
      techDetails([
        [`HTTP ${resp.status} · PAYMENT-REQUIRED header (raw base64)`, b64],
        ["Decoded bill (PaymentRequired)", bill],
      ]),
    ];
  }));

  // 2) Make a payment (mock mode only)
  $("t-pay").addEventListener("click", () => run(async () => {
    const r = await postJson("/try/simulate", { path: PATH, symbol: SYMBOL });
    const [billStep, payStep, paidStep] = r.steps;
    const parts = [];
    if (r.ok) {
      parts.push(head("ok", `Paid ✓ ${atomicCents(r.bill.amount)}: here's what the AI got`));
      parts.push(dataCard(PATH, SYMBOL, paidStep.body));
      parts.push(el("p", "muted", `Receipt number ${shortHex(paidStep.receipt && paidStep.receipt.transaction)}. In mock mode it starts with "0xmock", so it's clearly not a real transaction.`));
    } else {
      parts.push(head("bad", `Refused ✗: ${reasonText(paidStep && paidStep.body && paidStep.body.error, "sentence")}`));
    }
    parts.push(techDetails([
      ["The bill (PAYMENT-REQUIRED, decoded)", billStep.body],
      ["The payment (PAYMENT-SIGNATURE, decoded)", payStep && payStep.payload],
      ["The receipt (PAYMENT-RESPONSE, decoded)", paidStep && paidStep.receipt],
      ["The data (response body)", paidStep && paidStep.body],
    ]));
    return parts;
  }));

  // 3) Try to cheat
  $("t-cheat").addEventListener("click", () => run(async () => {
    const kind = document.querySelector('input[name="cheat"]:checked').value;
    const r = await postJson("/try/attack", { kind, path: PATH, symbol: SYMBOL });
    const [billStep, payStep, paidStep] = r.steps;
    const auth = payStep ? payStep.payload.payload.authorization : {};
    const bill = r.bill || {};
    const parts = [];
    if (r.rejected) {
      parts.push(head("bad", `Refused ✗: ${reasonText(r.error, "sentence")}. No data given.`));
    } else {
      parts.push(head("info", "Not refused. This should never happen: please tell the team."));
    }
    if (kind === "underpay") {
      parts.push(compare([
        ["The bill asked for", atomicCents(bill.amount)],
        ["The cheater signed for", atomicCents(auth.value), true],
      ]));
    } else {
      parts.push(compare([
        ["The bill said pay to", `${SITE.brand} ${shortHex(bill.payTo)}`],
        ["The cheater signed to", `someone else ${shortHex(auth.to)}`, true],
      ]));
    }
    parts.push(el("p", "muted", "This attempt now shows as \"Refused ✗\" on Live payments."));
    parts.push(techDetails([
      ["The bill (PAYMENT-REQUIRED, decoded)", billStep && billStep.body],
      ["The tampered payment (PAYMENT-SIGNATURE, decoded)", payStep && payStep.payload],
      [`Our answer: HTTP ${r.status}`, paidStep && paidStep.body],
    ]));
    return parts;
  }));
})();
