"""RealChromeLauncher, exercised with a stand-in `chrome` script that records how it was started."""
import json
import stat
import sys
import pytest

from portal_auto.runtime.chrome import RealChromeLauncher, free_loopback_port
from portal_auto.services.errors import Invalid

FAKE_CHROME = f"""#!{sys.executable}
import json, os, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
args = sys.argv[1:]
profile = next(a.split("=", 1)[1] for a in args if a.startswith("--user-data-dir="))
port = int(next(a.split("=", 1)[1] for a in args if a.startswith("--remote-debugging-port=")))
json.dump(args, open(os.path.join(profile, "..", "argv.json"), "w"))
time.sleep(0.3)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({{"Browser": os.environ.get("FAKE_BROWSER", "Chrome/156.0.0.0")}}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""


@pytest.fixture
def fake_chrome(tmp_path):
    path = tmp_path / "fake-chrome"
    path.write_text(FAKE_CHROME)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@pytest.fixture
def profile(tmp_path):
    directory = tmp_path / "profile"
    directory.mkdir()
    return directory


def test_chrome_is_started_on_an_explicit_port_because_port_zero_makes_it_report_itself_as_automated(
    fake_chrome, profile, tmp_path
):
    port = free_loopback_port()
    handle = RealChromeLauncher(fake_chrome, pick_port=lambda: port).launch("https://x.test", profile)
    try:
        args = json.loads((tmp_path / "argv.json").read_text())
    finally:
        handle.terminate()

    assert f"--remote-debugging-port={port}" in args and "--remote-debugging-port=0" not in args
    assert f"--user-data-dir={profile}" in args and "--no-first-run" in args and args[-1] == "https://x.test"
    assert not [a for a in args if a.startswith("--remote-debugging-address") or a == "--no-default-browser-check"]
    assert handle.port == port


def test_a_different_program_holding_the_port_is_detected_and_chrome_is_stopped(fake_chrome, profile, monkeypatch):
    monkeypatch.setenv("FAKE_BROWSER", "NotChrome/1")  # whatever answers on the port is not Chrome

    with pytest.raises(Invalid, match="(?i)another program"):
        RealChromeLauncher(fake_chrome, pick_port=free_loopback_port).launch("https://x.test", profile)
