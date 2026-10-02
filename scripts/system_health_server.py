"""Read-only local endpoint for the aggregated system health verdict.

Binds 127.0.0.1 only. A second instance cannot bind the port and exits, so
the watcher tick may call ``ensure_server`` every time without duplicates.

  GET /api/system-health          last published verdict + age/staleness
  GET /api/system-health?live=1   fresh evaluation (no alarms, no writes)
  GET /api/system-health/alarms   tail of the append-only alarm log (?limit=N)
  GET /api/system-health/alerts   alert history: one entry per alert, active first, then newest first
  GET /api/system-health/banner   compact banner payload for dashboards
  GET /api/ping
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parents[1] / "services" / "btc-conservative-agent"))
import system_health as sh  # noqa: E402
import system_health_alerts as alerts  # noqa: E402

_live_lock = threading.Lock()


def published(state_dir: str) -> dict:
    report = sh.read_json(Path(state_dir) / "health" / "system-health-latest.json")
    if not isinstance(report, dict):
        return {"schema": sh.SCHEMA, "verdict": sh.AMBER, "stale": True,
                "failing": [{"id": "watcher.published", "status": sh.AMBER,
                             "observed": "no verdict published yet", "hint": "watcher tick has not run"}]}
    age = sh.utcnow() - float(report.get("generated_ts") or 0)
    report["age_sec"] = round(age, 1)
    report["stale"] = age > sh.THRESHOLDS["watcher_stale_sec"]
    if report["stale"]:
        stale = {"id": "watcher.stale", "status": sh.AMBER, "observed": f"verdict is {sh.fmt_age(age)} old",
                 "threshold": f"<= {sh.fmt_age(sh.THRESHOLDS['watcher_stale_sec'])}",
                 "hint": "health watcher not ticking (scheduled task / supervisor)",
                 "runbook": f"{sh.RUNBOOK}#watcher-stale"}
        report["failing"] = [stale] + list(report.get("failing") or [])
        if report.get("verdict") == sh.GREEN:
            report["verdict"] = sh.AMBER
    return report


def alarms_tail(state_dir: str, limit: int) -> list:
    path = Path(state_dir) / "health" / "alarms.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-max(1, min(limit, 500)):]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def make_handler(opts: argparse.Namespace):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # quiet
            return

        def _send(self, code: int, payload) -> None:
            body = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path == "/api/ping":
                return self._send(200, {"ok": True})
            if url.path == "/api/system-health":
                if query.get("live", ["0"])[0] in ("1", "true"):
                    with _live_lock:
                        args = sh.parse_args(["--state-dir", opts.state_dir, "--no-notify", "--no-fly-banner"])
                        return self._send(200, sh.run_once(args, alarms=False))
                return self._send(200, published(opts.state_dir))
            if url.path == "/api/system-health/alarms":
                limit = int(query.get("limit", ["50"])[0] or 50)
                return self._send(200, {"alarms": alarms_tail(opts.state_dir, limit)})
            if url.path == "/api/system-health/alerts":
                health = Path(opts.state_dir) / "health"
                limit = int(query.get("limit", ["0"])[0] or 0) or None
                return self._send(200, alerts.history_from_file(health / "alarms.jsonl",
                                                                health / "system-health-latest.json", limit=limit))
            if url.path == "/api/system-health/banner":
                report = published(opts.state_dir)
                if "checks" in report:
                    banner = sh.banner_payload({**report, "failing": report.get("failing") or []})
                    banner["stale"] = report.get("stale")
                    return self._send(200, banner)
                return self._send(200, report)
            return self._send(404, {"error": "not_found"})

    return Handler


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--state-dir", default=sh.DEFAULT_STATE_DIR)
    p.add_argument("--port", type=int, default=sh.SERVER_PORT)
    opts = p.parse_args(argv)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", opts.port), make_handler(opts))
    except OSError:
        return 0  # another instance owns the port
    server.daemon_threads = True
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
