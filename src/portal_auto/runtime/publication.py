"""Is a published page really public? Open it the way a stranger would: no cookies, no login.

The address comes off a web page, so it is only fetched when it points at a public host (not loopback, a
private network, or a link-local address), and a redirect to an internal host is refused too."""
import ipaddress
import re
import socket
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from portal_auto.db.models import PublicationStatus

PUBLISHED = PublicationStatus.PUBLISHED
NOT_PUBLIC = PublicationStatus.NOT_PUBLIC
UNREACHABLE = PublicationStatus.UNREACHABLE

CHECK_TIMEOUT_SECONDS = 15
_BROWSER_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
_SIGN_IN_PATH = re.compile(r"login|log-in|signin|sign-in|sign_in", re.IGNORECASE)


@dataclass(frozen=True)
class PublicationCheck:
    status: str
    detail: str


    @property
    def log_line(self) -> str:
        """The line the worker writes into the Run Log; `Run.publication_status` reads it back."""
        return f"Publication status: {self.status} - {self.detail}"


class _InternalAddress(Exception):
    pass


def resolve_host(host: str) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})


def is_internal_host(host: str) -> bool:
    """True for loopback, private, link-local and other non-public addresses (resolving the name if needed)."""
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        addresses = [ipaddress.ip_address(address) for address in resolve_host(host)]
    return any(
        a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_multicast or a.is_unspecified
        for a in addresses
    )


def _guarded_opener(is_internal: Callable[[str], bool]) -> urllib.request.OpenerDirector:
    class GuardedRedirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if is_internal(urlsplit(newurl).hostname or ""):
                raise _InternalAddress
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return urllib.request.build_opener(GuardedRedirects)  # no cookie processor: the request is anonymous


def _same_site(first: str, second: str) -> bool:
    return first.removeprefix("www.") == second.removeprefix("www.")


def _classify(original: str, final: str, status: int) -> PublicationCheck:
    if status >= 500:
        return PublicationCheck(UNREACHABLE, f"the site answered with HTTP {status}")
    if status >= 400:
        return PublicationCheck(NOT_PUBLIC, f"HTTP {status} without signing in")
    before, after = urlsplit(original), urlsplit(final)
    if not _same_site((before.hostname or "").lower(), (after.hostname or "").lower()):
        return PublicationCheck(NOT_PUBLIC, f"redirected to another site ({after.hostname}), probably a sign-in")
    if _SIGN_IN_PATH.search(after.path) and not _SIGN_IN_PATH.search(before.path):
        return PublicationCheck(NOT_PUBLIC, "redirected to a sign-in page")
    return PublicationCheck(PUBLISHED, f"the page loads without signing in (HTTP {status})")


def check_public_page(
    url: str,
    timeout: float = CHECK_TIMEOUT_SECONDS,
    is_internal: Callable[[str], bool] = is_internal_host,
) -> PublicationCheck:
    parts = urlsplit((url or "").strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return PublicationCheck(UNREACHABLE, "only web addresses (http or https) can be checked")
    try:
        if is_internal(parts.hostname):
            return PublicationCheck(UNREACHABLE, "not checked: that is an internal address")
        request = urllib.request.Request(url.strip(), headers={"User-Agent": _BROWSER_USER_AGENT})
        try:
            with _guarded_opener(is_internal).open(request, timeout=timeout) as response:
                return _classify(url.strip(), response.geturl(), response.status)
        except urllib.error.HTTPError as error:
            return _classify(url.strip(), error.url or url.strip(), error.code)
    except _InternalAddress:
        return PublicationCheck(UNREACHABLE, "not checked: the page redirected to an internal address")
    except urllib.error.URLError as error:
        if isinstance(error.reason, _InternalAddress):
            return PublicationCheck(UNREACHABLE, "not checked: the page redirected to an internal address")
        return PublicationCheck(UNREACHABLE, "the site did not answer")
    except (OSError, ValueError):
        return PublicationCheck(UNREACHABLE, "the site did not answer")
