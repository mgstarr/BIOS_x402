// Home page "Watch a payment" box.
// The SERVER runs the whole purchase through the real paywall (POST /try/simulate, or
// POST /try/attack for the cheat). This page then plays the 4 steps one by one
// (about 0.8 s each) and shows the data as a friendly card.

(function () {
  const STEP_MS = 800;
  const $ = (id) => document.getElementById(id);
  const productSel = $("w-product");
  const coinSel = $("w-coin");
  const buyBtn = $("w-buy");
  const cheatBtn = $("w-cheat");
  const result = $("w-result");
  const steps = [...document.querySelectorAll("#w-steps .fstep")];

  function setStep(i, state, text, sub = "") {
    const s = steps[i];
    s.classList.remove("active", "done", "bad");
    if (state) s.classList.add(state);
    s.querySelector(".ftext").textContent = text;
    s.querySelector(".fsub").textContent = sub;
  }

  function resetSteps() {
    const texts = ["Asking for data", "Bill", "Payment signed", "Check, collect, deliver"];
    texts.forEach((t, i) => setStep(i, null, t));
    result.replaceChildren();
  }

  async function play(cheat) {
    const path = productSel.value;
    const symbol = coinSel.value;
    const what = `${productName(path)} of ${coinName(symbol)}`;
    const wasBuyDisabled = buyBtn.disabled;
    buyBtn.disabled = cheatBtn.disabled = true;
    resetSteps();

    // Start the real purchase now; animate step 1 while it runs.
    const request = cheat
      ? postJson("/try/attack", { kind: "underpay", path, symbol })
      : postJson("/try/simulate", { path, symbol });
    setStep(0, "active", `Asking for ${what}…`);
    await sleep(STEP_MS);

    let r;
    try {
      r = await request;
    } catch (err) {
      setStep(0, "bad", "Something went wrong", err.message);
      buyBtn.disabled = wasBuyDisabled;
      cheatBtn.disabled = false;
      return;
    }
    const [billStep, payStep, paidStep] = r.steps || [];
    const bill = billStep && billStep.body && billStep.body.accepts ? billStep.body.accepts[0] : null;
    const auth = payStep ? payStep.payload.payload.authorization : null;
    setStep(0, "done", `Asked for ${what}`);

    // Step 2: the bill
    setStep(1, "active", "Waiting for the bill…");
    await sleep(STEP_MS);
    if (!bill) {
      setStep(1, "bad", "No bill came back");
      return finish(r, path, symbol, cheat);
    }
    setStep(1, "done", `Bill received: ${atomicCents(bill.amount)} to ${SITE.brand || "TickToll"}`,
      "Pay within 60 seconds");

    // Step 3: the signature
    setStep(2, "active", "Wallet is signing…");
    await sleep(STEP_MS);
    if (cheat && auth) {
      setStep(2, "bad", "Payment signed, but for less",
        `The cheater signed for ${atomicCents(auth.value)} instead of ${atomicCents(bill.amount)}`);
    } else {
      setStep(2, "done", "Payment approved and signed", "Like signing a cheque");
    }

    // Step 4: our check
    setStep(3, "active", "Checking the payment…");
    await sleep(STEP_MS);
    if (r.ok) {
      setStep(3, "done", "Paid ✓, here's the data", "Receipt attached");
    } else {
      setStep(3, "bad", `Refused ✗: ${reasonText(r.error, "sentence")}. No data given.`);
    }
    finish(r, path, symbol, cheat);
  }

  function finish(r, path, symbol, cheat) {
    const [billStep, payStep, paidStep] = r.steps || [];
    if (r.ok && paidStep) {
      result.appendChild(dataCard(path, symbol, paidStep.body));
    } else if (cheat) {
      const card = el("div", "data-card refused");
      card.appendChild(el("div", "dc-label", "Cheat attempt"));
      card.appendChild(el("div", "dc-big", "Refused. No data, no money moved."));
      card.appendChild(el("p", "dc-line",
        "TickToll checks that the signed payment matches the bill exactly before handing over anything."));
      result.appendChild(card);
    }
    result.appendChild(techDetails([
      ["The bill we sent (PAYMENT-REQUIRED, decoded)", billStep && billStep.body],
      ["The payment the AI signed (PAYMENT-SIGNATURE, decoded)", payStep && payStep.payload],
      ["The receipt (PAYMENT-RESPONSE, decoded)", paidStep && paidStep.receipt],
      [paidStep && paidStep.status === 200 ? "The data (response body)" : `Our answer: HTTP ${paidStep ? paidStep.status : "?"}`,
        paidStep && paidStep.body],
    ]));
    buyBtn.disabled = !SITE.mockMode;
    cheatBtn.disabled = false;
  }

  buyBtn.addEventListener("click", () => play(false));
  cheatBtn.addEventListener("click", () => play(true));
})();
