// Attacker page: launch known attacks or a custom one, see how often each works, watch live payments.

let busy = false;
let wire = [];

// --- figures -----------------------------------------------------------------------------
window.onStatus = (s) => {
  $("f-agent").textContent = s.agent_usd == null ? "–" : dollars(s.agent_usd);
  $("f-loot").textContent = s.attacker_usd == null ? "–" : dollars(s.attacker_usd);
  $("f-guard").textContent = s.guard == null ? "offline" : (s.guard ? "On" : "Off");
};

function rate(t) {
  if (!t.tries) return '<span class="muted small">not tried</span>';
  const pct = Math.round((100 * t.wins) / t.tries);
  return `<div class="rate ${t.wins ? "" : "zero"}">${t.wins} of ${t.tries} worked
    ${t.lost_usd ? `<span class="muted"> · ${money(t.lost_usd)}</span>` : ""}
    <div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div></div>`;
}

// --- attack list -------------------------------------------------------------------------
async function loadAttacks() {
  const r = await api("attacks");
  if (r.error) {
    $("attacks").innerHTML = `<tr><td colspan="5" class="empty">Attack engine: ${esc(r.error)}</td></tr>`;
    return;
  }
  let wins = 0, tries = 0;
  $("attacks").innerHTML = r.attacks.map((a) => {
    ["off", "on"].forEach((g) => { wins += a.tally[g].wins; tries += a.tally[g].tries; });
    return `<tr class="attack-row">
      <td><div class="name">${esc(a.name)}</div><div class="plain">${esc(a.plain)}</div></td>
      <td class="small">${esc(a.difficulty)}</td>
      <td>${rate(a.tally.off)}</td>
      <td>${rate(a.tally.on)}</td>
      <td class="num"><button class="small" data-attack="${esc(a.id)}">Launch</button></td>
    </tr>`;
  }).join("");
  $("f-wins").textContent = wins;
  $("f-tries").textContent = tries ? `out of ${tries} tries` : "";
  $("assessed").textContent = r.assessed_at
    ? `Includes the full test run at ${clock(r.assessed_at)}.`
    : "Rates come from your launches. Use the full test to try every attack with the guard on and off.";
}

// --- launching ---------------------------------------------------------------------------
function showResult(r, name) {
  const box = $("result");
  box.className = "result-box";
  if (r.error && !r.outcome) {
    box.innerHTML = `<span class="label">${esc(name)}</span><p class="error-line">${esc(r.error)}</p>`;
    return;
  }
  let line, detail = esc(r.reason || "");
  if (r.outcome === "stolen") {
    box.classList.add("stolen");
    line = `You took ${money(r.lost_usd)}`;
    detail = `The agent paid without the guard stopping it.${r.tx ? `<div class="mono small" style="margin-top:6px">Transaction ${esc(r.tx)}</div>` : ""}`;
  } else if (r.outcome === "blocked") {
    box.classList.add("blocked");
    line = "Stopped by the guard";
  } else if (r.outcome === "paid") {
    line = "Honest payment went through";
  } else {
    line = "Nothing was paid";
    detail = esc(r.error || r.reason || "The agent did not pay this bill.");
  }
  box.innerHTML = `<span class="label">${esc(name)} · guard ${esc(r.guard || "")} · ${esc(r.mode || $("route").value)}</span>
    <div class="verdict-line">${line}</div><div class="small">${detail}</div>`;
}

async function launch(body, name, button) {
  if (busy) return;
  busy = true;
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "Running…";
  $("result").className = "result-box";
  $("result").innerHTML = `<span class="label">${esc(name)}</span><div class="verdict-line">Sending the bill…</div>
    <p class="small muted">${$("route").value === "agent" ? "The AI agent is reading the task and deciding what to pay." : "Paying the bill directly."}</p>`;
  const r = await api("fire", { ...body, mode: $("route").value, player: "console" });
  showResult({ ...r, mode: $("route").value }, name);
  button.disabled = false;
  button.textContent = label;
  busy = false;
  loadAttacks();
  refreshStatus();
}

$("attacks").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-attack]");
  if (b) launch({ attack_id: b.dataset.attack }, b.closest("tr").querySelector(".name").textContent, b);
});

$("custom").addEventListener("submit", (e) => {
  e.preventDefault();
  const f = e.target.elements;
  const custom = {
    price_usd: parseFloat(f.price_usd.value), pay_to: f.pay_to.value, token: f.token.value,
    expiry_s: parseInt(f.expiry_s.value, 10), hidden_text: f.hidden_text.value.trim(),
  };
  launch({ custom }, "Your attack", $("custom-go"));
});

$("assess").onclick = async () => {
  const b = $("assess");
  b.disabled = true;
  b.textContent = "Testing every attack…";
  const r = await api("assess", {});
  b.disabled = false;
  b.textContent = "Test all, guard on and off";
  if (r.error) $("assessed").textContent = "Full test failed: " + r.error;
  loadAttacks();
  refreshStatus();
};

$("reset").onclick = async () => {
  await api("reset", {});
  refreshStatus();
};

// --- live payments -----------------------------------------------------------------------
const OUTCOME = { allowed: ["paid", "ok"], blocked: ["blocked", "bad"], paid_unprotected: ["paid, unchecked", "warn"] };

async function pollWire() {
  const r = await api("decisions");
  if (r.error) return;
  wire = r.decisions.slice(0, 12);
  if (!wire.length) {
    $("wire").innerHTML = '<tr><td colspan="7" class="empty">Quiet so far.</td></tr>';
    return;
  }
  $("wire").innerHTML = wire.map((d, i) => {
    const [label, cls] = OUTCOME[d.verdict] || [d.verdict, "plain"];
    return `<tr>
      <td class="time">${clock(d.ts)}</td><td>${esc(d.shop)}</td><td>${esc(d.item)}</td>
      <td class="num">${money(d.amount_usd)}</td><td>${esc(walletName(d.payee))}</td>
      <td><span class="tag ${cls}">${label}</span></td>
      <td class="num"><button class="ghost small" data-copy="${i}">Copy</button></td></tr>`;
  }).join("");
}

$("wire").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-copy]");
  if (!b) return;
  const d = wire[+b.dataset.copy];
  const f = $("custom").elements;
  f.price_usd.value = d.amount_usd;
  f.pay_to.value = "attacker";
  $("custom-note").textContent = `Copied ${money(d.amount_usd)} from ${d.shop} ${d.item}. Now it pays you.`;
});

loadAttacks();
pollWire();
setInterval(pollWire, 2000);
setInterval(() => { if (!busy) loadAttacks(); }, 5000);
