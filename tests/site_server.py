"""A tiny local web server for browser tests, so they never touch the network."""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def serve(pages: dict[str, str], requested: list[str] | None = None) -> tuple[HTTPServer, str]:
    """`requested`, when given, collects every requested path (with its query)."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if requested is not None:
                requested.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(pages.get(self.path, "<p>missing</p>").encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"
