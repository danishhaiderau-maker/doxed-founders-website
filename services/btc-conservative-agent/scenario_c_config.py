"""
Scenario C exit profile — single source for profit-lock ladder (bot + analyzer).

Updated 2026-08-06 (Danish decision): add (8, 5) as the new first rung of BOTH
ladders. The 61-trade history showed 4 winners peaking in the 0-10% MFE band
that exited via THESIS_INVALIDATED at near-zero profit. A lower first rung locks
in a +5% floor once peak hits +8%, catching those weak winners earlier. This
overrides the prior v2a backtest warning for TYPE_B_HUNTER_V1 (which used 10→6
as its first rung) — the Stage 1 strategy fixes (commit f03640dd) changed the
entry direction logic, which may invalidate that older backtest. Paper soak
will provide fresh data.

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
    (8, 5),       # NEW first rung (2026-08-06) — captures 0-10% MFE winners earlier
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
    (8, 5),       # CHANGED from (10, 6) — Danish decision 2026-08-06
    (19, 17),
    (40, 28),
    (60, 45),
    (80, 60),
    (100, 75),
    (150, 120),
]

SCENARIO_C_PROFILE_ID = "SCENARIO_C_RUNNER_8_v6_20260806"
SCENARIO_C_LADDER_LABEL = "8→5, 12→10, 19→17, 40→28, 60→45, 80→60, 100→75, 150→120"
SCENARIO_C_LEGACY_10_6_PROFILE_ID = "SCENARIO_C_RUNNER_8_v6_TYPE_B_20260806"
SCENARIO_C_LEGACY_10_6_LADDER_LABEL = "8→5, 19→17, 40→28, 60→45, 80→60, 100→75, 150→120 (TYPE_B_HUNTER_V1)"
