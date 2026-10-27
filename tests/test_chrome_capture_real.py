"""Opt-in (-m chrome): real headless Chrome captures a cookie set by a local page (S6)."""
import json
import os
import shutil
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from playwright.sync_api import sync_playwright

from portal_auto.runtime.chrome import LOOPBACK, CdpStateReader, RealChromeLauncher
from portal_auto.services.capture import CaptureService

pytestmark = pytest.mark.chrome


class CookieHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Set-Cookie", "sid=real-capture-value; Path=/")
        self.end_headers()
        self.wfile.write(b"<p>signed in</p>")

    def log_message(self, *args):
        pass


@pytest.fixture
def cookie_site():
    server = HTTPServer(("127.0.0.1", 0), CookieHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_real_chrome_capture_returns_the_cookie_and_removes_the_profile(cookie_site, tmp_path):
    if not shutil.which("google-chrome"):
        pytest.skip("google-chrome is not installed")
    service = CaptureService(
        RealChromeLauncher(extra_args=("--headless=new",)), CdpStateReader(), temp_root=tmp_path
    )
    info = service.start(1, cookie_site)
    try:
        with socket.create_connection((LOOPBACK, info.port), timeout=2):
            pass
        state = json.loads(service.finish(info.id))
    finally:
        service.shutdown()
    assert any(cookie["name"] == "sid" and cookie["value"] == "real-capture-value" for cookie in state["cookies"])
    assert list(tmp_path.iterdir()) == []


def test_the_launched_chrome_does_not_report_itself_as_automated(tmp_path):
    """Regression: --remote-debugging-port=0 made navigator.webdriver true, and Google then refuses sign-in."""
    if not shutil.which("google-chrome"):
        pytest.skip("google-chrome is not installed")
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        pytest.skip("needs a display: headless Chrome always reports webdriver")
    profile = tmp_path / "profile"
    profile.mkdir()
    handle = RealChromeLauncher().launch("about:blank", profile)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(f"http://{LOOPBACK}:{handle.port}")
            webdriver = browser.contexts[0].pages[0].evaluate("navigator.webdriver")
            browser.close()
    finally:
        handle.terminate()
    assert webdriver is False
