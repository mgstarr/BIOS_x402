"""Entry point: `python app.py <service>` starts the named service: shop, evil, dashboard, agent, attacker, defender."""

import sys

from dotenv import load_dotenv


def prefer_ipv4() -> None:
    """Windows tries "localhost" as IPv6 (::1) first and waits ~2 s per call before falling back to
    IPv4, where our Flask services listen. Make outgoing HTTP calls resolve to IPv4 only."""
    if sys.platform != "win32":
        return
    import socket

    import urllib3.util.connection as conn

    conn.allowed_gai_family = lambda: socket.AF_INET


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python app.py <service>")
        sys.exit(1)

    load_dotenv()  # read .env into environment variables before any service starts
    prefer_ipv4()

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
    elif service == "defender":  # payment guard in front of the signer (port 7003)
        from defender.server import run

        run()
    else:
        print(f"TODO: start service '{service}'")


if __name__ == "__main__":
    main()
