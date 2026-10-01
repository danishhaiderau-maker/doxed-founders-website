"""Paired Tile 1-4 comparison on identical signals and pre-registered verdicts."""
import json

import pytest

import tile_paired_comparison as tpc
from combo_pathway_config import ACTIVE_TILE_ORDER, ACTIVE_TILE_REGISTRY

T1, T2, T3, T4 = ACTIVE_TILE_ORDER
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


def test_tiles_pair_only_on_signals_both_filled():
    rows = []
    for i in range(12):
        ts = T0 + i * 4 * 3600
        rows += [_fill(T1, f"c{i}", -2.0, ts), _fill(T2, f"c{i}", 1.0, ts), _fill(T3, f"c{i}", 0.5, ts),
                 _fill(T4, f"c{i}", 2.0, ts, reason="PATH_END_60M")]
    rows.append(_fill(T1, "solo", -9.0, T0))
    rows.append(_fill(T2, "other-lane-only", 4.0, T0))
    rows.append(_fill("FAMILY_ATR_TRAIL", "c0", 99.0, T0))
    report = _report(rows)
    assert report["tile_order"] == [T1, T2, T3, T4]
    assert report["tiles"][T1]["fills"] == 13
    pairs = {(p["control"], p["challenger"]): p for p in report["paired"]}
    t2_vs_t1 = pairs[(T1, T2)]
    assert t2_vs_t1["paired_signals"] == 12
    assert t2_vs_t1["mean_difference_bp"] == pytest.approx(3.0)
    assert t2_vs_t1["difference_ci95_bp"] == [pytest.approx(3.0), pytest.approx(3.0)]
    assert t2_vs_t1["unpaired_control_fills"] == 1 and t2_vs_t1["unpaired_challenger_fills"] == 1
    assert pairs[(T2, T3)]["mean_difference_bp"] == pytest.approx(-0.5)
    assert report["all_tiles_paired"]["signals_filled_by_every_tile"] == 12
    assert report["all_tiles_paired"]["per_tile_ev_bp"] == {T1: -2.0, T2: 1.0, T3: 0.5, T4: 2.0}
    assert pairs[(T1, T4)]["mean_difference_bp"] == pytest.approx(4.0)
    assert set(report["pre_registered"]) == {T2, T3, T4}
    assert report["pre_registered"][T4]["control_meaning"].startswith("AI's own")
    json.dumps(report, allow_nan=False)


def test_k1_kills_a_tile_whose_per_fill_ev_upper_ci_is_below_zero():
    rows = [_fill(T2, f"c{i}", -3.0 + (i % 3) * 0.1, T0 + i * 1800) for i in range(160)]
    verdict = _report(rows)["pre_registered"][T2]["verdict"]
    assert verdict["status"] == "KILL"
    assert "K1_PER_FILL_EV_UPPER_CI_BELOW_ZERO" in verdict["kill_reasons"]


def test_k3_stop_failure_and_k4_drawdown():
    rows = [_fill(T3, f"h{i}", -30.0, T0 + i * 60, reason="PHYSICAL_HARD_STOP_30PCT") for i in range(3)]
    rows += [_fill(T3, f"d{i}", -40.0, T0 + 600 + i * 60) for i in range(20)]
    verdict = _report(rows)["pre_registered"][T3]["verdict"]
    assert {"K3_STOP_FAILURE", "K4_DRAWDOWN"} <= set(verdict["kill_reasons"])


def test_lock_overshoot_beyond_ten_bp_is_a_stop_failure():
    row = _fill(T2, "o1", -5.0, T0, reason="PROFIT_LOCK_LADDER", dir="LONG",
                entry=65000.0, policy_stop_price=65032.5, exit_price=65032.5 - 65.0 * 1.1)
    report = _report([row])
    assert report["tiles"][T2]["max_lock_or_stop_overshoot_bp"] == pytest.approx(11.0)
    assert "K3_STOP_FAILURE" in report["pre_registered"][T2]["verdict"]["kill_reasons"]


def test_k5_time_box_and_young_tiles_keep_collecting():
    rows = [_fill(T2, "c1", 1.0, T0)]
    young = _report(rows, now=tpc._ts("2026-10-02T00:00:00Z"))["pre_registered"][T2]["verdict"]
    assert young["status"] == "COLLECTING" and young["kill_reasons"] == []
    old = _report(rows, now=tpc._ts("2026-10-23T00:00:00Z"))["pre_registered"][T2]["verdict"]
    assert "K5_TIME_BOX_INCONCLUSIVE" in old["kill_reasons"]


def test_promotion_requires_every_pre_registered_check():
    rows = []
    for i in range(420):
        ts = tpc._ts("2026-10-01T08:00:00Z") + i * 3600
        rows.append(_fill(T1, f"c{i}", -1.0 + (i % 5) * 0.2, ts))
        rows.append(_fill(T2, f"c{i}", 2.0 + (i % 5) * 0.2, ts))
    entry = _report(rows, now=tpc._ts("2026-10-20T00:00:00Z"))["pre_registered"][T2]
    checks = entry["verdict"]["promotion_checks"]
    assert checks["min_fills"] and checks["min_days"] and checks["per_fill_ev_lower_ci95_gt_0"]
    assert checks["both_halves_positive"] and checks["beats_control_paired"]
    assert entry["verdict"]["deflated_sharpe"] is not None
    assert entry["verdict"]["status"] == (
        "PROMOTION_ELIGIBLE_FOR_OWNER_REVIEW" if checks["deflated_sharpe"] else "COLLECTING"
    )


def test_deflated_sharpe_penalises_more_trials():
    values = [3.0, -2.5, 1.0, -1.8, 2.2, -1.5, 0.4, -0.9] * 10
    one = tpc.deflated_sharpe(values, trials=1, sr_variance=None)
    three = tpc.deflated_sharpe(values, trials=3, sr_variance=0.05)
    assert 0.0 < three < one < 1.0


def _fade(i, bp, *, ts=None, reason="PATH_END_60M"):
    return _fill(T4, f"f{i}", bp, T0 + i * 3 * 3600 if ts is None else ts, reason=reason)


def test_tile4_k1_kills_after_forty_trades_down_forty_cents():
    rows = [_fade(i, -4.1) for i in range(40)]
    verdict = _report(rows)["pre_registered"][T4]["verdict"]
    assert verdict["status"] == "KILL"
    assert "K1_NET_LOSS_AFTER_40" in verdict["kill_reasons"]
    assert "K2_NOT_POSITIVE_AFTER_80" not in verdict["kill_reasons"]


def test_tile4_k2_and_k3_single_trade_beyond_sixty_bp():
    rows = [_fade(i, 0.0 if i else -61.0, reason="PHYSICAL_HARD_STOP_40PCT" if not i else "PATH_END_60M")
            for i in range(80)]
    report = _report(rows)
    verdict = report["pre_registered"][T4]["verdict"]
    assert {"K2_NOT_POSITIVE_AFTER_80", "K3_STOP_FAILURE"} <= set(verdict["kill_reasons"])
    assert report["tiles"][T4]["worst_fill_bp"] == pytest.approx(-61.0)
    assert report["tiles"][T4]["max_hard_stops_in_rolling_50"] == 1


def test_tile4_promotion_needs_150_trades_and_no_dominant_two_hour_window():
    spread_out = [_fade(i, 3.0 + (i % 5) * 0.2) for i in range(150)]
    verdict = _report(spread_out, now=T0 + 150 * 3 * 3600)["pre_registered"][T4]["verdict"]
    assert verdict["status"] == "PROMOTION_ELIGIBLE_FOR_OWNER_REVIEW", verdict
    assert all(verdict["promotion_checks"].values())
    burst = [_fade(i, 0.5) for i in range(149)] + [_fade(999, 400.0, ts=T0 + 10)]
    verdict = _report(burst, now=T0 + 150 * 3 * 3600)["pre_registered"][T4]["verdict"]
    assert verdict["promotion_checks"]["no_2h_window_dominates"] is False
    assert verdict["status"] != "PROMOTION_ELIGIBLE_FOR_OWNER_REVIEW"


def test_tile4_time_box_kills_after_fourteen_days_without_promotion():
    rows = [_fade(i, 1.0) for i in range(10)]
    registered = tpc._ts(ACTIVE_TILE_REGISTRY[T4]["pre_registration"]["registered_utc"])
    verdict = _report(rows, now=registered + 14.5 * 86400)["pre_registered"][T4]["verdict"]
    assert verdict["kill_reasons"] == ["K5_TIME_BOX_INCONCLUSIVE"]
