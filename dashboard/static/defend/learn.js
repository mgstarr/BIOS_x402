// Self-learning panel. Talks to /defend/api/learning|train|train/stop|learning/reset.
(() => {
  const $ = (id) => document.getElementById(id);
  if (!$("learn")) return;
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (x) => (x == null ? "–" : Math.round(x * 100) + "%");
  const post = (path, body) => fetch("api/" + path, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}) }).then((r) => r.json().catch(() => ({})));
  const TITLE = { overpayment: "Overpayment", wrong_party: "Wrong party", trick_agent: "Tricking the agent" };

  function chart(rounds) {
    if (!rounds.length) return "<p class='muted empty'>Waiting for the first round…</p>";
    const W = 300, H = 150, P = 14, n = rounds.length;
    const x = (i) => P + (n === 1 ? (W - 2 * P) / 2 : (i * (W - 2 * P)) / (n - 1));
    const y = (v) => H - P - v * (H - 2 * P);
    const line = (key, color) => {
      const pts = rounds.map((r, i) => [x(i), y(r[key] == null ? 0 : r[key])]);
      return `<polyline fill="none" stroke="${color}" stroke-width="3" points="${pts.map((p) => p.join(",")).join(" ")}"/>` +
        pts.map((p) => `<circle cx="${p[0]}" cy="${p[1]}" r="3.5" fill="${color}"/>`).join("");
    };
    return `<svg viewBox="0 0 ${W} ${H}"><line x1="${P}" x2="${W - P}" y1="${y(0)}" y2="${y(0)}" stroke="#2c313b"/>` +
      `<line x1="${P}" x2="${W - P}" y1="${y(1)}" y2="${y(1)}" stroke="#2c313b" stroke-dasharray="3"/>` +
      line("attacker_success_rate", "#ff3b4a") + line("guard_accuracy", "#2fd67b") + "</svg>";
  }

  function redView(red) {
    const fams = Object.entries(red.families).map(([f, v]) =>
      `<div class="lbar"><b>${TITLE[f] || f}</b><div class="track"><div class="fill" style="width:${v.mean * 100}%"></div></div><span>${pct(v.mean)}</span></div>`).join("");
    const top = Object.entries(red.tactics).flatMap(([f, ts]) => Object.entries(ts).map(([t, v]) => ({ f, t, ...v })))
      .filter((x) => x.pulls > 0).sort((a, b) => b.breaches - a.breaches || b.mean - a.mean).slice(0, 6);
    return fams + (top.length ? `<table class="ltable"><tr><th>top tactic</th><th>tried</th><th>won</th></tr>` +
      top.map((x) => `<tr><td>${esc(x.t)}</td><td>${x.pulls}</td><td>${x.breaches}</td></tr>`).join("") + "</table>" : "");
  }

  function blueView(blue) {
    if (!blue) return "<p class='muted empty'>Guard not reachable.</p>";
    const names = Object.keys(blue.profiles);
    let h = `<table class="ltable"><tr><th>shop trust</th>${names.map((n) => `<th>${n}</th>`).join("")}</tr>`;
    for (const band of ["new", "low", "trusted"]) {
      h += `<tr><td><b>${band}</b></td>` + names.map((n) => {
        const a = blue.bandits[band][n];
        return `<td class="${blue.live_profile[band] === n ? "live" : ""}">${pct(a.mean)} <span class="muted">(${a.pulls})</span></td>`;
      }).join("") + "</tr>";
    }
    return h + `</table><p class="muted">green = live profile · accuracy ${pct(blue.accuracy)} · ` +
      `${blue.counts.rollbacks} rollbacks · ${blue.counts.false_blocks} false blocks · ${blue.counts.missed} missed</p>`;
  }

  function trustView(blue) {
    const rows = Object.entries((blue && blue.trust) || {}).map(([o, t]) => ({ o, ...t }))
      .sort((a, b) => b.honest + b.bad - (a.honest + a.bad)).slice(0, 6);
    return rows.length ? `<table class="ltable"><tr><th>shop</th><th>ok</th><th>bad</th><th>score</th><th>band</th></tr>` +
      rows.map((r) => `<tr><td>${esc(r.o.replace("http://", ""))}</td><td>${r.honest}</td><td>${r.bad}</td><td>${r.score}</td><td>${r.band}</td></tr>`).join("") + "</table>"
      : "<p class='muted empty'>No shops seen yet.</p>";
  }

  function discView(list) {
    if (!list.length) return "None yet.";
    return list.slice().reverse().slice(0, 8).map((d) =>
      `<div class="disc"><b>${esc(d.id)}</b> · ${esc(d.text)}<br><code>${esc(d.tx)}</code></div>`).join("");
  }

  async function refresh() {
    let d;
    try { d = await (await fetch("api/learning")).json(); } catch { return; }
    if (d.error || !d.training) { $("l-status").textContent = "Attacker engine not reachable."; return; }
    const t = d.training;
    $("l-train").disabled = t.running;
    $("l-stop").hidden = !t.running;
    $("l-status").innerHTML = t.error ? `<b style="color:var(--red)">${esc(t.error)}</b>`
      : t.running ? `Training… round <b>${t.round}/${t.rounds}</b> · ${t.done} heists done`
      : t.rounds_log.length ? `Done: ${t.rounds_log.length} rounds. The demo wallet was restored (chain snapshot reverted: ${t.reverted ? "yes" : "no"}).`
      : "Not trained yet. Press <b>Train</b>.";
    $("l-chart").innerHTML = chart(t.rounds_log);
    $("l-red").innerHTML = redView(d.red);
    $("l-blue").innerHTML = blueView(d.blue);
    $("l-trust").innerHTML = trustView(d.blue);
    $("l-disc").innerHTML = discView(d.discoveries || []);
  }

  $("l-train").onclick = async () => {
    const r = await post("train", { rounds: +$("l-rounds").value, heists: +$("l-heists").value });
    if (r.error) $("l-status").textContent = r.error;
    refresh();
  };
  $("l-stop").onclick = () => post("train/stop");
  $("l-reset").onclick = async () => { const r = await post("learning/reset"); if (r.error) $("l-status").textContent = r.error; refresh(); };
  refresh();
  setInterval(refresh, 3000);
})();
