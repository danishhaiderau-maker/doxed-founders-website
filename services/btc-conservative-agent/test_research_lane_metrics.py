"""Policy-filtered LAB/counterfactual analyzer contract."""

import os
import sys
import inspect
import json
import tempfile

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import analyzer_research_engine_v62 as analyzer
from sr_micro_tile_v2 import POLICY_ID as TILE2_POLICY_ID
from type_b_hunter_v1 import POLICY_VERSION as TYPE_B_POLICY_VERSION


def main() -> int:
    rows = pd.DataFrame(
        [
            {
                "trade_id": "tb-accepted",
                "research_lane": "TYPE_B_HUNTER_V1",
                "policy_version": TYPE_B_POLICY_VERSION,
                "collection_mode": "LAB",
                "policy_entered": True,
                "is_counterfactual": False,
                "filled": True,
                "net_pnl_usd": 2.5,
            },
            {
                "trade_id": "tb-rejected",
                "research_lane": "TYPE_B_HUNTER_V1",
                "policy_version": TYPE_B_POLICY_VERSION,
                "collection_mode": "CALIBRATION_COUNTERFACTUAL",
                "policy_entered": False,
                "is_counterfactual": True,
                "filled": True,
                "net_pnl_usd": -1.25,
            },
            {
                "trade_id": "tb-old-policy",
                "research_lane": "TYPE_B_HUNTER_V1",
                "policy_version": "old-policy",
                "collection_mode": "LAB",
                "policy_entered": True,
                "filled": True,
                "net_pnl_usd": 99.0,
            },
            {
                "trade_id": "tile2-accepted",
                "research_lane": "SR_MICRO_TILE_V2_STATIC",
                "policy_id": TILE2_POLICY_ID,
                "collection_mode": "LAB",
                "policy_entered": True,
                "filled": True,
                "net_pnl_usd": 3.0,
            },
            {
                "trade_id": "tile2-old-policy",
                "research_lane": "SR_MICRO_TILE_V2_STATIC",
                "policy_id": "old-policy",
                "collection_mode": "LAB",
                "policy_entered": True,
                "filled": True,
                "net_pnl_usd": 50.0,
            },
        ]
    )

    type_b = analyzer._policy_filtered_research_lane_metrics(
        rows, "TYPE_B_HUNTER_V1"
    )
    tile2 = analyzer._policy_filtered_research_lane_metrics(
        rows, "SR_MICRO_TILE_V2_STATIC"
    )
    direction_only = analyzer._enrich_trades_with_buckets(pd.DataFrame([{
        "trade_id": "direction-only",
        "research_lane": "CONTINUOUS",
        "dir": "LONG",
        "ai_win_prob": 0,
        "conviction_spread": 6,
        "adx_at_entry": 28.4,
        "entry_mode": "AI_DIRECT_LIMIT",
        "limit_chase_count": 2,
    }])).iloc[0]
    mixed_confidence = analyzer._direction_only_trade_cohort(pd.DataFrame([
        {
            "trade_id": "direction-only", "research_lane": "CONTINUOUS", "dir": "LONG",
            "ai_win_prob": 0, "confidence_requested": False, "net_pnl_usd": 1.0,
        },
        {
            "trade_id": "probability-era", "research_lane": "CONTINUOUS", "dir": "LONG",
            "ai_win_prob": 62, "confidence_requested": True, "net_pnl_usd": 1.0,
        },
    ]))
    original_specs_file = analyzer.PATHWAY_LANE_SPECS_FILE
    materialized_stats = {}
    try:
        with tempfile.TemporaryDirectory() as td:
            analyzer.PATHWAY_LANE_SPECS_FILE = os.path.join(
                td, "pathway_lane_specs.json"
            )
            analyzer.pathway_lane_specs_report(
                trades=pd.DataFrame(),
                session={},
                benchmark_report={
                    "lanes": {
                        "CONTINUOUS": {
                            "approves": 10,
                            "real_fills": 4,
                            "approve_to_fill_pct": 40.0,
                            "net_pnl_real": 2.0,
                            "per_approve_ev": 0.2,
                            "wins": 3,
                            "losses": 1,
                            "win_rate_pct": 75.0,
                            "lab_mode": True,
                            "lab_closes": 6,
                            "lab_net_pnl": -1.5,
                            "lab_wins": 2,
                            "lab_losses": 4,
                            "lab_win_rate": 33.3,
                            "lab_per_close_ev": -0.25,
                            "lab_pnl_source": "lane_lab_pnl_ledger",
                        },
                        "TYPE_B_HUNTER_V1": {},
                    }
                },
                shadow_report={},
            )
            with open(analyzer.PATHWAY_LANE_SPECS_FILE, encoding="utf-8") as f:
                materialized = json.load(f)
            materialized_stats = next(
                row["session_stats"]
                for row in materialized["lanes"]
                if row["lane"] == "CONTINUOUS"
            )
    finally:
        analyzer.PATHWAY_LANE_SPECS_FILE = original_specs_file

    checks = {
        "Type B accepted LAB close": type_b["lab_closes"] == 1,
        "Type B LAB PnL": type_b["lab_net_pnl"] == 2.5,
        "Type B rejected control separated": type_b["counterfactual_closes"] == 1,
        "Type B counterfactual PnL": type_b["counterfactual_pnl_usd"] == -1.25,
        "Tile 2 current cohort only": tile2["lab_closes"] == 1,
        "Tile 2 LAB PnL": tile2["lab_net_pnl"] == 3.0,
        "Direction-only output is not fake confidence": (
            direction_only["ai_probability_bucket"] == "DIRECTION_ONLY"
        ),
        "Shared score gap survives missing bull/bear fields": (
            float(direction_only["directional_spread"]) == 6.0
        ),
        "ADX cohort is retained": direction_only["adx_bucket"] not in ("unknown", ""),
        "Actual chase path is retained": direction_only["entry_mode_bucket"] == "CHASE_1_2",
        "Probability-era rows are excluded from direction-only combinations": (
            mixed_confidence["trade_id"].tolist() == ["direction-only"]
        ),
        "Benchmark approvals use crash-safe lane verdict truth": (
            "shared_verdict_metrics.get(\"accepted\")"
            in inspect.getsource(analyzer.benchmark_vs_lanes_report)
            and "type_b_research_v2_lane_verdict"
            in inspect.getsource(analyzer.benchmark_vs_lanes_report)
        ),
        "Real EV excludes counterfactual PnL": (
            "per_approve_ev = round(net_pnl_real / approves_n"
            in inspect.getsource(analyzer.benchmark_vs_lanes_report)
            and "counterfactual_ev_per_approve"
            in inspect.getsource(analyzer.benchmark_vs_lanes_report)
        ),
        "Compact lane report keeps real win rate": (
            materialized_stats.get("win_rate_pct") == 75.0
            and materialized_stats.get("wins") == 3
            and materialized_stats.get("losses") == 1
        ),
        "Compact lane report keeps separate counterfactual ledger": (
            materialized_stats.get("lab_closes") == 6
            and materialized_stats.get("lab_net_pnl") == -1.5
            and materialized_stats.get("lab_per_close_ev") == -0.25
            and materialized_stats.get("lab_pnl_source")
            == "lane_lab_pnl_ledger"
        ),
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        for name in failed:
            print(f"[FAIL] {name}")
        print({"type_b": type_b, "tile2": tile2})
        return 1
    print(f"[PASS] {len(checks)} policy-filtered analyzer checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
