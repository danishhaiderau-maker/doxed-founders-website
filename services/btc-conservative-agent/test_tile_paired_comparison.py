"""Paired tile comparison on identical signals and pre-registered verdicts."""
import json

import pytest

import tile_paired_comparison as tpc
from combo_pathway_config import ACTIVE_TILE_ORDER, ACTIVE_TILE_REGISTRY

DCF, DCN, DCA, CBL, CFM, CFT, NTF, XVS = ACTIVE_TILE_ORDER
DANISH = (DCF, DCN, DCA)
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
    registry = {A: {"label": "A"}, B: {"label": "B"}, XVS: ACTIVE_TILE_REGISTRY[XVS]}
    return tpc.build_report(trades=rows, registry=registry, tile_order=(A, B, XVS), now_ts=T0 + 3600)


def test_tiles_pair_only_on_signals_both_filled():
    rows = []
    for i in range(12):
        ts = T0 + i * 4 * 3600
        rows += [_fill(A, f"c{i}", -2.0, ts), _fill(B, f"c{i}", 1.0, ts),
                 _fill(XVS, f"xvs-{i}", 2.0, ts, reason="PATH_END_60M")]
    rows.append(_fill(A, "solo", -9.0, T0))
    rows.append(_fill(B, "other-lane-only", 4.0, T0))
    rows.append(_fill("FAMILY_ATR_TRAIL", "c0", 99.0, T0))
    report = _synthetic_report(rows)
    assert report["tile_order"] == [A, B, XVS]
    assert report["tiles"][A]["fills"] == 13
    pairs = {(p["control"], p["challenger"]): p for p in report["paired"]}
    b_vs_a = pairs[(A, B)]
    assert b_vs_a["paired_signals"] == 12
    assert b_vs_a["mean_difference_bp"] == pytest.approx(3.0)
    assert b_vs_a["difference_ci95_bp"] == [pytest.approx(3.0), pytest.approx(3.0)]
    assert b_vs_a["unpaired_control_fills"] == 1 and b_vs_a["unpaired_challenger_fills"] == 1
    assert report["all_tiles_paired"]["paired_tiles"] == [A, B]
    assert report["all_tiles_paired"]["signals_filled_by_every_tile"] == 12
    assert report["all_tiles_paired"]["per_tile_ev_bp"] == {A: -2.0, B: 1.0}
    assert not any(XVS in (p["control"], p["challenger"]) for p in report["paired"])
    assert set(report["pre_registered"]) == {XVS}
    assert report["pre_registered"][XVS]["control_lane"] is None
    json.dumps(report, allow_nan=False)


def test_registered_tiles_report_without_a_paired_control():
    rows = [_fill(XVS, f"xvs-{i}", 1.0, T0 + i * 3600, reason="PATH_END_60M") for i in range(5)]
    report = tpc.build_report(trades=rows, registry={XVS: ACTIVE_TILE_REGISTRY[XVS]}, tile_order=(XVS,),
                              now_ts=T0 + 3600)
    assert report["tile_order"] == [XVS]
    assert report["paired"] == []
    assert report["tiles"][XVS]["fills"] == 5
    assert report["pre_registered"][XVS]["verdict"]["status"] == "COLLECTING"
    assert set(tpc.VERDICT_RULES) == {
        "tile_pre_registration_committed_fade_maker_v1", "tile_pre_registration_hypothesis_v1",
    }
    assert set(tpc.VERDICT_RULES) >= {
        ACTIVE_TILE_REGISTRY[lane]["pre_registration"]["schema"]
        for lane in ACTIVE_TILE_ORDER if ACTIVE_TILE_REGISTRY[lane].get("pre_registration")
    }
    json.dumps(report, allow_nan=False)


def test_active_roster_pairs_every_shared_call_tile_but_not_the_clock_tile():
    rows = [_fill(XVS, f"xvs-{i}", 2.0, T0 + i * 600, reason="PATH_END_60M") for i in range(4)]
    rows += [_fill(CBL, f"b{i}", 1.0, T0 + i * 600) for i in range(4)]
    rows += [_fill(DCF, f"b{i}", 2.0, T0 + i * 600, reason="PATH_END_90M") for i in range(4)]
    report = _report(rows)
    assert report["tile_order"] == [DCF, DCN, DCA, CBL, CFM, CFT, NTF, XVS]
    assert report["all_tiles_paired"]["paired_tiles"] == [DCF, DCN, DCA, CBL, CFM, CFT, NTF]
    assert {(p["control"], p["challenger"]) for p in report["paired"]} >= {(DCF, DCN), (DCF, DCA), (CBL, CFM)}
    assert not any(XVS in (p["control"], p["challenger"]) for p in report["paired"])
    assert set(report["pre_registered"]) == {DCF, DCN, DCA, CFM, CFT, NTF, XVS}
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


def test_committed_fade_maker_kills_and_needs_shadow_before_promotion():
    winners = [_fill(CFM, f"m{i}", 6.0 + (i % 4) * 0.5, T0 + i * 3000, reason="PATH_END_90M",
                     dir="LONG" if i % 2 else "SHORT") for i in range(200)]
    verdict = _report(winners, now=T0 + 200 * 3000)["pre_registered"][CFM]["verdict"]
    checks = verdict["promotion_checks"]
    assert checks["min_fills"] and checks["min_utc_days"] and checks["sessions"]
    assert checks["per_fill_ev_lower_ci95_1h_gt_0"] and checks["both_sides_non_negative"]
    assert checks["shadow_5s_delay_positive"] is False and checks["replay_parity"] is False
    assert verdict["status"] == "COLLECTING"
    flat = [_fill(CFM, f"z{i}", -0.5, T0 + i * 3000, reason="PATH_END_90M") for i in range(80)]
    assert "K1_MEAN_NOT_POSITIVE_AFTER_80" in _report(flat)["pre_registered"][CFM]["verdict"]["kill_reasons"]
    bad = [_fill(CFM, "b0", -61.0, T0, reason="PHYSICAL_HARD_STOP_40PCT")]
    assert "K3_STOP_OR_STALE_FEED_FAILURE" in _report(bad)["pre_registered"][CFM]["verdict"]["kill_reasons"]


def test_session_follow_time_box_kills_after_its_registered_days_without_promotion():
    rows = [_fill(XVS, f"xvs-{i}", 1.0, T0 + i * 3600, reason="PATH_END_60M") for i in range(10)]
    pre = ACTIVE_TILE_REGISTRY[XVS]["pre_registration"]
    registered = tpc._ts(pre["registered_utc"])
    now = registered + (pre["kill"]["k5_max_days_without_promotion"] + 0.5) * 86400
    verdict = _report(rows, now=now)["pre_registered"][XVS]["verdict"]
    assert verdict["kill_reasons"] == ["K5_TIME_BOX_INCONCLUSIVE"]


def test_danish_tiles_have_no_time_box_and_use_the_owner_kill_rule():
    for lane in DANISH:
        pre = ACTIVE_TILE_REGISTRY[lane]["pre_registration"]
        assert pre["kill"]["k5_max_days_without_promotion"] is None and pre["kill"]["k2_after_fills"] is None
        rows = [_fill(lane, f"d{i}", 1.0, T0 + i * 3600, reason="PATH_END_90M") for i in range(10)]
        verdict = _report(rows, now=tpc._ts(pre["registered_utc"]) + 400 * 86400)["pre_registered"][lane]["verdict"]
        assert verdict["kill_reasons"] == [] and verdict["status"] == "COLLECTING"


def test_ai_tiles_split_by_prompt_input_revision_and_clock_tiles_are_not():
    challenger_rows = [
        {"row_kind": "CALL", "shared_ai_call_id": "old"},
        {"row_kind": "CALL", "shared_ai_call_id": "new", "prompt_input_revision": "shared_direction_inputs_r2_20261002"},
        {"row_kind": "MARKOUT", "shared_ai_call_id": "new", "prompt_input_revision": "ignored"},
    ]
    revisions = tpc.call_input_revisions(challenger_rows)
    assert revisions == {"old": tpc.PRE_REVISION_INPUTS, "new": "shared_direction_inputs_r2_20261002"}
    rows = [_fill(A, "old", 4.0, T0), _fill(A, "new", -2.0, T0 + 60), _fill(A, "missing", 1.0, T0 + 120),
            _fill(XVS, "xvs-1", 3.0, T0)]
    registry = {A: {"label": "A"}, **ACTIVE_TILE_REGISTRY}
    report = tpc.build_report(trades=rows, registry=registry, tile_order=(A, *ACTIVE_TILE_ORDER),
                              now_ts=T0 + 3600, call_revisions=revisions)
    cohorts = report["input_revision_cohorts"]["lanes"]
    assert cohorts[A] == {
        tpc.PRE_REVISION_INPUTS: {"n": 1, "mean_bp": 4.0},
        "shared_direction_inputs_r2_20261002": {"n": 1, "mean_bp": -2.0},
        tpc.UNJOINED_INPUT_REVISION: {"n": 1, "mean_bp": 1.0},
    }
    assert XVS not in cohorts


def test_continuous_baseline_is_the_yardstick_not_a_trial():
    rows = []
    for i in range(6):
        ts = T0 + i * 3600
        rows += [_fill(CBL, f"b{i}", 1.0, ts), _fill(A, f"b{i}", 3.0, ts)]
    registry = {**ACTIVE_TILE_REGISTRY, A: {"label": "A"}}
    order = (*ACTIVE_TILE_ORDER, A)
    report = tpc.build_report(trades=rows, registry=registry, tile_order=order, now_ts=T0 + 3600)
    assert report["baseline_lane"] == CBL
    assert report["deflated_sharpe_trials"] == len(order) - 1
    pairs = {p["challenger"]: p for p in report["vs_baseline"]}
    assert set(pairs) == {A, *DANISH, CFM, NTF, CFT}
    assert all(p["control"] == CBL for p in report["vs_baseline"])
    assert pairs[A]["paired_signals"] == 6
    assert pairs[A]["mean_difference_bp"] == pytest.approx(2.0)
    assert XVS not in pairs


@pytest.mark.parametrize("lane, k1, k4, k5", [
    (DCF, 80, 1.0, None), (DCN, 80, 1.0, None), (DCA, 80, 1.0, None),
    (NTF, 300, 3.0, 21), (XVS, 300, 1.0, 21), (CFT, 80, 1.0, 21),
])
def test_hypothesis_tiles_kill_rules_follow_their_registration(lane, k1, k4, k5):
    pre = ACTIVE_TILE_REGISTRY[lane]["pre_registration"]
    assert pre["schema"] == "tile_pre_registration_hypothesis_v1"
    assert pre["kill"]["k1_after_fills"] == k1 and pre["kill"]["k4_max_drawdown_usd"] == k4
    assert pre["kill"]["k5_max_days_without_promotion"] == k5
    flat = [_fill(lane, f"z{i}", -0.5, T0 + i * 3000, reason="PATH_END") for i in range(k1)]
    kills = _report(flat)["pre_registered"][lane]["verdict"]["kill_reasons"]
    assert f"K1_MEAN_NOT_POSITIVE_AFTER_{k1}" in kills
    bad = [_fill(lane, "b0", -61.0, T0, reason="PHYSICAL_HARD_STOP_40PCT")]
    assert "K3_STOP_OR_STALE_FEED_FAILURE" in _report(bad)["pre_registered"][lane]["verdict"]["kill_reasons"]


def test_hypothesis_tile_sessions_use_the_runtime_session_map_and_need_parity():
    n = ACTIVE_TILE_REGISTRY[CFT]["pre_registration"]["promotion"]["min_fills"]
    winners = [_fill(CFT, f"w{i}", 6.0 + (i % 4) * 0.5, T0 + i * 3000, reason="PATH_END",
                     dir="LONG" if i % 2 else "SHORT") for i in range(n + 50)]
    verdict = _report(winners, now=T0 + (n + 50) * 3000)["pre_registered"][CFT]["verdict"]
    checks = verdict["promotion_checks"]
    assert checks["min_fills"] and checks["min_utc_days"] and checks["sessions"]
    assert checks["per_fill_ev_lower_ci95_1h_gt_0"] and checks["both_halves_positive"]
    assert checks["replay_parity"] is False and verdict["status"] == "COLLECTING"
    stats = _report(winners)["tiles"][CFT]
    assert stats["session_hours_utc"] == {"ASIA": [0, 8], "EU": [8, 16]}
