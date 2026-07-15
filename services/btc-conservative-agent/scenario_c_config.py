"""
Scenario C exit profile — single source for profit-lock ladder (bot + analyzer).

Updated 2026-07-15: tighter first rung lock (10→6 promoted to 12→10) for the
GLOBAL / CONTINUOUS benchmark ladder. Fresh 300-trade data showed the
PROFIT_LOCK_LADDER winners (avg MFE 17.16%) left ~$255 on the table at 57%
peak capture; raising the first lock from 6% to 10% recovers more of that MFE
while still triggering inside most winners' MFE.

NOTE — the v2a backtest specifically found that tightening the first rung made
TYPE_B_HUNTER_V1 *worse* (-$22.20 vs v2a baseline). TYPE_B_HUNTER_V1 therefore
keeps the legacy 10→6 first rung via `TRAIL_LADDER_SCENARIO_C_LEGACY_10_6` and
`get_lane_ladder_override` in combo_pathway_config.py.

All Scenario C tiles (combo, CONTINUOUS, experimental except Recovery Monster) use this file
as the GLOBAL default. Per-lane ladder overrides (e.g. TYPE_B_HUNTER_V1's legacy
10→6 profile) are applied via `get_lane_ladder()` in bot.py — lanes without an
override fall back to `TRAIL_LADDER_SCENARIO_C` below.
"""
from __future__ import annotations

TRAIL_LADDER_SCENARIO_C = [
    (12, 10),
    (19, 17),
    (40, 28),
    (60, 45),
    (80, 60),
    (100, 75),
    (150, 120),
]

# Legacy ladder retained for TYPE_B_HUNTER_V1 only — v2a backtest showed tighter
# first rungs (12→10) made Type B Hunter worse; it stays on the original 10→6.
TRAIL_LADDER_SCENARIO_C_LEGACY_10_6 = [
    (10, 6),
    (19, 17),
    (40, 28),
    (60, 45),
    (80, 60),
    (100, 75),
    (150, 120),
]

SCENARIO_C_PROFILE_ID = "SCENARIO_C_RUNNER_12_v5_20260715"
SCENARIO_C_LADDER_LABEL = "12→10, 19→17, 40→28, 60→45, 80→60, 100→75, 150→120"
SCENARIO_C_LEGACY_10_6_PROFILE_ID = "SCENARIO_C_RUNNER_10_v4_LEGACY_TYPE_B"
SCENARIO_C_LEGACY_10_6_LADDER_LABEL = "10→6, 19→17, 40→28, 60→45, 80→60, 100→75, 150→120 (TYPE_B_HUNTER_V1 legacy)"
