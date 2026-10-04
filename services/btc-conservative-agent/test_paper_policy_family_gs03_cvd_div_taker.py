"""Dedicated contract for FREEZE21B GS-03: 3-minute CVD-divergence reversion, indicator only."""
import time

import paper_policy_family_gs03_cvd_div_taker as policy
import regime_bars_3m as bars3m
from combo_pathway_config import BAR_CLOSE_SIGNAL_CLOCK, evaluator_loop_lanes, is_evaluator_clock_lane
from gs_tile_contract_support import assert_dashboard, assert_paper_only_registry_tile, bar, decide
from regime_adaptive_binding import CvdDivergenceEvaluator


def test_registry_owns_a_bar_clock_tile_without_ai():
    spec = assert_paper_only_registry_tile(policy, index=6, prefix="gs3", hypothesis_id="GS-20261004-03",
                                           bonferroni_k=4)
    assert spec["signal_clock"] == BAR_CLOSE_SIGNAL_CLOCK and spec["uses_shared_ai_direction"] is False
    assert is_evaluator_clock_lane(policy.LANE) and policy.LANE in evaluator_loop_lanes()
    assert spec["entry_policy"]["direction_source"] == "CVD_DIVERGENCE_3M"


def test_never_admits_a_shared_ai_call():
    view = policy.lane_admission({"decision": "APPROVE", "direction": "LONG", "raw_decision": "LONG",
                                  "raw_direction": "LONG", "long_score": 70, "short_score": 30},
                                 {"applied": True, "accepted": True, "effective_direction": "LONG"})
    assert view["accepted"] is False and view["reason"] == "LANE_ADMISSION_NOT_A_SHARED_AI_TILE"


class _Engine(bars3m.RegimeBars3m):
    def __init__(self):
        super().__init__()
        self.bar = None

    def latest(self, at_ts=None):
        return self.bar


def test_evaluator_fires_once_per_fresh_divergence_bar_and_skips_the_boot_bar():
    engine = _Engine()
    ev = CvdDivergenceEvaluator(engine=engine)
    now = time.time()
    engine.bar = {"seq": 1, "close_ts": now - 2, "available_ts": now - 1, "events": {bars3m.DIVERGENCE: 1}}
    assert ev.step(now=now)[1] is None  # first bar after boot is never traded
    engine.bar = {"seq": 2, "close_ts": now + 178, "available_ts": now + 179, "events": {bars3m.DIVERGENCE: -1}}
    status, trigger, _ = ev.step(now=now + 180)
    assert trigger["side"] == "SHORT" and trigger["trigger_id"].startswith("cvd-")
    assert ev.step(now=now + 181)[1] is None
    engine.bar = {"seq": 3, "close_ts": now + 358, "available_ts": now + 359, "events": {bars3m.DIVERGENCE: 0}}
    assert ev.step(now=now + 360)[1] is None
    engine.bar = {"seq": 4, "close_ts": now + 538, "available_ts": now + 539, "events": {bars3m.DIVERGENCE: 1}}
    assert ev.step(now=now + 600)[0]["status"] == "BAR_NOT_FRESH"


def test_cvd_trigger_is_a_taker():
    d = decide(policy, engine_bar=bar(), ai_feature={"cvd_trigger_id": "cvd-1-long"})
    assert d["action"] == "TAKER" and d["trigger_kind"] == "CVD_DIVERGENCE_3M"
    assert isinstance(policy.make_evaluator(), CvdDivergenceEvaluator)


def test_dashboard_discloses_the_rule():
    payload = assert_dashboard(policy, "GS-20261004-03")
    assert payload["entry"]["cadence_label"] == "No AI — 3-minute bar-close CVD evaluator"
