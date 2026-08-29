"""Startup safety checks: one owner process and fatal dashboard bind."""

from __future__ import annotations

import os
import inspect
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot
from process_singleton import ProcessSingleton, acquire_process_singleton


def run():
    passed = 0

    def check(name, condition):
        nonlocal passed
        if not condition:
            raise AssertionError(name)
        passed += 1

    with tempfile.TemporaryDirectory() as td:
        owner = acquire_process_singleton("singleton-contract", Path(td))
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    "from process_singleton import acquire_process_singleton, ProcessSingletonError; "
                    f"root=Path({td!r}); "
                    "\ntry:\n acquire_process_singleton('singleton-contract', root)\n"
                    "except ProcessSingletonError:\n raise SystemExit(73)\n"
                    "raise SystemExit(0)"
                ),
            ],
            cwd=str(Path(__file__).parent),
            capture_output=True,
            text=True,
            timeout=15,
        )
        check("second process is refused by OS lock", child.returncode == 73)
        owner.release()

    acquire_source = inspect.getsource(ProcessSingleton.acquire)
    check(
        "dead-owner Windows lock file has guarded stale recovery",
        "except PermissionError" in acquire_source
        and "self._pid_alive(owner_pid)" in acquire_source
        and "self.path.unlink(missing_ok=True)" in acquire_source
        and "for _ in range(20)" in acquire_source,
    )

    main_source = inspect.getsource(bot.main)
    check(
        "dashboard singleton uses a machine-wide lock directory",
        'os.getenv("BOT_SINGLETON_DIR")' in main_source
        and 'os.getenv("LOCALAPPDATA")' in main_source,
    )

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    old_port = bot.DASHBOARD_PORT
    old_host = bot.DASHBOARD_BIND_HOST
    bot.DASHBOARD_PORT = listener.getsockname()[1]
    bot.DASHBOARD_BIND_HOST = "127.0.0.1"
    fatal = False
    try:
        bot._create_dashboard_server()
    except SystemExit:
        fatal = True
    finally:
        bot.DASHBOARD_PORT = old_port
        bot.DASHBOARD_BIND_HOST = old_host
        listener.close()
    check("dashboard bind failure is process-fatal", fatal)

    server_source = inspect.getsource(bot._create_dashboard_server)
    check(
        "dashboard overload never blocks the accept loop",
        "acquire(blocking=False)" in server_source
        and "503 Service Unavailable" in server_source
        and "dashboard_busy" in server_source,
    )
    check(
        "relay authority and operator controls have reserved worker capacity",
        "_dispatch_thread_cap = threading.BoundedSemaphore(32)" in server_source
        and "_canonical_thread_cap = threading.BoundedSemaphore(8)" in server_source
        and "_relay_state_thread_cap = threading.BoundedSemaphore(4)" in server_source
        and "_data_sync_thread_cap = threading.BoundedSemaphore(4)" in server_source
        and "_control_thread_cap = threading.BoundedSemaphore(2)" in server_source
        and 'b"/api/relay-execution-state"' in server_source
        and 'b"/api/data-sync/manifest"' in server_source
        and 'b"/api/data-sync/file"' in server_source
        and 'b"/api/data-sync/ack"' in server_source
        and 'b"/api/data-sync/analyzer-report"' in server_source
        and 'b"/api/data-sync/platform-relay-evidence"' in server_source
        and "return self._data_sync_thread_cap" in server_source
        and 'b"/api/pause"' in server_source
        and 'b"/api/resume"' in server_source
        and "socket.MSG_PEEK" in server_source
        and "_priority_client_io_timeout_sec = 2.0" in server_source
        and "request_cap.release()" in server_source,
    )

    request_path = bot._dashboard_request_path_from_head
    check(
        "dashboard request classifier strips query strings deterministically",
        request_path(b"GET /api/data-sync/manifest HTTP/1.1\r\n")
        == b"/api/data-sync/manifest"
        and request_path(b"GET /api/data-sync/file?path=v3%2Fledger.jsonl&offset=1 HTTP/1.1\r\n")
        == b"/api/data-sync/file"
        and request_path(b"POST /api/data-sync/analyzer-report?kind=current HTTP/1.1\r\n")
        == b"/api/data-sync/analyzer-report"
        and request_path(b"malformed") == b"",
    )
    check(
        "accept loop delegates request classification without peeking",
        "target=self._classify_and_process" in server_source
        and "self._dispatch_thread_cap.acquire(blocking=False)" in server_source
        and "self._request_cap(request)" in server_source,
    )

    canonical_route_source = inspect.getsource(bot.api_relay_execution_state)
    canonical_builder_source = inspect.getsource(bot._publish_relay_execution_snapshot)
    canonical_refresher_source = inspect.getsource(
        bot._relay_execution_cache_refresher_loop
    )
    check(
        "canonical HTTP workers never rebuild money authority",
        "_build_relay_execution_state_snapshot" not in canonical_route_source
        and "_cached_relay_execution_snapshot" in canonical_route_source
        and "app.response_class(body" in canonical_route_source,
    )
    check(
        "canonical authority has one nonblocking background builder",
        "_RELAY_EXECUTION_REFRESH_LOCK.acquire(blocking=False)"
        in canonical_builder_source
        and "_publish_relay_execution_snapshot()" in canonical_refresher_source
        and "shutdown_event.wait(_RELAY_EXECUTION_REFRESH_INTERVAL_SEC)"
        in canonical_refresher_source,
    )

    old_payload = bot._RELAY_EXECUTION_CACHE_PAYLOAD
    old_body = bot._RELAY_EXECUTION_CACHE_BODY
    old_at = bot._RELAY_EXECUTION_CACHE_AT
    try:
        with bot._RELAY_EXECUTION_CACHE_LOCK:
            bot._RELAY_EXECUTION_CACHE_PAYLOAD = {"ok": True}
            bot._RELAY_EXECUTION_CACHE_BODY = b'{"ok":true}'
            bot._RELAY_EXECUTION_CACHE_AT = time.monotonic()
        with bot.app.test_request_context("/api/relay-execution-state"):
            response = bot.api_relay_execution_state()
        check(
            "fresh canonical cache is returned as immutable pre-serialized JSON",
            response.status_code == 200
            and response.get_data() == b'{"ok":true}'
            and response.headers.get("X-Relay-State-Cache")
            == "EXECUTION_BACKGROUND",
        )

        with bot._RELAY_EXECUTION_CACHE_LOCK:
            bot._RELAY_EXECUTION_CACHE_AT = (
                time.monotonic() - bot._RELAY_EXECUTION_MAX_STALE_SEC - 1
            )
        with bot.app.test_request_context("/api/relay-execution-state"):
            response = bot.api_relay_execution_state()
        check(
            "stale canonical cache fails closed without rebuilding",
            response.status_code == 503
            and response.get_json().get("api_state_error")
            == "canonical execution snapshot unavailable or stale",
        )
    finally:
        with bot._RELAY_EXECUTION_CACHE_LOCK:
            bot._RELAY_EXECUTION_CACHE_PAYLOAD = old_payload
            bot._RELAY_EXECUTION_CACHE_BODY = old_body
            bot._RELAY_EXECUTION_CACHE_AT = old_at

    snapshot_source = inspect.getsource(bot._build_api_state_snapshot)
    check(
        "dashboard snapshot funding uses cached exchange state",
        "accrue_position_funding(pos, now_ts, refresh=False)" in snapshot_source,
    )

    print(f"PASS: {passed} singleton startup checks")


if __name__ == "__main__":
    run()
