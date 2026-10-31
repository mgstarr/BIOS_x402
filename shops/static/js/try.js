// "Try it" box on the landing page: shows a real curl.exe command and can run the request.

(function () {
  const pathSel = document.getElementById("try-path");
  const symSel = document.getElementById("try-symbol");
  const cmd = document.getElementById("try-cmd");
  const out = document.getElementById("try-out");
  const runBtn = document.getElementById("try-run");

  function url() {
    return `${window.BASE_URL}${pathSel.value}?symbol=${symSel.value}`;
  }
  function render() {
    cmd.textContent = `curl.exe -i "${url()}"`;
  }

  async function run() {
    runBtn.disabled = true;
    out.className = "";
    out.textContent = "…";
    try {
      const resp = await fetch(url(), { cache: "no-store" });
      const lines = [`HTTP ${resp.status} ${resp.statusText || ""}`.trim()];
      const bill = resp.headers.get("PAYMENT-REQUIRED");
      if (bill) {
        lines.push(`PAYMENT-REQUIRED: ${bill.slice(0, 48)}…`, "");
        const decoded = b64ToJson(bill);
        const req = decoded.accepts[0];
        lines.push(`# decoded bill: pay ${req.amount} atomic units = ${usd(req.amount / 1e6)} USDC`);
        lines.push(`#               to ${req.payTo} on ${req.network}`, "");
      }
      lines.push(pretty(await resp.json()));
      out.textContent = lines.join("\n");
    } catch (err) {
      out.className = "c";
      out.textContent = `# request failed: ${err}`;
    } finally {
      runBtn.disabled = false;
    }
  }

  // --- "Simulate paid call (mock)": the server runs the whole flow, we print each step. ---
  const simBtn = document.getElementById("try-simulate"); // only exists in mock mode

  // Long hex strings (signature, nonce) shortened so the payload fits on screen.
  function shortenPayload(payment) {
    const copy = JSON.parse(JSON.stringify(payment));
    const p = copy.payload || {};
    if (p.signature) p.signature = shortHex(p.signature);
    if (p.authorization && p.authorization.nonce) p.authorization.nonce = shortHex(p.authorization.nonce);
    return copy;
  }
  const clip = (s, n = 48) => (s && s.length > n ? s.slice(0, n) + "…" : s || "(none)");

  function showSteps(result) {
    const [bill, pay, paid] = result.steps;
    const L = [];
    L.push(`# 1) GET ${result.url}   (no payment)`);
    L.push(`HTTP ${bill.status}`);
    L.push(`PAYMENT-REQUIRED: ${clip(bill.header)}`);
    if (bill.body && bill.body.accepts) {
      const req = bill.body.accepts[0];
      L.push(`# the bill: ${req.amount} atomic = ${usd(req.amount / 1e6)} USDC to ${req.payTo}`);
      if (req.extra && req.extra.requestId) L.push(`#           request id ${req.extra.requestId}`);
    } else {
      L.push(pretty(bill.body));
    }
    if (pay) {
      L.push("", "# 2) build a mock-signed PaymentPayload (dummy signature, shortened below)");
      L.push(pretty(shortenPayload(pay.payload)));
    }
    if (paid) {
      L.push("", "# 3) retry the same GET with the payment attached");
      L.push(`PAYMENT-SIGNATURE: ${clip(pay.header)}`);
      L.push(`HTTP ${paid.status}`);
      if (paid.receipt) {
        L.push(`PAYMENT-RESPONSE: ${clip(paid.header)}`, "", "# the receipt (decoded):");
        L.push(pretty(paid.receipt));
      }
      L.push("", paid.status === 200 ? "# the data you paid for:" : "# rejected:");
      L.push(pretty(paid.body));
    }
    L.push("", result.ok ? "# ✔ paid. Open the Live page: this purchase is one row, marked PAID."
                         : "# ✘ the flow did not finish (see above)");
    return L.join("\n");
  }

  async function simulate() {
    simBtn.disabled = true;
    runBtn.disabled = true;
    cmd.textContent = `# server-side simulation of ${url()}`;
    out.className = "";
    out.textContent = "…";
    try {
      const resp = await fetch("/try/simulate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: pathSel.value, symbol: symSel.value }),
      });
      const result = await resp.json();
      if (!resp.ok) throw new Error(result.error || `HTTP ${resp.status}`);
      out.textContent = showSteps(result);
    } catch (err) {
      out.className = "c";
      out.textContent = `# simulation failed: ${err.message || err}`;
    } finally {
      simBtn.disabled = false;
      runBtn.disabled = false;
    }
  }

  pathSel.addEventListener("change", render);
  symSel.addEventListener("change", render);
  runBtn.addEventListener("click", () => { render(); run(); });
  if (simBtn) simBtn.addEventListener("click", simulate);
  render();
})();
