// Docs page: "Fetch a real 402" and "Pay it (mock)" buttons. Both make REAL HTTP calls
// to our own /api/price, so they also show up on the /live dashboard.

(function () {
  const DUMMY_PAYER = "0x000000000000000000000000000000000000dEaD"; // obviously fake, same as try_pay.py
  const DUMMY_SIGNATURE = "0x" + "00".repeat(65);
  const $ = (id) => document.getElementById(id);

  async function fetchBill() {
    const resp = await fetch(window.LIVE_URL, { cache: "no-store" });
    const b64 = resp.headers.get("PAYMENT-REQUIRED");
    if (resp.status !== 402 || !b64) throw new Error(`expected 402 with a bill, got ${resp.status}`);
    return { resp, b64, bill: b64ToJson(b64) };
  }

  // Same logic as shops/try_pay.py: accept the first option, copy amount/payTo exactly.
  function buildPayment(bill) {
    const req = bill.accepts[0];
    const now = Math.floor(Date.now() / 1000);
    return {
      x402Version: 2,
      accepted: req,
      payload: {
        signature: DUMMY_SIGNATURE,
        authorization: {
          from: DUMMY_PAYER,
          to: req.payTo,
          value: req.amount,
          validAfter: String(now - 60),
          validBefore: String(now + req.maxTimeoutSeconds),
          nonce: randomHex(32),
        },
      },
    };
  }

  $("fetch-live").addEventListener("click", async (e) => {
    e.currentTarget.disabled = true;
    try {
      const { resp, b64, bill } = await fetchBill();
      $("bill-b64").textContent = b64;
      $("bill-json").textContent = pretty(bill);
      $("bill-status").textContent = `LIVE · HTTP ${resp.status} · ${new Date().toLocaleTimeString()}`;
      $("fetch-note").textContent = "This came over HTTP from our own endpoint, just now.";
    } catch (err) {
      $("fetch-note").textContent = `Failed: ${err.message}`;
    } finally {
      e.currentTarget.disabled = false;
    }
  });

  const payBtn = $("pay-mock");
  payBtn.addEventListener("click", async () => {
    payBtn.disabled = true;
    try {
      const { bill } = await fetchBill();
      const payment = buildPayment(bill);
      $("payment-json").textContent = pretty(payment);

      const resp = await fetch(window.LIVE_URL, {
        cache: "no-store",
        headers: { "PAYMENT-SIGNATURE": jsonToB64(payment) },
      });
      const body = await resp.json();
      const receipt = resp.headers.get("PAYMENT-RESPONSE");
      if (resp.status === 200 && receipt) {
        $("receipt-json").textContent = pretty(b64ToJson(receipt));
        $("receipt-status").textContent = `LIVE · HTTP 200 · ${new Date().toLocaleTimeString()}`;
        $("data-json").textContent = pretty(body);
        $("data-block").hidden = false;
        $("pay-note").textContent = "Paid. Check the Live page: earnings just went up.";
      } else {
        $("pay-note").textContent = `Rejected (HTTP ${resp.status}): ${body.error}`;
      }
    } catch (err) {
      $("pay-note").textContent = `Failed: ${err.message}`;
    } finally {
      payBtn.disabled = false;
    }
  });
})();
