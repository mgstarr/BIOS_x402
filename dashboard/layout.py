"""Shared page shell for every dashboard page: <head>, local CSS, and the top nav.

Any blueprint (attacker.py, defender.py) calls render_page(...) so all pages look
the same and share one nav bar. Everything is local: no CDN links (venue Wi-Fi may fail).
"""

from flask import render_template_string

LIVE_URL = "http://localhost:5001/live"

SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }} | x402 Red/Blue</title>
<link rel="stylesheet" href="/assets/style.css">
</head>
<body class="theme-{{ theme }}">
<nav class="topnav">
  <a class="brand" href="/">x402 Red/Blue</a>
  <a href="/attack" class="{{ 'on' if active == 'attack' }}">Attack Lab</a>
  <a href="/defend" class="{{ 'on' if active == 'defend' }}">Defender</a>
  <a href="{{ live_url }}" target="_blank">TickToll Live &#8599;</a>
</nav>
<main>
{{ body|safe }}
</main>
{% for src in scripts %}<script src="{{ src }}"></script>{% endfor %}
</body>
</html>"""


def render_page(title: str, body: str, active: str = "", theme: str = "plain",
                scripts: tuple = ()) -> str:
    """Wrap a page's HTML body in the shared shell (head + nav)."""
    return render_template_string(SHELL, title=title, body=body, active=active,
                                  theme=theme, scripts=scripts, live_url=LIVE_URL)
