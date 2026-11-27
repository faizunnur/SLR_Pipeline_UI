"""
server.py -- browser mode (`slr-desktop --browser`).

The desktop window needs a native web view (Edge WebView2 on Windows, WebKit on
macOS, GTK or Qt on Linux). Where none is available the same front end can be
shown in the default browser instead: this module serves the bundled page and
answers its API calls.

It is a local tool, not a web service: it binds to 127.0.0.1 only, picks a free
port, and every API call must carry the random token generated at start-up
(the address opened in the browser includes it). The Host header is checked
too, so another site cannot reach it through DNS rebinding.
"""
from __future__ import annotations

import json
import mimetypes
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from slr_desktop.api import Api

WEB_DIR = Path(__file__).resolve().parent / "web"
MAX_BODY = 8 * 1024 * 1024
_TYPES = {".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml"}
# The page's own <meta> policy has to allow 'unsafe-eval' for pywebview's bridge
# in the desktop window. A browser needs no bridge, so browser mode adds this
# stricter policy as a header; a browser enforces both, so eval stays refused.
STRICT_CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; "
              "script-src 'self'; connect-src 'self'")


def make_server(api: Api, port: int = 0, token: str | None = None):
    token = token or secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        server_version = "slr-desktop"

        def log_message(self, *args):  # keep the terminal quiet
            pass

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", STRICT_CSP)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, payload) -> None:
            self._send(code, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):  # noqa: N802
            if not self._host_ok():
                return self._send(403, b"forbidden", "text/plain")
            name = self.path.split("?", 1)[0].lstrip("/") or "index.html"
            target = (WEB_DIR / name).resolve()
            if WEB_DIR not in target.parents or not target.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = _TYPES.get(target.suffix) or mimetypes.guess_type(target.name)[0] \
                or "application/octet-stream"
            if ctype.startswith("text/"):
                ctype += "; charset=utf-8"
            self._send(200, target.read_bytes(), ctype)

        def do_POST(self):  # noqa: N802
            # Read the body before any refusal: answering while the client is
            # still sending makes the OS reset the connection, and the caller
            # then sees a network error instead of the reason.
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                self.close_connection = True
                return self._json(413, {"ok": False, "error": "request too large"})
            raw = self.rfile.read(length) if length else b""

            if not self._host_ok() or not self.path.startswith("/api/"):
                return self._json(403, {"ok": False, "error": "forbidden"})
            sent = (self.headers.get("X-SLR-Token") or "").encode("utf-8")
            if not secrets.compare_digest(sent, token.encode("utf-8")):
                return self._json(403, {"ok": False, "error": "This page's session has expired. "
                                                              "Start the app again."})
            name = self.path[len("/api/"):]
            method = None if name.startswith("_") else getattr(api, name, None)
            if not callable(method):
                return self._json(404, {"ok": False, "error": f"unknown call {name}"})
            try:
                args = json.loads(raw or b"{}").get("args", [])
                if not isinstance(args, list):
                    raise ValueError("args must be a list")
            except (ValueError, AttributeError):
                return self._json(400, {"ok": False, "error": "bad request"})
            self._json(200, method(*args))

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{httpd.server_address[1]}/index.html?t={token}"
    return httpd, url


def serve_in_thread(api: Api, port: int = 0, token: str | None = None):
    httpd, url = make_server(api, port, token)
    threading.Thread(target=httpd.serve_forever, name="slr-browser-mode", daemon=True).start()
    return httpd, url
