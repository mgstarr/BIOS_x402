"""Entry point: `python app.py <service>` starts the named service: shop, evil, dashboard, agent, attacker."""

import sys

from dotenv import load_dotenv


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python app.py <service>")
        sys.exit(1)

    load_dotenv()  # read .env into environment variables before any service starts

    service = sys.argv[1]
    if service == "shop":
        from shops.server import run

        run()
    elif service == "evil":
        from shops.malicious import run

        run()
    elif service == "dashboard":
        from dashboard.app import run

        run()
    elif service == "agent":  # Person A: agent (port 7000)
        from agent.agent import app, payment_mode

        print(payment_mode())
        app.run(host="0.0.0.0", port=7000)
    elif service == "attacker":  # Person B: attacker engine (port 7004)
        from attacker.server import app

        app.run(host="0.0.0.0", port=7004)
    else:
        print(f"TODO: start service '{service}'")


if __name__ == "__main__":
    main()
