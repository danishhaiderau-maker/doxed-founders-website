"""B1 + B2 + GS-03 on the same 3 m CVD trigger (freeze21b B2 defect).

B2 declares no tile-level ``signal_clock`` (shared-AI tile that also rides the
bar clock), so its CVD attempts stamped ``PER_SECOND_CROSS_VENUE_EVALUATOR``
while GS-03/B1 stamped the bar-close clock.  The shared episode receipt
collided (``PRE_ENTRY_FEATURE_RECEIPT_COLLISION``) and every B2 CVD attempt was
dropped as ``PRE_ENTRY_EVIDENCE_UNAVAILABLE``.
"""
import json
import time

import pytest

import bot
import research_v3_bridge as bridge
from combo_pathway_config import (
    ACTIVE_TILE_ORDER, ACTIVE_TILE_REGISTRY, BAR_CLOSE_SIGNAL_CLOCK, CROSS_VENUE_SIGNAL_CLOCK,
    EVALUATOR_SIGNAL_CLOCKS, evaluator_loop_lanes,
)
from research.research_v3_report import join_pre_entry_feature_receipts

GS03 = "FAMILY_GS03_CVD_DIV_TAKER"
B1 = "FAMILY_GSB1_CVD_DIV_REGIME"
B2 = "FAMILY_GSB2_REGIME_SWITCHER"
# Tiles triggered only by the shared AI call (no evaluator, no own clock).
SHARED_AI_ONLY = {
    "FAMILY_COMMITTED_FADE_TAKER_90", "FAMILY_NOTRADE_FOLLOW_TAKER_60", "FAMILY_RANDOM_CONTROL_TAKER_90",
    "FAMILY_GS02_NOTRADE_REGIME_ENTRY", "FAMILY_GS04_NOTRADE_ATR_TP", "FAMILY_GSB3_COMMITTED_FADE_REGIME",
    "FAMILY_GS06_COMMITTED_FADE_ATR_TP",
}
EVALUATOR_SOURCES = {"CVD_DIVERGENCE_3M", "CROSS_VENUE_PREMIUM", "CROSS_VENUE_LEAD"}


def _clock(lane):
    policy = bot._patient_chase_policy(lane)
    return bot._xvl_trigger_signal_clock(policy, policy.make_evaluator()) if hasattr(policy, "make_evaluator") \
        else bot._xvl_trigger_signal_clock(policy, bot._xvl.LeadEvaluator)


def test_cvd_lanes_stamp_one_bar_close_clock():
    assert {_clock(lane) for lane in (GS03, B1, B2)} == {BAR_CLOSE_SIGNAL_CLOCK}


def test_no_evaluator_driven_tile_lacks_a_signal_clock():
    loop = set(evaluator_loop_lanes())
    assert loop | SHARED_AI_ONLY == set(ACTIVE_TILE_ORDER) and not (loop & SHARED_AI_ONLY)
    for lane in loop:
        spec = ACTIVE_TILE_REGISTRY[lane]
        clock = _clock(lane)
        assert clock in EVALUATOR_SIGNAL_CLOCKS, lane
        if spec.get("signal_clock"):
            assert clock == spec["signal_clock"], lane
        elif spec["entry_policy"].get("bar_clock_trigger"):
            assert clock == BAR_CLOSE_SIGNAL_CLOCK, lane
        else:
            pytest.fail(f"{lane}: evaluator lane without a tile clock or bar_clock_trigger")
    for lane in SHARED_AI_ONLY:
        entry = ACTIVE_TILE_REGISTRY[lane]["entry_policy"]
        assert entry.get("direction_source") not in EVALUATOR_SOURCES, lane
        assert not entry.get("bar_clock_trigger") and not ACTIVE_TILE_REGISTRY[lane].get("signal_clock"), lane


def test_premium_lanes_keep_the_per_second_clock():
    for lane in ("FAMILY_PREMIUM_REVERSION_60M", "FAMILY_GS01_XV_PREMIUM_ATR_TP"):
        assert _clock(lane) == CROSS_VENUE_SIGNAL_CLOCK


def _source(call_id, signal_ts, clock):
    trigger = {"trigger_id": call_id, "evaluated_ts": signal_ts, "side": "LONG", "bar_close_ts": signal_ts - 1,
               "cvd_divergence_score": 1, "bar_atr_bp": 4.9, "bar_atr_pct_rank": 95.4, "bar_adx": 21.0,
               "bar_spread_bp": 1.64, "bar_ok": True}
    features = bot._stamp_feature_capture({"cvd_trigger": trigger, "signal_clock": clock}, signal_ts)
    return {"trade_id": call_id, "shared_ai_call_id": call_id, "shared_ai_call_ts_epoch": signal_ts,
            "raw_direction": "LONG", "executed_direction": "LONG", "feature_snapshot_at_signal": features}


def _decide(tmp_path, lane, source):
    policy = bot._patient_chase_policy(lane)
    return bridge.dual_write_lane_decision(
        source, lane=lane, policy_decision="ACCEPT", execution_disposition="ORDER_ELIGIBLE",
        exact_reason="CVD_TRIGGER_AND_POLICY_PASS", epoch_id="epoch-shared-cvd-test",
        data_dir=str(tmp_path), lane_policy={"policy_id": policy.POLICY_ID},
    )


def _receipts(tmp_path):
    store = bridge.V3EvidenceStore(str(tmp_path), epoch_id="epoch-shared-cvd-test")
    with store.ledger_path("pre_entry_features").open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def test_b1_b2_gs03_same_trigger_all_reach_a_verified_decision(tmp_path):
    signal_ts = time.time() - 1.0
    call_id = f"cvd-{int(signal_ts)}-long"
    for lane in (GS03, B1, B2):
        receipt = _decide(tmp_path, lane, _source(call_id, signal_ts, _clock(lane)))
        assert receipt["store_verification"]["passed"] is True, lane
        assert len([w for w in receipt["writes"] if w.get("ledger") == "pre_entry_features"]) == 1
    rows = _receipts(tmp_path)
    assert len(rows) == 1 and "receipt_scope" not in rows[0]  # one shared episode receipt
    assert rows[0]["features"]["signal_clock"] == BAR_CLOSE_SIGNAL_CLOCK


def test_divergent_lane_payload_is_keyed_per_lane_not_a_collision(tmp_path):
    signal_ts = time.time() - 1.0
    call_id = f"cvd-{int(signal_ts)}-long"
    _decide(tmp_path, GS03, _source(call_id, signal_ts, BAR_CLOSE_SIGNAL_CLOCK))
    _decide(tmp_path, B1, _source(call_id, signal_ts, BAR_CLOSE_SIGNAL_CLOCK))
    # The pre-fix B2 payload (per-second fallback clock) used to raise here.
    receipt = _decide(tmp_path, B2, _source(call_id, signal_ts, CROSS_VENUE_SIGNAL_CLOCK))
    assert receipt["store_verification"]["passed"] is True
    rows = _receipts(tmp_path)
    lane_rows = [row for row in rows if row.get("receipt_scope") == "LANE"]
    assert len(rows) == 2 and len(lane_rows) == 1
    assert lane_rows[0]["research_lane"] == B2
    assert lane_rows[0]["record_id"].endswith(f":lane:{B2}")
    assert lane_rows[0]["episode_receipt_record_id"] == rows[0]["record_id"]
    # Idempotent: the same lane re-writing the same payload is a duplicate.
    assert _decide(tmp_path, B2, _source(call_id, signal_ts, CROSS_VENUE_SIGNAL_CLOCK))[
        "store_verification"]["passed"] is True
    assert len(_receipts(tmp_path)) == 2
    # The analyzer still joins exactly one receipt per opportunity.
    opportunity = {"episode_id": rows[0]["episode_id"], "opportunity_id": rows[0]["opportunity_id"],
                   "signal_ts": signal_ts}
    joined, coverage = join_pre_entry_feature_receipts([opportunity], rows)
    assert "PRE_ENTRY_FEATURE_RECEIPT_AMBIGUOUS" not in joined[0]["pre_entry_feature_blockers"]
    assert coverage["receipt_joined_opportunities"] == 1


def test_lane_less_writer_still_fails_closed_on_a_collision(tmp_path):
    store = bridge.V3EvidenceStore(str(tmp_path), epoch_id="epoch-shared-cvd-test")
    identity = {"episode_id": "episode-collision", "shared_ai_call_id": "c1", "symbol": "BTCUSD"}
    kwargs = dict(store=store, source={}, identity=identity, causal_ids={"opportunity_id": "opportunity:x"},
                  signal_ts=time.time(), segment_refs=[])
    bridge._pre_entry_features_receipt(features={"a": 1}, **kwargs)
    with pytest.raises(ValueError, match="PRE_ENTRY_FEATURE_RECEIPT_COLLISION"):
        bridge._pre_entry_features_receipt(features={"a": 2}, **kwargs)
