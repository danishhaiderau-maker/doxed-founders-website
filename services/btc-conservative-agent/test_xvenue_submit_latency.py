"""Latency regression for IMMEDIATE (taker-at-signal) cross-venue tiles.

Live paper fills arrived ~9 s after the trigger: the pre-signal market segment
parsed the whole active 1 s tape (up to 20 MB) inside every attempt, and the
attempt thread inherited the evaluator's nice 5. The pre-registered gate is a
median signal->fill <= 2 s.
"""
import json
import os
import time

import pytest

import bot
import paper_policy_family_premium_reversion_60m as xvs_policy
import research_v3_bridge as bridge
import tile_paired_comparison as tpc

TAPE_ROWS = 30_000


def _write_tape(directory, end_ts, rows=TAPE_ROWS):
    start = int(end_ts) - rows + 1
    with open(os.path.join(directory, bridge.MICROSTRUCTURE_TAPE_NAME), "w", encoding="utf-8") as handle:
        for ts in range(start, int(end_ts) + 1):
            handle.write(json.dumps({
                "bucket_ts": ts, "last": 65000.0 + (ts % 7), "bid": 64999.5, "ask": 65000.5,
                "bid_qty": 0.8, "ask_qty": 1.1, "trades": ts % 3, "high": 65001.0, "low": 64999.0,
            }) + "\n")
    return start


@pytest.mark.parametrize("age_sec", [0.6, 30.4, 900.2, 6 * 3600.7, TAPE_ROWS - 100.5])
def test_recent_segment_matches_the_full_scan(tmp_path, age_sec):
    end = 1_790_000_000.0
    _write_tape(str(tmp_path), end)
    signal_ts = end - age_sec
    start = signal_ts - bridge.PRE_SIGNAL_CONTEXT_SEC
    full = bridge._paper_market_segment(str(tmp_path), start_ts=start, end_ts=signal_ts)
    tail = bridge._recent_market_segment(str(tmp_path), start_ts=start, end_ts=signal_ts)
    assert tail == full
    assert full[1]["row_count"] > 0


def test_recent_segment_reads_a_bounded_tail_of_a_large_tape(tmp_path):
    end = 1_790_000_000.0
    _write_tape(str(tmp_path), end)
    size = os.path.getsize(tmp_path / bridge.MICROSTRUCTURE_TAPE_NAME)
    rows, coverage = bridge._recent_market_segment(
        str(tmp_path), start_ts=end + 0.6 - bridge.PRE_SIGNAL_CONTEXT_SEC, end_ts=end + 0.6,
    )
    assert coverage["requested_bounds_complete"] and len(rows) == bridge.PRE_SIGNAL_CONTEXT_SEC
    assert bridge.LAST_RECENT_SEGMENT_SCAN["bytes_scanned"] <= bridge.RECENT_SEGMENT_INITIAL_TAIL_BYTES * 2
    assert size > 10 * bridge.LAST_RECENT_SEGMENT_SCAN["bytes_scanned"]


def test_pre_signal_lane_decision_never_full_scans_the_tape(tmp_path, monkeypatch):
    end = time.time()
    _write_tape(str(tmp_path), end)

    def forbidden(*args, **kwargs):
        raise AssertionError("full-tape scan on the pre-signal hot path")

    monkeypatch.setattr(bridge, "_paper_market_segment", forbidden)
    receipt = bridge.dual_write_lane_decision(
        {"trade_id": "xvs-latency-1", "shared_ai_call_id": "xvs-latency-1",
         "shared_ai_call_ts_epoch": end + 0.6, "raw_direction": "LONG",
         "feature_snapshot_at_signal": {"xvs_trigger": {"evaluated_ts": end + 0.6}}},
        lane=xvs_policy.LANE, policy_decision="ACCEPT", execution_disposition="ORDER_ELIGIBLE",
        exact_reason="XVS_TRIGGER_AND_POLICY_PASS", epoch_id="epoch-latency-test",
        data_dir=str(tmp_path), lane_policy={"policy_id": xvs_policy.POLICY_ID},
    )
    assert receipt["store_verification"]["passed"] is True


def test_attempt_workers_start_before_the_evaluator_nices_itself(monkeypatch):
    order = []
    monkeypatch.setattr(bot, "_XVL_ATTEMPT_QUEUES", {})
    monkeypatch.setattr(bot, "XVL_EVALUATOR_ENABLED", True)
    monkeypatch.setattr(bot, "evaluator_loop_lanes", lambda: (xvs_policy.LANE,))
    real_start = bot._xvl_start_attempt_workers
    monkeypatch.setattr(bot, "_xvl_start_attempt_workers", lambda lanes: order.append("workers") or real_start(lanes))
    monkeypatch.setattr(bot, "_xvl_lower_thread_priority", lambda: order.append("nice"))
    monkeypatch.setattr(bot.shutdown_event, "is_set", lambda: True)
    bot.xvl_evaluator_loop()
    assert order == ["workers", "nice"]
    assert set(bot._XVL_ATTEMPT_QUEUES) == {xvs_policy.LANE}


def test_gate_hands_the_trigger_to_the_lane_worker_without_spawning(monkeypatch):
    from queue import Queue
    queue = Queue(maxsize=1)
    monkeypatch.setattr(bot, "_XVL_ATTEMPT_QUEUES", {xvs_policy.LANE: queue})
    monkeypatch.setattr(bot, "_xvl_lane_runtime", {})
    monkeypatch.setattr(bot, "is_research_lane_enabled", lambda lane: True)
    monkeypatch.setattr(bot.threading, "Thread", lambda *a, **k: pytest.fail("spawned a niced thread"))
    now = time.time()
    trigger = {"trigger_id": "xvs-9", "side": "LONG", "evaluated_ts": now - 0.6}
    bot._xvl_maybe_attempt_paper(xvs_policy.LANE, {"status": "TRIGGER"}, trigger, now)
    assert queue.get_nowait()["trigger_id"] == "xvs-9"
    row = bot._xvl_lane_runtime[xvs_policy.LANE]["latency_open"]["xvs-9"]
    assert row["signal_ts"] == pytest.approx(now - 0.6) and row["gate_ts"] == pytest.approx(now)


def _sample(lane, trigger_id, signal_ts, submit_after, fill_after):
    bot._xvl_latency_mark(lane, trigger_id, "attempt_ts", signal_ts + 0.1, signal_ts=signal_ts)
    bot._xvl_latency_mark(lane, trigger_id, "evidence_ts", signal_ts + 0.3)
    bot._xvl_latency_mark_order(lane, {"shared_ai_call_id": trigger_id}, "submit_ts", signal_ts + submit_after)
    bot._xvl_latency_mark_order(lane, {"shared_ai_call_id": trigger_id}, "fill_ts", signal_ts + fill_after)


@pytest.mark.parametrize("fill_after,expected", [(1.4, "OK"), (9.3, "SLOW")])
def test_latency_gate_uses_the_pre_registered_two_second_median(monkeypatch, fill_after, expected):
    monkeypatch.setattr(bot, "_xvl_lane_runtime", {})
    base = time.time() - 3600
    for i in range(bot.XVL_LATENCY_MIN_FILLS):
        _sample(xvs_policy.LANE, f"xvs-{i}", base + i * 10, fill_after - 0.4, fill_after)
    latency = bot.xvl_evaluator_snapshot()["lanes"][xvs_policy.LANE]["latency"]
    assert latency["target_median_signal_to_fill_s"] == 2.0
    assert latency["status"] == expected
    assert latency["stages"]["signal_to_fill"]["p50_s"] == pytest.approx(fill_after, abs=1e-3)
    assert latency["stages"]["attempt_to_evidence"]["p50_s"] == pytest.approx(0.2, abs=1e-3)
    assert bot._xvl_lane_runtime[xvs_policy.LANE]["latency_open"] == {}


def test_latency_gate_waits_for_enough_fills_and_ignores_non_xvenue_lanes(monkeypatch):
    monkeypatch.setattr(bot, "_xvl_lane_runtime", {})
    _sample(xvs_policy.LANE, "xvs-1", time.time() - 60, 0.8, 9.0)
    bot._xvl_latency_mark("AI_SCAN", "scan-1", "attempt_ts", signal_ts=time.time())
    assert "AI_SCAN" not in bot._xvl_lane_runtime
    latency = bot.xvl_evaluator_snapshot()["lanes"][xvs_policy.LANE]["latency"]
    assert latency["status"] == "INSUFFICIENT_FILLS"


def test_end_to_end_attempt_stamps_every_stage_on_a_large_tape(tmp_path, monkeypatch):
    now = time.time()
    _write_tape(str(tmp_path), now)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bot, "_xvl_lane_runtime", {})
    monkeypatch.setattr(bot, "is_research_data_collection", lambda: True)
    monkeypatch.setattr(bot, "is_research_lane_enabled", lambda lane: True)
    monkeypatch.setattr(bot, "invert_signal_active", lambda: False)
    monkeypatch.setattr(bot, "ensure_lane_signal_capacity", lambda lane: True)
    monkeypatch.setattr(bot, "_record_adaptive_entry_decision", lambda lane, d: None)
    monkeypatch.setattr(bridge, "_paper_market_segment",
                        lambda *a, **k: pytest.fail("full-tape scan on the attempt path"))

    def spawn(ctx, ai, edge, features, lane, reason):
        bot._xvl_latency_mark_order(lane, ctx, "submit_ts")
        bot._xvl_latency_mark_order(lane, ctx, "fill_ts")

    monkeypatch.setattr(bot, "_spawn_combo_lane", spawn)
    for key, value in {"bid": 64999.0, "ask": 65000.0, "bbo_ts": now - 0.2, "price": 64999.5}.items():
        monkeypatch.setitem(bot.state, key, value)
    trigger = {"trigger_id": "xvs-e2e", "side": "LONG", "evaluated_ts": time.time(), "lead_bp": 9.0,
               "anchor_bucket_ts": int(now) - 1}
    assert bot._xvl_paper_attempt_inner(xvs_policy.LANE, trigger) == "ORDER_ELIGIBLE"
    sample = list(bot._xvl_lane_runtime[xvs_policy.LANE]["latency_samples"])[0]
    assert sample["signal_ts"] <= sample["attempt_ts"] <= sample["evidence_ts"] <= sample["submit_ts"] <= sample["fill_ts"]
    assert bridge.LAST_RECENT_SEGMENT_SCAN["bytes_scanned"] <= bridge.RECENT_SEGMENT_INITIAL_TAIL_BYTES * 2


def _trade(lane, call_ts, fill_after, hold=60.0, pnl=0.01):
    close = call_ts + fill_after + hold
    return {
        "research_lane": lane, "shared_ai_call_id": f"{lane[:3]}-{call_ts}", "net_pnl_usd": pnl,
        "shared_ai_call_ts": call_ts, "close_ts": close, "outcome_duration_sec": hold,
        "exit_reason": "PATH_END_1M", "dir": "LONG", "entry": 65000.0,
        "signal_age_sec": 0.3,
    }


def test_analyzer_measures_signal_to_fill_from_the_trigger_not_signal_creation():
    row = tpc._fill_row(_trade(xvs_policy.LANE, 1_790_921_868.604, 8.684))
    assert row["signal_to_fill_sec"] == pytest.approx(8.684, abs=1e-6)
    pre = bot.COMBO_LANE_SPECS[xvs_policy.LANE]["pre_registration"]
    fills = [tpc._fill_row(_trade(xvs_policy.LANE, 1_790_921_000.0 + i * 600, lat))
             for i, lat in enumerate((1.0, 1.5, 9.0))]
    assert tpc._session_side_extra_stats(fills, pre)["median_signal_to_fill_sec"] == 1.5
    fills_slow = [tpc._fill_row(_trade(xvs_policy.LANE, 1_790_921_000.0 + i * 600, lat))
                  for i, lat in enumerate((9.0, 9.4))]
    assert tpc._session_side_extra_stats(fills_slow, pre)["median_signal_to_fill_sec"] == pytest.approx(9.2)
