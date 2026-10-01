"""Paired tile comparison on identical signals and pre-registered verdicts."""
import json

import pytest

import tile_paired_comparison as tpc
from combo_pathway_config import ACTIVE_TILE_ORDER, ACTIVE_TILE_REGISTRY

T1, XVL = ACTIVE_TILE_ORDER
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
    report = _report(rows)
    assert report["tile_order"] == [T1, XVL]
    assert report["paired"] == []
    assert report["all_tiles_paired"]["paired_tiles"] == [T1]
    assert report["pre_registered"][XVL]["control_lane"] is None
    assert report["tiles"][T1]["fills"] == 5
    assert report["pre_registered"][T1]["verdict"]["status"] == "COLLECTING"
    assert set(tpc.VERDICT_RULES) == {"tile_pre_registration_trade_count_v1", "tile_pre_registration_xvl_v1"}
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


def test_tile1_time_box_kills_after_fourteen_days_without_promotion():
    rows = [_fade(i, 1.0) for i in range(10)]
    registered = tpc._ts(ACTIVE_TILE_REGISTRY[T1]["pre_registration"]["registered_utc"])
    verdict = _report(rows, now=registered + 14.5 * 86400)["pre_registered"][T1]["verdict"]
    assert verdict["kill_reasons"] == ["K5_TIME_BOX_INCONCLUSIVE"]
