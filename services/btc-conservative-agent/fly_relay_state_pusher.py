"""Publish the authoritative Fly relay snapshot to the platform cache.

This replaces the retired Windows-only relay-state-pusher.ps1. It never makes
trading decisions or places orders; it only copies the bot's bounded
/api/relay-state document to the authenticated internal cache.
"""

from __future__ import annotations

import os
import signal
import threading
import time

import requests


BOT_URL = os.getenv("LOCAL_RELAY_STATE_URL", "http://127.0.0.1:7002/api/relay-state")
SNAPSHOT_URL = os.getenv(
    "SHOWCASE_SNAPSHOT_URL",
    "https://doxed-founders-website-production.up.railway.app/api/internal/showcase-snapshot",
)
INTERVAL_SEC = max(1.0, float(os.getenv("SHOWCASE_SNAPSHOT_INTERVAL_SEC", "2")))
CONTROL_SECRET = (os.getenv("BOT_CONTROL_SECRET") or "").strip()
STOP = threading.Event()


def _stop(_signum, _frame):
    STOP.set()


def main() -> int:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    if not CONTROL_SECRET:
        print("[fly-relay-pusher] disabled: BOT_CONTROL_SECRET missing", flush=True)
        return 0

    session = requests.Session()
    session.trust_env = False
    session.headers.update(
        {
            "Accept": "application/json",
            "X-Bot-Control-Secret": CONTROL_SECRET,
        }
    )
    seq = int(time.time() * 1000)
    failures = 0
    print(
        f"[fly-relay-pusher] started interval={INTERVAL_SEC:g}s",
        flush=True,
    )
    while not STOP.is_set():
        started = time.monotonic()
        try:
            source = session.get(BOT_URL, timeout=5)
            source.raise_for_status()
            snapshot = source.json()
            seq = max(seq + 1, int(time.time() * 1000))
            pushed = session.post(
                SNAPSHOT_URL,
                json={
                    "snapshot_seq": seq,
                    "snapshot": snapshot,
                    "bot_version": snapshot.get("bot_version"),
                    "server_ts": snapshot.get("server_ts"),
                },
                timeout=8,
            )
            pushed.raise_for_status()
            failures = 0
        except Exception as exc:  # best-effort transport; executor still fails closed
            failures += 1
            if failures == 1 or failures % 30 == 0:
                print(
                    f"[fly-relay-pusher] push failed count={failures}: {str(exc)[:240]}",
                    flush=True,
                )
        remaining = max(0.0, INTERVAL_SEC - (time.monotonic() - started))
        STOP.wait(remaining)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
