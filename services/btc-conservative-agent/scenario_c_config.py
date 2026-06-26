"""
Scenario C exit profile — single source for profit-lock ladder (bot + analyzer).

Updated 2026-06-25: wider first rung (10→6) to reduce runner cuts vs legacy 12→8.
All Scenario C tiles (combo, CONTINUOUS, experimental except Recovery Monster) use this file.
"""
from __future__ import annotations

TRAIL_LADDER_SCENARIO_C = [
    (10, 6),
    (19, 17),
    (40, 28),
    (60, 45),
    (80, 60),
    (100, 75),
    (150, 120),
]

SCENARIO_C_PROFILE_ID = "SCENARIO_C_RUNNER_10_v4"
SCENARIO_C_LADDER_LABEL = "10→6, 19→17, 40→28, 60→45, 80→60, 100→75, 150→120"
