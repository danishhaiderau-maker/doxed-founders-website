"""Policy-filtered LAB/counterfactual analyzer contract."""

import os
import sys

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

    checks = {
        "Type B accepted LAB close": type_b["lab_closes"] == 1,
        "Type B LAB PnL": type_b["lab_net_pnl"] == 2.5,
        "Type B rejected control separated": type_b["counterfactual_closes"] == 1,
        "Type B counterfactual PnL": type_b["counterfactual_pnl_usd"] == -1.25,
        "Tile 2 current cohort only": tile2["lab_closes"] == 1,
        "Tile 2 LAB PnL": tile2["lab_net_pnl"] == 3.0,
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
