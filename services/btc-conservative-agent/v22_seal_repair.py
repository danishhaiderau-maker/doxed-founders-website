"""Quarantine research_events_v22 seal receipts whose sealed generation file is gone.

    python v22_seal_repair.py plan    --runtime-root /app/data/runtime
    python v22_seal_repair.py execute --runtime-root /app/data/runtime --confirm QUARANTINE-V22-SEALS:<sha12>

Receipts are moved under ``research_events_v22.seals/quarantine/<stamp>/`` with a
manifest; nothing is deleted. Execute requires the paper maintenance hold.

When the orphan receipts crash the bot at boot, ``/health`` never answers and the
hold cannot be proven over HTTP. The offline pair replaces that proof:

    python v22_seal_repair.py offline-proof   --data-dir /app/data --expected-rev <rev12> --expect-generations 1,2
    python v22_seal_repair.py offline-execute --data-dir /app/data --expected-rev <rev12> --expect-generations 1,2 \
        --confirm QUARANTINE-V22-SEALS:<sha12>

offline-proof runs while the process crash-loops and requires the boot crash to be
this seal error, the durable paper lifecycle to be flat and unarmed, and the orphan
set to be exactly the expected generations. offline-execute runs with no bot
process on the machine and verifies every receipt hash before and after the move.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable

import clean_epoch_wipe
from collector_v22 import plan_orphan_event_seals, quarantine_orphan_event_seals

CONFIRM_PREFIX = "QUARANTINE-V22-SEALS"
SEAL_ERROR = "V22_SEAL_RECEIPT_INVALID"
BOOT_MARKER = "[fly-entrypoint] bot starting"
EXIT_MARKER = "[fly-entrypoint] bot exited rc="
CRASH_MARKER = "GLOBAL CRASH DETECTED"
MIN_CRASH_BOOTS = 3
MAX_LOG_AGE_SEC = 180.0
LOG_TAIL_BYTES = 2 * 1024 * 1024
BOT_PROCESS_MARKERS = ("btc_conservative_agent", "/app/bot.py", "fly-entrypoint", "fly_relay_state_pusher")


def confirm_token(plan_sha256: str) -> str:
    return f"{CONFIRM_PREFIX}:{plan_sha256[:12]}"


def _sha256(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def durable_state_violations(data_dir: str, expected_rev: str, env=os.environ) -> tuple[list[str], dict]:
    runtime = Path(data_dir) / "runtime"
    violations: list[str] = []
    if str(env.get("SOURCE_GIT_REV") or "")[:12].lower() != expected_rev.lower():
        violations.append("revision mismatch")
    try:
        lifecycle = json.loads((runtime / "paper_lifecycle_v1.json").read_text(encoding="utf-8"))
        config = json.loads((runtime / "config-7002.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return violations + [f"durable state unreadable: {type(exc).__name__}"], {}
    if lifecycle.get("paper_only") is not True or lifecycle.get("live_armed") is not False:
        violations.append("lifecycle not paper-only/unarmed")
    if lifecycle.get("positions") != [] or lifecycle.get("pending_orders") != []:
        violations.append("paper lifecycle not flat")
    if config.get("live_armed") is True or config.get("bitfinex_live_enabled") is True:
        violations.append("config armed")
    state = {
        "paper_only": lifecycle.get("paper_only"), "live_armed": lifecycle.get("live_armed"),
        "open_positions": len(lifecycle.get("positions") or []),
        "pending_orders": len(lifecycle.get("pending_orders") or []),
        "config_live_armed": config.get("live_armed"),
        "config_bitfinex_live_enabled": config.get("bitfinex_live_enabled"),
        "config_execution_paused": config.get("execution_paused"),
        "config_manual_admin_pause": config.get("manual_admin_pause"),
        "config_pause_owner": config.get("pause_owner"),
    }
    return violations, state


def crash_loop_violations(data_dir: str, now: float | None = None) -> tuple[list[str], dict]:
    log = Path(data_dir) / "bot.log"
    try:
        stat = log.stat()
        with log.open("rb") as handle:
            handle.seek(max(0, stat.st_size - LOG_TAIL_BYTES))
            tail = handle.read().decode("utf-8", "replace")
    except OSError as exc:
        return [f"bot.log unreadable: {type(exc).__name__}"], {}
    age = (time.time() if now is None else now) - stat.st_mtime
    # Boot segments that finished: every one must have crashed on the seal error.
    finished = tail.split(BOOT_MARKER)[1:-1]
    recent = finished[-MIN_CRASH_BOOTS:]
    crashed = [s for s in recent if SEAL_ERROR in s and CRASH_MARKER in s and EXIT_MARKER in s]
    violations = []
    if age > MAX_LOG_AGE_SEC:
        violations.append("bot.log not advancing")
    if len(recent) < MIN_CRASH_BOOTS or len(crashed) != len(recent):
        violations.append("recent boots did not all crash on the seal error")
    return violations, {"log_age_sec": round(age, 1), "recent_boots": len(recent),
                        "recent_seal_crashes": len(crashed)}


def _cmdlines(proc_root: str) -> list[str]:
    rows = []
    for entry in os.listdir(proc_root):
        if not entry.isdigit() or int(entry) == os.getpid():
            continue
        try:
            raw = Path(proc_root, entry, "cmdline").read_bytes()
        except OSError:
            continue
        rows.append(raw.replace(b"\0", b" ").decode("utf-8", "replace").strip())
    return rows


def bot_processes(proc_root: str = "/proc") -> list[str]:
    return [c[:200] for c in _cmdlines(proc_root) if any(m in c for m in BOT_PROCESS_MARKERS)]


def process_report(proc_root: str = "/proc") -> dict:
    return {"bot_processes": bot_processes(proc_root),
            "sleep_hold": any(c == "sleep infinity" for c in _cmdlines(proc_root))}


def _expected_plan(runtime_root: str, generations: list[int]) -> tuple[dict, list[str]]:
    plan = plan_orphan_event_seals(runtime_root)
    plan["confirm_token"] = confirm_token(plan["plan_sha256"])
    actual = [row["generation"] for row in plan["orphans"]]
    return plan, ([] if actual == generations else [f"orphan generations {actual} != expected {generations}"])


def _offline(args, env, now, proc_root) -> int:
    runtime_root = str(Path(args.data_dir) / "runtime")
    generations = sorted(int(g) for g in args.expect_generations.split(",") if g.strip())
    violations, state = durable_state_violations(args.data_dir, args.expected_rev, env)
    plan, plan_violations = _expected_plan(runtime_root, generations)
    violations += plan_violations
    out = {"schema": "v22_seal_offline_repair_v1", "mode": args.mode, "revision": args.expected_rev,
           "state": state, "plan": plan}
    if args.mode == "offline-proof":
        loop_violations, loop = crash_loop_violations(args.data_dir, now)
        violations += loop_violations
        out["crash_loop"] = loop
    else:
        running = bot_processes(proc_root)
        out["bot_processes"] = running
        if running:
            violations.append("bot process is running")
        if args.confirm != plan["confirm_token"]:
            violations.append("confirm token mismatch")
    if violations:
        print(json.dumps({**out, "status": "REFUSED", "violations": violations}, sort_keys=True))
        return 9
    if args.mode == "offline-proof":
        print(json.dumps({**out, "status": "PROVEN"}, sort_keys=True))
        return 0
    seal_dir = Path(runtime_root) / "research_events_v22.seals"
    kept_before = {p.name: _sha256(str(p)) for p in seal_dir.glob("generation-*.json")
                   if p.name not in {row["name"] for row in plan["orphans"]}}
    result = quarantine_orphan_event_seals(runtime_root, expected_plan_sha256=plan["plan_sha256"])
    quarantine = Path(runtime_root) / result["quarantine_dir"]
    after = {row["name"]: _sha256(str(quarantine / row["name"])) for row in plan["orphans"]}
    kept_after = {p.name: _sha256(str(p)) for p in seal_dir.glob("generation-*.json")}
    remaining = plan_orphan_event_seals(runtime_root)["orphans"]
    checks = {
        "moved_hashes_match": after == {row["name"]: row["sha256"] for row in plan["orphans"]},
        "other_receipts_unchanged": kept_after == kept_before,
        "no_orphans_remain": remaining == [],
    }
    status = "COMPLETE" if all(checks.values()) else "VERIFY_FAILED"
    print(json.dumps({**out, "status": status, "moved": result["moved"],
                      "quarantine_dir": result["quarantine_dir"], "moved_sha256": after,
                      "checks": checks}, sort_keys=True))
    return 0 if status == "COMPLETE" else 8


def main(argv: list[str] | None = None, fetch: Callable[[str], dict] | None = None,
         env=None, now: float | None = None, proc_root: str = "/proc") -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("plan", "execute", "offline-proof", "offline-execute", "processes"))
    parser.add_argument("--runtime-root", default="")
    parser.add_argument("--data-dir", default="")
    parser.add_argument("--expected-rev", default="")
    parser.add_argument("--expect-generations", default="")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--health-url", default=clean_epoch_wipe.HELD_DOWN_URLS["fly"])
    args = parser.parse_args(argv)
    if args.mode == "processes":
        print(json.dumps(process_report(proc_root), sort_keys=True))
        return 0
    if args.mode.startswith("offline-"):
        if not (args.data_dir and len(args.expected_rev) == 12 and args.expect_generations):
            parser.error("offline modes need --data-dir, a 12-character --expected-rev and --expect-generations")
        return _offline(args, os.environ if env is None else env, now, proc_root)
    if not args.runtime_root:
        parser.error("--runtime-root is required")
    plan = plan_orphan_event_seals(args.runtime_root)
    plan["confirm_token"] = confirm_token(plan["plan_sha256"])
    if args.mode == "plan":
        print(json.dumps(plan, sort_keys=True))
        return 0
    if args.confirm != plan["confirm_token"]:
        print(json.dumps({"error": "CONFIRM_TOKEN_MISMATCH", "expected_prefix": CONFIRM_PREFIX}))
        return 5
    try:
        violations = clean_epoch_wipe.held_down_violations((fetch or clean_epoch_wipe._fetch_health)(args.health_url))
    except Exception as exc:  # noqa: BLE001 - any doubt fails closed
        violations = [f"health unavailable: {type(exc).__name__}"]
    if violations:
        print(json.dumps({"error": "BOT_NOT_HELD_DOWN", "violations": violations}))
        return 7
    result = quarantine_orphan_event_seals(args.runtime_root, expected_plan_sha256=plan["plan_sha256"])
    remaining = plan_orphan_event_seals(args.runtime_root)["orphans"]
    print(json.dumps({**result, "remaining_orphans": remaining}, sort_keys=True))
    return 0 if not remaining else 8


if __name__ == "__main__":
    sys.exit(main())
