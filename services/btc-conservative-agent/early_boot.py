"""
Instant liveness on DASHBOARD_PORT before the heavy bot.py import (~25s on home PC).
Serves /api/ping and /health only; full Flask replaces this after boot.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

_server: Optional[ThreadingHTTPServer] = None
_thread: Optional[threading.Thread] = None
_boot_version = "booting"
_source_git_rev = "unknown"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_RESERVED_PORTS = frozenset({7810})


def _write_json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    """Write a boot response; silently accept a client that already disconnected."""
    body = json.dumps(payload).encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
        # Health checkers and browsers routinely abandon the temporary boot
        # response while the full dashboard is taking ownership of the port.
        # That is a normal client disconnect, not a bot crash.
        return


def start_early_ping_server(
    port: int,
    *,
    version: str = "booting",
    host: str = "0.0.0.0",
    source_git_rev: str = "unknown",
) -> None:
    global _server, _thread, _boot_version, _source_git_rev
    if int(port) in _RESERVED_PORTS:
        raise RuntimeError(
            f"Port {port} is reserved for the home bridge (:7810). "
            "Start bot with DASHBOARD_PORT=7002 (showcase), not the bridge port."
        )
    if _server is not None:
        return
    _boot_version = version
    _source_git_rev = (source_git_rev or "unknown").strip()[:12]

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

        def _write_json(self, status: int, payload: dict) -> None:
            _write_json_response(self, status, payload)

        def do_GET(self) -> None:  # noqa: N802
            path = (self.path or "/").split("?", 1)[0]
            if path in ("/api/ping", "/health", "/"):
                self._write_json(
                    200,
                    {
                        "ok": True,
                        "boot": "starting",
                        "bot_pid": os.getpid(),
                        "dashboard_pid": os.getpid(),
                        "dashboard_port": int(self.server.server_address[1]),
                        "dashboard_owner": True,
                        "source_git_rev": _source_git_rev,
                        "bot_version": _boot_version,
                        "server_ts": _utc_iso(),
                    },
                )
                return
            self._write_json(503, {"ok": False, "boot": "starting", "error": "dashboard loading"})

    _server = ThreadingHTTPServer((host, int(port)), _Handler)
    _thread = threading.Thread(target=_server.serve_forever, name="early-ping", daemon=True)
    _thread.start()


def stop_early_ping_server() -> None:
    global _server, _thread
    if _server is None:
        return
    try:
        _server.shutdown()
        _server.server_close()
    finally:
        _server = None
        _thread = None
