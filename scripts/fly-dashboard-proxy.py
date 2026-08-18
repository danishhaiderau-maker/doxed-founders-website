"""Local :7002 compatibility proxy for the canonical Fly.io bot.

This process contains no strategy, exchange, or AI code. It keeps old desktop
dashboard bookmarks working while guaranteeing that every state/control request
is served by the single Fly runtime.
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import ssl
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


CANONICAL_UPSTREAM = "https://doxed-btc-bot.fly.dev"
MIRROR_MUTATION_ALLOWLIST = {
    "/api/set_ai_bands",
    "/api/set_chase_buckets",
    "/api/set_edge_range",
    "/api/set_edge_threshold",
    "/api/set_leverage",
    "/api/set_max_active_signals",
    "/api/set_pullback_threshold",
    "/api/set_spread_gate",
    "/api/set_threshold",
    "/api/spread-gate",
    "/api/tile2/reset_counters",
    "/api/toggle_continuous_ai_direct",
    "/api/toggle_continuous_ai_research",
    "/api/toggle_debug",
    "/api/toggle_duplicate_limit_block",
    "/api/toggle_early_fail",
    "/api/toggle_fresh_collection",
    "/api/fresh_epoch_reset",
    "/api/toggle_invert_signal",
    "/api/toggle_profit_gates",
    "/api/toggle_research_lane",
}
FORWARDED_REQUEST_HEADERS = {
    "accept",
    "accept-language",
    "content-type",
    "cookie",
    "if-modified-since",
    "if-none-match",
    "user-agent",
}
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


def loopback_response_header(name: str, value: str) -> str:
    """Make Fly's host-only admin cookie usable on the HTTP loopback mirror.

    The proxy cannot terminate HTTPS locally, and it listens only on loopback.
    Fly therefore sets ``Secure`` for its HTTPS request, but a browser correctly
    refuses to return that cookie to ``http://127.0.0.1``. Remove only that
    attribute on this local response; public Fly/Agent Hub cookies are untouched.
    """
    if name.lower() != "set-cookie":
        return value
    return re.sub(r";\s*Secure(?=;|$)", "", value, flags=re.IGNORECASE)


class FlyProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream = CANONICAL_UPSTREAM
    timeout_sec = 45

    def _proxy(self) -> None:
        parsed = urllib.parse.urlsplit(self.upstream)
        target = self.path if self.path.startswith("/") else f"/{self.path}"
        target_path = urllib.parse.urlsplit(target).path
        if (
            self.command not in {"GET", "HEAD", "OPTIONS"}
            and target_path not in MIRROR_MUTATION_ALLOWLIST
        ):
            payload = json.dumps(
                {
                    "ok": False,
                    "status": "desktop_mirror_read_only",
                    "detail": "Use authenticated Agent Hub controls for trading, live relay, reset, and position actions.",
                }
            ).encode("utf-8")
            self.send_response(405)
            self.send_header("Allow", "GET, HEAD, OPTIONS")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else None

        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() in FORWARDED_REQUEST_HEADERS
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
                self.send_header(key, loopback_response_header(key, value))
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
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7002)
    parser.add_argument("--upstream", default=CANONICAL_UPSTREAM)
    args = parser.parse_args()
    normalized_upstream = args.upstream.rstrip("/")
    if normalized_upstream != CANONICAL_UPSTREAM:
        raise SystemExit(
            f"REFUSED_NON_CANONICAL_UPSTREAM: expected {CANONICAL_UPSTREAM}"
        )
    if args.bind not in {"127.0.0.1", "::1", "localhost"}:
        raise SystemExit("REFUSED_NON_LOOPBACK_BIND")
    FlyProxyHandler.upstream = normalized_upstream
    server = ThreadingHTTPServer((args.bind, args.port), FlyProxyHandler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
