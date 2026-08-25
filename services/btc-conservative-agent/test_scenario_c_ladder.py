"""
Scenario C profit-lock ladder contract tests.

Locks in the 2026-08-20 operator decision: revert to the mature-trend ladder
starting at (8, 5) — no early (4, 2) / (5, 3) rungs.
"""
from scenario_c_config import (
    TRAIL_LADDER_SCENARIO_C,
    TRAIL_LADDER_SCENARIO_C_LEGACY_10_6,
    SCENARIO_C_LADDER_LABEL,
    SCENARIO_C_LEGACY_10_6_LADDER_LABEL,
)

EXPECTED_LADDER = [
    (8, 5), (12, 10), (19, 17), (40, 28),
    (60, 45), (80, 60), (100, 75), (150, 120),
]


def test_continuous_starts_at_eight_five_rung():
    assert TRAIL_LADDER_SCENARIO_C == EXPECTED_LADDER
    assert TRAIL_LADDER_SCENARIO_C[0] == (8, 5)
    assert (4, 2) not in TRAIL_LADDER_SCENARIO_C
    assert (5, 3) not in TRAIL_LADDER_SCENARIO_C


def test_legacy_generic_ladder_starts_at_eight_five_rung():
    assert TRAIL_LADDER_SCENARIO_C_LEGACY_10_6 == EXPECTED_LADDER
    assert TRAIL_LADDER_SCENARIO_C_LEGACY_10_6[0] == (8, 5)
    assert (4, 2) not in TRAIL_LADDER_SCENARIO_C_LEGACY_10_6
    assert (5, 3) not in TRAIL_LADDER_SCENARIO_C_LEGACY_10_6


def test_labels_show_8_5_first():
    assert "8→5" in SCENARIO_C_LADDER_LABEL
    assert "8→5" in SCENARIO_C_LEGACY_10_6_LADDER_LABEL
    assert "4→2" not in SCENARIO_C_LADDER_LABEL


def test_ladders_remain_monotonically_increasing():
    # All ladder rungs should be sorted ascending by trigger (first element)
    for ladder in [TRAIL_LADDER_SCENARIO_C, TRAIL_LADDER_SCENARIO_C_LEGACY_10_6]:
        triggers = [rung[0] for rung in ladder]
        assert triggers == sorted(triggers), f"Ladder not sorted: {ladder}"
