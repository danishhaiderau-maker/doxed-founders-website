"""Exit lattice, censor list, liquidation-before-stop, and undefined trailing stats."""
import inspect
import math

import numpy as np
import pytest

from research import ext2_search as search


def _event(prices, mts, *, entry=100.0, entry_mts=0, side=1.0, thesis=None):
    return {
        "entry_price": entry,
        "entry_mts": entry_mts,
        "side": side,
        "print_mts": list(mts),
        "print_price": list(prices),
        "thesis_cross": [False] * len(prices) if thesis is None else list(thesis),
    }


def test_closed_form_and_enumeration_are_181442():
    assert search.count_combos_closed_form() == 181442
    assert search.count_combos_enumerated() == 181442
    assert search.count_combos() == 181442
    assert search.EXIT_GRID_N == 54
    assert len(search._exit_table()[0]) == 54


def test_cpu_fallback_is_off_by_default_and_stops_without_a_gpu():
    flag = inspect.signature(search.evaluate_exit_lattice).parameters["allow_cpu_fallback"]
    assert flag.default is False
    with pytest.raises(search.Ext2Stop, match="STOP_GPU_MISSING"):
        search.evaluate_exit_lattice([_event([100.0], [1_000])])


def test_numpy_and_torch_kernels_match_within_1e_9_bp():
    torch = pytest.importorskip("torch")
    assert torch.cuda.is_available() is False
    event = _event([100.1, 100.4, 99.0, 101.0], [2_000, 80_000, 200_000, 400_000], thesis=[False, False, True, False])
    boundaries = [10 ** 15]
    numpy_result = search.evaluate_exit_lattice(
        [event], allow_cpu_fallback=True, kernel="numpy", censor_boundaries=boundaries,
    )
    torch_result = search.evaluate_exit_lattice(
        [event], allow_cpu_fallback=True, kernel="torch", censor_boundaries=boundaries,
    )
    assert numpy_result["combos_searched"] == torch_result["combos_searched"] == 181442
    assert numpy_result["fallback"] is True and numpy_result["parity_test"] is True
    assert numpy_result["cells"][0]["reasons"] == torch_result["cells"][0]["reasons"]
    assert numpy_result["liquidation_count"] == torch_result["liquidation_count"]
    for left, right in zip(numpy_result["cells"][0]["net_bp"], torch_result["cells"][0]["net_bp"]):
        if left is None:
            assert right is None
        else:
            assert right == pytest.approx(left, abs=search.PARITY_ATOL)


def test_liquidation_gap_is_booked_from_the_print_before_the_stop_and_stays_in_the_fold():
    boundaries = [10 ** 15]
    result = search.evaluate_exit_lattice(
        [_event([99.0], [1_000])], allow_cpu_fallback=True, kernel="numpy", censor_boundaries=boundaries,
    )
    cell = result["cells"][0]
    assert cell["reasons"] == ["LIQUIDATION"] * 54
    assert cell["exit_price"] == [99.0] * 54
    assert cell["exit_price"][0] != pytest.approx(99.5)
    assert cell["exit_price"][0] != pytest.approx(99.6)
    assert cell["net_bp"][0] == pytest.approx(-102.0)
    assert result["liquidation_count"] == 54
    assert result["costs"]["fee_bp"] == 0.0
    assert result["costs"]["funding"] == "set_aside"
    fold = search.fold_gate_inputs(
        [{"reason": "LIQUIDATION", "net_bp": cell["net_bp"][0]}, {"reason": "TAKE_PROFIT", "net_bp": 10.0}]
    )
    assert fold["liquidations_excluded"] is False
    assert fold["liquidation_count"] == 1
    assert fold["trades"] == 2
    assert fold["net_bp"] == pytest.approx(-92.0)
    assert fold["passes_net_gate"] is False


def test_a_wick_inside_the_bar_liquidates_even_when_the_later_print_recovers():
    boundaries = [10 ** 15]
    result = search.evaluate_exit_lattice(
        [_event([99.0, 100.0], [1_000, 2_000])],
        allow_cpu_fallback=True, kernel="numpy", censor_boundaries=boundaries,
    )
    assert result["cells"][0]["exit_price"] == [99.0] * 54
    assert result["cells"][0]["reasons"] == ["LIQUIDATION"] * 54
    assert result["liquidation_count"] == 54


def test_a_mild_print_hits_the_stop_and_not_liquidation():
    boundaries = [10 ** 15]
    result = search.evaluate_exit_lattice(
        [_event([99.7], [1_000])], allow_cpu_fallback=True, kernel="numpy", censor_boundaries=boundaries,
    )
    reasons = result["cells"][0]["reasons"]
    assert "LIQUIDATION" not in reasons
    assert reasons.count("HARD_STOP") == 36
    assert result["liquidation_count"] == 0


def test_censor_boundaries_are_per_fold_end_and_later_prints_are_not_used():
    assert search.FOLD_CENSOR_ISO == (
        "2024-12-28T00:00:00Z",
        "2025-01-08T00:00:00Z",
        "2025-01-19T00:00:00Z",
        "2025-01-29T00:00:00Z",
    )
    boundaries = search.default_censor_boundaries_ms()
    assert len(boundaries) == 4
    assert search.active_censor_mts(search.utc_ms("2024-12-27T23:00:00Z"), boundaries) == search.utc_ms("2024-12-28T00:00:00Z")
    assert search.active_censor_mts(search.utc_ms("2024-12-28T00:00:00Z"), boundaries) == search.utc_ms("2025-01-08T00:00:00Z")
    assert search.active_censor_mts(search.utc_ms("2025-01-19T00:00:00Z"), boundaries) == search.utc_ms("2025-01-29T00:00:00Z")
    custom = [3_000, 9_000]
    early = _event([100.2, 1.0, 1.0], [1_000, 4_000, 9_500], entry_mts=0)
    later = _event([100.2, 1.0, 50.0], [1_000, 4_000, 9_500], entry_mts=3_500)
    result = search.evaluate_exit_lattice(
        [early, later], allow_cpu_fallback=True, kernel="numpy", censor_boundaries=custom, embargo_mode="force_close",
    )
    assert result["cells"][0]["print_mts_read_max"] == 1_000
    assert 1.0 not in result["cells"][0]["exit_price"]
    assert result["cells"][1]["print_mts_read_max"] == 4_000
    assert result["cells"][1]["boundary_mts"] == 9_000
    assert 50.0 not in result["cells"][1]["exit_price"]
    kept_mts, _, _ = search.censor_prints(
        np.array([1_000, 3_000, 4_000]), np.array([1.0, 2.0, 3.0]), np.array([False, False, False]),
        entry_mts=0, boundary=3_000,
    )
    assert kept_mts.tolist() == [1_000]


def test_drop_mode_counts_the_open_trade_and_does_not_use_the_post_boundary_print():
    result = search.evaluate_exit_lattice(
        [_event([100.05, 50.0], [1_000, 5_000])],
        allow_cpu_fallback=True, kernel="numpy", censor_boundaries=[3_000], embargo_mode="drop",
    )
    assert result["dropped_count"] == 54
    assert result["liquidation_count"] == 0
    assert result["cells"][0]["net_bp"] == [None] * 54
    assert result["cells"][0]["print_mts_read_max"] == 1_000


def test_undefined_trailing_stats_take_no_entry_and_the_lookback_is_not_shortened():
    closes = [100.0] * 301
    closes[100] = 200.0
    closes[240] = 110.0
    assert search.feature_log_ret_bp(closes, 100, 240) is None
    assert search.feature_log_ret_bp(closes, 240, 240) == pytest.approx(math.log(110.0 / 100.0) * 10000.0)
    screened = search.screen_entries(closes)
    assert screened["bars"] == 301
    assert screened["entries"] == 0
    assert screened["undefined_stat_bars"] == 301
    assert search.history_undefined(239) is True
    assert search.history_undefined(240) is True
    assert search.first_eligible_bar() == 240 + 30 * 1440
    assert search.history_undefined(search.first_eligible_bar() - 1) is True
    assert search.history_undefined(search.first_eligible_bar()) is False
    assert search.undefined_stat_bar_count(100) == 100
    assert search.undefined_stat_bar_count(search.first_eligible_bar() + 5) == search.first_eligible_bar()
    required_hours = 29 * 24
    assert search.trailing_sigma([0.1] * (required_hours - 1), required_hours) is None
    assert search.trailing_sigma([0.0] * required_hours, required_hours) == pytest.approx(0.0)
    need = 30 * 1440
    assert search.trailing_percentile([1.0] * (need - 1), need, 90.0) is None
    assert search.trailing_percentile(list(range(need)), need, 90.0) is not None
    assert search.bar_takes_entry(feature_bp=1.0, sigma=None, percentile=1.0) is False
    assert search.bar_takes_entry(feature_bp=None, sigma=1.0, percentile=1.0) is False


def test_bot_does_not_reference_the_screen():
    from pathlib import Path
    text = (Path(__file__).parent / "bot.py").read_text(encoding="utf-8", errors="ignore")
    assert "ext2_search" not in text
    assert "ext2_loader" not in text
    assert "ext2_runner" not in text
