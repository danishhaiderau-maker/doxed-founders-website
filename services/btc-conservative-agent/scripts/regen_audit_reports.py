#!/usr/bin/env python3
"""Regenerate key reports after dashboard audit fixes."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import analyzer_research_engine_v62 as eng

session = eng.load_research_session()
trades = eng.robust_read_csv(eng.TRADES_FILE, "Trades")
if session and hasattr(eng, "filter_df_since_session"):
    trades = eng.filter_df_since_session(trades, session, ts_cols=("ts", "close_ts"))
print(f"trades={len(trades)}")

chase_payload = eng.chase_attribution_report(trades=trades, session=session)
eng.chase_effectiveness_report(trades=trades, session=session, chase_payload=chase_payload)
eng.chase_threshold_report(trades=trades, session=session, chase_payload=chase_payload)

bench = eng.benchmark_vs_lanes_report(trades=trades, session=session)
eng.exit_leakage_by_reason_report(trades=trades, session=session)
eng.exit_combinations_report(trades=trades, session=session)
eng.exit_ladder_simulator_report(trades=trades, session=session)
eng.type_b_predictor_report(trades=trades, session=session)

v2 = eng._v2_lane_metrics_from_logs(session)
print("V2 metrics:", v2)

a160 = (bench or {}).get("lanes", {}).get("A160_CONTEXT_CHASE_EXIT_V2", {})
print("A160 lane:", {k: a160.get(k) for k in (
    "approves", "real_fills", "v2_checker_pass_sims", "v2_reject_counterfactual_sims"
)})

eff = eng._load_json_report(eng.CHASE_EFFECTIVENESS_REPORT_FILE)
print("chase buckets:", eff.get("buckets"))
