"""Dashboard Flask app (port 8000): landing page + Attack Lab + Defender views.

Start it with:   python app.py dashboard     then open http://localhost:8000

  /          landing page with three big links
  /attack    "Rob the Robot" game (dashboard/attacker.py)
  /defend    Defender view (dashboard/defender.py, built by Person B; placeholder if missing)
"""

from flask import Flask

from dashboard.layout import LIVE_URL, render_page

LANDING = f"""
<section class="landing">
  <h1>Can you rob an AI's wallet?</h1>
  <p class="lead">An AI agent pays for data on its own, with real (test) money.
     We built a guard that stops cheaters. Try to beat it.</p>
  <div class="bigcards">
    <a class="bigcard red" href="/attack"><span class="icon">&#9760;</span>
      <b>Attack Lab</b><small>Play "Rob the Robot": try to steal from the AI</small></a>
    <a class="bigcard blue" href="/defend"><span class="icon">&#128737;</span>
      <b>Defender</b><small>See what the guard checks and why it said no</small></a>
    <a class="bigcard green" href="{LIVE_URL}" target="_blank"><span class="icon">&#9889;</span>
      <b>TickToll Live</b><small>The honest shop, selling data for 1 cent</small></a>
  </div>
</section>
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
        return render_page("Home", LANDING)

    @app.get("/health")
    def health():
        return {"ok": True, "service": "dashboard"}

    return app


def run() -> None:
    create_app().run(host="0.0.0.0", port=8000)
