"""A local page that only greets a visitor who carries the session cookie."""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

COOKIE_NAME = "sid"
COOKIE_VALUE = "valid-session-cookie-1234"


def serve_cookie_gated() -> tuple[HTTPServer, str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            signed_in = f"{COOKIE_NAME}={COOKIE_VALUE}" in self.headers.get("Cookie", "")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<p>signed in</p>" if signed_in else b"<p>please log in</p>")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"
