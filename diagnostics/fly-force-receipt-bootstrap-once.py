#!/usr/bin/env python3
"""Force receipt-bootstrap while holding entrypoint restarts frozen.

Fly entrypoint auto-restarts the bot every 3s. A continuous freezer that
SIGKILLs the bot without SIGSTOP on PID 1 OOMs the force process. Sequence:

1. SIGSTOP PID 1 (entrypoint restart loop)
2. SIGKILL bot + lifecycle + invent + future_paths
3. Bounded bootstrap rounds with early status heartbeats
4. SIGCONT PID 1 in finally so HTTP/paper revive

Never arms Bitfinex. Disk all_complete alone is not enough for invent; after
this returns, a live lifecycle SUCCESS must project receipt_bootstrap COMPLETE.
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
    "data_sync_inventory_worker",
    "research_v3_future_paths_worker",
)
_entrypoint_stopped = False


def _write_status(payload: dict) -> None:
    payload = dict(payload)
    payload["pid"] = os.getpid()
    payload["written_at"] = time.time()
    text = json.dumps(payload, sort_keys=True, default=str) + "\n"
    try:
        STATUS_PATH.write_text(text, encoding="utf-8")
    except OSError as exc:
        print(json.dumps({"status_write_error": str(exc)}, sort_keys=True), flush=True)
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
        if pid == me or pid == 1:
            continue
        if "fly-force-receipt-bootstrap" in cmd:
            continue
        if any(tok in cmd for tok in _KILL_TOKENS):
            found.append(pid)
    return found


def _ps_snapshot() -> list[str]:
    out = []
    for pid, cmd in _proc_cmdlines():
        if any(tok in cmd for tok in ("python", "btc", "lifecycle", "7002", "agent", "entrypoint")):
            out.append(f"{pid} {cmd[:200]}")
    return out[:40]


def _fuser_kill_ledgers() -> int:
    ledgers = RUNTIME / "v3" / "ledgers"
    if not ledgers.is_dir():
        return 0
    n = 0
    me = os.getpid()
    for path in ledgers.glob("*.jsonl*"):
        try:
            target = str(path.resolve())
        except OSError:
            continue
        for pid, _cmd in _proc_cmdlines():
            if pid in (me, 1):
                continue
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
        except (ProcessLookupError, PermissionError):
            pass
    killed += _fuser_kill_ledgers()
    return killed


def _stop_entrypoint() -> bool:
    global _entrypoint_stopped
    try:
        os.kill(1, signal.SIGSTOP)
        _entrypoint_stopped = True
        return True
    except (ProcessLookupError, PermissionError, OSError) as exc:
        print(json.dumps({"entrypoint_sigstop_failed": f"{type(exc).__name__}:{exc}"}, sort_keys=True), flush=True)
        return False


def _cont_entrypoint() -> None:
    global _entrypoint_stopped
    if not _entrypoint_stopped:
        return
    try:
        os.kill(1, signal.SIGCONT)
    except (ProcessLookupError, PermissionError, OSError) as exc:
        print(json.dumps({"entrypoint_sigcont_failed": f"{type(exc).__name__}:{exc}"}, sort_keys=True), flush=True)
    _entrypoint_stopped = False


def _freezer(stop: threading.Event) -> None:
    while not stop.wait(0.5):
        _kill_bots()


def _resolve_epoch() -> str:
    session_paths = (
        RUNTIME / "research_session.json",
        DATA_ROOT / "research_session.json",
        Path("/app/research_session.json"),
    )
    for path in session_paths:
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        bound = str(meta.get("collector_v22_epoch_id") or "").strip()
        if bound.startswith("epoch-"):
            return bound
    return EXPECTED_EPOCH


def main() -> int:
    if not EXPECTED_EPOCH.startswith("epoch-"):
        raise SystemExit("EXPECTED_EPOCH required")
    live_epoch = _resolve_epoch()
    if live_epoch and live_epoch != EXPECTED_EPOCH:
        # Prefer the live bound epoch; refuse silent mismatch only when both set.
        print(json.dumps({
            "epoch_resolve": {"expected": EXPECTED_EPOCH, "live": live_epoch},
        }, sort_keys=True), flush=True)
        epoch = live_epoch
    else:
        epoch = EXPECTED_EPOCH or live_epoch
    if not epoch.startswith("epoch-"):
        raise SystemExit("no live epoch resolved")

    _write_status({"ok": True, "status": "BOOTSTRAP_STARTING", "epoch": epoch, "phase": "import"})

    sys.path.insert(0, "/app")
    import research_v3_store as store_module  # type: ignore
    from lifecycle_pipeline_worker import LEDGER_NAMES  # type: ignore

    # Entrypoint is SIGSTOP'd so larger batches are safe and finish sooner.
    if MAX_SECONDS <= 45:
        store_module._BOOTSTRAP_RECORDS_PER_STEP = 128
        store_module._BOOTSTRAP_BYTES_PER_STEP = 4 * 1024 * 1024
    else:
        store_module._BOOTSTRAP_RECORDS_PER_STEP = 512
        store_module._BOOTSTRAP_BYTES_PER_STEP = 8 * 1024 * 1024

    probe = {
        "data_root": str(DATA_ROOT),
        "runtime": str(RUNTIME),
        "epoch": epoch,
        "expected_epoch_input": EXPECTED_EPOCH,
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
        _write_status({"ok": True, "dry_run": True, "epoch": epoch})
        return 0

    stopped = _stop_entrypoint()
    _write_status({
        "ok": True, "status": "BOOTSTRAP_PARTIAL", "phase": "entrypoint_stopped",
        "entrypoint_stopped": stopped, "rounds": 0, "final": None, "deadline": False,
        "source_cleanup_authorized": False,
    })

    stop = threading.Event()
    thr = threading.Thread(target=_freezer, args=(stop,), daemon=True)
    thr.start()
    time.sleep(0.8)
    killed = _kill_bots()
    print(json.dumps({
        "freezer_started": True,
        "entrypoint_stopped": stopped,
        "killed": killed,
        "alive_after": _bot_pids(),
        "ps_after_kill": _ps_snapshot(),
    }, sort_keys=True), flush=True)
    time.sleep(1.0)

    _write_status({
        "ok": True, "status": "BOOTSTRAP_PARTIAL", "phase": "store_init",
        "entrypoint_stopped": stopped, "rounds": 0, "final": None, "deadline": False,
        "source_cleanup_authorized": False,
    })
    store = store_module.V3EvidenceStore(RUNTIME, epoch_id=epoch)
    print(json.dumps({"phase": "store_ready", "epoch": epoch}, sort_keys=True), flush=True)

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
            worker.join(timeout=max(15.0, remaining - 2.0))
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
            _write_status({
                "ok": True,
                "status": "BOOTSTRAP_PARTIAL",
                "phase": "advancing",
                "rounds": rounds,
                "final": last,
                "deadline": False,
                "entrypoint_stopped": stopped,
                "source_cleanup_authorized": False,
            })
            if isinstance(last, dict) and last.get("all_complete") is True:
                break
            if isinstance(last, dict) and last.get("blocked") is True:
                time.sleep(0.15)
    finally:
        stop.set()
        thr.join(timeout=1.0)
        _cont_entrypoint()

    if hit_deadline and not (isinstance(last, dict) and last.get("all_complete") is True):
        _write_status({
            "ok": True,
            "status": "BOOTSTRAP_PARTIAL",
            "rounds": rounds,
            "final": last,
            "bot_pids_after": _bot_pids(),
            "deadline": True,
            "entrypoint_stopped": False,
            "source_cleanup_authorized": False,
        })
        os._exit(0)

    final = store.advance_one_emergency_bootstrap_round_robin()
    _write_status({
        "ok": True,
        "status": "BOOTSTRAP_FORCED",
        "rounds": rounds,
        "final": final,
        "bot_pids_after": _bot_pids(),
        "entrypoint_stopped": False,
        "source_cleanup_authorized": False,
    })
    if final.get("all_complete") is not True:
        raise SystemExit("BOOTSTRAP_NOT_COMPLETE:" + json.dumps(final, sort_keys=True, default=str)[:2000])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        _cont_entrypoint()
