"""
Scenario C exit profile — single source for profit-lock ladder (bot + analyzer).

Updated 2026-08-13 (operator decision): add early profit-lock rungs (4, 2)
and (5, 3) ahead of the existing (8, 5) rung. A rung never closes at its peak:
it protects a trade only after that peak has been reached and unrealized margin
profit subsequently retreats to the associated lock floor. This preserves the
existing higher-profit trail while preventing small positive moves from turning
into losses during fast, crowded trading.

Historical context (2026-07-15): tighter first rung lock (10→6 promoted to
12→10) for the GLOBAL / CONTINUOUS benchmark ladder. Fresh 300-trade data
showed the PROFIT_LOCK_LADDER winners (avg MFE 17.16%) left ~$255 on the table
at 57% peak capture; raising the first lock from 6% to 10% recovered more of
that MFE while still triggering inside most winners' MFE.

NOTE — the v2a backtest specifically found that tightening the first rung made
TYPE_B_HUNTER_V1 *worse* (-$22.20 vs v2a baseline). That conclusion is now
considered stale (see 2026-08-06 note above); TYPE_B_HUNTER_V1 adopts the new
(8, 5) first rung via `TRAIL_LADDER_SCENARIO_C_LEGACY_10_6` and
`get_lane_ladder_override` in combo_pathway_config.py.

All Scenario C tiles (combo, CONTINUOUS, experimental except Recovery Monster) use this file
as the GLOBAL default. Per-lane ladder overrides (e.g. TYPE_B_HUNTER_V1's
profile) are applied via `get_lane_ladder()` in bot.py — lanes without an
override fall back to `TRAIL_LADDER_SCENARIO_C` below.
"""
from __future__ import annotations

TRAIL_LADDER_SCENARIO_C = [
    (4, 2),       # early winner: peak +4% then protect +2%
    (5, 3),       # early winner: peak +5% then protect +3%
    (8, 5),       # existing first mature-trend rung
    (12, 10),
    (19, 17),
    (40, 28),
    (60, 45),
    (80, 60),
    (100, 75),
    (150, 120),
]

# Legacy ladder retained for TYPE_B_HUNTER_V1. The constant NAME is kept for
# backwards compatibility (referenced in combo_pathway_config.py:621+ via
# get_lane_ladder_override) — the name is HISTORICAL; the first rung is now
# (8, 5), changed from (10, 6) on 2026-08-06 (Danish decision).
TRAIL_LADDER_SCENARIO_C_LEGACY_10_6 = [
    (4, 2),       # early winner: peak +4% then protect +2%
    (5, 3),       # early winner: peak +5% then protect +3%
    (8, 5),       # CHANGED from (10, 6) — Danish decision 2026-08-06
    (19, 17),
    (40, 28),
    (60, 45),
    (80, 60),
    (100, 75),
    (150, 120),
]

SCENARIO_C_PROFILE_ID = "SCENARIO_C_RUNNER_4_v7_20260813"
SCENARIO_C_LADDER_LABEL = "4→2, 5→3, 8→5, 12→10, 19→17, 40→28, 60→45, 80→60, 100→75, 150→120"
SCENARIO_C_LEGACY_10_6_PROFILE_ID = "SCENARIO_C_RUNNER_4_v7_TYPE_B_20260813"
SCENARIO_C_LEGACY_10_6_LADDER_LABEL = "4→2, 5→3, 8→5, 19→17, 40→28, 60→45, 80→60, 100→75, 150→120 (TYPE_B_HUNTER_V1)"
