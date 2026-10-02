"""Paired tile comparison on identical signals and pre-registered verdicts."""
import json

import pytest

import tile_paired_comparison as tpc
from combo_pathway_config import ACTIVE_TILE_ORDER, ACTIVE_TILE_REGISTRY

T1, T2, XVL, XVP = ACTIVE_TILE_ORDER
A, B = "SYNTHETIC_TILE_A", "SYNTHETIC_TILE_B"
T0 = 1_790_000_000.0
BP = 0.0025  # 1 bp of $25 notional


def _fill(lane, call, bp, ts, reason="INITIAL_ATR_STOP", **extra):
    row = {"research_lane": lane, "shared_ai_call_id": call, "net_pnl_usd": bp * BP,
           "close_ts": ts, "exit_reason": reason, "margin_usdt": 0.25, "leverage": 100}
    row.update(extra)
    return row


def _report(rows, now=None):
    return tpc.build_report(trades=rows, registry=ACTIVE_TILE_REGISTRY, tile_order=ACTIVE_TILE_ORDER,
                            now_ts=T0 + 3600 if now is None else now)


def _synthetic_report(rows):
    registry = {A: {"label": "A"}, B: {"label": "B"}, T1: ACTIVE_TILE_REGISTRY[T1]}
    return tpc.build_report(trades=rows, registry=registry, tile_order=(A, B, T1), now_ts=T0 + 3600)


def test_tiles_pair_only_on_signals_both_filled():
    rows = []
    for i in range(12):
        ts = T0 + i * 4 * 3600
        rows += [_fill(A, f"c{i}", -2.0, ts), _fill(B, f"c{i}", 1.0, ts),
                 _fill(T1, f"c{i}", 2.0, ts, reason="PATH_END_60M")]
    rows.append(_fill(A, "solo", -9.0, T0))
    rows.append(_fill(B, "other-lane-only", 4.0, T0))
    rows.append(_fill("FAMILY_ATR_TRAIL", "c0", 99.0, T0))
    report = _synthetic_report(rows)
    assert report["tile_order"] == [A, B, T1]
    assert report["tiles"][A]["fills"] == 13
    pairs = {(p["control"], p["challenger"]): p for p in report["paired"]}
    b_vs_a = pairs[(A, B)]
    assert b_vs_a["paired_signals"] == 12
    assert b_vs_a["mean_difference_bp"] == pytest.approx(3.0)
    assert b_vs_a["difference_ci95_bp"] == [pytest.approx(3.0), pytest.approx(3.0)]
    assert b_vs_a["unpaired_control_fills"] == 1 and b_vs_a["unpaired_challenger_fills"] == 1
    assert report["all_tiles_paired"]["signals_filled_by_every_tile"] == 12
    assert report["all_tiles_paired"]["per_tile_ev_bp"] == {A: -2.0, B: 1.0, T1: 2.0}
    assert pairs[(A, T1)]["mean_difference_bp"] == pytest.approx(4.0)
    assert set(report["pre_registered"]) == {T1}
    assert report["pre_registered"][T1]["control_lane"] is None
    assert report["pre_registered"][T1]["control_meaning"].startswith("AI's own")
    json.dumps(report, allow_nan=False)


def test_registered_tiles_report_without_a_paired_control():
    rows = [_fill(T1, f"c{i}", 1.0, T0 + i * 3600, reason="PATH_END_60M") for i in range(5)]
    report = tpc.build_report(trades=rows, registry={T1: ACTIVE_TILE_REGISTRY[T1]}, tile_order=(T1,),
                              now_ts=T0 + 3600)
    assert report["tile_order"] == [T1]
    assert report["paired"] == []
    assert report["all_tiles_paired"]["paired_tiles"] == [T1]
    assert report["tiles"][T1]["fills"] == 5
    assert report["pre_registered"][T1]["verdict"]["status"] == "COLLECTING"
    assert set(tpc.VERDICT_RULES) == {
        "tile_pre_registration_trade_count_v1", "tile_pre_registration_xvl_v1",
        "tile_pre_registration_committed_fade_v1", "tile_pre_registration_xvp_v1",
    }
    assert set(tpc.VERDICT_RULES) >= {ACTIVE_TILE_REGISTRY[lane]["pre_registration"]["schema"] for lane in ACTIVE_TILE_ORDER}
    json.dumps(report, allow_nan=False)


def test_committed_fade_pairs_against_trend_fade_on_the_same_calls():
    rows = []
    for i in range(12):
        ts = T0 + i * 4 * 3600
        rows += [_fill(T1, f"c{i}", 2.0, ts, reason="PATH_END_60M"),
                 _fill(T2, f"c{i}", 5.0, ts, reason="PATH_END_60M")]
    rows += [_fill(T1, f"nt{i}", 1.0, T0 + i, reason="PATH_END_60M") for i in range(4)]
    report = _report(rows)
    assert report["tile_order"] == [T1, T2, XVL, XVP]
    assert report["all_tiles_paired"]["paired_tiles"] == [T1, T2]
    assert not any({XVL, XVP} & {p["control"], p["challenger"]} for p in report["paired"])
    pairs = {(p["control"], p["challenger"]): p for p in report["paired"]}
    assert pairs[(T1, T2)]["paired_signals"] == 12
    assert pairs[(T1, T2)]["mean_difference_bp"] == pytest.approx(3.0)
    assert pairs[(T1, T2)]["unpaired_control_fills"] == 4
    assert set(report["pre_registered"]) == {T1, T2, XVL, XVP}
    assert report["pre_registered"][T1]["control_lane"] is None
    assert report["pre_registered"][T2]["control_lane"] == T1
    json.dumps(report, allow_nan=False)


def test_lock_overshoot_beyond_ten_bp_is_measured():
    row = _fill(A, "o1", -5.0, T0, reason="PROFIT_LOCK_LADDER", dir="LONG",
                entry=65000.0, policy_stop_price=65032.5, exit_price=65032.5 - 65.0 * 1.1)
    report = _synthetic_report([row])
    assert report["tiles"][A]["max_lock_or_stop_overshoot_bp"] == pytest.approx(11.0)


def test_deflated_sharpe_penalises_more_trials():
    values = [3.0, -2.5, 1.0, -1.8, 2.2, -1.5, 0.4, -0.9] * 10
    one = tpc.deflated_sharpe(values, trials=1, sr_variance=None)
    three = tpc.deflated_sharpe(values, trials=3, sr_variance=0.05)
    assert 0.0 < three < one < 1.0


def _fade(i, bp, *, ts=None, reason="PATH_END_60M"):
    return _fill(T1, f"f{i}", bp, T0 + i * 3 * 3600 if ts is None else ts, reason=reason)


def test_tile1_k1_kills_after_forty_trades_down_forty_cents():
    rows = [_fade(i, -4.1) for i in range(40)]
    verdict = _report(rows)["pre_registered"][T1]["verdict"]
    assert verdict["status"] == "KILL"
    assert "K1_NET_LOSS_AFTER_40" in verdict["kill_reasons"]
    assert "K2_NOT_POSITIVE_AFTER_80" not in verdict["kill_reasons"]


def test_tile1_k2_and_k3_single_trade_beyond_sixty_bp():
    rows = [_fade(i, 0.0 if i else -61.0, reason="PHYSICAL_HARD_STOP_40PCT" if not i else "PATH_END_60M")
            for i in range(80)]
    report = _report(rows)
    verdict = report["pre_registered"][T1]["verdict"]
    assert {"K2_NOT_POSITIVE_AFTER_80", "K3_STOP_FAILURE"} <= set(verdict["kill_reasons"])
    assert report["tiles"][T1]["worst_fill_bp"] == pytest.approx(-61.0)
    assert report["tiles"][T1]["max_hard_stops_in_rolling_50"] == 1


def test_tile1_promotion_needs_150_trades_and_no_dominant_two_hour_window():
    spread_out = [_fade(i, 3.0 + (i % 5) * 0.2) for i in range(150)]
    verdict = _report(spread_out, now=T0 + 150 * 3 * 3600)["pre_registered"][T1]["verdict"]
    assert verdict["status"] == "PROMOTION_ELIGIBLE_FOR_OWNER_REVIEW", verdict
    assert all(verdict["promotion_checks"].values())
    burst = [_fade(i, 0.5) for i in range(149)] + [_fade(999, 400.0, ts=T0 + 10)]
    verdict = _report(burst, now=T0 + 150 * 3 * 3600)["pre_registered"][T1]["verdict"]
    assert verdict["promotion_checks"]["no_2h_window_dominates"] is False
    assert verdict["status"] != "PROMOTION_ELIGIBLE_FOR_OWNER_REVIEW"


def test_committed_fade_kill_rules():
    losers = [_fill(T2, f"l{i}", -1.0 if i % 2 else 1.0, T0 + i * 3600, reason="PATH_END_60M") for i in range(150)]
    verdict = _report(losers)["pre_registered"][T2]["verdict"]
    assert "K1_HIT_RATE_BELOW_52_AFTER_150" in verdict["kill_reasons"]
    gap = [_fill(T2, "g0", -61.0, T0, reason="PHYSICAL_HARD_STOP_40PCT")]
    assert "K3_STOP_FAILURE" in _report(gap)["pre_registered"][T2]["verdict"]["kill_reasons"]
    trend_day = [_fill(T2, f"t{i}", -20.0, T0 + i * 600, reason="PATH_END_60M", entry=65000.0 * (1 + 0.004 * i))
                 for i in range(6)]
    verdict = _report(trend_day)["pre_registered"][T2]["verdict"]
    assert "K2_TREND_DAY_MEAN_BELOW_MINUS_15" in verdict["kill_reasons"]


def test_committed_fade_promotion_needs_control_beat_and_regime_days():
    rows = []
    for i in range(160):
        ts = T0 + i * 3 * 3600
        price = 65000.0 * (1 + 0.02 * ((i // 8) % 3 - 1) * ((i % 8) / 7))
        rows += [_fill(T2, f"w{i}", 14.0 + (i % 5) * 0.2, ts, reason="PATH_END_60M", entry=price),
                 _fill(T1, f"w{i}", 2.0, ts, reason="PATH_END_60M")]
    report = _report(rows, now=T0 + 160 * 3 * 3600)
    verdict = report["pre_registered"][T2]["verdict"]
    assert verdict["promotion_checks"]["beats_control"] is True
    assert verdict["promotion_checks"]["min_fills"] is True
    assert report["tiles"][T2]["regime_method"] == "ENTRY_PRICE_PROXY"
    assert verdict["status"] in {"PROMOTION_ELIGIBLE_FOR_OWNER_REVIEW", "COLLECTING"}


def test_premium_tile_needs_shadow_parity_before_promotion_and_kills_on_bad_trade():
    winners = [_fill(XVP, f"p{i}", 2.0 + (i % 3) * 0.1, T0 + i * 1500, reason="PATH_END_1M",
                     dir="LONG" if i % 2 else "SHORT") for i in range(520)]
    verdict = _report(winners, now=T0 + 520 * 1500)["pre_registered"][XVP]["verdict"]
    checks = verdict["promotion_checks"]
    assert checks["min_fills"] and checks["min_utc_days"] and checks["sessions"]
    assert checks["both_sides_non_negative"] and checks["no_day_dominates"]
    assert checks["shadow_5s_delay_positive"] is False and checks["replay_parity"] is False
    assert verdict["status"] == "COLLECTING"
    bad = [_fill(XVP, "b0", -46.0, T0, reason="PHYSICAL_HARD_STOP_40PCT")]
    assert "K3_STOP_OR_STALE_FEED_FAILURE" in _report(bad)["pre_registered"][XVP]["verdict"]["kill_reasons"]
    flat = [_fill(XVP, f"z{i}", -0.1, T0 + i * 600, reason="PATH_END_1M") for i in range(300)]
    assert "K1_MEAN_NOT_POSITIVE_AFTER_300" in _report(flat)["pre_registered"][XVP]["verdict"]["kill_reasons"]


def test_tile1_time_box_kills_after_fourteen_days_without_promotion():
    rows = [_fade(i, 1.0) for i in range(10)]
    registered = tpc._ts(ACTIVE_TILE_REGISTRY[T1]["pre_registration"]["registered_utc"])
    verdict = _report(rows, now=registered + 14.5 * 86400)["pre_registered"][T1]["verdict"]
    assert verdict["kill_reasons"] == ["K5_TIME_BOX_INCONCLUSIVE"]



def test_ai_tiles_split_by_prompt_input_revision_and_clock_tiles_are_not():
    challenger_rows = [
        {"row_kind": "CALL", "shared_ai_call_id": "old"},
        {"row_kind": "CALL", "shared_ai_call_id": "new", "prompt_input_revision": "shared_direction_inputs_r2_20261002"},
        {"row_kind": "MARKOUT", "shared_ai_call_id": "new", "prompt_input_revision": "ignored"},
    ]
    revisions = tpc.call_input_revisions(challenger_rows)
    assert revisions == {"old": tpc.PRE_REVISION_INPUTS, "new": "shared_direction_inputs_r2_20261002"}
    rows = [_fill(T1, "old", 4.0, T0), _fill(T1, "new", -2.0, T0 + 60), _fill(T1, "missing", 1.0, T0 + 120),
            _fill(XVL, "xvl-1", 3.0, T0)]
    report = tpc.build_report(trades=rows, registry=ACTIVE_TILE_REGISTRY, tile_order=ACTIVE_TILE_ORDER,
                              now_ts=T0 + 3600, call_revisions=revisions)
    cohorts = report["input_revision_cohorts"]["lanes"]
    assert cohorts[T1] == {
        tpc.PRE_REVISION_INPUTS: {"n": 1, "mean_bp": 4.0},
        "shared_direction_inputs_r2_20261002": {"n": 1, "mean_bp": -2.0},
        tpc.UNJOINED_INPUT_REVISION: {"n": 1, "mean_bp": 1.0},
    }
    assert XVL not in cohorts and XVP not in cohorts
