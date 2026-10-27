"""Which pages the agent may open. Page content is untrusted, so the agent is confined to the Portal's origin
plus any hosts the operator declared for it (e.g. a single sign-on provider)."""
from collections.abc import Sequence
from urllib.parse import urlsplit

ALLOWED_SCHEMES = ("http", "https")
_DEFAULT_PORTS = {"http": 80, "https": 443}

Origin = tuple[str, str, int]


def origin_of(url: str) -> Origin:
    """(scheme, host, port) of an http(s) URL; ValueError for any other scheme (file:, data:, javascript: ...)."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES or not parts.hostname:
        raise ValueError(f"URL scheme not allowed: only http(s) URLs may be opened, got {scheme or 'none'}:")
    return scheme, parts.hostname.lower(), parts.port or _DEFAULT_PORTS[scheme]


def web_address(url: str | None) -> str | None:
    """`url` if it is an http(s) address, else None."""
    try:
        origin_of(url or "")
    except ValueError:
        return None
    return url


def is_allowed(url: str, origin: Origin, extra_hosts: Sequence[str] = ()) -> bool:
    """The start origin, or a declared extra host (or its subdomain) on the start origin's scheme."""
    try:
        candidate = origin_of(url)
    except ValueError:
        return False
    if candidate == origin:
        return True
    scheme, host, _ = candidate
    return scheme == origin[0] and any(host == extra or host.endswith(f".{extra}") for extra in extra_hosts)
