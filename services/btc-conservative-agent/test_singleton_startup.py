"""Startup safety checks: one owner process and fatal dashboard bind."""

from __future__ import annotations

import os
import inspect
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

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
        and "self.path.unlink(missing_ok=True)" in acquire_source,
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
        and "_authority_thread_cap = threading.BoundedSemaphore(8)" in server_source
        and "_control_thread_cap = threading.BoundedSemaphore(2)" in server_source
        and 'b"/api/relay-execution-state"' in server_source
        and 'b"/api/pause"' in server_source
        and 'b"/api/resume"' in server_source
        and "socket.MSG_PEEK" in server_source
        and "_priority_client_io_timeout_sec = 2.0" in server_source
        and "request_cap.release()" in server_source,
    )
    check(
        "accept loop delegates request classification without peeking",
        "target=self._classify_and_process" in server_source
        and "self._dispatch_thread_cap.acquire(blocking=False)" in server_source
        and "self._request_cap(request)" in server_source,
    )

    snapshot_source = inspect.getsource(bot._build_api_state_snapshot)
    check(
        "dashboard snapshot funding uses cached exchange state",
        "accrue_position_funding(pos, now_ts, refresh=False)" in snapshot_source,
    )

    print(f"PASS: {passed} singleton startup checks")


if __name__ == "__main__":
    run()
