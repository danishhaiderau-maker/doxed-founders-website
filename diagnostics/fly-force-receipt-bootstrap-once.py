#!/usr/bin/env python3
"""Force receipt-bootstrap while keeping trading-bot writers off ledger locks.

Fly guest image may lack `ps`/`pkill`; scan /proc instead. Advance calls can
block forever on LOCK_EX, so each round runs in a worker thread with a join
timeout and hard-exits the chunk with BOOTSTRAP_PARTIAL on stall.
"""
from __future__ import annotations

import json
import os
import signal
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
_KILL_TOKENS = (
    "btc_conservative_agent",
    "lifecycle_pipeline_worker",
)


def _write_status(payload: dict) -> None:
    text = json.dumps(payload, sort_keys=True, default=str) + "\n"
    STATUS_PATH.write_text(text, encoding="utf-8")
    print(text, end="", flush=True)


def _proc_cmdlines() -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return rows
    for name in entries:
        if not name.isdigit():
            continue
        pid = int(name)
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            continue
        cmd = raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()
        if cmd:
            rows.append((pid, cmd))
    return rows


def _bot_pids() -> list[int]:
    me = os.getpid()
    found = []
    for pid, cmd in _proc_cmdlines():
        if pid == me:
            continue
        if "fly-force-receipt-bootstrap" in cmd:
            continue
        if any(tok in cmd for tok in _KILL_TOKENS):
            found.append(pid)
    return found


def _ps_snapshot() -> list[str]:
    out = []
    for pid, cmd in _proc_cmdlines():
        if any(tok in cmd for tok in ("python", "btc", "lifecycle", "7002", "agent")):
            out.append(f"{pid} {cmd[:200]}")
    return out[:40]


def _fuser_kill_ledgers() -> int:
    ledgers = RUNTIME / "v3" / "ledgers"
    if not ledgers.is_dir():
        return 0
    n = 0
    for path in ledgers.glob("*.jsonl*"):
        # Best-effort: open+fcntl unlock isn't enough across processes; try
        # killing whoever has the file open via /proc/pid/fd.
        try:
            target = str(path.resolve())
        except OSError:
            continue
        for pid, _cmd in _proc_cmdlines():
            fd_dir = Path(f"/proc/{pid}/fd")
            if not fd_dir.is_dir():
                continue
            try:
                for fd in fd_dir.iterdir():
                    try:
                        link = os.readlink(fd)
                    except OSError:
                        continue
                    if link == target or link.startswith(target + " "):
                        try:
                            os.kill(pid, signal.SIGKILL)
                            n += 1
                        except (ProcessLookupError, PermissionError):
                            pass
                        break
            except OSError:
                continue
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
    killed += _fuser_kill_ledgers()
    return killed


def _freezer(stop: threading.Event) -> None:
    while not stop.wait(0.35):
        _kill_bots()


def main() -> int:
    if not EXPECTED_EPOCH.startswith("epoch-"):
        raise SystemExit("EXPECTED_EPOCH required")
    sys.path.insert(0, "/app")
    import research_v3_store as store_module  # type: ignore
    from lifecycle_pipeline_worker import LEDGER_NAMES  # type: ignore

    if MAX_SECONDS <= 45:
        store_module._BOOTSTRAP_RECORDS_PER_STEP = 128
        store_module._BOOTSTRAP_BYTES_PER_STEP = 4 * 1024 * 1024
    elif MAX_SECONDS <= 120:
        store_module._BOOTSTRAP_RECORDS_PER_STEP = 512
        store_module._BOOTSTRAP_BYTES_PER_STEP = 8 * 1024 * 1024
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
        _write_status({"ok": True, "dry_run": True})
        return 0

    stop = threading.Event()
    thr = threading.Thread(target=_freezer, args=(stop,), daemon=True)
    thr.start()
    time.sleep(0.8)
    killed = _kill_bots()
    print(json.dumps({
        "freezer_started": True,
        "killed": killed,
        "alive_after": _bot_pids(),
        "ps_after_kill": _ps_snapshot(),
    }, sort_keys=True), flush=True)

    print(json.dumps({"phase": "store_init"}, sort_keys=True), flush=True)
    store = store_module.V3EvidenceStore(RUNTIME, epoch_id=EXPECTED_EPOCH)
    print(json.dumps({"phase": "store_ready"}, sort_keys=True), flush=True)

    started = time.time()
    rounds = 0
    last = None
    hit_deadline = False
    try:
        while True:
            remaining = MAX_SECONDS - (time.time() - started)
            if remaining <= 0.5:
                hit_deadline = True
                break
            box: dict = {}

            def _worker() -> None:
                try:
                    box["last"] = store.advance_one_emergency_bootstrap_round_robin()
                except Exception as exc:  # noqa: BLE001
                    box["error"] = f"{type(exc).__name__}:{exc}"

            worker = threading.Thread(target=_worker, daemon=True)
            worker.start()
            worker.join(timeout=max(3.0, min(remaining, 20.0)))
            if worker.is_alive():
                hit_deadline = True
                print(json.dumps({
                    "phase": "advance_timeout",
                    "rounds": rounds,
                    "remaining_s": round(remaining, 1),
                    "killed_bots": _kill_bots(),
                }, sort_keys=True), flush=True)
                break
            if "error" in box:
                print(json.dumps({"phase": "advance_error", "error": box["error"]}, sort_keys=True), flush=True)
                hit_deadline = True
                break
            last = box.get("last")
            rounds += 1
            print(json.dumps({
                "round": rounds,
                "progress": last,
                "killed_bots": _kill_bots(),
                "elapsed_s": round(time.time() - started, 1),
            }, sort_keys=True, default=str), flush=True)
            if isinstance(last, dict) and last.get("all_complete") is True:
                break
            if isinstance(last, dict) and last.get("blocked") is True:
                time.sleep(0.15)
    finally:
        stop.set()
        thr.join(timeout=1.0)

    if hit_deadline and not (isinstance(last, dict) and last.get("all_complete") is True):
        _write_status({
            "ok": True,
            "status": "BOOTSTRAP_PARTIAL",
            "rounds": rounds,
            "final": last,
            "bot_pids_after": _bot_pids(),
            "deadline": True,
            "source_cleanup_authorized": False,
        })
        # Hard-exit so a stuck LOCK_EX worker thread cannot keep the process alive.
        os._exit(0)

    final = store.advance_one_emergency_bootstrap_round_robin()
    _write_status({
        "ok": True,
        "status": "BOOTSTRAP_FORCED",
        "rounds": rounds,
        "final": final,
        "bot_pids_after": _bot_pids(),
        "source_cleanup_authorized": False,
    })
    if final.get("all_complete") is not True:
        raise SystemExit("BOOTSTRAP_NOT_COMPLETE:" + json.dumps(final, sort_keys=True, default=str)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
