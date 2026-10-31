"""Entry point: `python app.py <service>` starts the named service (agent, signer, ...)."""

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
    else:
        print(f"TODO: start service '{service}'")


if __name__ == "__main__":
    main()
