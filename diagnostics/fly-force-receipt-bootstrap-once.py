#!/usr/bin/env python3
"""Force receipt-bootstrap while keeping the trading bot from holding ledger locks.

Fly entrypoint auto-restarts the bot every ~3s. This script:
1) continuously SIGKILLs bot/lifecycle writers so locks release
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
_KILL_MATCHES = (
    "btc_conservative_agent.py",
    "lifecycle_pipeline_worker.py",
)


def _matching_pids(*needles: str) -> list[int]:
    try:
        out = subprocess.check_output(["ps", "-eo", "pid,args"], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return []
    pids: list[int] = []
    for line in out.splitlines():
        if "fly-force-receipt-bootstrap" in line:
            continue
        if not any(n in line for n in needles):
            continue
        parts = line.strip().split(None, 1)
        if not parts:
            continue
        try:
            pids.append(int(parts[0]))
        except ValueError:
            pass
    return pids


def _bot_pids() -> list[int]:
    return _matching_pids(*_KILL_MATCHES)


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
    # Short machine-exec chunks (~35s) must finish ≥1 round before deadline, so
    # scale caps down when MAX_SECONDS is small (Fly HTTP 408 ~60s).
    if MAX_SECONDS <= 45:
        store_module._BOOTSTRAP_RECORDS_PER_STEP = 256
        store_module._BOOTSTRAP_BYTES_PER_STEP = 8 * 1024 * 1024
    elif MAX_SECONDS <= 120:
        store_module._BOOTSTRAP_RECORDS_PER_STEP = 1024
        store_module._BOOTSTRAP_BYTES_PER_STEP = 16 * 1024 * 1024
    else:
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
        "pid": os.getpid(),
    }
    print(json.dumps({"probe": probe}, sort_keys=True), flush=True)
    if not APPLY:
        print(json.dumps({"ok": True, "dry_run": True}, sort_keys=True), flush=True)
        return 0

    stop = threading.Event()
    thr = threading.Thread(target=_freezer, args=(stop,), daemon=True)
    thr.start()
    time.sleep(1.0)
    killed = _kill_bots()
    print(json.dumps({"freezer_started": True, "killed": killed, "alive_after": _bot_pids()}, sort_keys=True), flush=True)
    time.sleep(0.5)

    store = store_module.V3EvidenceStore(RUNTIME, epoch_id=EXPECTED_EPOCH)
    started = time.time()
    deadline = started + MAX_SECONDS
    rounds = 0
    last = None
    hit_deadline = False
    try:
        while True:
            if time.time() >= deadline:
                hit_deadline = True
                break
            last = store.advance_one_emergency_bootstrap_round_robin()
            rounds += 1
            print(json.dumps({
                "round": rounds,
                "progress": last,
                "killed_bots": _kill_bots(),
                "elapsed_s": round(time.time() - started, 1),
            }, sort_keys=True, default=str), flush=True)
            if last.get("all_complete") is True:
                break
            if last.get("blocked") is True:
                time.sleep(0.2)
    finally:
        stop.set()
        thr.join(timeout=2.0)

    if hit_deadline and not (isinstance(last, dict) and last.get("all_complete") is True):
        # Soft deadline for Fly machine-exec 600s hard cap; callers re-chunk.
        print(json.dumps({
            "ok": True,
            "status": "BOOTSTRAP_PARTIAL",
            "rounds": rounds,
            "final": last,
            "bot_pids_after": _bot_pids(),
            "deadline": True,
            "source_cleanup_authorized": False,
        }, sort_keys=True, default=str), flush=True)
        return 0

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
