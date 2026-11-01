// Rob the Robot: browser side. Talks only to /attack/* on this same server.
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const money = (n) => "$" + Number(n || 0).toFixed(2);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const shortAddr = (a) => (a && a.length > 12 ? a.slice(0, 6) + "…" + a.slice(-4) : a || "?");
  const LOW_WALLET = 20;

  const shown = { balance: null, loot: null };  // what the big numbers currently display
  let heists = {};       // id -> heist info (for step texts)
  let guard = "off";
  let busy = false;
  let localLoot = 0;     // fallback if the server doesn't report loot

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  async function api(path, body) {
    const opts = body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    };
    const data = await (await fetch("/attack" + path, opts)).json();
    if (data.mock) $("demo-badge").hidden = false;
    return data;
  }

  // Smoothly count a big number from what it shows now to `to`.
  function countTo(key, el, to) {
    const from = shown[key] == null ? to : shown[key];
    shown[key] = to;
    const start = performance.now(), dur = from === to ? 0 : 1000;
    (function frame(now) {
      const t = dur ? Math.min(1, (now - start) / dur) : 1;
      $(el).textContent = money(from + (to - from) * (1 - Math.pow(1 - t, 3)));
      if (t < 1) requestAnimationFrame(frame);
    })(start);
  }

  function setBalance(v) {
    countTo("balance", "balance", v);
    $("reset-low").hidden = !(v < LOW_WALLET);
  }

  function setLoot(v, gained) {
    countTo("loot", "loot", v);
    if (gained > 0) {
      const c = $("chaching");
      c.textContent = "+" + money(gained) + " cha-ching!";
      c.classList.remove("pop"); void c.offsetWidth; c.classList.add("pop");
      const box = $("loot-box");
      box.classList.remove("bump"); void box.offsetWidth; box.classList.add("bump");
    }
  }

  function flash(cls) {
    const w = $("wallet");
    w.classList.remove("flash-red", "flash-green"); void w.offsetWidth; w.classList.add(cls);
  }

  async function loadWallet() {
    const w = await api("/wallet");
    setBalance(Number(w.balance_usd));
    if (w.loot_usd !== undefined) localLoot = Number(w.loot_usd);
    setLoot(localLoot, 0);
  }

  // --- steps + banner -----------------------------------------------------------------
  const DEFAULT_STEPS = ["AI lured to …", "Bill: …", "Guard decision", "Money moved?"];

  function setStep(i, text, cls) {
    const li = $("steps").children[i];
    li.className = cls || "";
    li.querySelector("span").textContent = text;
  }

  function clearResult() {
    DEFAULT_STEPS.forEach((t, i) => setStep(i, t, ""));
    const b = $("banner");
    b.className = "banner idle";
    b.innerHTML = "Pick a heist on the left and press <b>Launch heist</b>.";
  }

  function guardTag(g) {
    return `<span class="gtag ${g}">Guard ${g.toUpperCase()}</span>`;
  }

  function showBanner(r, heist) {
    const g = r.guard || guard;
    const b = $("banner");
    const whyLabel = g === "on" ? "Why it was blocked" : "Why it worked";
    const why = r.why || (g === "on" ? heist.why_blocked : heist.why_worked) || "";
    let head, extra = "", cls;

    if (r.outcome === "blocked") {
      cls = "blocked";
      head = `\u{1F6E1} BLOCKED: ${esc(r.reason)}`;
    } else if (r.outcome === "stolen") {
      cls = "stolen";
      head = `\u{1F4B8} STOLEN ${money(r.amount_usd)}`;
      if (r.tx_hash) extra = `<div class="tx">This money really moved. Transaction ID: <code>${esc(r.tx_hash)}</code></div>`;
    } else if (r.outcome === "tricked") {
      cls = "tricked";
      head = "\u{1FA99} TRICKED, BUT WORTHLESS";
      extra = `<div class="sub">The AI signed a payment in a fake coin. A real attacker would use this
        to fool shops. Guard ON blocks it before signing.</div>`;
    } else {
      cls = "refused";
      head = `✋ REFUSED by shop: ${esc(r.reason)}`;
    }
    b.className = "banner " + cls;
    b.innerHTML = `${guardTag(g)}<div class="head">${head}</div>${extra}
      ${why ? `<div class="why"><b>${whyLabel}:</b> ${esc(why)}</div>` : ""}`;
  }

  // --- data loading -------------------------------------------------------------------
  function showGuard(g) {
    guard = g;
    document.querySelectorAll(".round").forEach((b) => b.classList.toggle("active", b.dataset.guard === g));
    document.body.classList.toggle("guard-on", g === "on");
  }

  async function loadMode() { showGuard((await api("/mode")).guard); }

  async function setMode(g) {
    if (busy || g === guard) return;
    showGuard((await api("/mode", { guard: g })).guard);
    clearResult();
  }

  async function loadAttacks() {
    const list = (await api("/attacks")).attacks || [];
    heists = Object.fromEntries(list.map((a) => [a.id, a]));
    $("cards").innerHTML = list.map((a) => `
      <div class="card">
        <div class="cardtop"><h3>${esc(a.name)}</h3><span class="diff ${esc(a.difficulty)}">${esc(a.difficulty)}</span></div>
        <p>${esc(a.plan)}</p>
        <button class="btn" data-id="${esc(a.id)}">Launch heist</button>
      </div>`).join("");
    $("cards").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => fire(b.dataset.id)));
  }

  async function loadBoard() {
    const rows = (await api("/scoreboard")).scores || [];
    $("board").innerHTML = rows.length ? rows.map((r) => {
      const on = Number(r.stolen_on_usd || 0);
      return `<tr><td>${esc(r.player)}</td><td class="off">${money(r.stolen_off_usd)}</td>
        <td class="${on > 0 ? "on-hit" : "on-zero"}">${money(on)}${on > 0 ? " \u{1F3C6}" : " ✔"}</td></tr>`;
    }).join("") : `<tr><td colspan="3" class="muted">No heists yet.</td></tr>`;
  }

  // --- firing a heist -----------------------------------------------------------------
  async function fire(id) {
    if (busy) return;
    busy = true;
    document.querySelectorAll(".card .btn, .round").forEach((b) => (b.disabled = true));
    clearResult();
    const heist = heists[id] || {};
    const b = $("banner");
    b.className = "banner idle";
    b.textContent = `Running "${heist.name || id}"…`;

    try {
      const player = $("player").value.trim() || "Anonymous";
      const call = api("/fire", { attack_id: id, player });
      setStep(0, `AI lured to ${heist.shop || "the hacker's shop"}`, "lit");
      await sleep(600);
      const r = await call;
      const g = r.guard || guard;
      const shop = r.shop || heist.shop;
      if (shop) setStep(0, `AI lured to ${shop}`, "lit");
      const bill = r.bill_label || heist.bill_label || money(r.bill_usd || heist.bill_usd);
      setStep(1, `Bill: ${bill} to ${shortAddr(r.pay_to || heist.pay_to)}`, "lit");
      await sleep(700);

      if (g === "off") setStep(2, "Guard OFF: no check", "bad");
      else if (r.outcome === "blocked") setStep(2, `Guard ON: BLOCKED (${r.reason})`, "good");
      else setStep(2, "Guard ON: allowed", "bad");
      await sleep(700);

      if (r.outcome === "stolen") setStep(3, `Yes: ${money(r.amount_usd)} went to the hacker`, "bad");
      else if (r.outcome === "tricked") setStep(3, "No money moved (fake coin is worthless)", "warn");
      else setStep(3, "No money moved", "good");

      showBanner(r, heist);
      if (r.outcome === "stolen") flash("flash-red");
      else if (r.outcome === "blocked") flash("flash-green");

      if (r.balance_usd !== undefined) setBalance(Number(r.balance_usd));
      const gained = r.outcome === "stolen" ? Number(r.amount_usd || 0) : 0;
      localLoot = r.loot_usd !== undefined ? Number(r.loot_usd) : localLoot + gained;
      setLoot(localLoot, gained);
      if (r.balance_usd === undefined) await loadWallet();
      await loadBoard();
    } catch (e) {
      b.className = "banner refused";
      b.textContent = "Couldn't reach the attack server. Try again.";
    } finally {
      busy = false;
      document.querySelectorAll(".card .btn, .round").forEach((x) => (x.disabled = false));
    }
  }

  async function resetGame() {
    if (busy) return;
    const w = await api("/reset", {});
    localLoot = 0;
    setBalance(Number(w.balance_usd !== undefined ? w.balance_usd : 100));
    setLoot(0, 0);
    clearResult();
  }

  // --- wire up ------------------------------------------------------------------------
  document.querySelectorAll(".round").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.guard)));
  $("reset").addEventListener("click", resetGame);
  $("reset-low").addEventListener("click", resetGame);
  $("player").value = localStorage.getItem("rtr-player") || "";
  $("player").addEventListener("input", () => localStorage.setItem("rtr-player", $("player").value));

  loadWallet(); loadMode(); loadAttacks(); loadBoard();
  setInterval(() => { if (!busy) loadBoard(); }, 5000);
})();
