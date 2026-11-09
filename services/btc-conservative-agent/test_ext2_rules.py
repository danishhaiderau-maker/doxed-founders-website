"""Frozen HAC / BH / RC / DSR / coverage / latency / liquidation-fill rules (push 3)."""
import math

import numpy as np
import pytest

from research import ext2_rules as rules

AR_SERIES = np.array([
    -0.001931, -1.005518, -0.531672, 0.421442, 1.708912, 1.455053, 0.640385, -0.08055, 1.020416,
    2.567033, 2.132988, 0.366464, -0.418387, 1.668987, 1.524275, -0.49757, -0.062238, -0.880569,
    -0.837629, -0.670583, -0.795663, 0.39598, 0.494502, 0.02727, 0.746, 1.597455, -0.36455,
    -0.15546, -0.754024, -0.305569,
])
# Independent reference (scipy.stats.t.sf with an explicit Bartlett HAC, lag 3, df 29).
AR_SERIES_T = 1.3509603278295945
AR_SERIES_P = 0.0935776127678215


def _manual_hac_t(values, lag):
    x = np.asarray(values, dtype=float)
    c = x - x.mean()
    n = x.size
    v = c @ c / n
    for ell in range(1, lag + 1):
        v += 2.0 * (1.0 - ell / (lag + 1.0)) * (c[ell:] @ c[:-ell] / n)
    return x.mean() / math.sqrt(v / n)


def test_newey_west_lag_values_at_the_cards_T_kill_lag_mutants():  # M13, M14
    assert rules.NEWEY_WEST_LAG_RULE == "floor(4*(T/100)**(2/9))"
    expected = {20: 2, 25: 2, 30: 3, 43: 3, 100: 4, 1: 0}
    for t_obs, lag in expected.items():
        assert rules.newey_west_lag(t_obs) == lag, t_obs
    stat = rules.hac_statistic(AR_SERIES)
    assert stat["lag"] == 3 and stat["T"] == 30 and stat["df"] == 29
    assert stat["t"] == pytest.approx(AR_SERIES_T, rel=1e-6)
    assert stat["t"] == pytest.approx(_manual_hac_t(AR_SERIES, 3), rel=1e-12)
    assert stat["t"] != pytest.approx(_manual_hac_t(AR_SERIES, 6), rel=1e-6)
    assert stat["t"] != pytest.approx(_manual_hac_t(AR_SERIES, 0), rel=1e-6)


def test_primary_p_uses_student_t_with_df_T_minus_1():  # M15
    assert rules.student_t_sf(2.0, 19) == pytest.approx(0.03000101819304917, rel=1e-8)
    assert rules.student_t_sf(3.0, 42) == pytest.approx(0.00226312306783345, rel=1e-8)
    assert rules.student_t_sf(7.0, 19) == pytest.approx(5.735777818228021e-07, rel=1e-6)
    assert rules.hac_onesided_t_pvalue(AR_SERIES) == pytest.approx(AR_SERIES_P, rel=1e-8)
    assert rules.student_t_sf(1.5, 5) > 0.5 * math.erfc(1.5 / math.sqrt(2.0))


def test_zero_variance_and_short_series_are_untested_with_p_one():  # M16
    assert rules.T_MIN_TEST_DAYS == 20
    constant = np.full(30, 2.5)
    stat = rules.hac_statistic(constant)
    assert stat["testable"] is False and stat["reason"] == "ZERO_VARIANCE"
    assert rules.hac_onesided_t_pvalue(constant) == 1.0
    assert rules.hac_onesided_t_pvalue(np.full(30, 1e9)) == 1.0
    # sd > 0 but below the relative floor: still untested, never a near-zero p.
    tiny = 1.0 + np.array([1e-14, -1e-14] * 15)
    assert rules.hac_statistic(tiny)["reason"] == "ZERO_VARIANCE"
    assert rules.hac_onesided_t_pvalue(tiny) == 1.0
    assert rules.primary_p_from_stat({"testable": False}) == 1.0
    assert rules.UNTESTED_P == 1.0
    assert rules.hac_onesided_t_pvalue(np.zeros(25)) == 1.0
    short = AR_SERIES[:19] + 5.0
    assert rules.hac_statistic(short)["reason"] == "T_BELOW_MIN"
    assert rules.hac_onesided_t_pvalue(short) == 1.0
    assert rules.hac_onesided_t_pvalue(AR_SERIES[:20] + 5.0) < 1e-6
    bad = AR_SERIES.copy()
    bad[3] = np.nan
    assert rules.hac_onesided_t_pvalue(bad) == 1.0


def test_p_min_attainable_is_computed_from_the_t_tail_per_T():
    assert not hasattr(rules, "PRIMARY_P_MIN_ATTAINABLE")
    table = rules.assert_primary_p_can_reject()
    assert set(table["by_T"]) == {str(t) for t in range(20, 44)}
    q_over_n = 0.05 / 181442
    assert table["bh_threshold"] == pytest.approx(q_over_n)
    for t_obs, row in table["by_T"].items():
        crit = row["critical_t"]
        assert row["reachable"] is True
        assert rules.student_t_sf(crit, int(t_obs) - 1) <= q_over_n
        assert rules.student_t_sf(crit * 0.999, int(t_obs) - 1) > q_over_n
    assert table["by_T"]["20"]["critical_t"] == pytest.approx(7.373, abs=2e-3)
    assert table["by_T"]["43"]["critical_t"] == pytest.approx(5.901, abs=2e-3)
    fields = rules.rule_receipt_fields()
    assert "gpu_dtype" not in fields
    assert fields["p_min_attainable_computed"]["by_T"]["30"]["critical_t"] == table["by_T"]["30"]["critical_t"]
    assert fields["fee_bp"] == 0
    assert fields["label"].startswith("LIMITED SCOPE / NOT A VERDICT")


def test_bh_bootstrap_and_role_flags():
    assert rules.bootstrap_p_value(0, 2000) == pytest.approx(1 / 2001)
    p = np.ones(181442)
    p[:3] = [1e-9, 2e-7, 9e-7]  # rank-3 BH line is 3q/N = 8.27e-7
    rej = rules.bh_reject(p)
    assert rej[:2].all() and not rej[2] and rej.sum() == 2
    fields = rules.rule_receipt_fields()
    assert fields["white_rc_role"] == "hard_gate_7"
    assert fields["bootstrap_role"] == "reported_only"
    assert rules.lead_neff_ok(29.9) is False and rules.lead_neff_ok(30) is True
    assert rules.white_rc_passes(0.05) is True and rules.white_rc_passes(0.051) is False
    assert rules.white_rc_gate(np.array([0.05, 0.0500001, 0.01])).tolist() == [True, False, True]


def test_white_rc_flags_a_real_edge_and_not_noise():
    rng = np.random.default_rng(1)
    panel = rng.standard_normal((300, 43))
    out = rules.white_rc_pvalues(panel, np.ones(300, dtype=bool), draws=500)
    assert out["rc_p_best"] > 0.05
    panel[7] += 3.0
    out = rules.white_rc_pvalues(panel, np.ones(300, dtype=bool), draws=500)
    assert out["best_cell"] == 7 and out["rc_p_best"] <= 1 / 501 + 1e-12
    untested = np.ones(300, dtype=bool)
    untested[7] = False
    assert rules.white_rc_pvalues(panel, untested, draws=200)["p"][7] == 1.0


def test_dsr_and_sr0_grow_with_trials():
    assert rules.expected_max_sharpe(0.01, 181442) > rules.expected_max_sharpe(0.01, 100)
    rng = np.random.default_rng(2)
    good = rng.standard_normal(43) * 0.1 + 1.0
    assert rules.deflated_sharpe(good, 0.0) > 0.99
    assert rules.deflated_sharpe(rng.standard_normal(43), 0.5) < 0.5


def test_95_share_rule_is_nan_aware():
    assert rules.PERCENTILE_DEFINED_SHARE == 0.95
    full = np.ones(rules.PERCENTILE_SPAN_BARS)
    full[: int(0.04 * rules.PERCENTILE_SPAN_BARS)] = np.nan
    assert rules.percentile_is_defined(full) is True
    thin = np.ones(rules.PERCENTILE_SPAN_BARS)
    thin[: int(0.06 * rules.PERCENTILE_SPAN_BARS)] = np.nan
    assert rules.percentile_is_defined(thin) is False
    assert rules.percentile_is_defined(np.ones(rules.PERCENTILE_SPAN_BARS - 1)) is False


def test_dry_check_counts_per_member_without_overcount():  # N4
    eligible = {"a": {240: np.array([False, False, True, True])}, "b": {240: np.array([False, True, True, False])}}
    folds = {"F1": np.array([True, False, True, True])}
    shares = {"a": {240: np.array([np.nan, 0.9, 0.96, 1.0])}, "b": {240: np.array([0.5, 0.97, 0.99, 0.2])}}
    rep = rules.dry_check_coverage(eligible, folds, shares)
    assert rep["reads_pnl"] is False and rep["reads_signals"] is False
    assert rep["per_member"]["a"]["240"]["first_eligible_bar"] == 2
    assert rep["per_member"]["a"]["240"]["by_fold"]["F1"]["eligible_share"] == pytest.approx(2 / 3)
    assert rep["per_member"]["a+b"]["240"]["by_fold"]["F1"]["eligible_bars"] == 1
    assert rep["per_member"]["a"]["240"]["min_window_defined_share_on_scored_bars"] == pytest.approx(0.96)
    assert rep["structurally_untestable_cells"] == 0
    dead = {"a": {240: np.zeros(4, dtype=bool)}, "b": {240: np.ones(4, dtype=bool)}}
    rep = rules.dry_check_coverage(dead, folds)
    # member a and pair a+b are untestable: 2 x (5 p x 2 M x 4 G x 54) -- not x4 per percentile.
    assert rep["structurally_untestable_cells"] == 2 * 2160


def test_liquidation_fill_is_the_worse_of_level_and_print():
    level = 100.0 * (1.0 - 50.0 / 10000.0)
    assert rules.liquidation_fill_price(100.0, 1.0, 99.0) == 99.0
    assert rules.liquidation_fill_price(100.0, 1.0, 99.8) == pytest.approx(level)
    assert rules.liquidation_fill_price(100.0, -1.0, 101.0) == 101.0
    assert "worse" in rules.LIQUIDATION_DISCLOSURE.lower()


def test_entries_past_the_latency_cap_are_not_trades():
    assert rules.ENTRY_LATENCY_CAP_MS == 60_000 and rules.ENTRY_DELAY_MS == 1_000
    assert rules.entry_latency_accepted(0, 1_000) is True
    assert rules.entry_latency_accepted(0, 61_000) is True
    assert rules.entry_latency_accepted(0, 61_001) is False


def test_projection_has_four_parts_and_scales_non_fp64_lattice_by_32():  # N11
    assert rules.dual_c2_projection_hours(3600.0, 1800.0, 1800.0) == pytest.approx(2.0)
    assert rules.dual_c2_projection_hours(3600.0, 0, 0, 3600.0) == pytest.approx(2.0)
    assert rules.dual_c2_projection_hours(36.0, 0, 0, 0, lattice_measured_fp64_on_device=False) == \
        pytest.approx(36.0 * 32 / 3600.0)
    assert rules.projection_needs_new_card(2.01) is True
    assert rules.rule_receipt_fields()["dual_c2_projection_parts"] == [
        "exit_lattice", "rolling_quantiles", "white_rc_panel", "cell_chains"]


def test_c2_disclosure_is_the_full_text():
    assert len(rules.C2_STUDY1_DISCLOSURE) > 80
    assert "R24 NOT TESTED" in rules.LABEL
