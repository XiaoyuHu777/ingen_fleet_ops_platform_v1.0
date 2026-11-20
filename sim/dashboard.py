"""Serve a loopback-only view of live public-safe simulator status."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def serve(live_path: Path, port: int) -> None:
    html = Path(__file__).with_name("dashboard.html").read_bytes()

    class Dashboard(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/":
                kind, data = "text/html; charset=utf-8", html
            elif path == "/status.json":
                kind = "application/json"
                try:
                    data = live_path.read_bytes()
                except FileNotFoundError:
                    data = b"{}"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            return

    print(f"Live view: http://127.0.0.1:{port}/", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Dashboard).serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("live", type=Path, help="Run's live.json path")
    parser.add_argument("--port", type=int, default=8055)
    args = parser.parse_args()
    serve(args.live, args.port)


if __name__ == "__main__":
    main()
