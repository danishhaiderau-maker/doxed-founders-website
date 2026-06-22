#!/usr/bin/env python3
"""Lightweight :9001 status endpoint so Agent Hub can detect analyzer is running."""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("ANALYZER_HEALTH_PORT", "9001"))
BOT_DASHBOARD = os.environ.get("HOME_BOT_DASHBOARD", "http://127.0.0.1:7800")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/", "/health", "/status"):
            self._json(
                {
                    "ok": True,
                    "service": "btc-research-analyzer",
                    "note": "Research loop runs in the Doxed Analyzer PowerShell window (30-min iterations).",
                    "bot_dashboard": BOT_DASHBOARD,
                    "kpi_hint": f"{BOT_DASHBOARD}/api/state includes research_kpis when bot is running.",
                }
            )
            return
        self._json({"ok": False, "error": "not found"}, 404)


def main() -> None:
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Analyzer status on http://127.0.0.1:{PORT}/health (logs are in the Analyzer console)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
    sys.exit(0)
