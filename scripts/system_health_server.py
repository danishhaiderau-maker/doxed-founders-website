"""Read-only local endpoint for the aggregated system health verdict.

Binds 127.0.0.1 only. A second instance cannot bind the port and exits, so
the watcher tick may call ``ensure_server`` every time without duplicates.

  GET /api/system-health          last published verdict + age/staleness
  GET /api/system-health?live=1   newest cached verdict at once, plus a single-flight background
                                  re-evaluation (no alarms, no writes); waits up to
                                  LIVE_WAIT_SEC only when the cache is older than LIVE_MAX_AGE_SEC
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

LIVE_MAX_AGE_SEC = 120.0
LIVE_WAIT_SEC = 10.0


def annotate(report: dict, now: float | None = None) -> dict:
    """Add ``age_sec``/``stale``; a verdict older than ``watcher_stale_sec`` is RED ``watcher.stale``."""
    now = sh.utcnow() if now is None else now
    age = now - float(report.get("generated_ts") or 0)
    report["age_sec"] = round(age, 1)
    report["stale"] = age > sh.THRESHOLDS["watcher_stale_sec"]
    if report["stale"]:
        stale = {"id": "watcher.stale", "status": sh.RED, "observed": f"verdict is {sh.fmt_age(age)} old",
                 "threshold": f"<= {sh.fmt_age(sh.THRESHOLDS['watcher_stale_sec'])}",
                 "hint": "health watcher not ticking (scheduled task / supervisor); every check below is stale",
                 "runbook": f"{sh.RUNBOOK}#watcher-stale"}
        report["failing"] = [stale] + [f for f in report.get("failing") or [] if f.get("id") != "watcher.stale"]
        report["verdict"] = sh.RED
    return report


def published(state_dir: str) -> dict:
    report = sh.read_json(Path(state_dir) / "health" / "system-health-latest.json")
    if not isinstance(report, dict):
        return {"schema": sh.SCHEMA, "verdict": sh.RED, "stale": True, "age_sec": None, "generated_at": None,
                "failing": [{"id": "watcher.stale", "status": sh.RED,
                             "observed": "no verdict published yet", "hint": "watcher tick has not run",
                             "runbook": f"{sh.RUNBOOK}#watcher-stale"}]}
    return annotate(report)


class LiveRefresher:
    """At most one live evaluation at a time; callers get the newest cached verdict immediately."""

    def __init__(self, state_dir: str, evaluate=None, *, max_age_sec: float = LIVE_MAX_AGE_SEC,
                 wait_sec: float = LIVE_WAIT_SEC) -> None:
        self.state_dir = state_dir
        self.evaluate = evaluate or self._evaluate
        self.max_age_sec, self.wait_sec = max_age_sec, wait_sec
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._live: dict | None = None
        self.last_error: str | None = None
        self.runs = 0

    def _evaluate(self) -> dict:
        args = sh.parse_args(["--state-dir", self.state_dir, "--no-notify", "--no-fly-banner"])
        return sh.run_once(args, alarms=False)

    def _run(self) -> None:
        try:
            report = self.evaluate()
            if isinstance(report, dict):
                with self._lock:
                    self._live = report
            self.last_error = None
        except Exception as exc:  # noqa: BLE001 - a failed refresh must never take the endpoint down
            self.last_error = type(exc).__name__
        finally:
            with self._lock:
                self.runs += 1
                self._thread = None

    def cached(self) -> dict:
        disk = sh.read_json(Path(self.state_dir) / "health" / "system-health-latest.json")
        with self._lock:
            live = self._live
        candidates = [r for r in (disk, live) if isinstance(r, dict)]
        if not candidates:
            return published(self.state_dir)
        newest = max(candidates, key=lambda r: float(r.get("generated_ts") or 0))
        out = dict(newest)
        out["cache_source"] = "live" if newest is live else "published"
        return annotate(out)

    def start(self) -> str:
        with self._lock:
            if self._thread is not None:
                return "running"
            self._thread = threading.Thread(target=self._run, name="system-health-live", daemon=True)
            self._thread.start()
            return "started"

    def get(self) -> dict:
        refresh = self.start()
        report = self.cached()
        age = report.get("age_sec")
        if age is None or age > self.max_age_sec:
            with self._lock:
                thread = self._thread
            if thread is not None:
                thread.join(self.wait_sec)
            report = self.cached()
        with self._lock:
            finished = self._thread is None
        if refresh == "started" and finished and report.get("cache_source") == "live" \
                and (report.get("age_sec") or 0) <= self.max_age_sec:
            refresh = "completed"
        report["refresh"] = refresh
        if self.last_error:
            report["refresh_error"] = self.last_error
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


def make_handler(opts: argparse.Namespace, refresher: LiveRefresher | None = None):
    live = refresher or LiveRefresher(opts.state_dir)

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
                    return self._send(200, live.get())
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
