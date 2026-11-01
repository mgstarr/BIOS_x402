"""Origin checks: validates that a payment request comes from an expected, trusted shop."""

from urllib.parse import urlsplit

DEFAULT_PORTS = {"http": 80, "https": 443}


def origin_of(url: str | None) -> str | None:
    """'http://LOCALHOST:5001/api/price?x=1' -> 'http://localhost:5001'. None if not a URL."""
    if not url:
        return None
    try:
        parts = urlsplit(str(url).strip())
        scheme, host, port = parts.scheme.lower(), (parts.hostname or "").lower(), parts.port
    except ValueError:  # e.g. a port that isn't a number
        return None
    if not scheme or not host:
        return None
    port = port or DEFAULT_PORTS.get(scheme)
    return f"{scheme}://{host}:{port}" if port else f"{scheme}://{host}"


def path_of(url: str | None) -> str:
    """Path part of a URL without the query string ('/api/price')."""
    try:
        return urlsplit(str(url or "")).path or "/"
    except ValueError:
        return "/"


def check_origin(intended_url: str | None, final_url: str | None) -> tuple[bool, str]:
    """Pass if the agent ended up at the same site it meant to visit (skipped if either is missing)."""
    if not intended_url or not final_url:
        return True, "skipped: no intended/final URL given"
    want, got = origin_of(intended_url), origin_of(final_url)
    if want is None or got is None:
        return False, "Could not read the website address of the bill"
    if want != got:
        return False, f"The agent meant to buy from {want} but was sent to {got}"
    return True, f"same site ({got})"
