"""Dashboard Flask app (port 8000): Demo landing page + Attack Lab + Defender views.

Start it with:   python app.py dashboard     then open http://localhost:8000

  /              Demo page: three big tiles + "Start new demo" (resets everything)
  /attack        "Rob the Robot" game (dashboard/attacker.py)
  /defend        Defender view (dashboard/defender.py; placeholder if missing)
  POST /api/new-demo   reset the defender, the attacker engine (wallet + scoreboard) and page state
"""

import os

import requests
from flask import Flask, jsonify

from dashboard.layout import LIVE_URL, render_page

DEFENDER_URL = os.getenv("DEFENDER_URL", "http://localhost:7003").rstrip("/")
ATTACKER_URL = os.getenv("ATTACKER_URL", "http://localhost:7004").rstrip("/")

LANDING = f"""
<section class="landing demo">
  <h1>Can you rob an AI's wallet?</h1>
  <p class="lead">An AI agent pays for data on its own, with real (test) money.
     We built a guard that stops cheaters. Try to beat it.</p>
  <div class="bigcards">
    <div class="bigcard red"><span class="icon">&#9760;</span>
      <b>Rob the Robot</b><small>Try to steal from an AI's wallet</small>
      <p>Pick a trick, send the AI a fake bill, and see if the money lands in your pocket.</p>
      <a class="btn" href="/attack">Play the game</a></div>
    <div class="bigcard blue"><span class="icon">&#128737;</span>
      <b>Defender</b><small>Watch every bill get checked</small>
      <p>Every bill passes 8 quick checks before the AI is allowed to pay it.</p>
      <a class="btn blue" href="/defend">Open the Defender</a></div>
    <div class="bigcard green"><span class="icon">&#9889;</span>
      <b>TickToll</b><small>The honest shop the AI buys from</small>
      <p>A real data shop that sells coin prices to AI agents for 1 cent each.</p>
      <a class="btn green" href="{LIVE_URL}" target="_blank">Visit TickToll &#8599;</a></div>
  </div>
  <button class="btn startdemo" id="newdemo" type="button">&#8635; Start new demo</button>
  <p class="muted small-note">Resets the wallet, the leaderboard, the guard and every live feed.</p>
</section>
<div class="toast" id="toast" role="status" aria-live="polite" hidden></div>
<script>
document.getElementById("newdemo").addEventListener("click", async (ev) => {{
  const b = ev.currentTarget, t = document.getElementById("toast");
  b.disabled = true; b.textContent = "Resetting…";
  let r = {{}};
  try {{ r = await (await fetch("/api/new-demo", {{method: "POST"}})).json(); }}
  catch (e) {{ r = {{problems: ["dashboard not reachable"]}}; }}
  localStorage.removeItem("rtr-player");
  const bad = r.problems || [];
  t.innerHTML = "<b>Ready for the next judge</b>" +
    (bad.length ? "<small>Could not reset: " + bad.join(", ") + "</small>" : "");
  t.className = "toast show" + (bad.length ? " warn" : "");
  t.hidden = false;
  clearTimeout(window._toast);
  window._toast = setTimeout(() => {{ t.className = "toast"; }}, 3500);
  b.disabled = false; b.innerHTML = "&#8635; Start new demo";
}});
</script>
"""

DEFEND_PLACEHOLDER = """
<section class="landing">
  <h1>Defender view</h1>
  <p class="lead">Coming soon: this page is being built by the defender team.</p>
  <p><a class="btn" href="/attack">Go to the Attack Lab</a></p>
</section>
"""


def create_app() -> Flask:
    app = Flask(__name__, static_folder="assets", static_url_path="/assets")

    from dashboard.attacker import bp as attacker_bp
    app.register_blueprint(attacker_bp, url_prefix="/attack")

    # Person B's blueprint. If it's missing or broken, the dashboard still starts.
    try:
        from dashboard.defender import bp as defender_bp
        app.register_blueprint(defender_bp, url_prefix="/defend")
    except Exception as exc:  # noqa: BLE001 - any import problem -> show placeholder
        print(f"[dashboard] defender view not loaded ({exc.__class__.__name__}); using placeholder")

        @app.get("/defend")
        @app.get("/defend/")
        def defend_placeholder():
            return render_page("Defender", DEFEND_PLACEHOLDER, active="defend", theme="blue")

    @app.get("/")
    def landing():
        return render_page("Demo", LANDING, active="demo", theme="demo")

    @app.post("/api/new-demo")
    def new_demo():
        """Reset everything for the next judge. Lists the parts that could not be reset."""
        problems = []
        for name, url, body in (("defender", f"{DEFENDER_URL}/reset", {}),
                                ("attack engine", f"{ATTACKER_URL}/reset", {"all": True})):
            try:
                requests.post(url, json=body, timeout=5).raise_for_status()
            except Exception:  # noqa: BLE001 - service down: report it, keep going
                problems.append(name)
        for module in ("dashboard.attacker", "dashboard.defender"):
            try:
                __import__(module, fromlist=["reset_local"]).reset_local()
            except Exception:  # noqa: BLE001
                problems.append(module.split(".")[1] + " page")
        return jsonify({"ok": not problems, "problems": problems})

    @app.get("/health")
    def health():
        return {"ok": True, "service": "dashboard"}

    return app


def run() -> None:
    create_app().run(host="0.0.0.0", port=8000)
