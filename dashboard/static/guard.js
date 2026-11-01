// Guard page: the on/off switch, the owner's rules form, and the live decision feed.

const VERDICT = {
  allowed: ["Allowed", "ok"],
  blocked: ["Blocked", "bad"],
  paid_unprotected: ["Paid, unchecked", "warn"],
};
const NUMBER_RULES = ["max_per_payment_usd", "budget_usd", "cap_usd", "price_multiplier",
                      "max_timeout_s", "duplicate_window_s"];
const open = new Set();  // decisions the user expanded (kept open across refreshes)
let checkNames = {};

// --- switch ------------------------------------------------------------------------------
function showSwitch(enabled) {
  document.querySelectorAll("#switch button").forEach((b) => {
    const mine = b.dataset.on === "true";
    b.classList.toggle("on", mine === enabled);
    b.classList.toggle("off-state", !enabled && !mine);
  });
}
document.querySelectorAll("#switch button").forEach((b) => b.addEventListener("click", async () => {
  const r = await api("mode", { enabled: b.dataset.on === "true" });
  if (!r.error) { showSwitch(r.enabled); refreshStatus(); }
}));
window.onStatus = (s) => { if (s.guard != null) showSwitch(s.guard); };

// --- rules -------------------------------------------------------------------------------
function payeeRow(origin = "", wallet = "") {
  const div = document.createElement("div");
  div.className = "payee-row";
  div.innerHTML = `<input placeholder="http://shop:port" value="${esc(origin)}">
    <input class="mono" placeholder="0x…" value="${esc(wallet)}">
    <button type="button" class="ghost small" title="Remove">x</button>`;
  div.querySelector("button").onclick = () => div.remove();
  $("payees").appendChild(div);
}

function fillRules(r) {
  const f = $("rules");
  NUMBER_RULES.forEach((k) => { f.elements[k].value = r[k]; });
  f.elements.known_shops_only.checked = r.known_shops_only;
  f.elements.blocked_wallets.value = (r.blocked_wallets || []).join("\n");
  $("payees").innerHTML = "";
  Object.entries(r.trusted_payees || {}).forEach(([o, w]) => payeeRow(o, w));
  checkNames = r.check_names || {};
  $("check-toggles").innerHTML = Object.entries(r.checks).map(([id, on]) =>
    `<label><input type="checkbox" data-check="${id}" ${on ? "checked" : ""}> <span>${esc(checkNames[id] || id)}</span></label>`).join("");
}

function readRules() {
  const f = $("rules"), out = { checks: {}, trusted_payees: {} };
  NUMBER_RULES.forEach((k) => { out[k] = parseFloat(f.elements[k].value); });
  out.known_shops_only = f.elements.known_shops_only.checked;
  out.blocked_wallets = f.elements.blocked_wallets.value.split(/\s+/).filter(Boolean);
  document.querySelectorAll("#check-toggles input").forEach((i) => { out.checks[i.dataset.check] = i.checked; });
  document.querySelectorAll("#payees .payee-row").forEach((row) => {
    const [o, w] = row.querySelectorAll("input");
    if (o.value.trim()) out.trusted_payees[o.value.trim()] = w.value.trim();
  });
  return out;
}

async function saveRules(body) {
  const r = await api("rules", body);
  $("rules-error").textContent = r.error || "";
  if (!r.error) {
    fillRules(r);
    $("rules-state").textContent = "Saved " + new Date().toLocaleTimeString();
  }
}

$("rules").addEventListener("submit", (e) => { e.preventDefault(); saveRules(readRules()); });
$("defaults").onclick = () => saveRules({ reset: true });
$("add-payee").onclick = () => payeeRow();
api("rules").then((r) => { if (!r.error) fillRules(r); else $("rules-error").textContent = "Guard: " + r.error; });

// --- ask the agent -----------------------------------------------------------------------
$("task-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const go = $("task-go");
  go.disabled = true;
  go.textContent = "Agent is working…";
  const r = await api("agent", { task: $("task").value });
  go.disabled = false;
  go.textContent = "Run";
  $("answer").classList.remove("hidden");
  $("answer-text").textContent = r.error ? "Error: " + r.error : (r.answer || "(no answer)");
  poll();
});

// --- live feed ---------------------------------------------------------------------------
function decisionHtml(d, key) {
  const [label, cls] = VERDICT[d.verdict] || [d.verdict, "plain"];
  const ctx = d.context || {};
  const wanted = ctx.intended_url ? `${shopName(ctx.intended_url)} ${esc(d.item || "")}` : esc(d.item || "");
  let reason = d.verdict === "allowed" ? "All checks passed" : esc(d.reason || "");
  if (d.verdict === "paid_unprotected") {
    reason = d.would_have_blocked && d.would_have_blocked.length
      ? "Guard off. Would have failed: " + d.would_have_blocked.map((id) => checkNames[id] || id).join(", ")
      : "Guard off. Nothing wrong found";
  }
  const checks = (d.checks || []).map((c) => `
    <div class="check-line ${c.ok ? "pass" : "fail"}">
      <span class="mark">${c.ok ? "pass" : "fail"}</span><span class="name">${esc(c.name)}</span>
      <span class="why">${esc(c.reason)}</span></div>`).join("");
  return `<div class="decision ${d.verdict} ${open.has(key) ? "open" : ""}" data-key="${key}">
    <div class="decision-main">
      <div class="small muted num">${clock(d.ts)}</div>
      <div><div class="who">${wanted || "–"}</div>
        <div class="what">${esc(tidyUrls(ctx.task).slice(0, 110))}</div></div>
      <div><div class="who">${esc(d.shop)} asks ${money(d.amount_usd)}</div>
        <div class="what">pay to ${esc(walletName(d.payee))}</div></div>
      <div class="verdict"><span class="tag ${cls}">${label}</span><div class="reason">${reason}</div></div>
    </div>
    <div class="checks">
      ${ctx.task ? `<p class="task-line">Task: ${esc(ctx.task)}</p>` : ""}
      ${ctx.final_url ? `<p class="task-line mono">${esc(ctx.final_url)}</p>` : ""}
      ${checks}
    </div>
  </div>`;
}

async function poll() {
  const r = await api("decisions");
  if (r.error) { $("feed").innerHTML = `<p class="empty">Guard: ${esc(r.error)}</p>`; return; }
  const s = r.stats || {};
  $("f-checked").textContent = s.checked || 0;
  $("f-allowed").textContent = s.allowed || 0;
  $("f-blocked").textContent = s.blocked || 0;
  $("f-saved").textContent = dollars(s.saved_usd || 0);
  const top = Object.entries(s.by_check || {}).filter(([, n]) => n).sort((a, b) => b[1] - a[1])[0];
  $("f-top").textContent = top ? `most often: ${checkNames[top[0]] || top[0]}` : "";
  $("f-unchecked").textContent = s.unprotected ? `${s.unprotected} paid while the guard was off` : "";
  if (!r.decisions.length) {
    $("feed").innerHTML = '<p class="empty">No bills yet. Run a task or launch an attack.</p>';
    return;
  }
  $("feed").innerHTML = r.decisions.slice(0, 40).map((d) => decisionHtml(d, String(d.ts))).join("");
}

$("feed").addEventListener("click", (e) => {
  const row = e.target.closest(".decision");
  if (!row || !e.target.closest(".decision-main")) return;
  const key = row.dataset.key;
  open.has(key) ? open.delete(key) : open.add(key);
  row.classList.toggle("open");
});
$("clear").onclick = async () => { await api("decisions/clear", {}); poll(); };

poll();
setInterval(poll, 1500);
