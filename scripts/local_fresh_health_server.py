"""Local-fresh system-health HTTP server on 127.0.0.1:9011 (RK 19:23).

Replaces Fly-centric watcher probes for day-1 local paper. Read-only GETs:

  GET /api/system-health
  GET /api/system-health?live=1
  GET /api/ping
  GET /health

No laptop start from this module alone — operator/R12 still required.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "services" / "btc-conservative-agent"
sys.path.insert(0, str(AGENT))

import local_fresh_health9011 as lf9011  # noqa: E402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = lf9011.DEFAULT_PORT


class State:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self._lock = threading.Lock()
        self._latest: dict | None = None
        self._live_fp: str | None = None

    def evaluate(self) -> dict:
        data_dir = self.args.data_dir or os.environ.get("BOT_DATA_DIR") or str(
            AGENT / "local-fresh-data"
        )
        logs_dir = self.args.logs_dir or str(ROOT / "logs")
        report = lf9011.build_report(
            data_dir=data_dir,
            logs_dir=logs_dir,
            engine_url=self.args.engine_url,
            railway_url=self.args.railway_url,
            vercel_url=self.args.vercel_url,
            analyzer_reports_dir=self.args.analyzer_reports_dir or None,
            prior_live_safety_fingerprint=self._live_fp,
        )
        with self._lock:
            self._latest = report
            self._live_fp = report.get("live_safety_fingerprint")
        return report

    def latest(self) -> dict:
        with self._lock:
            if self._latest is not None:
                return dict(self._latest)
        return self.evaluate()


def make_handler(state: State):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            sys.stderr.write("9011 " + (fmt % args) + "\n")

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, indent=2, sort_keys=True, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            qs = parse_qs(parsed.query)
            if path in ("/api/ping", "/ping"):
                self._send(200, {"ok": True, "service": "local_fresh_9011"})
                return
            if path in ("/health", "/api/health"):
                self._send(200, {"ok": True, "port": DEFAULT_PORT, "fly_owner": False})
                return
            if path == "/api/system-health":
                live = (qs.get("live") or [""])[0] in ("1", "true", "yes")
                report = state.evaluate() if live else state.latest()
                self._send(200, report)
                return
            self._send(404, {"ok": False, "error": "not_found", "path": path})

    return Handler


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Local-fresh :9011 health server (no Fly probes)")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--data-dir", default="")
    p.add_argument("--logs-dir", default="")
    p.add_argument("--engine-url", default=lf9011.DEFAULT_ENGINE_HEALTH)
    p.add_argument("--railway-url", default=lf9011.DEFAULT_RAILWAY_HEALTH)
    p.add_argument("--vercel-url", default=lf9011.DEFAULT_VERCEL_PROD)
    p.add_argument("--analyzer-reports-dir", default="")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    state = State(args)
    # Warm one evaluation so /api/system-health without live=1 is not empty.
    try:
        state.evaluate()
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"9011 warm evaluate failed: {type(exc).__name__}: {exc}\n")
    server = ThreadingHTTPServer((args.host, args.port), make_handler(state))
    sys.stderr.write(
        f"local-fresh health listening on http://{args.host}:{args.port}/api/system-health "
        f"(Fly probes dropped; alert=engine_down|live_safety_only)\n"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
