"""Operator-declared extra hosts (e.g. an SSO provider) widen what the agent may visit, and nothing else."""
import pytest

from portal_auto.browser.origin import is_allowed, origin_of

START = origin_of("https://www.behance.net/")


def test_the_start_origin_is_always_allowed():
    assert is_allowed("https://www.behance.net/gallery", START, ())


@pytest.mark.parametrize("url", ["https://adobe.com/", "https://auth.services.adobe.com/en/login"])
def test_a_declared_host_and_its_subdomains_are_allowed(url):
    assert is_allowed(url, START, ("adobe.com",))


@pytest.mark.parametrize("url", [
    "https://evil-adobe.com/", "https://adobe.com.evil.test/", "https://notadobe.com/", "https://other.example/",
])
def test_lookalike_and_undeclared_hosts_are_refused(url):
    assert not is_allowed(url, START, ("adobe.com",))


def test_an_extra_host_must_use_the_start_scheme():
    assert not is_allowed("http://adobe.com/", START, ("adobe.com",))


@pytest.mark.parametrize("url", ["file:///etc/passwd", "javascript:alert(1)", "data:text/html,x"])
def test_non_http_urls_are_never_allowed(url):
    assert not is_allowed(url, START, ("adobe.com",))
