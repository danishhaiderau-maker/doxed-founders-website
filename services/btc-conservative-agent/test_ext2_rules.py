"""Frozen HAC, coverage, latency, and liquidation-fill rules."""
import math

import numpy as np
import pytest

from research import ext2_rules as rules
from research.ext2_search import evaluate_exit_lattice


def test_newey_west_lag_and_student_t_are_the_primary_p():
    assert rules.NEWEY_WEST_LAG_RULE == "floor(4*(T/100)**(2/9))"
    assert rules.newey_west_lag(100) == 4
    assert rules.newey_west_lag(1) == 0
    daily = np.array([1.0, 1.2, 0.8, 1.1, 0.9, 1.3, 1.0, 1.1])
    p_value = rules.hac_onesided_t_pvalue(daily)
    assert 0.0 <= p_value < 0.05
    flat = np.zeros(8)
    assert rules.hac_onesided_t_pvalue(flat) == 1.0
    rules.assert_primary_p_can_reject()
    assert rules.PRIMARY_P_MIN_ATTAINABLE < rules.bh_threshold()
    assert rules.bh_threshold() == pytest.approx(0.05 / 181442)
    assert rules.bootstrap_p_value(0, 2000) == pytest.approx(1 / 2001)
    assert rules.bootstrap_p_value(0, 2000) != 0.0
    fields = rules.rule_receipt_fields()
    assert fields["hac_parametric"] is True
    assert fields["p_distribution"] == "student_t_df_T_minus_1"
    assert fields["white_rc_role"] == "hard_gate_7"
    assert fields["bootstrap_role"] == "reported_only"
    assert fields["neff_lead_floor_hard"] is True
    assert rules.lead_neff_ok(29.9) is False
    assert rules.lead_neff_ok(30) is True
    assert rules.white_rc_passes(0.05) is True
    assert rules.white_rc_passes(0.051) is False


def test_percentile_span_is_never_shortened_and_dry_check_is_coverage_only():
    assert rules.PERCENTILE_DEFINED_SHARE == 0.95
    full = np.ones(rules.PERCENTILE_SPAN_BARS)
    full[: int(0.04 * rules.PERCENTILE_SPAN_BARS)] = np.nan
    assert rules.percentile_is_defined(full) is True
    thin = np.ones(rules.PERCENTILE_SPAN_BARS)
    thin[: int(0.10 * rules.PERCENTILE_SPAN_BARS)] = np.nan
    assert rules.percentile_is_defined(thin) is False
    assert rules.percentile_is_defined(np.ones(rules.PERCENTILE_SPAN_BARS - 1)) is False
    defined = {"ret_L": {240: np.array([False, False, True, True])}}
    folds = {"F1": np.array([True, False, True, True])}
    windows = {"ret_L": {240: full}}
    report = rules.dry_check_coverage(defined, folds, windows)
    assert report["reads_pnl"] is False and report["reads_signals"] is False
    assert report["first_defined_bar"]["ret_L"]["240"] == 2
    assert report["fold_eligible_share"]["F1"] == pytest.approx(0.75)
    assert report["min_defined_share_by_percentile"]["95"] == pytest.approx(rules.defined_share(full))
    assert report["structurally_untestable_cells"] == 0
    empty = {"ret_L": {240: np.array([False, False])}}
    untestable = rules.dry_check_coverage(empty, folds, {"ret_L": {240: thin}})
    assert untestable["structurally_untestable_cells"] == 4 * 5 * 2 * 4 * 54


def test_liquidation_fill_is_the_worse_of_level_and_print():
    level = 100.0 * (1.0 - 50.0 / 10000.0)
    assert rules.liquidation_fill_price(100.0, 1.0, 99.0) == 99.0
    assert rules.liquidation_fill_price(100.0, 1.0, 99.0) < level
    assert rules.liquidation_fill_price(100.0, 1.0, 99.8) == pytest.approx(level)
    assert rules.liquidation_fill_price(100.0, -1.0, 101.0) == 101.0


def test_entries_past_the_latency_cap_are_not_trades():
    assert rules.ENTRY_LATENCY_CAP_MS == 60_000
    assert rules.entry_latency_accepted(0, 1_000) is True
    assert rules.entry_latency_accepted(0, 1_000 + 60_000) is True
    assert rules.entry_latency_accepted(0, 1_000 + 60_001) is False
    report = rules.latency_distribution([10, 60_000, 60_001, -1])
    assert report["n_accepted"] == 2
    assert report["n_rejected_stale"] == 1
    assert report["max_ms"] == 60_000


def test_path_end_count_is_per_cell_on_a_print_that_hits_nothing():
    result = evaluate_exit_lattice(
        [{
            "entry_price": 100.0, "entry_mts": 0, "side": 1.0,
            "print_mts": [1_000], "print_price": [100.01], "thesis_cross": [False],
        }],
        roll_half_spread_bp=0.0, allow_cpu_fallback=True, kernel="numpy",
        censor_boundaries=[10 ** 15],
    )
    assert result["cells"][0]["path_end_count"] == 54
    assert result["path_end_count"] == 54
    assert "PATH_END" in result["cells"][0]["reasons"]


def test_sigma_is_a_trailing_causal_estimate_not_a_fold_fit():
    import inspect
    from research.ext2_features import sigma_hourly_r24
    assert "fold" not in inspect.signature(sigma_hourly_r24).parameters
    close = np.full(100, 100.0)
    assert np.isnan(sigma_hourly_r24(close)).all()


def test_projection_includes_quantiles_and_the_white_panel():
    hours = rules.dual_c2_projection_hours(3600.0, 1800.0, 1800.0)
    assert hours == pytest.approx(2.0)
    assert rules.projection_needs_new_card(hours + 0.01) is True
    assert rules.rule_receipt_fields()["dual_c2_projection_parts"] == [
        "exit_lattice", "rolling_quantiles", "white_rc_panel",
    ]
    assert rules.rule_receipt_fields()["gpu_dtype"] == "float64"


def test_student_t_tail_is_heavier_than_a_normal_at_the_same_statistic():
    stat = 1.5
    t_tail = rules.student_t_sf(stat, 5)
    normal_tail = 0.5 * math.erfc(stat / math.sqrt(2.0))
    assert t_tail > normal_tail
