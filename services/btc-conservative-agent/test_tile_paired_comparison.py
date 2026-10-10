"""Paired tile comparison on identical signals and pre-registered verdicts."""
import json

import pytest

import tile_paired_comparison as tpc
from combo_pathway_config import ACTIVE_TILE_ORDER, ACTIVE_TILE_REGISTRY

CFT = "FAMILY_COMMITTED_FADE_TAKER_90"     # H-A
XVS = "FAMILY_PREMIUM_REVERSION_60M"        # H-C (cross-venue clock tile)
RND = "FAMILY_RANDOM_CONTROL_TAKER_90"      # control (yardstick, no orders)
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
    assert set(tpc.VERDICT_RULES) == set(tpc.EXTRA_STATS) == {"tile_pre_registration_freeze21_v1",
                                                              "tile_pre_registration_gs20261004_v1"}
    assert set(tpc.VERDICT_RULES) >= {
        ACTIVE_TILE_REGISTRY[lane]["pre_registration"]["schema"]
        for lane in ACTIVE_TILE_ORDER if ACTIVE_TILE_REGISTRY[lane].get("pre_registration")
    }
    json.dumps(report, allow_nan=False)


def test_active_roster_pairs_every_shared_call_tile_but_not_the_clock_tile():
    rows = [_fill(XVS, f"xvs-{i}", 2.0, T0 + i * 600, reason="PATH_END_60M") for i in range(4)]
    rows += [_fill(RND, f"b{i}", 1.0, T0 + i * 600) for i in range(4)]
    rows += [_fill(CFT, f"b{i}", 2.0, T0 + i * 600, reason="PATH_END_90M") for i in range(4)]
    report = _report(rows)
    assert report["tile_order"] == list(ACTIVE_TILE_ORDER)
    assert report["tile_order"][:4] == [CFT, XVS, RND, "FAMILY_GS01_XV_PREMIUM_ATR_TP"]
    # Shared-call tiles pair (H-A, control, B2, B3, GS-06, Danish, Fade Pool);
    # evaluator-clock tiles (H-C, GS-01, B1, GS-07) never do.
    assert report["all_tiles_paired"]["paired_tiles"] == [
        CFT, RND, "FAMILY_GSB2_REGIME_SWITCHER", "FAMILY_GSB3_COMMITTED_FADE_REGIME",
        "FAMILY_GS06_COMMITTED_FADE_ATR_TP", "FAMILY_DANISH_REGIME_ROUTER", "FAMILY_FADE_POOL"]
    assert {(p["control"], p["challenger"]) for p in report["paired"]} >= {
        (CFT, RND), (CFT, "FAMILY_GSB2_REGIME_SWITCHER")}
    assert not any(XVS in (p["control"], p["challenger"]) for p in report["paired"])
    assert set(report["pre_registered"]) == set(ACTIVE_TILE_ORDER)
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


def test_random_control_is_a_yardstick_not_a_trial_and_pairs_with_h_a():
    rows = []
    for i in range(6):
        ts = T0 + i * 3600
        rows += [_fill(RND, f"b{i}", -1.0, ts), _fill(CFT, f"b{i}", 3.0, ts)]
    report = _report(rows)
    assert report["baseline_lane"] is None
    # Ten hypotheses (H-A, H-C, GS-01, B1..B3, GS-06, GS-07, Danish, Fade Pool, GS-07 V07); the control is not a trial.
    assert report["deflated_sharpe_trials"] == 11
    verdict = report["pre_registered"][CFT]["verdict"]
    assert report["pre_registered"][CFT]["control_lane"] == RND
    assert verdict["vs_control_mean_difference_bp"] == pytest.approx(4.0)
    control = report["pre_registered"][RND]["verdict"]
    assert control["role"] == "CONTROL" and control["status"] == "CONTROL_COLLECTING"
    assert control["execution_cost_bp"] == pytest.approx(-1.0)


def test_cluster_ci_alpha_widens_the_interval():
    rows = [(T0 + i * 3600, float((i * 7) % 11 - 5)) for i in range(60)]
    lo95, hi95 = tpc._cluster_ci(rows, cluster_sec=3600)
    lo_b, hi_b = tpc._cluster_ci(rows, cluster_sec=3600, alpha=0.05 / 3)
    assert lo_b <= lo95 < hi95 <= hi_b


@pytest.mark.parametrize("lane, k1, k4", [(CFT, 80, 3.0), (XVS, 80, 3.0)])
def test_hypothesis_kill_rules_count_distinct_hours_not_fills(lane, k1, k4):
    pre = ACTIVE_TILE_REGISTRY[lane]["pre_registration"]
    assert pre["schema"] == "tile_pre_registration_freeze21_v1" and pre["role"] == "HYPOTHESIS"
    assert pre["kill"]["k1_after_distinct_hours"] == k1 and pre["kill"]["k4_max_drawdown_usd"] == k4
    # k1 fills inside a handful of hours are not enough: n_eff counts hours.
    crowded = [_fill(lane, f"c{i}", -0.5, T0 + (i % 5) * 3600 + i, reason="PATH_END") for i in range(k1)]
    assert _report(crowded)["pre_registered"][lane]["verdict"]["kill_reasons"] == []
    spread = [_fill(lane, f"z{i}", -0.5, T0 + i * 3600, reason="PATH_END") for i in range(k1)]
    verdict = _report(spread)["pre_registered"][lane]["verdict"]
    assert f"K1_MEAN_NOT_POSITIVE_AFTER_{k1}_HOURS" in verdict["kill_reasons"] and verdict["status"] == "KILL"
    bad = [_fill(lane, "b0", -61.0, T0, reason="PHYSICAL_HARD_STOP_40PCT")]
    assert "K3_STOP_OR_STALE_FEED_FAILURE" in _report(bad)["pre_registered"][lane]["verdict"]["kill_reasons"]


def _day21(lane):
    return tpc._ts(ACTIVE_TILE_REGISTRY[lane]["pre_registration"]["registered_utc"]) + 21.5 * 86400


def test_day21_pass_needs_target_bonferroni_ci_and_beating_the_control():
    start = tpc._ts(ACTIVE_TILE_REGISTRY[CFT]["pre_registration"]["registered_utc"])
    rows = []
    for i in range(320):
        ts = start + i * 3600 + 60
        rows += [_fill(CFT, f"w{i}", 6.0 + (i % 4) * 0.5, ts, reason="PATH_END", dir="LONG" if i % 2 else "SHORT"),
                 _fill(RND, f"w{i}", -1.0 + (i % 3), ts, reason="PATH_END")]
    verdict = _report(rows, now=_day21(CFT))["pre_registered"][CFT]["verdict"]
    assert verdict["n_eff_distinct_hours"] == 320
    assert verdict["promotion_checks"]["n_eff_target"] and verdict["promotion_checks"]["beats_control"]
    assert verdict["status"] == "DAY21_PASS" and verdict["day21_status"] == "DAY21_PASS"
    early = _report(rows, now=_day21(CFT) - 5 * 86400)["pre_registered"][CFT]["verdict"]
    assert early["status"] == "COLLECTING" and early["day21_status"] == "PENDING"
    assert early["day21_status_if_decided_now"] == "DAY21_PASS"


def test_day21_fail_and_inconclusive():
    start = tpc._ts(ACTIVE_TILE_REGISTRY[XVS]["pre_registration"]["registered_utc"])
    flat = [_fill(XVS, f"f{i}", 0.5 if i % 2 else -0.6, start + i * 3600, reason="PATH_END") for i in range(60)]
    assert _report(flat, now=_day21(XVS))["pre_registered"][XVS]["verdict"]["status"] == "DAY21_FAIL"
    thin = [_fill(XVS, f"t{i}", 4.0 + (i % 3), start + i * 3600, reason="PATH_END") for i in range(40)]
    assert _report(thin, now=_day21(XVS))["pre_registered"][XVS]["verdict"]["status"] == "DAY21_INCONCLUSIVE"


def test_profitable_random_control_flags_the_fill_model():
    start = tpc._ts(ACTIVE_TILE_REGISTRY[RND]["pre_registration"]["registered_utc"])
    rows = [_fill(RND, f"r{i}", 5.0 + (i % 3), start + i * 3600, reason="PATH_END") for i in range(100)]
    assert _report(rows)["pre_registered"][RND]["verdict"]["status"] == "FILL_MODEL_SUSPECT"


def test_hypothesis_tile_sessions_use_the_registered_session_map():
    stats = _report([_fill(CFT, "w0", 6.0, T0, reason="PATH_END")])["tiles"][CFT]
    assert stats["session_hours_utc"] == {"ASIA": [0, 8], "EU": [8, 16]}
    assert stats["bonferroni_alpha"] == pytest.approx(0.05 / 3)
