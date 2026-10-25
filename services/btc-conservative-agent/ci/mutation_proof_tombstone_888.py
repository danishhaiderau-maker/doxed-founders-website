"""Source-level mutation proof for PR #888 (tombstone-external-delete + trade-count parity).

Not a pytest file and not run in CI by default (it copies the service tree once and re-runs the targeted tests per
mutant, about 3 s each). Run it from services/btc-conservative-agent:

    python ci/mutation_proof_tombstone_888.py

Each mutant is an exact, single-occurrence text replacement in a copy of bot.py or api_state_analytics.py. The
baseline must pass and every mutant must make the targeted tests FAIL (non-zero exit); a surviving mutant, a missing
or ambiguous anchor, or a failing baseline exits 1. The working tree is never modified.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ["test_trade_tombstone_external_delete.py", "test_trade_count_id_diff.py",
         "test_monitor_trade_count_and_force_paper.py"]

_REFUSAL_500 = ('return jsonify({"ok": False, "refused": code, "error": "REFUSAL_RECEIPT_WRITE_FAILED", **extra}), 500')
_PENDING_500 = ('return jsonify({"ok": False, "error": "PENDING_RECEIPT_WRITE_FAILED", "applied": False, **out}), 500')
_FINAL_500 = 'return jsonify({"ok": False, "error": "RECEIPT_WRITE_FAILED", **out}), 500'
_DRY = '    dry_run = body.get("dry_run", True) is not False\n    '
_PENDING_WRITE = ('_write_tombstone_external_receipt({**base_rec, "in_memory_matches": in_mem, "outcome": "PENDING"})')

# (id, file, anchor, replacement, what it breaks)
MUTANTS = [
    ("M01_refusal_receipt_failure_swallowed", "bot.py", _REFUSAL_500, "pass", "refusal receipt failure not surfaced"),
    ("M02_pending_receipt_failure_ignored", "bot.py", _PENDING_500, "pass", "applies without durable PENDING"),
    ("M03_final_receipt_failure_swallowed", "bot.py", _FINAL_500, "pass", "final receipt failure not surfaced"),
    ("M04_pending_receipt_removed", "bot.py", _PENDING_WRITE, "None", "no PENDING receipt before apply"),
    ("M05_receipt_writer_noop", "bot.py", "        os.write(fd, line.encode())\n", "        pass\n", "receipts not written"),
    ("M06_fsync_removed", "bot.py", "        os.fsync(fd)\n    finally:\n        os.close(fd)\n    return path",
     "        pass\n    finally:\n        os.close(fd)\n    return path", "receipt not durable"),
    ("M07_dry_run_default_false", "bot.py", _DRY + 'expected_sha', _DRY.replace("True", "False") + 'expected_sha',
     "default apply"),
    ("M08_dry_run_truthy", "bot.py", _DRY + 'expected_sha',
     '    dry_run = bool(body.get("dry_run", True))\n    expected_sha', "any falsy value applies"),
    ("M09_fallback_pending_blocker_removed", "bot.py", 'blockers.append("CSV_FALLBACK_PENDING")', "pass",
     "CSV_FALLBACK_PENDING not refused"),
    ("M10_live_armed_blocker_removed", "bot.py", '        blockers.append("LIVE_ARMED")\n    elif not relay',
     '        pass\n    elif not relay', "live armed not refused"),
    ("M11_fly_direct_arm_ignored", "bot.py", 'if bool(state.get("live_armed")) or relay["live_armed"]',
     'if relay["live_armed"]', "Fly-direct live_armed ignored"),
    ("M12_relay_arm_ignored", "bot.py", 'or relay["live_armed"] or relay["live_copy_output_enabled"]:',
     'or relay["live_copy_output_enabled"]:', "relay armed ignored"),
    ("M13_relay_output_ignored", "bot.py", 'or relay["live_armed"] or relay["live_copy_output_enabled"]:',
     'or relay["live_armed"]:', "live copy output ignored"),
    ("M14_unknown_relay_state_allowed", "bot.py", 'blockers.append("LIVE_STATE_UNKNOWN")', "pass",
     "unknown arm state not fail-closed"),
    ("M15_open_positions_blocker_removed", "bot.py", 'blockers.append("OPEN_POSITIONS")', "pass",
     "open position not refused"),
    ("M16_open_orders_blocker_removed", "bot.py", 'blockers.append("OPEN_ORDERS")', "pass", "open order not refused"),
    ("M17_r21_receipt_not_required", "bot.py", "problem = _r21_receipt_problem(r21, ids)", "problem = None",
     "no R21 receipt needed"),
    ("M18_r21_ids_not_listed_allowed", "bot.py", "    if set(ids) - listed:\n        return \"R21_RECEIPT_IDS_NOT_LISTED\"",
     "    if False:\n        return \"R21_RECEIPT_IDS_NOT_LISTED\"", "ids need not be in the receipt"),
    ("M19_r21_receipt_sha_unchecked", "bot.py", 'if receipt.get("receipt_sha256") != r21_receipt_sha256(entries):',
     "if False:", "receipt sha unchecked"),
    ("M20_list_sha_unchecked", "bot.py", "if not isinstance(expected_sha, str) or expected_sha != computed_sha:",
     "if False:", "list sha unchecked"),
    ("M21_ids_still_on_disk_allowed", "bot.py", 'return _refuse("IDS_STILL_ON_DISK", 409, ids_on_disk=still)', "pass",
     "tombstones ids that still have rows"),
    ("M22_apply_failure_no_terminal_receipt", "bot.py",
     '_write_tombstone_external_receipt({**base_rec, "outcome": "APPLY_FAILED",', "(lambda *a, **k: None)({**base_rec,",
     "PENDING never terminal"),
    ("M23_system_health_red_to_amber", "bot.py", '"status": "RED" if _d["persistent"] else',
     '"status": "AMBER" if _d["persistent"] else', "system-health RED downgraded"),
    ("M24_route_red_to_amber", "bot.py", '"status": ("RED" if diff["persistent"] else "AMBER")',
     '"status": ("AMBER" if diff["persistent"] else "AMBER")', "route RED downgraded"),
    ("M25_timer_never_reset", "api_state_analytics.py", "_mismatch_since.pop(csv_path, None)", "pass",
     "mismatch timer survives resync"),
    ("M26_timer_always_reset", "api_state_analytics.py", "since = _mismatch_since.setdefault(csv_path, now)",
     "since = _mismatch_since[csv_path] = now", "persistence never reached"),
    ("M27_persist_threshold_off_by_one", "api_state_analytics.py", 'bool(mismatch and age >= MISMATCH_PERSIST_S)',
     'bool(mismatch and age > MISMATCH_PERSIST_S)', "boundary"),
    ("M28_ids_cache_ignores_inode", "api_state_analytics.py",
     "    key = (st.st_ino, st.st_size, st.st_mtime_ns)\n    with _lock:\n        hit = _ids_cache.get(path)",
     "    key = (0, st.st_size, st.st_mtime_ns)\n    with _lock:\n        hit = _ids_cache.get(path)",
     "replaced CSV served from stale cache"),
]


def run_tests(cwd: Path) -> tuple[int, str]:
    env = {**os.environ, "PYTHONPATH": str(cwd), "PYTHONDONTWRITEBYTECODE": "1"}
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *TESTS],
                       cwd=cwd, env=env, capture_output=True, text=True)
    tail = [ln for ln in (p.stdout + p.stderr).strip().splitlines() if ln.strip()][-1:]
    return p.returncode, (tail[0] if tail else "")


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="mut888-"))
    tree = work / "svc"
    try:
        shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"))
        originals = {f: (tree / f).read_text(encoding="utf-8") for f in ("bot.py", "api_state_analytics.py")}
        rc, tail = run_tests(tree)
        print(f"BASELINE rc={rc} {tail}")
        if rc != 0:
            return 1
        results, bad = [], 0
        for mid, fname, anchor, repl, what in MUTANTS:
            src = originals[fname]
            n = src.count(anchor)
            if n != 1:
                print(f"{mid}: ANCHOR_COUNT={n} (must be exactly 1)")
                results.append({"id": mid, "status": "ANCHOR_ERROR", "count": n})
                bad += 1
                continue
            (tree / fname).write_text(src.replace(anchor, repl), encoding="utf-8")
            try:
                rc, tail = run_tests(tree)
            finally:
                (tree / fname).write_text(src, encoding="utf-8")
            status = "KILLED" if rc != 0 else "SURVIVED"
            bad += status != "KILLED"
            print(f"{mid}: {status}  ({what}; {tail})")
            results.append({"id": mid, "status": status, "breaks": what, "last_line": tail})
        print(json.dumps({"mutants": len(MUTANTS), "killed": sum(r["status"] == "KILLED" for r in results),
                          "failed": bad}))
        return 1 if bad else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
