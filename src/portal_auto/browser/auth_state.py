"""Is the browser signed in? Evidence decides: a sign-out control proves it, a sign-in form disproves it, and
the mere absence of a login button proves nothing."""
from urllib.parse import urlsplit

AUTHENTICATED = "authenticated"
UNAUTHENTICATED = "unauthenticated"
UNKNOWN = "unknown"

_EVIDENCE_JS = """
() => {
  const visible = el => { const b = el.getBoundingClientRect(), s = getComputedStyle(el);
    return b.width > 0 && b.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const labelOf = el => (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().toLowerCase();
  const controls = [...document.querySelectorAll('a,button,[role=button],[role=link],[role=menuitem],input[type=submit]')]
    .filter(visible).map(labelOf);
  const passwordField = [...document.querySelectorAll('input[type=password]')].some(visible);
  const text = document.body.innerText.toLowerCase().slice(0, 3000);
  return {controls, passwordField, text};
}
"""
_SIGN_OUT = ("log out", "logout", "sign out", "signout", "log off")
_SIGN_IN = ("log in", "login", "sign in", "signin")
_CHALLENGE = ("verify you are human", "i'm not a robot", "enter the code", "verification code", "captcha")


def classify(evidence: dict) -> str:
    """UNAUTHENTICATED beats AUTHENTICATED: a page showing a sign-in form is not a signed-in page."""
    controls, text = evidence["controls"], evidence["text"]
    if evidence["passwordField"] or any(phrase in text for phrase in _CHALLENGE):
        return UNAUTHENTICATED
    if any(label in _SIGN_IN for label in controls):
        return UNAUTHENTICATED
    if any(label in _SIGN_OUT for label in controls):
        return AUTHENTICATED
    return UNKNOWN


def authentication_state(page, expected_host: str) -> str:
    """UNKNOWN on any host other than `expected_host` (a sign-in provider's page says nothing about the portal)."""
    host = (urlsplit(page.url).hostname or "").lower().removeprefix("www.")
    if host != expected_host.lower().removeprefix("www."):
        return UNKNOWN
    return classify(page.evaluate(_EVIDENCE_JS))
