"""Local :7002 compatibility proxy for the canonical Fly.io bot.

This process contains no strategy, exchange, or AI code. It keeps old desktop
dashboard bookmarks working while guaranteeing that every state/control request
is served by the single Fly runtime.
"""

from __future__ import annotations

import argparse
import http.client
import json
import ssl
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


class FlyProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream = "https://doxed-btc-bot.fly.dev"
    timeout_sec = 45

    def _proxy(self) -> None:
        parsed = urllib.parse.urlsplit(self.upstream)
        target = self.path if self.path.startswith("/") else f"/{self.path}"
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else None

        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP
            and key.lower() not in {"host", "content-length"}
        }
        headers["Host"] = parsed.netloc
        headers["X-Desktop-Mirror"] = "1"
        if body is not None:
            headers["Content-Length"] = str(len(body))

        connection = http.client.HTTPSConnection(
            parsed.hostname,
            parsed.port or 443,
            timeout=self.timeout_sec,
            context=ssl.create_default_context(),
        )
        try:
            connection.request(self.command, target, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                lower = key.lower()
                if lower in HOP_BY_HOP or lower in {"content-length", "content-encoding"}:
                    continue
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("X-Desktop-Mirror", "fly")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
        except Exception as exc:
            payload = json.dumps(
                {
                    "ok": False,
                    "status": "fly_proxy_unavailable",
                    "detail": str(exc),
                }
            ).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
        finally:
            connection.close()

    do_GET = _proxy
    do_HEAD = _proxy
    do_POST = _proxy
    do_PUT = _proxy
    do_PATCH = _proxy
    do_DELETE = _proxy
    do_OPTIONS = _proxy

    def log_message(self, fmt: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7002)
    parser.add_argument("--upstream", default="https://doxed-btc-bot.fly.dev")
    args = parser.parse_args()
    FlyProxyHandler.upstream = args.upstream.rstrip("/")
    server = ThreadingHTTPServer((args.bind, args.port), FlyProxyHandler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
