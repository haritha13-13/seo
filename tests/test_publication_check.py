"""The public-access check: open the published address the way a stranger would (no cookies, no login)."""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from portal_auto.runtime.publication import NOT_PUBLIC, PUBLISHED, UNREACHABLE, check_public_page

NOT_INTERNAL = lambda host: False  # the fixture site is local, so the internal-address guard is switched off for it


@pytest.fixture
def site():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append({"path": self.path, "cookie": self.headers.get("Cookie")})
            if self.path == "/deck":
                self._send(200, b"<h1>My deck</h1>")
            elif self.path == "/members-only":
                self.send_response(302)
                self.send_header("Location", "/login?next=/members-only")
                self.end_headers()
            elif self.path.startswith("/login"):
                self._send(200, b"<h1>Sign in</h1>")
            elif self.path == "/old":
                self.send_response(301)
                self.send_header("Location", "/deck")
                self.end_headers()
            elif self.path == "/slow":
                import time
                time.sleep(3)
                self._send(200, b"late")
            elif self.path == "/broken":
                self._send(500, b"oops")
            else:
                self._send(404, b"gone")

        def _send(self, status, body):
            self.send_response(status)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", seen
    server.shutdown()


def check(url, **kwargs):
    return check_public_page(url, is_internal=NOT_INTERNAL, **kwargs)


def test_a_page_that_loads_without_signing_in_is_published(site):
    base, seen = site
    result = check(base + "/deck")
    assert result.status == PUBLISHED
    assert seen[0]["cookie"] is None, "the check must not carry any cookies"


def test_a_redirect_to_the_same_public_page_is_followed(site):
    assert check(site[0] + "/old").status == PUBLISHED


def test_a_redirect_to_a_sign_in_page_is_not_public(site):
    result = check(site[0] + "/members-only")
    assert result.status == NOT_PUBLIC and "sign" in result.detail.lower()


@pytest.mark.parametrize("path", ["/missing", "/gone"])
def test_a_missing_page_is_not_public(site, path):
    result = check(site[0] + path)
    assert result.status == NOT_PUBLIC and "404" in result.detail


def test_a_server_error_or_a_slow_site_means_it_could_not_be_checked(site):
    assert check(site[0] + "/broken").status == UNREACHABLE
    assert check(site[0] + "/slow", timeout=1).status == UNREACHABLE


def test_a_site_that_does_not_answer_at_all_could_not_be_checked():
    assert check("http://127.0.0.1:1/never").status == UNREACHABLE


@pytest.mark.parametrize("url", ["http://127.0.0.1:5001/portals/", "http://localhost/x", "http://169.254.169.254/latest",
                                 "http://10.0.0.5/internal", "http://[::1]/x"])
def test_internal_addresses_are_never_fetched(url):
    result = check_public_page(url)  # the real resolver: these hosts are loopback/private/link-local
    assert result.status == UNREACHABLE and "internal" in result.detail.lower()


@pytest.mark.parametrize("url", ["file:///etc/passwd", "javascript:alert(1)", "ftp://x.example/a", "not a url", ""])
def test_only_web_addresses_are_checked(url):
    assert check_public_page(url).status == UNREACHABLE
