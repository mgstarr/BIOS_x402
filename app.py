"""Entry point: `python app.py <service>` starts the named service (agent, signer, ...)."""

import sys


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python app.py <service>")
        sys.exit(1)

    service = sys.argv[1]
    print(f"TODO: start service '{service}'")


if __name__ == "__main__":
    main()
