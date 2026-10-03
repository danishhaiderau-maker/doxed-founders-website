"""Indicator Edge core: per-indicator reference values, no repainting, closed bars only, bar builder."""

import json
import math
import random

import pytest

import cross_venue_tape as cvt
import indicator_edge_spec as spec
import indicator_engine_core as core
from cross_venue_premium import PremiumRule

T0 = 1790985600  # 2026-10-03 00:00:00 UTC (a UTC midnight and a bar boundary)


# ---------------------------------------------------------------------------
# Synthetic bars
# ---------------------------------------------------------------------------
def bar(k, c, *, o=None, h=None, l=None, vol=1.0, buy=None, sell=None, xv=None, mc=None, tape="OK", t0=T0,
        filled=False, tob_imb=0.0, spread_bp=0.5):
    o = c if o is None else o
    h = max(o, c) if h is None else h
    l = min(o, c) if l is None else l
    buy = vol / 2.0 if buy is None else buy
    sell = vol - buy if sell is None else sell
    return {"ts": t0 + k * 180, "close_ts": t0 + k * 180 + 180, "o": o, "h": h, "l": l, "c": c,
            "vol": vol, "buy": buy, "sell": sell, "tape": tape, "filled": filled, "tob_imb": tob_imb,
            "spread_bp": spread_bp,
            "xv": xv if xv is not None else {"state": "OK", "bn_net": 0.0, "bb_net": 0.0, "imb5": None,
                                             "imb20": None, "prem_ready": False, "prem_dev": None,
                                             "lead_long": 0, "lead_short": 0, "lead_close": None},
            "mc": mc if mc is not None else {"state": "OK", "funding": None, "oi": None, "liq_long": 0.0,
                                             "liq_short": 0.0, "liq_ok": False, "next_funding_ms": None}}


def series_bars(closes, **kw):
    out = []
    prev = closes[0]
    for k, c in enumerate(closes):
        out.append(bar(k, c, o=prev, **kw))
        prev = c
    return out


def walk(n, seed=7, vol=0.0008, start=60000.0):
    rnd = random.Random(seed)
    out, p = [], start
    for k in range(n):
        o = p
        hi = lo = p
        for _ in range(4):
            p *= 1 + rnd.gauss(0, vol)
            hi, lo = max(hi, p), min(lo, p)
        b = rnd.random() * 4
        s = rnd.random() * 4
        out.append(bar(k, p, o=o, h=hi, l=lo, vol=b + s, buy=b, sell=s, tob_imb=rnd.uniform(-1, 1),
                       xv={"state": "OK", "bn_net": rnd.gauss(0, 5), "bb_net": rnd.gauss(0, 3),
                           "imb5": rnd.uniform(-1, 1), "imb20": rnd.uniform(-1, 1), "prem_ready": True,
                           "prem_dev": rnd.gauss(0, 1.2), "lead_long": rnd.randint(0, 2),
                           "lead_short": rnd.randint(0, 2), "lead_close": rnd.gauss(0, 3)},
                       mc={"state": "OK", "funding": 1e-4 + rnd.gauss(0, 2e-5), "oi": 80000 + rnd.gauss(0, 50) + k,
                           "liq_long": rnd.random() * 1e5, "liq_short": rnd.random() * 1e5, "liq_ok": True,
                           "next_funding_ms": (T0 + 8 * 3600) * 1000.0}))
    return out


def feat(fn, bars, *args):
    return fn(core.Series(bars), *args)


# ---------------------------------------------------------------------------
# Series helpers: reference values
# ---------------------------------------------------------------------------
def test_ema_is_sma_seeded_and_matches_closed_form_on_a_line():
    x = [float(k + 1) for k in range(10)]
    e = core.ema(x, 3)
    assert e[:2] == [None, None]
    assert e[2] == pytest.approx(2.0)
    # For a line, an SMA-seeded EMA(3) lags exactly (n-1)/2 = 1.
    assert e[9] == pytest.approx(9.0)


def test_wilder_sma_wma_rolling_std_reference_values():
    assert core.wilder([5.0] * 20, 14)[-1] == pytest.approx(5.0)
    w = core.wilder([1.0, 2.0, 3.0, 4.0], 2)
    assert w[1] == pytest.approx(1.5) and w[2] == pytest.approx(1.5 + (3 - 1.5) / 2) and w[3] == pytest.approx(3.125)
    assert core.sma([1.0, 2.0, 3.0, 4.0], 2) == [None, 1.5, 2.5, 3.5]
    assert core.wma([1.0, 2.0, 3.0], 3)[2] == pytest.approx(14.0 / 6.0)
    assert core.rolling_std([1.0, 3.0], 2)[1] == pytest.approx(1.0)
    assert core.sma([1.0, None, 3.0, 4.0], 2) == [None, None, None, 3.5]


def test_linreg_exact_line_and_pct_rank_half_ties():
    slope, t = core.linreg([2.0 * k + 5 for k in range(14)], 14, 13)
    assert slope == pytest.approx(2.0) and t == math.inf
    assert core.linreg([1.0] * 5, 14, 4) is None
    series = [float(k) for k in range(200)]
    assert core.pct_rank(series, 199) == pytest.approx(99.75)
    assert core.pct_rank(series, 100) is None or core.pct_rank(series, 100) == pytest.approx(99.5 * 100 / 101, rel=1e-3)
    assert core.pct_rank(series[:100], 99) is None  # below the 160-bar minimum history
    assert core.pct_rank([1.0] * 200, 199) == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Family A: trend
# ---------------------------------------------------------------------------
def test_ema_family_scores_follow_price_vs_rising_ema():
    up = series_bars([60000 + 5 * k for k in range(320)])
    f = feat(core.ind_ema, up)
    for fid in ("EMA_200@F:STATE", "EMA_50@F:STATE", "EMA_288@F:STATE", "EMA_CROSS_9_21@F:STATE"):
        assert f[fid][1] == 1 and f[fid][3] == "A"
    down = series_bars([60000 - 5 * k for k in range(320)])
    assert feat(core.ind_ema, down)["EMA_200@F:STATE"][1] == -1


def test_ema_288_warms_up_until_288_bars():
    f = feat(core.ind_ema, series_bars([60000.0] * 290))
    assert f["EMA_288@F:STATE"][3] == "W" and f["EMA_200@F:STATE"][3] == "A"


def test_ichimoku_supertrend_psar_hma_linreg_tema_agree_on_a_clean_uptrend():
    # Accelerating: on an exact line TEMA has zero lag, so TEMA9 == TEMA21 and scores 0.
    up = series_bars([60000 + 4 * k + 0.05 * k * k for k in range(200)])
    for fn, fid in ((core.ind_ichimoku, "ICHIMOKU_FAST@F:STATE"), (core.ind_supertrend, "SUPERTREND@F:STATE"),
                    (core.ind_psar, "PSAR@F:STATE"), (core.ind_hma, "HMA_SLOPE@F:STATE"),
                    (core.ind_hma, "HMA_SLOPE@S:STATE"), (core.ind_linreg, "LINREG_SLOPE@F:STATE"),
                    (core.ind_tema, "TEMA@F:STATE")):
        out = feat(fn, up)
        assert out[fid][1] == 1, fid
        assert out[fid][3] == "A", fid
    dn = series_bars([60000 - 4 * k for k in range(200)])
    assert feat(core.ind_supertrend, dn)["SUPERTREND@F:STATE"][1] == -1
    assert feat(core.ind_psar, dn)["PSAR@F:STATE"][1] == -1


def test_linreg_raw_is_slope_in_bp_per_bar():
    closes = [60000 + 6 * k for k in range(60)]
    f = feat(core.ind_linreg, series_bars(closes))
    assert f["LINREG_SLOPE@F:STATE"][0] == pytest.approx(6.0 / closes[-1] * 1e4, rel=1e-6)


# ---------------------------------------------------------------------------
# Family B: momentum
# ---------------------------------------------------------------------------
def test_rsi_reference_extremes_and_textbook_value():
    up = series_bars([100.0 + k for k in range(40)])
    f = feat(core.ind_rsi, up)
    assert f["RSI@F:TREND"][0] == pytest.approx(100.0)
    assert f["RSI@F:TREND"][1] == 1 and f["RSI@F:REVERSION"][1] == -1
    dn = series_bars([100.0 - k for k in range(40)])
    assert feat(core.ind_rsi, dn)["RSI@F:REVERSION"][1] == 1
    # Alternating +2/-1: Wilder averages converge to gain 1, loss 0.5 per bar -> RSI 66.7.
    closes, p = [], 100.0
    for k in range(400):
        p += 2.0 if k % 2 == 0 else -1.0
        closes.append(p)
    r = core.rsi_series(closes, 9)[-1]
    assert 60.0 < r < 72.0


def test_stochastic_williams_cci_cmo_reference_values():
    up = series_bars([100.0 + k for k in range(80)])
    w = feat(core.ind_willr, up)
    assert w["WILLR@F:TREND"][0] == pytest.approx(0.0) and w["WILLR@F:TREND"][1] == 1
    c = feat(core.ind_cmo, up)
    assert c["CMO@F:TREND"][0] == pytest.approx(100.0)
    assert c["CMO@F:TREND"][1] == 1 and c["CMO@F:REVERSION"][1] == -1
    flat = series_bars([100.0] * 80)
    assert feat(core.ind_cci, flat)["CCI@F:TREND"][0] == pytest.approx(0.0)
    s = feat(core.ind_stoch, up)
    assert s["STOCH@F:TREND"][0] == pytest.approx(100.0)


def test_stochastic_cross_up_from_oversold_is_a_reversion_long():
    closes = [100.0 - 0.5 * k for k in range(60)] + [70.6, 71.5]
    f = feat(core.ind_stoch, series_bars(closes))
    assert f["STOCH@F:REVERSION"][1] in (0, 1)
    assert f["STOCH@F:REVERSION"][3] == "A"


def test_macd_histogram_positive_and_rising_on_acceleration():
    closes = [60000 + 0.05 * k * k for k in range(120)]
    f = feat(core.ind_macd, series_bars(closes))
    assert f["MACD_SCALP@F:TREND"][1] == 1 and f["MACD_SCALP@F:TREND"][0] > 0


def test_roc_reference_and_dpo_is_causal():
    closes = [60000.0 + 10 * k for k in range(250)]
    f = feat(core.ind_roc, series_bars(closes))
    expect = (closes[-1] / closes[-7] - 1.0) * 1e4
    assert f["ROC@F:TREND"][0] == pytest.approx(expect, rel=1e-6)
    d = feat(core.ind_dpo, series_bars(closes))
    ma = sum(closes[-1 - 7 - 11:-1 - 7 + 1]) / 12  # SMA12 ending shift=7 bars ago
    assert d["DPO@F:TREND"][0] == pytest.approx((closes[-1] - ma) / closes[-1] * 1e4, rel=1e-6)


def test_kst_and_ao_follow_a_rising_market():
    closes = [60000 + 0.08 * k * k for k in range(120)]
    bars = series_bars(closes)
    assert feat(core.ind_kst, bars)["KST@F:TREND"][1] == 1
    assert feat(core.ind_ao, bars)["AO@F:TREND"][1] == 1


# ---------------------------------------------------------------------------
# Family C: volume / order flow
# ---------------------------------------------------------------------------
def test_obv_mfi_cmf_ad_vwma_eom_klinger_on_accumulation():
    closes = [60000 + 3 * k for k in range(150)]
    bars = []
    for k, c in enumerate(closes):
        bars.append(bar(k, c, o=c - 2, h=c, l=c - 4, vol=1.0 + (k % 5), buy=0.8 + (k % 5), sell=0.2))
    s = core.Series(bars)
    assert core.ind_obv(s)["OBV_EMA@F:TREND"][1] == 1
    assert core.ind_mfi(s)["MFI@F:TREND"][0] == pytest.approx(100.0)
    cmf = core.ind_cmf(s)["CMF@F:TREND"]
    assert cmf[0] == pytest.approx(1.0) and cmf[1] == 1
    assert core.ind_ad(s)["AD_SLOPE@F:TREND"][1] == 1
    assert core.ind_eom(s)["EOM@F:TREND"][1] == 1
    assert core.ind_vwma(s)["VWMA_20@F:TREND"][3] == "A"
    assert core.ind_klinger(s)["KLINGER@F:TREND"][3] == "A"


def test_net_taker_needs_a_large_imbalance_to_score():
    bars = walk(300)
    bars[-1] = dict(bars[-1], buy=500.0, sell=0.0, vol=500.0)
    f = feat(core.ind_net_taker, bars)
    assert f["NET_TAKER_VOL@F:TREND"][0] == pytest.approx(500.0) and f["NET_TAKER_VOL@F:TREND"][1] == 1


def test_book_imbalance_thresholds_and_uncollected_binance_depth_is_unavailable():
    bars = walk(10)
    bars[-1] = dict(bars[-1], tob_imb=0.5, xv=dict(bars[-1]["xv"], imb5=-0.4, imb20=0.1))
    f = feat(core.ind_book_imbalance, bars, {"imb5": True, "imb20": True})
    assert f["BOOK_IMBALANCE@BFX:TREND"][1] == 1
    assert f["BOOK_IMBALANCE@BN5:TREND"][1] == -1
    assert f["BOOK_IMBALANCE@BN20:TREND"][1] == 0
    f = feat(core.ind_book_imbalance, bars, {})
    assert f["BOOK_IMBALANCE@BN5:TREND"][3] == "U" and f["BOOK_IMBALANCE@BN20:TREND"][3] == "U"


def test_cvd_trend_and_divergence():
    closes = [60000 - 2 * k for k in range(40)]
    bars = [bar(k, c, vol=2.0, buy=1.5, sell=0.5, xv={"state": "OK", "bn_net": 3.0, "bb_net": 1.0})
            for k, c in enumerate(closes)]
    f = feat(core.ind_cvd, bars)
    assert f["CVD@BFX:TREND"][1] == 1
    assert f["CVD@BFX:DIVERGENCE"][1] == 1  # price down while CVD rises
    assert f["CVD@BN:TREND"][1] == 1
    bars[-1] = dict(bars[-1], xv={"state": "MISSING", "bn_net": None, "bb_net": None})
    assert feat(core.ind_cvd, bars)["CVD@BN:TREND"][3] == "U"


def test_session_vwap_and_volume_profile():
    closes = [60000 + 2 * k for k in range(60)]
    bars = series_bars(closes)
    v = feat(core.ind_avwap, bars)
    assert v["AVWAP_SESSION@F:TREND"][1] == 1
    vp = feat(core.ind_vp_session, bars)
    assert vp["VP_SESSION@F:TREND"][3] == "A"
    early = series_bars(closes[:5])
    assert feat(core.ind_avwap, early)["AVWAP_SESSION@F:TREND"][3] == "W"


def test_xvenue_net_flow_is_binance_plus_bybit_minus_bitfinex():
    bars = walk(300)
    last = bars[-1]
    f = feat(core.ind_xvenue_flow, bars)
    expect = last["xv"]["bn_net"] + last["xv"]["bb_net"] - (last["buy"] - last["sell"])
    assert f["XVENUE_NET_FLOW@F:TREND"][0] == pytest.approx(expect, rel=1e-6)


# ---------------------------------------------------------------------------
# Family D: volatility
# ---------------------------------------------------------------------------
def test_bollinger_squeeze_then_breakout_scores_the_break_direction():
    rnd = random.Random(3)
    closes = [60000 + rnd.gauss(0, 20) for _ in range(300)] + [60000 + rnd.gauss(0, 1) for _ in range(40)]
    closes.append(60200.0)
    regime = {}
    f = feat(core.ind_bb, series_bars(closes), regime)
    assert f["BB@F:BREAKOUT"][1] == 1 and f["BB@F:BREAKOUT"][0] > 1.0


def test_atr_keltner_donchian_stddev_hv_reference():
    bars = [bar(k, 60000.0, o=60000.0, h=60010.0, l=59990.0) for k in range(300)]
    regime = {}
    a = feat(core.ind_atr_regime, bars, regime)
    assert a["ATR@F:STATE"][0] == pytest.approx(20.0 / 60000.0 * 100.0, abs=1e-6)
    bars.append(bar(300, 60100.0, o=60000.0, h=60100.0, l=60000.0))
    assert feat(core.ind_donchian, bars)["DONCHIAN@F:BREAKOUT"][1] == 1
    assert feat(core.ind_keltner, bars)["KELTNER@F:TREND"][1] == 1
    assert feat(core.ind_stddev_z, bars)["STDDEV_Z@F:REVERSION"][1] == -1
    assert regime["vol_tercile"] in ("LOW", "MID", "HIGH")


def test_hv_fades_the_last_three_bars_only_above_the_95th_percentile():
    rnd = random.Random(5)
    closes, p = [], 60000.0
    for _ in range(300):
        p *= math.exp(rnd.gauss(0, 0.0003))
        closes.append(p)
    for k in range(11):  # violent two-way bars, net up over the last three
        p *= 1.012 if k % 2 == 0 else 0.992
        closes.append(p)
    f = feat(core.ind_hv, series_bars(closes))
    assert closes[-1] > closes[-4]
    assert f["HV@F:REVERSION"][2] > 95 and f["HV@F:REVERSION"][1] == -1


# ---------------------------------------------------------------------------
# Family E: structure (previous-day / confirmed-swing values only)
# ---------------------------------------------------------------------------
def _two_days(prev_h=61000.0, prev_l=59000.0, prev_c=60500.0, today=None):
    bars = []
    per_day = 86400 // 180
    for k in range(per_day):
        c = prev_c if k == per_day - 1 else 60000.0
        h = prev_h if k == 10 else max(c, 60000.0)
        l = prev_l if k == 20 else min(c, 60000.0)
        bars.append(bar(k, c, o=60000.0, h=h, l=l, t0=T0 - 86400))
    for k, c in enumerate(today or [60500.0] * 5):
        bars.append(bar(k, c, t0=T0))
    return bars


def test_cpr_and_camarilla_use_previous_utc_day_only():
    levels = {}
    f = feat(core.ind_pivots, _two_days(), levels)
    p = (61000.0 + 59000.0 + 60500.0) / 3.0
    assert levels["cpr_p"] == pytest.approx(p, abs=0.01)
    assert levels["cam_h3"] == pytest.approx(60500.0 + 2000.0 * 1.1 / 4, abs=0.01)
    assert f["CPR_PIVOTS@F:TREND"][3] == "A"
    # Moving today's bars cannot move the levels.
    levels2 = {}
    feat(core.ind_pivots, _two_days(today=[58000.0, 63000.0, 60100.0]), levels2)
    assert {k: levels2[k] for k in ("cpr_p", "cpr_tc", "cpr_bc", "cam_h4")} == \
        {k: levels[k] for k in ("cpr_p", "cpr_tc", "cpr_bc", "cam_h4")}


def test_pivots_warm_up_without_a_well_covered_previous_day():
    bars = _two_days()[300:]
    assert feat(core.ind_pivots, bars, {})["CPR_PIVOTS@F:TREND"][3] == "W"


def test_zigzag_uses_only_swings_confirmed_before_the_current_bar():
    closes = [60000, 60100, 60250, 60300, 60100, 60000, 59950, 60050, 60200, 60300, 60400]
    bars = series_bars([float(c) for c in closes])
    s = core.Series(bars)
    swings = core.zigzag_swings(s, 0.3, s.n - 2)
    assert all(sw[3] <= s.n - 2 for sw in swings)
    before = core.zigzag_swings(s, 0.3, s.n - 2)
    bars2 = list(bars)
    bars2[-1] = dict(bars2[-1], h=70000.0, l=50000.0)
    assert core.zigzag_swings(core.Series(bars2), 0.3, s.n - 2) == before
    f = feat(core.ind_zigzag, bars)
    assert f["ZIGZAG_MSB@F:BREAKOUT"][1] == 1  # broke the confirmed 60300 swing high


def test_fib_levels_ignore_the_current_bar_and_score_a_bounce():
    closes = [60000 + 10 * k for k in range(81)]
    bars = series_bars([float(c) for c in closes])
    lvl = closes[-2] - 0.618 * (closes[-2] - closes[0])
    bars.append(bar(81, lvl + 5, o=lvl + 5, h=lvl + 6, l=lvl - 5))
    levels = {}
    f = feat(core.ind_fib, bars, levels)
    assert levels["fib_leg"] == "UP"
    assert f["FIB_4H@F:REVERSION"][1] == 1
    bars2 = list(bars)
    bars2[-1] = dict(bars2[-1], h=99999.0)
    levels2 = {}
    feat(core.ind_fib, bars2, levels2)
    assert levels2["fib_0618"] == levels["fib_0618"]


def test_pitchfork_needs_240_bars_and_three_confirmed_swings():
    assert feat(core.ind_pitchfork, walk(100))["PITCHFORK_12H@F:STATE"][3] == "W"
    out = feat(core.ind_pitchfork, walk(400, vol=0.003))["PITCHFORK_12H@F:STATE"]
    assert out[3] == "A" and out[1] in (-1, 0, 1)


# ---------------------------------------------------------------------------
# Family F: derivatives
# ---------------------------------------------------------------------------
def test_liquidation_burst_fades_long_liquidations():
    bars = walk(300)
    bars[-1] = dict(bars[-1], mc=dict(bars[-1]["mc"], liq_long=5e7, liq_short=0.0, liq_ok=True))
    f = feat(core.ind_liq, bars)
    assert f["LIQ_BURST@F:REVERSION"][1] == 1 and f["LIQ_BURST@F:TREND"][1] == -1
    bars[-1] = dict(bars[-1], mc=dict(bars[-1]["mc"], liq_ok=False))
    assert feat(core.ind_liq, bars)["LIQ_BURST@F:REVERSION"][3] == "U"


def test_funding_extreme_and_oi_delta_with_price_direction():
    bars = walk(300)
    bars[-1] = dict(bars[-1], mc=dict(bars[-1]["mc"], funding=1e-3))
    assert feat(core.ind_funding, bars)["FUNDING@F:REVERSION"][1] == -1
    bars = series_bars([60000 + k for k in range(30)],
                       mc={"state": "OK", "oi": None, "liq_ok": False, "funding": None})
    for k, b in enumerate(bars):
        b["mc"] = dict(b["mc"], oi=80000.0 + 10 * k)
    f = feat(core.ind_oi, bars)
    assert f["OI_DELTA@F:TREND"][0] == pytest.approx(10.0) and f["OI_DELTA@F:TREND"][1] == 1
    bars[-1]["mc"] = dict(bars[-1]["mc"], oi=None)
    assert feat(core.ind_oi, bars)["OI_DELTA@F:TREND"][3] == "U"


def test_basis_premium_and_xvenue_lead_use_the_generic_primitives():
    rule = PremiumRule()
    bars = walk(300)
    bars[-1] = dict(bars[-1], xv=dict(bars[-1]["xv"], prem_dev=rule.long_threshold_bps + 0.1, prem_ready=True))
    assert feat(core.ind_basis_premium, bars, rule)["BASIS_PREMIUM@F:TREND"][1] == 1
    bars[-1] = dict(bars[-1], xv=dict(bars[-1]["xv"], prem_dev=rule.short_threshold_bps - 0.1))
    assert feat(core.ind_basis_premium, bars, rule)["BASIS_PREMIUM@F:TREND"][1] == -1
    bars[-1] = dict(bars[-1], xv=dict(bars[-1]["xv"], lead_long=1, lead_short=4))
    assert feat(core.ind_xvenue_lead, bars)["XVENUE_LEAD@F:TREND"][1] == -1
    bars[-1] = dict(bars[-1], xv={"state": "MISSING"})
    assert feat(core.ind_xvenue_lead, bars)["XVENUE_LEAD@F:TREND"][3] == "U"


# ---------------------------------------------------------------------------
# Whole vector: shape, determinism, closed bars only, no repainting
# ---------------------------------------------------------------------------
def test_compute_features_emits_every_registered_feature_in_order():
    out = core.compute_features(walk(700), xv_seen={"imb5": True, "imb20": True})
    assert list(out["f"]) == spec.feature_ids()
    for fid, (raw, score, pct, status) in out["f"].items():
        assert status in ("A", "W", "U"), fid
        assert score in (None, -1, 0, 1), fid
        assert pct is None or 0.0 <= pct <= 100.0, fid
        assert raw is None or math.isfinite(raw), fid
    json.dumps(out, allow_nan=False)
    for key in ("vol_tercile", "trend_state", "session", "spread_bucket", "ttf_bucket", "ai_class", "ai_side"):
        assert key in out["regime"]


def test_compute_features_is_deterministic_and_bounded_by_history():
    bars = walk(1300)
    a = core.compute_features(bars)
    b = core.compute_features(bars)
    assert a == b
    assert core.compute_features(bars[-spec.HISTORY_BARS:]) == a


def test_no_repainting_value_at_bar_k_never_depends_on_later_bars():
    bars = walk(520, seed=11)
    alt = walk(520, seed=99)
    for k in (180, 300, 450):
        mixed = bars[:k + 1] + [dict(b, ts=bars[k]["ts"] + (j + 1) * 180) for j, b in enumerate(alt[k + 1:])]
        assert core.compute_features(bars[:k + 1]) == core.compute_features(mixed[:k + 1])


def test_closed_bars_only_the_current_bar_never_moves_levels_or_swings():
    bars = walk(500, seed=4, vol=0.002)
    base = core.compute_features(bars)
    shocked = list(bars)
    shocked[-1] = dict(shocked[-1], h=shocked[-1]["h"] * 1.05, l=shocked[-1]["l"] * 0.95)
    out = core.compute_features(shocked)
    for key in ("fib_0618", "fib_leg", "cpr_p", "cam_h3"):
        if key in base["levels"]:
            assert out["levels"][key] == base["levels"][key]


def test_short_and_gappy_history_warms_up_instead_of_failing():
    for n in (0, 1, 2, 15, 60):
        bars = walk(n) if n else []
        if not bars:
            continue
        if n > 4:
            bars[n // 2] = dict(bars[n // 2], o=None, h=None, l=None, c=None, tape="MISSING", filled=True)
        out = core.compute_features(bars)
        assert set(out["f"]) == set(spec.feature_ids())
        counts = core.status_counts(out["f"])
        assert sum(counts.values()) == len(spec.feature_ids())
        assert counts["WARMING_UP"] > 0


# ---------------------------------------------------------------------------
# Bar builder
# ---------------------------------------------------------------------------
def tape_row(sec, bid=60000.0, ask=60000.5, last=None, buy=0.0, sell=0.0, fresh=True, bq=1.0, aq=1.0):
    return {"schema": "market_microstructure_1s_v1", "bucket_ts": sec, "fresh": fresh, "valid_bbo": True,
            "bid": bid, "ask": ask, "bid_qty": bq, "ask_qty": aq, "last": last, "buy_qty": buy, "sell_qty": sell,
            "trade_count": 1 if last else 0}


def test_bar_builder_ohlc_from_trade_prints_with_idempotent_seconds():
    b = core.BarBuilder()
    b.add_tape_row(tape_row(T0 + 5, last=60010.0, buy=0.5))
    b.add_tape_row(tape_row(T0 + 1, last=60000.0, sell=0.2))
    b.add_tape_row(tape_row(T0 + 9, last=60020.0, buy=0.1))
    b.add_tape_row(tape_row(T0 + 100, last=59990.0, sell=0.3))
    assert b.add_tape_row(tape_row(T0 + 100, last=1.0, sell=99.0)) is False
    for s in range(T0 + 110, T0 + 180):
        b.add_tape_row(tape_row(s))
    out = b.finalize(T0)
    assert (out["o"], out["h"], out["l"], out["c"]) == (60000.0, 60020.0, 59990.0, 59990.0)
    assert out["buy"] == pytest.approx(0.6) and out["sell"] == pytest.approx(0.5)
    assert out["fresh_sec"] == 74 and out["tape"] == "PARTIAL" and out["filled"] is False
    assert out["close_ts"] == T0 + 180


def test_bar_builder_mid_fallback_and_missing_bar_carries_previous_close():
    b = core.BarBuilder()
    for s in range(T0, T0 + 180):
        b.add_tape_row(tape_row(s, bid=60000.0 + (s - T0), ask=60001.0 + (s - T0)))
    first = b.finalize(T0)
    assert first["c"] == pytest.approx(60000.5 + 179) and first["tape"] == "OK"
    assert first["tob_imb"] == pytest.approx(0.0)
    empty = b.finalize(T0 + 180, first)
    assert empty["tape"] == "MISSING" and empty["filled"] is True and empty["c"] == first["c"]


def _xv_row(minute, bn_buy=0.0, imb5=None, up=True):
    samples = {}
    for venue in ("binance", "bybit"):
        seq = []
        for i in range(60):
            seq.append({"sec": minute + i, "mid": 60010.0, "last": None, "buy": bn_buy if venue == "binance" else 0.0,
                        "sell": 0.0, "up": up, "imb5": imb5 if venue == "binance" else None, "imb20": None})
        samples[venue] = seq
    return cvt.encode_minute(minute, samples, [60000.0] * 60)


def test_bar_builder_cross_venue_flow_imbalance_and_time_order():
    b = core.BarBuilder()
    assert b.add_cross_venue_row(_xv_row(T0, bn_buy=0.01, imb5=0.4))
    assert b.add_cross_venue_row(_xv_row(T0 + 60, bn_buy=0.01, imb5=0.2))
    assert b.add_cross_venue_row(_xv_row(T0, bn_buy=5.0)) is False  # out of order
    assert b.add_cross_venue_row(_xv_row(T0 + 120, bn_buy=0.0, up=False))
    out = b.finalize(T0)
    assert out["xv"]["bn_net"] == pytest.approx(1.2)
    assert out["xv"]["imb5"] == pytest.approx(0.3)
    assert out["xv"]["minutes"] == 3 and out["xv"]["state"] == "PARTIAL"  # last minute was down
    assert b.xv_imb_seen == {"imb5": True, "imb20": False}


def test_bar_builder_market_context_funding_oi_and_liquidations():
    b = core.BarBuilder()
    for m in range(3):
        b.add_market_context_row({
            "schema": "market_context_1m_v1", "minute_ts": T0 + 60 * m,
            "derivatives": {"binance": {"status": "OK", "predicted_funding_rate": 1e-4, "oi_btc": 100.0,
                                        "next_funding_ms": 1.0},
                            "bybit": {"status": "OK", "funding_rate": 3e-4, "oi_btc": 50.0},
                            "okx": {"status": "OK", "funding_rate": 2e-4, "oi_btc": 25.0}},
            "liquidations": {"binance": {"status": "OK", "long_usd": 10.0, "short_usd": 0.0},
                             "bybit": {"status": "OK", "long_usd": 0.0, "short_usd": 5.0},
                             "okx": {"status": "PARTIAL", "long_usd": 1.0, "short_usd": 0.0}}})
    out = b.finalize(T0)
    assert out["mc"]["funding"] == pytest.approx(2e-4)
    assert out["mc"]["oi"] == pytest.approx(175.0)
    assert out["mc"]["liq_long"] == pytest.approx(33.0) and out["mc"]["liq_short"] == pytest.approx(15.0)
    assert out["mc"]["liq_ok"] is True and out["mc"]["state"] == "OK"


def test_latest_ai_classes():
    rows = [{"decision_ts": T0 - 30, "ai": {"raw_direction": "LONG", "long_score": 1, "short_score": 2}},
            {"decision_ts": T0 - 10, "ai": {"raw_direction": "NO_TRADE", "long_score": 3, "short_score": 1}},
            {"decision_ts": T0 + 10, "ai": {"raw_direction": "SHORT"}}]
    ai = core.latest_ai(rows, T0)
    assert ai == {"ai_class": "NO_TRADE_SCORE_LED", "ai_side": "LONG", "ai_age_sec": 10.0}
    assert core.latest_ai(rows[:1], T0)["ai_class"] == "COMMITTED_SCORE_CONFLICT"
    assert core.latest_ai([], T0)["ai_class"] is None


def test_row_health_flags_tape_holes():
    bars = walk(30)
    assert core.row_health(bars)["ok"] is True
    bars[-5] = dict(bars[-5], tape="MISSING")
    h = core.row_health(bars)
    assert h["ok"] is False and h["tape_holes_recent"] == 1
