"""Features, trailing percentiles, sigma/regimes, thesis and entry timing (push 3).

Kills mutants M21 (percentile window includes bar t), M22 (sigma includes the
current hour sample) and M23 (0 ms entry delay); covers B1-B3, B8, N2, N3, N5."""
from datetime import date

import numpy as np
import pytest

from ext2_test_data import small_calendar, synth_trades
from research import ext2_features as F
from research import ext2_loader as L
from research.ext2_calendar import day_ms


def test_trailing_percentile_is_strictly_prior_and_exact():  # M21
    rng = np.random.default_rng(3)
    v = rng.standard_normal(400)
    v[rng.integers(0, 400, 6)] = np.nan
    window = 100
    out = F.trailing_percentiles(v, window)
    for t in range(window, 400, 7):
        for k, q in enumerate(F.ALL_Q):
            ref = F.trailing_percentile_prior(v, t, q * 100.0, window=window)
            if ref is None:
                assert np.isnan(out[t, k])
            else:
                assert out[t, k] == ref
    spike = v.copy()
    spike[250] = 1e9
    out2 = F.trailing_percentiles(spike, window)
    assert np.array_equal(out2[250], out[250], equal_nan=True)  # bar t is not in its own window
    assert not np.array_equal(out2[251], out[251], equal_nan=True)
    assert np.isnan(out[:window]).all()


def test_95_percent_rule_is_nan_aware_and_called():
    window = 100
    v = np.arange(300, dtype=float)
    ok = v.copy()
    ok[100:105] = np.nan  # 5% missing in windows covering them -> still defined
    bad = v.copy()
    bad[100:106] = np.nan  # 6% missing -> undefined
    # window at t is [t-100, t): t=200 covers all of 100..105
    assert np.isfinite(F.trailing_percentiles(ok, window)[200]).all()
    assert np.isnan(F.trailing_percentiles(bad, window)[200]).all()
    assert np.isfinite(F.trailing_percentiles(bad, window)[201]).all()


def test_sigma_uses_exactly_the_prior_hours_and_not_the_current_sample():  # M22, B3
    cal = small_calendar()
    rng = np.random.default_rng(9)
    n = 60 * 80
    close = 100.0 * np.exp(np.cumsum(rng.standard_normal(n) * 1e-3))
    sigma = F.sigma_hourly_r24(close, cal)
    r24 = F.r24_series(close, cal.r24_bars)
    sample = r24[np.arange(n // 60) * 60]
    for h in (cal.sigma_hours + 4, cal.sigma_hours + 10, 70):
        ref = np.std(sample[h - cal.sigma_hours:h])
        assert sigma[h * 60] == pytest.approx(ref, rel=1e-12)
        assert sigma[h * 60] != pytest.approx(np.std(sample[h - cal.sigma_hours + 1:h + 1]), rel=1e-9)
    first = np.flatnonzero(np.isfinite(sigma))[0]
    assert first == (cal.r24_bars // 60 + cal.sigma_hours) * 60


def test_regime_is_undefined_not_side_when_r24_or_sigma_is_missing():  # B3
    cal = small_calendar()
    close = np.full(60 * 60, 100.0)
    close[:10] = np.nan
    reg = F.regime_labels(close, cal)
    assert (reg["code"][: cal.r24_bars] == 0).all()
    flat = F.regime_labels(np.full(60 * 60, 100.0), cal)
    assert (flat["code"] == 0).all()  # sigma == 0 -> undefined, never SIDE


def test_entry_waits_one_second_and_stale_prints_are_no_trade():  # M23, N3
    mts = np.array([60_000, 60_500, 60_999, 61_000, 200_000], dtype=np.int64)
    px = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    assert F.first_entry_print(mts, px, 60_000) == (61_000, 4.0)
    assert F.first_entry_print(mts[:3], px[:3], 60_000) is None
    late = np.array([60_000 + 1_000 + 60_001], dtype=np.int64)
    assert F.first_entry_print(late, np.array([1.0]), 60_000) is None
    edge = np.array([60_000 + 1_000 + 60_000], dtype=np.int64)
    assert F.first_entry_print(edge, np.array([1.0]), 60_000) == (int(edge[0]), 1.0)
    assert F.entry_print_index(mts, np.array([60_000]))[0] == 3


def test_cooldown_next_signal_bar_is_at_least_exit_bar_plus_one():  # N2
    assert F.apply_cooldown([10, 12, 13, 20], [12, 15, 16, 25]) == [10, 13, 20]
    assert F.apply_cooldown([10, 12], [11, 14]) == [10, 12]


def test_thesis_reads_only_closed_bars():  # B8
    origin = 0
    close_mts = (np.arange(10) + 1) * 60_000
    values = np.array([5, 5, 5, 0, 0, 0, 0, 0, 0, 0], dtype=float)
    p50 = np.full(10, 1.0)
    prints = np.array([3 * 60_000 + 10, 4 * 60_000 - 1, 4 * 60_000, 4 * 60_000 + 5], dtype=np.int64)
    flags = F.thesis_cross_flags(values, p50, 1, prints, close_mts, origin, entry_bar=0)
    # bar 3 (values 0 <= p50) closes at 240000; the print at 239999 is inside bar 3 and must not see it.
    assert flags.tolist() == [False, False, True, True]
    nxt = F.thesis_next_bar(values, p50)
    assert nxt[1][0] == 3


def test_carry_forward_close_for_features_and_empty_bars_never_print():  # B1 ruling
    day = date(2024, 11, 1)
    start = day_ms(day)
    rows = [(1, start + 5 * 60_000 + 10, 0.5, 100.0), (2, start + 9 * 60_000 + 3, -0.2, 101.0)]
    tr = L.trades_to_arrays(rows)
    g = L.build_grid(tr, [day])
    assert np.isnan(g["close"][:5]).all() and not g["defined"][:5].any()
    assert g["close"][6] == 100.0 and g["carried"][6] and g["empty"][6]
    assert np.isnan(g["vwap"][6])
    bars = L.minute_grid(rows, [day])
    assert bars[0]["close"] is None
    assert bars[6]["close"] == 100.0 and L.bar_ohlc_usable_as_print(bars[6]) is False
    assert L.bar_ohlc_usable_as_print(bars[5]) is True


def test_f6_is_signed_and_uses_an_exact_trailing_p95():  # B2
    cal = small_calendar()
    days = cal.days()[:3]
    tr = synth_trades(days, seed=4)
    g = L.build_grid(tr, days)
    thr = F.large_print_threshold(tr, g, cal.f6_window_ms)
    size = np.abs(tr["amount"])
    for t in (1440 + 5, 1440 + 700, 2 * 1440 + 33):
        sel = (tr["mts"] >= g["close_mts"][t] - cal.f6_window_ms) & (tr["mts"] < g["close_mts"][t])
        assert thr[t] == np.quantile(size[sel], 0.95)
    assert np.isnan(thr[:1439]).all()
    f6 = F.large_print_feature(tr, g, thr, 15)
    vals = f6[np.isfinite(f6)]
    assert vals.size > 0 and (vals > 0).any() and (vals < 0).any()


def test_rv_is_sqrt_of_summed_squares():  # N5
    cal = small_calendar()
    days = cal.days()[:2]
    tr = synth_trades(days, seed=5)
    g = L.build_grid(tr, days)
    fm = F.feature_matrix(g, tr, cal)
    lc = np.log(g["close"])
    t = 2000
    steps = np.diff(lc[t - cal.rv_bars:t + 1])
    assert fm["rv_24h"][t] == pytest.approx(np.sqrt(np.sum(steps ** 2)), rel=1e-10)
