"""
Scenario C profit-lock ladder contract tests.

Locks in the 2026-08-13 operator decision: both lanes start with the early
profit-lock rungs (4, 2) and (5, 3), ahead of the existing (8, 5) rung.
"""
from scenario_c_config import (
    TRAIL_LADDER_SCENARIO_C,
    TRAIL_LADDER_SCENARIO_C_LEGACY_10_6,
    SCENARIO_C_LADDER_LABEL,
    SCENARIO_C_LEGACY_10_6_LADDER_LABEL,
)


def test_continuous_starts_with_early_profit_lock_rungs():
    assert TRAIL_LADDER_SCENARIO_C[:3] == [(4, 2), (5, 3), (8, 5)]


def test_type_b_starts_with_early_profit_lock_rungs():
    assert TRAIL_LADDER_SCENARIO_C_LEGACY_10_6[:3] == [(4, 2), (5, 3), (8, 5)]


def test_labels_show_8_5_first():
    assert "8→5" in SCENARIO_C_LADDER_LABEL
    assert "8→5" in SCENARIO_C_LEGACY_10_6_LADDER_LABEL


def test_ladders_remain_monotonically_increasing():
    # All ladder rungs should be sorted ascending by trigger (first element)
    for ladder in [TRAIL_LADDER_SCENARIO_C, TRAIL_LADDER_SCENARIO_C_LEGACY_10_6]:
        triggers = [rung[0] for rung in ladder]
        assert triggers == sorted(triggers), f"Ladder not sorted: {ladder}"
