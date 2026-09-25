#!/usr/bin/env python3
"""Force receipt-bootstrap while keeping the trading bot from holding ledger locks.

Fly entrypoint auto-restarts the bot every ~3s. This script:
1) continuously SIGKILLs btc_conservative_agent.py so locks release
2) advances emergency idempotency bootstrap round-robin until all_complete
3) exits so the entrypoint can revive the bot

Env:
  DATA_ROOT=/app/data (default)
  EXPECTED_EPOCH=epoch-... (required)
  APPLY=true to mutate; false = probe only
  MAX_SECONDS=1200
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

DATA_ROOT = Path(os.environ.get("DATA_ROOT", "/app/data")).resolve()
EXPECTED_EPOCH = str(os.environ.get("EXPECTED_EPOCH") or "").strip()
APPLY = os.environ.get("APPLY", "false").strip().lower() in {"1", "true", "yes"}
MAX_SECONDS = max(60, int(os.environ.get("MAX_SECONDS") or "1200"))
RUNTIME = DATA_ROOT / "runtime"


def _bot_pids() -> list[int]:
    try:
        out = subprocess.check_output(["ps", "-eo", "pid,args"], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        if "btc_conservative_agent.py" not in line:
            continue
        if "fly-force-receipt-bootstrap" in line:
            continue
        parts = line.strip().split(None, 1)
        if not parts:
            continue
        try:
            pids.append(int(parts[0]))
        except ValueError:
            pass
    return pids


def _kill_bots() -> int:
    killed = 0
    for pid in _bot_pids():
        try:
            os.kill(pid, signal.SIGKILL)
            killed += 1
        except ProcessLookupError:
            pass
        except PermissionError:
            pass
    return killed


def _freezer(stop: threading.Event) -> None:
    while not stop.wait(0.4):
        _kill_bots()


def main() -> int:
    if not EXPECTED_EPOCH.startswith("epoch-"):
        raise SystemExit("EXPECTED_EPOCH required")
    sys.path.insert(0, "/app")
    import research_v3_store as store_module  # type: ignore
    from lifecycle_pipeline_worker import LEDGER_NAMES  # type: ignore

    # Raise cooperative caps for this one-shot only (module clamps use these).
    # Incident needs multi-GB ledger indexing; default 64/8MiB is too slow under
    # a single SSH wall clock.
    store_module._BOOTSTRAP_RECORDS_PER_STEP = 4096
    store_module._BOOTSTRAP_BYTES_PER_STEP = 64 * 1024 * 1024

    probe = {
        "data_root": str(DATA_ROOT),
        "runtime": str(RUNTIME),
        "epoch": EXPECTED_EPOCH,
        "apply": APPLY,
        "bot_pids_before": _bot_pids(),
        "ledgers": list(LEDGER_NAMES),
        "records_cap": store_module._BOOTSTRAP_RECORDS_PER_STEP,
        "bytes_cap": store_module._BOOTSTRAP_BYTES_PER_STEP,
    }
    print(json.dumps({"probe": probe}, sort_keys=True), flush=True)
    if not APPLY:
        print(json.dumps({"ok": True, "dry_run": True}, sort_keys=True), flush=True)
        return 0

    stop = threading.Event()
    thr = threading.Thread(target=_freezer, args=(stop,), daemon=True)
    thr.start()
    time.sleep(1.0)
    _kill_bots()
    time.sleep(0.5)

    store = store_module.V3EvidenceStore(RUNTIME, epoch_id=EXPECTED_EPOCH)
    deadline = time.time() + MAX_SECONDS
    rounds = 0
    last = None
    try:
        while time.time() < deadline:
            last = store.advance_one_emergency_bootstrap_round_robin()
            rounds += 1
            if rounds == 1 or rounds % 25 == 0 or last.get("all_complete") is True or last.get("blocked") is True:
                print(json.dumps({"round": rounds, "progress": last}, sort_keys=True, default=str), flush=True)
            if last.get("all_complete") is True:
                break
            if last.get("blocked") is True:
                time.sleep(0.2)
                continue
        else:
            raise SystemExit("BOOTSTRAP_DEADLINE:" + json.dumps(last or {}, sort_keys=True, default=str)[:2000])
    finally:
        stop.set()
        thr.join(timeout=2.0)

    final = store.advance_one_emergency_bootstrap_round_robin()
    print(json.dumps({
        "ok": True,
        "status": "BOOTSTRAP_FORCED",
        "rounds": rounds,
        "final": final,
        "bot_pids_after": _bot_pids(),
        "source_cleanup_authorized": False,
    }, sort_keys=True, default=str), flush=True)
    if final.get("all_complete") is not True:
        raise SystemExit("BOOTSTRAP_NOT_COMPLETE:" + json.dumps(final, sort_keys=True, default=str)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
