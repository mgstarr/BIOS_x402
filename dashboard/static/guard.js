// Guard page: the switch, the latest block, the owner's rules, a live agent run, and every bill.

const VERDICT = { allowed: ["Allowed", "ok"], blocked: ["Blocked", "bad"], paid_unprotected: ["Paid, unchecked", "warn"] };
const NUMBER_RULES = ["max_per_payment_usd", "budget_usd", "cap_usd", "price_multiplier", "max_timeout_s", "duplicate_window_s"];
const open = new Set();  // bills the user expanded (kept open across refreshes)
let checkNames = {}, guardOn = true, latest = [];

// --- switch ------------------------------------------------------------------------------
function showSwitch(enabled) {
  guardOn = enabled;
  document.querySelectorAll("#switch button").forEach((b) => {
    const mine = b.dataset.on === "true";
    b.classList.toggle("on", mine === enabled);
    b.classList.toggle("off-state", !enabled && !mine);
  });
  drawAlert();
}
document.querySelectorAll("#switch button").forEach((b) => b.addEventListener("click", async () => {
  const r = await api("mode", { enabled: b.dataset.on === "true" });
  if (!r.error) { showSwitch(r.enabled); refreshStatus(); }
}));
window.onStatus = (s) => { if (s.guard != null) showSwitch(s.guard); };

// --- the latest block, front and centre --------------------------------------------------
function drawAlert() {
  const a = $("alert");
  const lastBlock = latest.find((d) => d.verdict === "blocked");
  let kind = "calm", tag = "Watching", title = "No bills yet", sub = "Ask your agent something below.", when = "";
  if (!guardOn) {
    kind = "unchecked"; tag = "Guard off";
    title = "Bills are being paid without any checks";
    sub = "Switch the guard on to protect the wallet.";
  } else if (lastBlock) {
    kind = "blocked"; tag = "Blocked";
    title = lastBlock.reason;
    sub = `${lastBlock.shop} asked ${money(lastBlock.amount_usd)} for ${lastBlock.item}, to be paid to ${walletName(lastBlock.payee)}. ` +
          `Stopped by the ${checkNames[lastBlock.check] || lastBlock.check} rule. Nothing was signed.`;
    when = ago(lastBlock.ts);
  } else if (latest.length) {
    tag = "All clear"; title = "Every bill so far followed your rules";
    sub = `${latest.length} bill${latest.length > 1 ? "s" : ""} checked, none blocked.`;
  }
  a.className = "alert " + kind;
  a.innerHTML = `<span class="alert-kind">${tag}</span>
    <div><div class="alert-reason">${esc(title)}</div><div class="alert-sub">${esc(sub)}</div></div>
    <span class="small muted">${when}</span>`;
}

// --- rules: plain sentences, and the form behind them ------------------------------------
function rulesSummary(r) {
  const on = (id) => r.checks[id];
  const shops = Object.entries(r.trusted_payees).map(([o, w]) => `${shopName(o)} at ${shortAddr(w)}`).join(", ");
  const lines = [
    ["mandate", "Never pay more than this in one payment", money(r.max_per_payment_usd)],
    ["mandate", "Spend at most this on one task", money(r.budget_usd)],
    ["cap", "Refuse any single bill above", money(r.cap_usd)],
    ["price", "Refuse prices above the shop's usual price by", `${r.price_multiplier}x`],
    ["payee", `Pay trusted shops only at their own wallet (${shops || "none set"}), and refuse look-alike wallets`, ""],
    ["asset", "Pay only in real USDC on Base Sepolia", ""],
    ["origin", "Pay only the website the agent meant to visit", ""],
    ["expiry", "Sign only payments that expire within", `${r.max_timeout_s} s`],
    ["duplicate", "Never pay twice for the same thing within", `${r.duplicate_window_s} s`],
  ];
  if (r.known_shops_only) lines.push(["payee", "Refuse every shop that is not on the trusted list", ""]);
  if (r.blocked_wallets.length) lines.push(["payee", `Never pay ${r.blocked_wallets.map(shortAddr).join(", ")}`, ""]);
  $("rules-summary").innerHTML = lines.map(([id, text, k]) =>
    `<li class="${on(id) ? "" : "off"}" title="${on(id) ? "" : "This check is switched off"}"><span>${esc(text)}</span><span class="k">${esc(k)}</span></li>`).join("");
}

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
  rulesSummary(r);
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
    $("rules-state").textContent = "Saved at " + new Date().toLocaleTimeString() + ". New bills are checked with these rules.";
    $("rules").classList.add("hidden");
    $("edit").textContent = "Edit rules";
  }
}

$("edit").onclick = () => {
  const hidden = $("rules").classList.toggle("hidden");
  $("edit").textContent = hidden ? "Edit rules" : "Close";
};
$("rules").addEventListener("submit", (e) => { e.preventDefault(); saveRules(readRules()); });
$("defaults").onclick = () => saveRules({ reset: true });
$("add-payee").onclick = () => payeeRow();
api("rules").then((r) => { if (!r.error) fillRules(r); else $("rules-state").textContent = "Guard: " + r.error; });

// --- the agent, step by step -------------------------------------------------------------
let runId = null, runTimer = null;

$("examples").addEventListener("click", (e) => {
  if (e.target.matches(".chip")) { $("task").value = e.target.textContent; $("task").focus(); }
});

$("task-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const task = $("task").value.trim();
  if (!task) return;
  $("task-go").disabled = true;
  $("answer").classList.add("hidden");
  $("timeline").innerHTML = item("active", "Sending the task to the agent", esc(task), "");
  const r = await api("agent", { task });
  if (r.error) {
    $("timeline").innerHTML = item("fail", "Could not start the agent", esc(r.error), "");
    $("task-go").disabled = false;
    return;
  }
  runId = r.run_id;
  clearInterval(runTimer);
  runTimer = setInterval(followRun, 700);
  followRun();
});

function item(state, what, detail, when, sub = "") {
  const mark = { done: "", money: "$", fail: "!", active: "" }[state] || "";
  return `<li class="tl ${state}"><span class="dot">${mark}</span>
    <div><div class="what">${what}</div>${detail ? `<div class="detail">${detail}</div>` : ""}${sub}</div>
    <span class="when">${when}</span></li>`;
}

const fullUrl = (u) => (u.startsWith("/") ? "http://localhost:5001" + u : u);  // the agent may give a TickToll path
const pathOf = (url) => { try { return new URL(url).pathname; } catch (e) { return url; } };
const queryOf = (url) => { try { return [...new URL(url).searchParams.values()].join(", "); } catch (e) { return ""; } };

function checksSub(g) {
  if (!g || !g.checks) return "";
  return `<div class="sub">${g.checks.map((c) =>
    `<div class="${c.ok ? "pass" : "fail"}"><b>${c.ok ? "pass" : "fail"}</b><span class="chk">${esc(c.name)}</span>: ${esc(c.reason)}</div>`).join("")}</div>`;
}

function summarise(data) {
  if (!data || typeof data !== "object") return esc(String(data));
  if (data.headlines) return esc(data.headlines.slice(0, 2).join(" · "));
  const keys = ["name", "city", "pair", "ticker", "symbol", "price_usd", "price", "temp_c", "condition", "rate", "aqi", "label", "title"];
  return keys.filter((k) => data[k] != null).slice(0, 4).map((k) => `${k.replace(/_/g, " ")}: ${esc(data[k])}`).join(" · ")
    || esc(JSON.stringify(data).slice(0, 140));
}

function timeline(run) {
  const t0 = run.started || (run.trace[0] && run.trace[0].ts) || Date.now() / 1000;
  const at = (s) => s.ts ? `+${(s.ts - t0).toFixed(1)} s` : "";
  const out = [item("done", "Task received", esc(run.task), "+0.0 s")];
  run.trace.forEach((s, i) => {
    const last = i === run.trace.length - 1 && run.status === "running";
    switch (s.step) {
      case "mandate":
        out.push(item("done", "Limits set", s.mandate
          ? `Up to ${money(s.mandate.max_per_payment_usd)} a payment, ${money(s.mandate.budget_usd)} in total`
          : "No limits sent with the task, so your rules on the left apply", at(s)));
        break;
      case "thinking":
        if (last) out.push(item("active", "Agent is thinking", "Asking the local model what to do next", at(s)));
        break;
      case "thought":
        if (!s.text.startsWith("decided to call")) out.push(item("done", "Agent's note", esc(s.text), at(s)));
        break;
      case "tool":
        if (s.name === "list_paid_apis") {
          out.push(item(last ? "active" : "done", "Searching TickToll for data APIs", "GET http://localhost:5001/catalog", at(s)));
        } else {
          const u = fullUrl(s.args.url || "");
          out.push(item(last ? "active" : "done", `Decided it needs ${esc(shopName(u))} ${esc(pathOf(u))}`,
            queryOf(u) ? `For ${esc(queryOf(u))}` : "", at(s)));
        }
        break;
      case "request":
        out.push(item(last ? "active" : "done", `Request sent to ${esc(shopName(s.url))}`, `<span class="mono">GET ${esc(s.url)}</span>`, at(s)));
        break;
      case "402": {
        out.push(item("money", `Payment required: ${esc(shopName(s.url))} asks ${money(s.amount_usd)}`,
          `To be paid to ${esc(walletName(s.payTo))} <span class="mono">${esc(shortAddr(s.payTo))}</span>, in USDC on Base Sepolia`, at(s)));
        const g = s.guard;
        if (g) {
          const verdict = g.verdict === "blocked" ? "fail" : "done";
          const title = g.verdict === "blocked" ? "Guard checked the bill: rule broken"
            : g.verdict === "paid_unprotected" ? "Guard is off: bill not checked" : "Guard checked the bill against your rules";
          const passed = g.checks.filter((c) => c.ok).length;
          out.push(item(verdict, title, `${passed} of ${g.checks.length} checks passed`, "", checksSub(g)));
        } else if (last) {
          out.push(item("active", "Guard is checking the bill", "", ""));
        }
        break;
      }
      case "signed":
        out.push(item("done", "Approved. The wallet signed a USDC transfer",
          `Signed by ${esc(walletName(s.payer))} (EIP-3009 authorisation). Sent back to the shop with the request.`, at(s)));
        if (last) out.push(item("active", "Shop is settling the payment on the blockchain", "", ""));
        break;
      case "paid": {
        const c = s.chain || {};
        out.push(item("money", "Transaction sent to Base Sepolia (local fork)", `<span class="mono">${esc(s.tx)}</span>`, at(s)));
        out.push(item("done", c.block ? `Added to block #${c.block.toLocaleString()}` : "Added to a block",
          c.block ? `Block <span class="mono">${esc(shortAddr(c.block_hash))}</span> · gas used ${c.gas_used.toLocaleString()} · ${c.confirmations || 1} confirmation${(c.confirmations || 1) > 1 ? "s" : ""}` : "", ""));
        out.push(item(c.success === false ? "fail" : "money", c.success === false ? "Transaction failed on-chain" : `Approved: ${money(s.amount_usd)} moved`,
          `From ${esc(walletName(s.payer))} to ${esc(walletName(s.payTo))}`, ""));
        break;
      }
      case "blocked":
        out.push(item("fail", "Payment blocked by the guard", `${esc(s.reason)} (${esc(checkNames[s.check] || s.check)} rule). Nothing was signed, no money moved.`, at(s)));
        break;
      case "failed":
        out.push(item("fail", "Something went wrong", esc(s.error), at(s)));
        break;
      case "result":
        if (s.catalog) {
          out.push(item("done", `Found ${s.catalog.length} APIs`, s.catalog.map((c) => `${esc(c.path.replace("/api/", ""))} ${money(c.price_usd)}`).join(", "), at(s)));
        } else if (s.data) {
          out.push(item("done", "Data received", summarise(s.data), at(s)));
        } else if (s.error && !/blocked by the guard/.test(s.error)) {
          out.push(item("fail", "No data", esc(s.error), at(s)));
        }
        break;
      case "answer":
        out.push(item("done", "Answer ready", s.error ? esc(s.error) : "", at(s)));
        break;
    }
  });
  if (run.status === "error") out.push(item("fail", "The agent stopped", esc(run.error || ""), ""));
  return out.join("");
}

async function followRun() {
  if (!runId) return;
  const run = await api("agent/" + runId);
  if (run.error && !run.trace) return;
  $("timeline").innerHTML = timeline(run);
  if (run.status !== "running") {
    clearInterval(runTimer);
    runId = null;
    $("task-go").disabled = false;
    if (run.answer) {
      $("answer").classList.remove("hidden");
      $("answer-text").textContent = run.answer;
    }
    poll();
  }
}

// --- every bill --------------------------------------------------------------------------
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
  latest = r.decisions;
  drawAlert();
  $("feed").innerHTML = r.decisions.length
    ? r.decisions.slice(0, 40).map((d) => decisionHtml(d, String(d.ts))).join("")
    : '<p class="empty">No bills yet.</p>';
}

$("feed").addEventListener("click", (e) => {
  const row = e.target.closest(".decision");
  if (!row || !e.target.closest(".decision-main") || !row.dataset.key) return;
  const key = row.dataset.key;
  open.has(key) ? open.delete(key) : open.add(key);
  row.classList.toggle("open");
});
$("clear").onclick = async () => { await api("decisions/clear", {}); poll(); };

poll();
setInterval(poll, 1500);

// /guard?run=<id> follows a run that was started elsewhere (handy for sharing a run's steps).
const sharedRun = new URLSearchParams(location.search).get("run");
if (sharedRun) {
  runId = sharedRun;
  runTimer = setInterval(followRun, 700);
  followRun();
}

// --- the guard that teaches itself (reinforcement learning) ------------------------------
let band = "new", learned = null;
const BAND_NAMES = { new: "New shop", trusted: "Trusted", low: "Suspicious" };
const knobValue = (knob, v) => knob === "price_mult" ? `${v}x usual` : knob === "expiry_s" ? `${v} s`
  : knob === "look" ? `${v[0]}+${v[1]} chars` : money(v);

function drawKnobs() {
  if (!learned) return;
  document.querySelectorAll("#band-tabs button").forEach((b) => b.classList.toggle("on", b.dataset.band === band));
  const knobs = learned.blue.bands[band];
  $("knobs").innerHTML = Object.entries(knobs).filter(([k]) => band === "new" || k !== "new_shop_usd").map(([k, v]) => `
    <tr><td>${esc(v.title)}</td><td><div class="knob-cells">${v.levels.map((l) =>
      `<span class="${l.name === v.live ? "live" : ""} ${l.safe ? "" : "unsafe"}" title="${l.name}: score ${Math.round(l.score * 100)}% from ${l.evidence} bills">${knobValue(k, l.value)}</span>`).join("")}
    </div></td></tr>`).join("");
}

$("band-tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (b) { band = b.dataset.band; drawKnobs(); }
});

async function loadLearned() {
  const d = await api("learning");
  if (d.error || !d.blue) return;
  learned = d;
  const c = d.blue.counts;
  $("b-labelled").textContent = c.labelled;
  $("b-acc").textContent = d.blue.accuracy == null ? "–" : Math.round(d.blue.accuracy * 100) + "%";
  $("b-fb").textContent = c.false_blocks;
  $("b-missed").textContent = c.missed;
  const rounds = d.training.rounds_log;
  raceSummary($("race-summary"), rounds);
  lineChart($("race"), rounds.map((m) => m.round), [
    { name: "Attacker got paid (5-round avg)", cls: "attack", values: rollingAvg(rounds.map((m) => m.attacker_success_rate)) },
    { name: "Guard correct (5-round avg)", cls: "guard", values: rollingAvg(rounds.map((m) => m.guard_accuracy)) },
  ]);
  const bad = Object.entries(d.blue.bad_wallets).sort((a, b) => b[1].hits - a[1].hits);
  $("bad-count").textContent = bad.length ? `${bad.length} wallet${bad.length > 1 ? "s" : ""} took attack money and will not be paid again.`
    : "None yet.";
  $("bad-wallets").innerHTML = bad.slice(0, 6).map(([w, v]) => `${esc(walletName(w))} · ${v.hits} attack${v.hits > 1 ? "s" : ""}`).join("<br>");
  const shops = Object.entries(d.blue.trust).filter(([o]) => !/127\.0\.0\./.test(o));
  const aliases = Object.entries(d.blue.trust).filter(([o]) => /127\.0\.0\./.test(o)).length;
  $("trust").innerHTML = shops.map(([o, t]) => `<tr><td>${esc(shopName(o))}</td><td class="num">${t.honest}</td>
    <td class="num">${t.bad}</td><td>${BAND_NAMES[t.band]}</td></tr>`).join("") +
    (aliases ? `<tr><td colspan="4" class="small muted">plus ${aliases} brand-new shops the attacker set up during training</td></tr>` : "");
  drawKnobs();
  setTimeout(loadLearned, d.training.running ? 2000 : 8000);
}
loadLearned();
