"""
Scenario C exit profile — single source for profit-lock ladder (bot + analyzer).

Updated 2026-08-20 (operator decision): revert to the mature-trend ladder starting
at (8, 5) — remove the 2026-08-13 early rungs (4, 2) and (5, 3). A rung never
closes at its peak: it protects a trade only after that peak has been reached and
unrealized margin profit subsequently retreats to the associated lock floor.

Historical context (2026-07-15): tighter first rung lock (10→6 promoted to
12→10) for the GLOBAL / CONTINUOUS benchmark ladder. Fresh 300-trade data
showed the PROFIT_LOCK_LADDER winners (avg MFE 17.16%) left ~$255 on the table
at 57% peak capture; raising the first lock from 6% to 10% recovered more of
that MFE while still triggering inside most winners' MFE.

NOTE — a historical backtest of a retired research lane performed worse than
its baseline after tightening the first rung. That conclusion is stale; current
execution uses the operator-approved
(8, 5) first rung via `TRAIL_LADDER_SCENARIO_C_LEGACY_10_6` and
`get_lane_ladder_override` in combo_pathway_config.py.

The current execution lanes use this file as the GLOBAL default. Per-lane
ladder overrides are applied via `get_lane_ladder()` in bot.py — lanes without an
override fall back to `TRAIL_LADDER_SCENARIO_C` below.
"""
from __future__ import annotations

TRAIL_LADDER_SCENARIO_C = [
    (8, 5),
    (12, 10),
    (19, 17),
    (40, 28),
    (60, 45),
    (80, 60),
    (100, 75),
    (150, 120),
]

# Compatibility aliases must not silently create a different live treatment.
# Current lanes use the same operator-approved eight-rung policy unless a future
# treatment has its own explicit signature and qualification record.
TRAIL_LADDER_SCENARIO_C_LEGACY_10_6 = list(TRAIL_LADDER_SCENARIO_C)

SCENARIO_C_PROFILE_ID = "SCENARIO_C_RUNNER_8_v8_20260820"
SCENARIO_C_LADDER_LABEL = "8→5, 12→10, 19→17, 40→28, 60→45, 80→60, 100→75, 150→120"
SCENARIO_C_LEGACY_10_6_PROFILE_ID = SCENARIO_C_PROFILE_ID
SCENARIO_C_LEGACY_10_6_LADDER_LABEL = SCENARIO_C_LADDER_LABEL
