#!/usr/bin/env python3
"""
Signal parity probe — Phase 2.

Validates combo/lane logic and proves SHOWCASE_EXECUTION_ONLY does not alter
signal-path flags (is_research_data_collection, golden stack, AI temperature).

Usage:
  python signal_probe.py
  python signal_probe.py --json
  python signal_probe.py --full   # imports bot.py (slow)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ENGINE_DIR = Path(__file__).resolve().parent
AGENT_DIR = ENGINE_DIR.parent / "btc-conservative-agent"
FIXTURES = ROOT / "tests" / "fixtures" / "signal-parity-cases.json"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def _run_combo_fixtures(combos_mod) -> dict:
    data = json.loads(FIXTURES.read_text(encoding="utf-8"))
    results = []
    failed = 0
    for case in data.get("combo_cases", []):
        cid = case["id"]
        kind = case["kind"]
        lane = case["lane"]
        if kind == "benchmark":
            actual = bool(combos_mod.is_benchmark_lane(lane))
        elif kind == "independent-research-candidate":
            actual = (
                bool(combos_mod.is_independent_ai_lane(lane))
                and bool(combos_mod.is_research_candidate_lane(lane))
                and bool(combos_mod.is_combo_execution_lane(lane))
            )
        elif kind == "inherited-match":
            actual = bool(
                combos_mod.combo_lane_matches(
                    lane,
                    case["ai"],
                    case["direction"],
                    case.get("spread"),
                )
            )
        else:
            raise ValueError(f"Unknown combo fixture kind: {kind}")
        ok = actual is case["expect"]
        results.append(
            {"id": cid, "lane": lane, "kind": kind, "expect": case["expect"], "actual": actual, "ok": ok}
        )
        if not ok:
            failed += 1
    return {"combo_tests": len(results), "combo_failed": failed, "details": results}


def _run_type_b_fixtures(type_b_mod) -> dict:
    data = json.loads(FIXTURES.read_text(encoding="utf-8"))
    results = []
    failed = 0
    for case in data.get("type_b_cases", []):
        entered, detail = type_b_mod.should_enter_type_b(case["ai_prob"], case["features"])
        reason = detail.get("block_reason") or ""
        reason_ok = not case.get("expect_reason") or reason.startswith(case["expect_reason"])
        ok = bool(entered) is case["expect_enter"] and reason_ok
        results.append(
            {
                "id": case["id"],
                "expect_enter": case["expect_enter"],
                "actual_enter": bool(entered),
                "expect_reason": case.get("expect_reason"),
                "actual_reason": reason or None,
                "ok": ok,
            }
        )
        if not ok:
            failed += 1
    return {"tests": len(results), "failed": failed, "details": results}


def _run_sr_fixtures(sr_mod) -> dict:
    data = json.loads(FIXTURES.read_text(encoding="utf-8"))
    results = []
    failed = 0
    for case in data.get("sr_cases", []):
        entered, detail = sr_mod.should_enter_sr(case["side"], case["features"], case["ai_prob"])
        reason = detail.get("block_reason") or ""
        reason_ok = not case.get("expect_reason") or reason.startswith(case["expect_reason"])
        ok = bool(entered) is case["expect_enter"] and reason_ok
        results.append(
            {
                "id": case["id"],
                "expect_enter": case["expect_enter"],
                "actual_enter": bool(entered),
                "expect_reason": case.get("expect_reason"),
                "actual_reason": reason or None,
                "ok": ok,
            }
        )
        if not ok:
            failed += 1
    return {"tests": len(results), "failed": failed, "details": results}


def _load_bot(agent_dir: Path, showcase_only: bool):
    os.environ["SHOWCASE_EXECUTION_ONLY"] = "1" if showcase_only else "0"
    os.environ.pop("RAILWAY_ENVIRONMENT", None)
    os.environ.pop("RAILWAY_PROJECT_ID", None)
    if str(agent_dir) not in sys.path:
        sys.path.insert(0, str(agent_dir))
    # Fresh import per mode
    for name in list(sys.modules):
        if name in ("bot", "combo_pathway_config"):
            del sys.modules[name]
    import bot  # noqa: E402

    return bot


# Flags that must stay identical regardless of SHOWCASE_EXECUTION_ONLY.
SIGNAL_PATH_KEYS = (
    "research_data_collection",
    "golden_stack",
    "ai_temperature",
    "edge_threshold",
)


def _signal_flags_snapshot(bot_mod) -> dict:
    showcase_fn = getattr(bot_mod, "_showcase_execution_only", None)
    return {
        "research_data_collection": bot_mod.is_research_data_collection(),
        "showcase_execution_only": showcase_fn() if callable(showcase_fn) else False,
        "golden_stack": bot_mod.get_golden_stack_thresholds(),
        "ai_temperature": bot_mod.research_ai_temperature(),
        "edge_threshold": bot_mod.get_edge_threshold(),
    }


def _run_full_parity() -> dict:
    research = _signal_flags_snapshot(_load_bot(AGENT_DIR, showcase_only=False))
    showcase = _signal_flags_snapshot(_load_bot(AGENT_DIR, showcase_only=True))
    diffs = []
    for key in SIGNAL_PATH_KEYS:
        if research[key] != showcase[key]:
            diffs.append({"field": key, "research": research[key], "showcase": showcase[key]})
    return {
        "research": research,
        "showcase": showcase,
        "flag_diffs": diffs,
        "flag_failed": len(diffs),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Signal engine parity probe")
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    parser.add_argument("--full", action="store_true", help="Import bot.py and compare signal flags")
    args = parser.parse_args()

    engine_combos = ENGINE_DIR / "combos.py"
    agent_combos = AGENT_DIR / "combo_pathway_config.py"
    if not engine_combos.is_file() or not agent_combos.is_file():
        print("PARITY FAIL: missing combos modules", file=sys.stderr)
        return 1

    mod_engine = _load_module(engine_combos, "engine_combos")
    mod_agent = _load_module(agent_combos, "agent_combos")
    type_b_mod = _load_module(ENGINE_DIR / "type_b_hunter_v1.py", "engine_type_b_hunter_v1")
    sr_mod = _load_module(ENGINE_DIR / "sr_micro_tile_v1.py", "engine_sr_micro_tile_v1")

    out = {"engine_combos": str(engine_combos), "agent_combos": str(agent_combos)}

    # Cross-module combo parity (same inputs → same outputs on fixtures)
    r_engine = _run_combo_fixtures(mod_engine)
    r_agent = _run_combo_fixtures(mod_agent)
    r_type_b = _run_type_b_fixtures(type_b_mod)
    r_sr = _run_sr_fixtures(sr_mod)
    out["engine"] = r_engine
    out["agent"] = r_agent
    out["type_b"] = r_type_b
    out["sr"] = r_sr
    combo_ok = (
        r_engine["combo_failed"] == 0
        and r_agent["combo_failed"] == 0
        and r_type_b["failed"] == 0
        and r_sr["failed"] == 0
    )

    cross_failed = 0
    for e, a in zip(r_engine["details"], r_agent["details"]):
        if e.get("actual") != a.get("actual"):
            cross_failed += 1
    out["cross_module_mismatch"] = cross_failed
    combo_ok = combo_ok and cross_failed == 0

    if args.full:
        out["full"] = _run_full_parity()
        combo_ok = combo_ok and out["full"]["flag_failed"] == 0

    out["passed"] = combo_ok

    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print("\n=== Signal probe ===\n")
        print(f"Combo fixtures: engine failed={r_engine['combo_failed']} agent failed={r_agent['combo_failed']}")
        print(f"Type B fixtures: failed={r_type_b['failed']}")
        print(f"S/R fixtures: failed={r_sr['failed']}")
        print(f"Cross-module mismatches: {cross_failed}")
        if args.full:
            print(f"Signal flag diffs (research vs showcase-only): {out['full']['flag_failed']}")
        print(f"\nResult: {'PASS' if combo_ok else 'FAIL'}\n")

    return 0 if combo_ok else 1


if __name__ == "__main__":
    sys.exit(main())
