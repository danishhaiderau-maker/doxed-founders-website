#!/usr/bin/env python3
"""Canonical signal probe (replaces the retired btc-signal-engine mirror parity gate).

Runs directly against the canonical bot in services/btc-conservative-agent:

* Phase 1 - frozen combo fixtures (tests/fixtures/signal-parity-cases.json)
  through combo_pathway_config.combo_lane_matches / is_benchmark_lane.
* Phase 2 (--full) - imports bot.py twice (research vs SHOWCASE_EXECUTION_ONLY)
  in a hermetic temporary working directory and proves the signal-path flags
  (research data collection, golden stack, AI temperature, edge threshold) are
  identical.

The byte-for-byte mirror (services/btc-signal-engine) only ever compared a file
with a copy of itself; this keeps the behavioural checks and drops the copy.

Usage:
  python scripts/signal_probe.py           # combo fixtures
  python scripts/signal_probe.py --full    # + bot.py import flag probe
  python scripts/signal_probe.py --json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = ROOT / "services" / "btc-conservative-agent"
FIXTURES = ROOT / "tests" / "fixtures" / "signal-parity-cases.json"

# Flags that must stay identical regardless of SHOWCASE_EXECUTION_ONLY.
SIGNAL_PATH_KEYS = ("research_data_collection", "golden_stack", "ai_temperature", "edge_threshold")


def _load_combos(path: Path):
    agent_path = str(AGENT_DIR)
    if agent_path not in sys.path:
        sys.path.insert(0, agent_path)
    spec = importlib.util.spec_from_file_location("canonical_combos", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def run_combo_fixtures(combos_mod, fixtures: Path = FIXTURES) -> dict:
    data = json.loads(fixtures.read_text(encoding="utf-8"))
    results, failed = [], 0
    for case in data.get("cases", []):
        cid = case["id"]
        if "lane" in case and "expect_benchmark" in case:
            ok = bool(combos_mod.is_benchmark_lane(case["lane"])) == case["expect_benchmark"]
            results.append({"id": cid, "ok": ok, "benchmark": case["lane"]})
            failed += 0 if ok else 1
            continue
        ai, direction, spread = case["ai"], case["direction"], case.get("spread")
        for lane in case.get("expect_lanes", []):
            ok = combos_mod.combo_lane_matches(lane, ai, direction, spread) is True
            results.append({"id": cid, "lane": lane, "expect": "match", "ok": ok})
            failed += 0 if ok else 1
        for lane in case.get("reject_lanes", []):
            ok = combos_mod.combo_lane_matches(lane, ai, direction, spread) is False
            results.append({"id": cid, "lane": lane, "expect": "reject", "ok": ok})
            failed += 0 if ok else 1
    return {"combo_tests": len(results), "combo_failed": failed, "details": results}


def _load_bot(showcase_only: bool):
    os.environ["SHOWCASE_EXECUTION_ONLY"] = "1" if showcase_only else "0"
    os.environ.pop("RAILWAY_ENVIRONMENT", None)
    os.environ.pop("RAILWAY_PROJECT_ID", None)
    if str(AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(AGENT_DIR))
    for name in list(sys.modules):
        if name in ("bot", "combo_pathway_config"):
            del sys.modules[name]
    import bot  # noqa: E402

    return bot


def _signal_flags_snapshot(bot_mod) -> dict:
    showcase_fn = getattr(bot_mod, "_showcase_execution_only", None)
    return {
        "research_data_collection": bot_mod.is_research_data_collection(),
        "showcase_execution_only": showcase_fn() if callable(showcase_fn) else False,
        "golden_stack": bot_mod.get_golden_stack_thresholds(),
        "ai_temperature": bot_mod.research_ai_temperature(),
        "edge_threshold": bot_mod.get_edge_threshold(),
    }


def run_full_probe() -> dict:
    # bot.py owns legacy runtime defaults at import time; keep the import
    # hermetic so it cannot leave config/log/archive artifacts in the repo.
    for key, value in (("FORCE_PAPER_MODE", "1"), ("RESEARCH_DATA_COLLECTION", "1"),
                       ("SKIP_EXCHANGE_MARKET_LOAD", "1")):
        os.environ.setdefault(key, value)
    previous = os.getcwd()
    work = tempfile.mkdtemp(prefix="dcf-signal-probe-")
    try:
        os.chdir(work)
        research = _signal_flags_snapshot(_load_bot(showcase_only=False))
        showcase = _signal_flags_snapshot(_load_bot(showcase_only=True))
    finally:
        os.chdir(previous)
        shutil.rmtree(work, ignore_errors=True)
    diffs = [
        {"field": key, "research": research[key], "showcase": showcase[key]}
        for key in SIGNAL_PATH_KEYS if research[key] != showcase[key]
    ]
    return {"research": research, "showcase": showcase, "flag_diffs": diffs, "flag_failed": len(diffs)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Canonical signal probe")
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    parser.add_argument("--full", action="store_true", help="Import bot.py and compare signal flags")
    args = parser.parse_args(argv)

    combos = AGENT_DIR / "combo_pathway_config.py"
    if not combos.is_file() or not FIXTURES.is_file():
        print("SIGNAL PROBE FAIL: missing combo_pathway_config.py or fixtures", file=sys.stderr)
        return 1
    if not (AGENT_DIR / "btc_conservative_agent.py").is_file():
        print("SIGNAL PROBE FAIL: missing btc_conservative_agent.py entry point", file=sys.stderr)
        return 1
    out = {"combos": str(combos), "agent": run_combo_fixtures(_load_combos(combos))}
    ok = out["agent"]["combo_failed"] == 0
    if args.full:
        out["full"] = run_full_probe()
        ok = ok and out["full"]["flag_failed"] == 0
    out["passed"] = ok
    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print("\n=== Canonical signal probe ===\n")
        print(f"Combo fixtures: {out['agent']['combo_tests']} checks, failed={out['agent']['combo_failed']}")
        if args.full:
            print(f"Signal flag diffs (research vs showcase-only): {out['full']['flag_failed']}")
        print(f"\nResult: {'PASS' if ok else 'FAIL'}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
