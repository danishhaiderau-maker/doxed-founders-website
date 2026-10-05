"""FREEZE21B GS/B shared machinery: 3 m regime/CVD bar engine, regime classifier, streaming exit stack,
reprice schedules and the GS pre-registered verdict."""
import time

import pytest

import combo_pathway_config as cfg
import gs_regime_exit_stack as stack
import regime_bars_3m as bars3m
import tile_paired_comparison as tpc
from regime_adaptive_binding import reprice_ages

T0 = 1_790_000_000 - (1_790_000_000 % 180)


def _rows(n_bars, *, price0=60000.0, drift=0.05, buy=1.0, sell=2.0, skip=()):
    out = []
    for i in range(n_bars * 180):
        ts = T0 + i
        if ts // 180 * 180 in skip:
            continue
        mid = price0 + drift * i
        out.append({"bucket_ts": ts, "fresh": True, "valid_bbo": True, "bid": mid - 0.5, "ask": mid + 0.5,
                    "buy_qty": buy, "sell_qty": sell, "source_age_sec": 0.2})
    return out


def test_bars_close_on_the_three_minute_grid_without_lookahead():
    eng = bars3m.RegimeBars3m()
    eng.hydrate(_rows(3))
    assert [b["close_ts"] for b in eng.bars] == [T0 + 180, T0 + 360]  # third bar still open
    assert eng.latest(T0 + 360)["close_ts"] == T0 + 180  # close + 1 s availability
    assert eng.latest(T0 + 361)["close_ts"] == T0 + 360
    assert all(b["bar_ok"] and b["nvalid"] == 180 for b in eng.bars)


def test_rising_price_with_net_selling_is_a_bearish_cvd_divergence_event():
    eng = bars3m.RegimeBars3m()
    eng.hydrate(_rows(32))
    bars = list(eng.bars)
    first = next(i for i, b in enumerate(bars) if b["scores"][bars3m.DIVERGENCE] != 0)
    assert first == bars3m.CVD_LOOKBACK - 1
    assert bars[first]["scores"][bars3m.DIVERGENCE] == -1 and bars[first]["events"][bars3m.DIVERGENCE] == -1
    assert bars[first + 1]["events"][bars3m.DIVERGENCE] == 0  # an event only on the change
    assert bars[first]["scores"][bars3m.TREND_SCORE] == -1
    assert bars[-1]["atr_bp"] > 0 and bars[-1]["adx"] > 90  # one-way move: ADX near 100 after warm-up


def test_bar_with_too_few_valid_seconds_never_signals():
    gap_bucket = T0 + 20 * 180
    eng = bars3m.RegimeBars3m()
    eng.hydrate(_rows(23, skip={gap_bucket}))
    gap = next(b for b in eng.bars if b["bucket"] == gap_bucket)
    assert gap["bar_ok"] is False and gap["scores"][bars3m.DIVERGENCE] == 0


def test_rows_before_hydration_are_replayed_in_order():
    rows = _rows(3)
    eng = bars3m.RegimeBars3m()
    for row in rows[300:]:
        assert eng.observe_row(row) is False
    eng.hydrate(rows[:300])
    assert eng.hydrated and len(eng.bars) == 2


def test_shock_inputs_use_the_sixty_second_mid_window():
    eng = bars3m.RegimeBars3m()
    eng.hydrate(_rows(1))
    shock = eng.shock_inputs()
    assert shock["r60_bp"] == pytest.approx(0.05 * 59 / (60000 + 0.05 * 179) * 1e4, rel=1e-6)
    assert shock["ret60_bp"] == pytest.approx(0.05 * 60 / (60000 + 0.05 * 179) * 1e4, rel=1e-6)


@pytest.mark.parametrize("features,expected", [
    ({"atr_pct_rank": 85.0, "spread_bp": 0.5, "adx": 10.0}, "VIOLENT"),
    ({"atr_pct_rank": 20.0, "spread_bp": 3.0, "adx": 10.0}, "VIOLENT"),
    ({"atr_pct_rank": 20.0, "spread_bp": 0.5, "adx": 30.0}, "TREND"),
    ({"atr_pct_rank": 20.0, "spread_bp": 0.5, "adx": 10.0}, "QUIET"),
    ({"atr_pct_rank": None, "spread_bp": None, "adx": None}, "QUIET"),
])
def test_b_regime_classifier(features, expected):
    assert bars3m.classify_regime(features, violent_pct=80.0, violent_spread_bp=3.0, trend_adx=25.0) == expected


def test_gs02_classifier_has_no_trend_state():
    assert bars3m.classify_regime({"atr_pct_rank": 10.0, "spread_bp": 0.1, "adx": 60.0}, violent_pct=66.0,
                                  violent_spread_bp=2.0, trend_adx=None) == "QUIET"


def test_reprice_schedules_match_the_preregistration():
    b = cfg.COMBO_LANE_SPECS["FAMILY_GSB2_REGIME_SWITCHER"]["entry_policy"]["regime_exec"]
    gs2 = cfg.COMBO_LANE_SPECS["FAMILY_GS02_NOTRADE_REGIME_ENTRY"]["entry_policy"]["regime_exec"]
    assert reprice_ages(b["QUIET"]) == list(range(60, 600, 60))
    assert reprice_ages(b["TREND"]) == [120, 240, 300, 420, 540, 600, 720, 840]
    assert reprice_ages(b["VIOLENT"]) == []
    assert reprice_ages(gs2["VIOLENT"]) == [300, 480, 600, 780]


def _profile(lane, name):
    return cfg.COMBO_LANE_SPECS[lane]["exit_policy"]["profiles"][name]


def test_arming_takes_effect_from_the_next_tick():
    prof = _profile("FAMILY_GS04_NOTRADE_ATR_TP", "ALL")
    st = stack.new_state()
    assert stack.evaluate_tick(prof, st, cur_bp=8.5, age_sec=5, atr_bp=4.0) is None  # arms BE (8 bp)
    assert st["be_armed_age"] == 5
    hit = stack.evaluate_tick(prof, st, cur_bp=0.5, age_sec=6, atr_bp=4.0)
    # lock_bp is 1.0 on GS-04: book the floor, not the 0.5 tick that crossed it
    assert hit["rule"] == "BREAKEVEN_LOCK" and hit["book_bp"] == float(prof.get("lock_bp", 1.0))


def test_first_rule_in_profile_order_wins_a_tie():
    prof = _profile("FAMILY_GSB3_COMMITTED_FADE_REGIME", "MOM_QUIET")
    st = stack.new_state()
    stack.evaluate_tick(prof, st, cur_bp=7.0, age_sec=5, atr_bp=4.0)  # arms BE (6 bp floor)
    hit = stack.evaluate_tick(prof, st, cur_bp=-8.5, age_sec=6, atr_bp=4.0)  # cut and BE both fire
    assert hit["rule"] == "THESIS_CUT"


def test_main_exit_beats_tp1_on_the_same_tick_and_tp1_needs_tp1_below_tp():
    prof = _profile("FAMILY_GSB1_CVD_DIV_REGIME", "MOM_QUIET")
    st = stack.new_state()
    hit = stack.evaluate_tick(prof, st, cur_bp=10.5, age_sec=30, atr_bp=4.0)
    assert hit["rule"] == "ATR_TAKE_PROFIT" and hit["maker"] and hit["book_bp"] == 10.0
    lv = stack.levels({**prof, "tp1_atr": 5.0}, 4.0)
    assert lv["tp1"] is None


def test_profit_only_shock_needs_break_even_armed():
    prof = _profile("FAMILY_GSB2_REGIME_SWITCHER", "REV_QUIET")
    st = stack.new_state()
    shock = {"r60_bp": 20.0, "ret60_bp": -15.0}
    assert stack.evaluate_tick(prof, st, cur_bp=-1.0, age_sec=30, atr_bp=4.0, shock=shock, side_sign=1) is None
    mom = _profile("FAMILY_GSB3_COMMITTED_FADE_REGIME", "MOM_QUIET")
    hit = stack.evaluate_tick(mom, stack.new_state(), cur_bp=-1.0, age_sec=30, atr_bp=4.0, shock=shock, side_sign=1)
    assert hit["rule"] == "VOL_SHOCK"


def test_indicator_flip_fires_once_on_an_adverse_change():
    prof = _profile("FAMILY_GSB3_COMMITTED_FADE_REGIME", "MOM_QUIET")
    st = stack.new_state()
    bars = [{"available_ts": 100.0, "score": 1, "prev_score": 1}]
    assert stack.evaluate_tick(prof, st, cur_bp=1.0, age_sec=30, atr_bp=4.0, bars=bars, side_sign=1) is None
    bars.append({"available_ts": 280.0, "score": -1, "prev_score": 1})
    hit = stack.evaluate_tick(prof, st, cur_bp=1.0, age_sec=31, atr_bp=4.0, bars=bars, side_sign=1)
    assert hit["rule"] == "INDICATOR_FLIP"


def test_missing_atr_defaults_to_four_bp():
    assert stack.atr_or_default(None) == 4.0
    assert stack.levels(_profile("FAMILY_GS01_XV_PREMIUM_ATR_TP", "ALL"), None)["tp"] == 10.0


def _fills(lane, n, bp, hours=1.0, now=None):
    now = now or time.time()
    return [{"research_lane": lane, "shared_ai_call_id": f"{lane}-{i}", "net_pnl_usd": bp * 25.0 / 1e4,
             "close_ts": now - i * 3600 * hours, "exit_reason": "GS_ATR_TAKE_PROFIT", "leverage": 100,
             "margin_usdt": 0.25, "max_pnl_pct": 0.0} for i in range(n)]


def test_gs_verdict_harm_kill_and_day21_outcomes():
    lane = "FAMILY_GS03_CVD_DIV_TAKER"
    now = time.time()
    rep = tpc.build_report(trades=_fills(lane, 35, -5.0, now=now), registry=cfg.ACTIVE_TILE_REGISTRY,
                           tile_order=cfg.ACTIVE_TILE_ORDER, now_ts=now)
    v = rep["pre_registered"][lane]["verdict"]
    assert v["status"] == "KILL" and "HARM" in v["kill_reasons"]
    later = now + 22 * 86400
    few = tpc.build_report(trades=_fills(lane, 10, 5.0, now=later), registry=cfg.ACTIVE_TILE_REGISTRY,
                           tile_order=cfg.ACTIVE_TILE_ORDER, now_ts=later)
    assert few["pre_registered"][lane]["verdict"]["status"] == "INSUFFICIENT"
    rows = [dict(r, net_pnl_usd=(6.0 + (i % 3)) * 25.0 / 1e4) for i, r in enumerate(_fills(lane, 40, 0, now=later))]
    good = tpc.build_report(trades=rows, registry=cfg.ACTIVE_TILE_REGISTRY, tile_order=cfg.ACTIVE_TILE_ORDER,
                            now_ts=later)
    v = good["pre_registered"][lane]["verdict"]
    assert v["status"] == "PASS_FORWARD" and v["promotion_checks"]["beats_offline_random_control"] == "OFFLINE"


def test_breakeven_lock_never_books_below_lock_floor():
    """Once armed, a gapped tick below the lock still books at lock_bp (never negative)."""
    prof = _profile("FAMILY_GS01_XV_PREMIUM_ATR_TP", "ALL")
    st = stack.new_state()
    lock = float(prof.get("lock_bp", 1.0))
    stack.evaluate_tick(prof, st, cur_bp=max(float(prof.get("be_floor") or 6), 8.0), age_sec=5, atr_bp=4.0)
    assert st["be_armed_age"] is not None
    hit = stack.evaluate_tick(prof, st, cur_bp=-0.47, age_sec=6, atr_bp=4.0)
    assert hit["rule"] == "BREAKEVEN_LOCK" and hit["book_bp"] == lock >= 0
