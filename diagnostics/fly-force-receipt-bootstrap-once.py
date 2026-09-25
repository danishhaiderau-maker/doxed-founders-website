#!/usr/bin/env python3
"""Force receipt-bootstrap while keeping the trading bot from holding ledger locks.

Fly entrypoint auto-restarts the bot every ~3s. This script:
1) continuously SIGKILLs bot/lifecycle writers + fuser-kills ledger lock holders
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
MAX_SECONDS = max(15, int(os.environ.get("MAX_SECONDS") or "1200"))
RUNTIME = DATA_ROOT / "runtime"
STATUS_PATH = Path("/tmp/force_chunk_status.json")
_KILL_MATCHES = (
    "btc_conservative_agent.py",
    "btc_conservative_agent",
    "lifecycle_pipeline_worker.py",
    "lifecycle_pipeline_worker",
    "analyzer_research_engine",
)


def _write_status(payload: dict) -> None:
    STATUS_PATH.write_text(json.dumps(payload, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _ps_snapshot() -> list[str]:
    try:
        out = subprocess.check_output(["ps", "-eo", "pid,args"], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return []
    lines = []
    for line in out.splitlines():
        low = line.lower()
        if any(tok in low for tok in ("python", "btc", "lifecycle", "7002", "agent")):
            lines.append(line.strip()[:240])
    return lines[:40]


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


def _fuser_kill_ledgers() -> int:
    ledgers = RUNTIME / "v3" / "ledgers"
    if not ledgers.is_dir():
        return 0
    n = 0
    for path in ledgers.glob("*.jsonl*"):
        try:
            proc = subprocess.run(
                ["fuser", "-k", "-9", str(path)],
                check=False, capture_output=True, text=True,
            )
            if proc.returncode == 0 or (proc.stdout or proc.stderr):
                n += 1
        except Exception:
            pass
    return n


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
    try:
        subprocess.run(
            ["pkill", "-9", "-f", "btc_conservative_agent"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["pkill", "-9", "-f", "lifecycle_pipeline_worker"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["pkill", "-9", "-f", "python /app/btc_conservative"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
    _fuser_kill_ledgers()
    return killed


def _freezer(stop: threading.Event) -> None:
    while not stop.wait(0.4):
        _kill_bots()


class _Deadline(Exception):
    pass


def main() -> int:
    if not EXPECTED_EPOCH.startswith("epoch-"):
        raise SystemExit("EXPECTED_EPOCH required")
    sys.path.insert(0, "/app")
    import research_v3_store as store_module  # type: ignore
    from lifecycle_pipeline_worker import LEDGER_NAMES  # type: ignore

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
        "ps_snapshot": _ps_snapshot(),
        "ledgers": list(LEDGER_NAMES),
        "records_cap": store_module._BOOTSTRAP_RECORDS_PER_STEP,
        "bytes_cap": store_module._BOOTSTRAP_BYTES_PER_STEP,
        "pid": os.getpid(),
        "max_seconds": MAX_SECONDS,
    }
    print(json.dumps({"probe": probe}, sort_keys=True), flush=True)
    if not APPLY:
        payload = {"ok": True, "dry_run": True}
        print(json.dumps(payload, sort_keys=True), flush=True)
        _write_status(payload)
        return 0

    stop = threading.Event()
    thr = threading.Thread(target=_freezer, args=(stop,), daemon=True)
    thr.start()
    time.sleep(1.0)
    killed = _kill_bots()
    print(json.dumps({
        "freezer_started": True,
        "killed": killed,
        "alive_after": _bot_pids(),
        "ps_after_kill": _ps_snapshot(),
        "fuser_targets": True,
    }, sort_keys=True), flush=True)
    time.sleep(0.5)

    def _alarm_handler(signum, frame):  # noqa: ARG001
        raise _Deadline("BOOTSTRAP_ALARM")

    # Hard interrupt hung LOCK_EX waits so short chunks always emit PARTIAL.
    old_handler = signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(max(10, MAX_SECONDS))
    store = None
    started = time.time()
    rounds = 0
    last = None
    hit_deadline = False
    try:
        print(json.dumps({"phase": "store_init"}, sort_keys=True), flush=True)
        store = store_module.V3EvidenceStore(RUNTIME, epoch_id=EXPECTED_EPOCH)
        print(json.dumps({"phase": "store_ready"}, sort_keys=True), flush=True)
        while True:
            remaining = MAX_SECONDS - (time.time() - started)
            if remaining <= 0:
                hit_deadline = True
                break
            signal.alarm(max(5, int(remaining) + 1))
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
    except _Deadline:
        hit_deadline = True
        print(json.dumps({"phase": "alarm_deadline", "rounds": rounds}, sort_keys=True), flush=True)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)
        stop.set()
        thr.join(timeout=2.0)

    if hit_deadline and not (isinstance(last, dict) and last.get("all_complete") is True):
        payload = {
            "ok": True,
            "status": "BOOTSTRAP_PARTIAL",
            "rounds": rounds,
            "final": last,
            "bot_pids_after": _bot_pids(),
            "deadline": True,
            "source_cleanup_authorized": False,
        }
        print(json.dumps(payload, sort_keys=True, default=str), flush=True)
        _write_status(payload)
        return 0

    assert store is not None
    final = store.advance_one_emergency_bootstrap_round_robin()
    payload = {
        "ok": True,
        "status": "BOOTSTRAP_FORCED",
        "rounds": rounds,
        "final": final,
        "bot_pids_after": _bot_pids(),
        "source_cleanup_authorized": False,
    }
    print(json.dumps(payload, sort_keys=True, default=str), flush=True)
    _write_status(payload)
    if final.get("all_complete") is not True:
        raise SystemExit("BOOTSTRAP_NOT_COMPLETE:" + json.dumps(final, sort_keys=True, default=str)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
