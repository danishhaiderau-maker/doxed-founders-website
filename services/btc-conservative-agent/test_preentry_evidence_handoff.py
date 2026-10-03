"""Submit-first pre-entry evidence for the cross-venue signal-clock tiles.

Research rows written between a trigger and its paper order (V3 lane decision,
provisional multiverse event, duplicate-intent audit) are fsync'd to a journal
before the order and materialized afterwards. Every later evidence writer for
the same call/trade drains the earlier receipts first, so a terminal or
lifecycle row can never precede its provisional rows, and a crash before
materialization is replayed at restart.
"""
import inspect
import json
import math
import os
import time

import pytest

import ai_shadow_challengers as ai_shadow
import bot
import cross_venue_lead as xvl
import paper_policy_family_xvenue_lead as xvl_policy


class _HeldWorker:
    """Stands in for the background worker so tests control when receipts apply."""

    def __init__(self):
        self.submitted = []

    def submit(self, key, payload, source_ts=None):
        self.submitted.append(key)
        return True


@pytest.fixture
def queue(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bot, "PREENTRY_EVIDENCE_DEFERRAL_ENABLED", True)
    monkeypatch.setattr(bot, "_preentry_evidence_pending", bot.collections.OrderedDict())
    monkeypatch.setattr(bot, "_preentry_evidence_status", {
        **bot._preentry_evidence_status,
        **{k: 0 for k in ("enqueued", "applied", "epoch_preserved", "failures", "dead",
                          "journal_failures", "barrier_drains", "barrier_timeouts", "replayed",
                          "compactions", "sync_fallbacks")},
        "last_failure_ts": 0.0, "last_error": None,
    })
    monkeypatch.setattr(bot, "_preentry_evidence_journal_path",
                        lambda: str(tmp_path / "pre_entry_evidence_handoffs.jsonl"))
    monkeypatch.setattr(bot, "_collector_v22_epoch_id", lambda: "epoch-test")
    worker = _HeldWorker()
    monkeypatch.setattr(bot, "_get_preentry_evidence_worker", lambda: worker)
    calls = []
    monkeypatch.setattr(bot, "_write_v3_shared_lane_decision",
                        lambda lane, ai, ctx, features, **verdict: calls.append(("V3", ctx["shared_ai_call_id"])) or True)
    monkeypatch.setattr(bot, "_sync_order_multiverse",
                        lambda source, path_complete=False: calls.append(("MULTIVERSE", source["trade_id"])))
    monkeypatch.setattr(bot, "_safe_append_jsonl",
                        lambda path, row, **k: calls.append(("AUDIT", row["trade_id"])) or True)
    return {"path": tmp_path / "pre_entry_evidence_handoffs.jsonl", "calls": calls, "worker": worker}


def _journal(path):
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _enqueue_trade(call_id, trade_id):
    v3 = {"lane": xvl_policy.LANE, "ai": {"shared_ai_call_id": call_id},
          "ctx": {"shared_ai_call_id": call_id}, "features": {},
          "decision": {"policy_decision": "ACCEPT", "execution_disposition": "ORDER_ELIGIBLE",
                       "exact_reason": "XVL_TRIGGER_AND_POLICY_PASS"}}
    assert bot._enqueue_preentry_evidence("V3_LANE_DECISION", {call_id}, v3, lane=xvl_policy.LANE)
    signal = {"trade_id": trade_id, "shared_ai_call_id": call_id}
    assert bot._enqueue_preentry_evidence(
        "MULTIVERSE_PROVISIONAL", bot._preentry_evidence_keys(signal), {"signal": signal}, lane=xvl_policy.LANE,
    )


def test_receipt_is_durable_before_it_is_applied(queue):
    _enqueue_trade("xvl-1", "xvl-aaa")
    rows = _journal(queue["path"])
    assert [r["kind"] for r in rows] == ["V3_LANE_DECISION", "MULTIVERSE_PROVISIONAL"]
    assert all(r["schema"] == bot.PREENTRY_EVIDENCE_PENDING_SCHEMA and len(r["receipt_id"]) == 64 for r in rows)
    assert queue["calls"] == [] and len(queue["worker"].submitted) == 2


def test_later_evidence_drains_its_provisional_rows_first(queue, monkeypatch):
    _enqueue_trade("xvl-1", "xvl-aaa")
    _enqueue_trade("xvl-2", "xvl-bbb")
    monkeypatch.setattr(bot, "_promote_collector_v22_registered_order", lambda order, signal: None, raising=False)
    monkeypatch.setattr(bot, "dual_write_paper_order_intent",
                        lambda order, signal, **k: queue["calls"].append(("ORDER_INTENT", order["trade_id"])) or {})
    monkeypatch.setattr(bot, "_emit_genome_execution_event", lambda *a, **k: None)
    bot._write_pending_order_evidence({"payload": {
        "order": {"trade_id": "xvl-aaa", "shared_ai_call_id": "xvl-1"}, "signal": {}, "is_paper_entry": True,
    }})
    assert queue["calls"] == [("V3", "xvl-1"), ("MULTIVERSE", "xvl-aaa"), ("ORDER_INTENT", "xvl-aaa")]
    assert [r["kind"] for r in bot._preentry_evidence_pending.values()] == ["V3_LANE_DECISION", "MULTIVERSE_PROVISIONAL"]
    results = [r for r in _journal(queue["path"]) if r["schema"] == bot.PREENTRY_EVIDENCE_RESULT_SCHEMA]
    assert [r["status"] for r in results] == ["APPLIED", "APPLIED"]


def test_entry_resolution_waits_for_the_lane_decision(queue, monkeypatch):
    _enqueue_trade("xvl-1", "xvl-aaa")
    monkeypatch.setattr(bot, "dual_write_lane_entry_resolution",
                        lambda source, **k: queue["calls"].append(("RESOLUTION", k["entry_resolution"])))
    bot._append_v3_lane_entry_resolution({"shared_ai_call_id": "xvl-1"}, xvl_policy.LANE, "NO_ORDER", "TEST")
    assert queue["calls"][0] == ("V3", "xvl-1") and queue["calls"][-1] == ("RESOLUTION", "NO_ORDER")


def test_every_later_evidence_writer_calls_the_barrier():
    for name in ("_sync_order_multiverse", "_write_pending_order_evidence", "_write_fill_evidence_handoff",
                 "_refresh_collector_v22_registered_order_evidence", "_append_v3_lane_entry_resolution",
                 "close_position"):
        assert '"_preentry_evidence_barrier"' in inspect.getsource(getattr(bot, name)), name


def test_restart_replays_unapplied_receipts_once(queue):
    _enqueue_trade("xvl-1", "xvl-aaa")
    bot._preentry_evidence_pending.clear()
    assert bot._replay_preentry_evidence_handoffs() == 2
    assert queue["calls"] == [("V3", "xvl-1"), ("MULTIVERSE", "xvl-aaa")]
    assert bot._replay_preentry_evidence_handoffs() == 0
    assert len(queue["calls"]) == 2


def test_torn_final_row_is_isolated_and_the_rest_replays(queue):
    _enqueue_trade("xvl-1", "xvl-aaa")
    with open(queue["path"], "ab") as handle:
        handle.write(b'{"schema": "pre_entry_evidence_handoff_pending_v1", "receipt_id": "tor')
    bot._preentry_evidence_pending.clear()
    assert bot._replay_preentry_evidence_handoffs() == 2


def test_receipt_from_an_old_epoch_is_preserved_not_written(queue, monkeypatch):
    _enqueue_trade("xvl-1", "xvl-aaa")
    monkeypatch.setattr(bot, "_collector_v22_epoch_id", lambda: "epoch-next")
    assert bot._preentry_evidence_barrier({"trade_id": "xvl-aaa"})
    assert queue["calls"] == []
    results = [r for r in _journal(queue["path"]) if r["schema"] == bot.PREENTRY_EVIDENCE_RESULT_SCHEMA]
    assert {r["status"] for r in results} == {"EPOCH_MISMATCH_PRESERVED"}


def test_failed_materialization_turns_deferral_off(queue, monkeypatch):
    monkeypatch.setattr(bot, "_write_v3_shared_lane_decision", lambda *a, **k: False)
    _enqueue_trade("xvl-1", "xvl-aaa")
    for _ in range(bot.PREENTRY_EVIDENCE_MAX_ATTEMPTS):
        bot._preentry_evidence_barrier({"shared_ai_call_id": "xvl-1"})
    assert bot._preentry_evidence_status["dead"] == 1
    assert not bot._preentry_evidence_deferrable(xvl_policy.LANE)
    assert bot.preentry_evidence_snapshot()["health"] == "DEGRADED"
    # The multiverse row still lands; the dead receipt keeps its payload in the
    # journal but is never replayed after the trade's later evidence.
    assert queue["calls"] == [("MULTIVERSE", "xvl-aaa")]
    rows = _journal(queue["path"])
    assert any(r.get("kind") == "V3_LANE_DECISION" and r.get("payload") for r in rows)
    assert {r["status"] for r in rows if r["schema"] == bot.PREENTRY_EVIDENCE_RESULT_SCHEMA} == {"APPLIED", "DEAD_LETTERED"}
    bot._preentry_evidence_pending.clear()
    assert bot._replay_preentry_evidence_handoffs() == 0


def test_backlog_or_journal_failure_uses_the_synchronous_path(queue, monkeypatch):
    monkeypatch.setattr(bot, "PREENTRY_EVIDENCE_MAX_PENDING", 2)
    _enqueue_trade("xvl-1", "xvl-aaa")
    assert not bot._preentry_evidence_deferrable(xvl_policy.LANE)
    bot._preentry_evidence_barrier({"trade_id": "xvl-aaa"})
    assert bot._preentry_evidence_deferrable(xvl_policy.LANE)
    assert not bot._preentry_evidence_deferrable("AI_SCAN")
    monkeypatch.setattr(bot, "_append_durable_handoff_row", lambda *a, **k: False)
    assert not bot._enqueue_preentry_evidence("V3_LANE_DECISION", {"xvl-3"}, {}, lane=xvl_policy.LANE)
    assert not bot._preentry_evidence_deferrable(xvl_policy.LANE)


def _attempt_env(monkeypatch, order):
    monkeypatch.setattr(bot, "_xvl_lane_runtime", {})
    monkeypatch.setattr(bot, "is_research_data_collection", lambda: True)
    monkeypatch.setattr(bot, "is_research_lane_enabled", lambda lane: True)
    monkeypatch.setattr(bot, "invert_signal_active", lambda: False)
    monkeypatch.setattr(bot, "ensure_lane_signal_capacity", lambda lane: True)
    monkeypatch.setattr(bot, "_record_adaptive_entry_decision", lambda lane, d: None)

    def spawn(ctx, ai, edge, features, lane, reason):
        order.append("SUBMIT")
        bot._xvl_latency_mark_order(lane, ctx, "submit_ts")

    monkeypatch.setattr(bot, "_spawn_combo_lane", spawn)
    now = time.time()
    for key, value in {"bid": 64999.0, "ask": 65000.0, "bbo_ts": now - 0.2, "price": 64999.5}.items():
        monkeypatch.setitem(bot.state, key, value)
    return {"trigger_id": f"xvl-{now}", "side": "LONG", "evaluated_ts": now, "lead_bp": 9.0,
            "anchor_bucket_ts": int(now) - 1}


def test_attempt_submits_before_the_lane_decision_is_written(queue, monkeypatch):
    order = []
    trigger = _attempt_env(monkeypatch, order)

    def slow_v3(lane, ai, ctx, features, **verdict):
        time.sleep(0.5)
        order.append("V3")
        return True

    monkeypatch.setattr(bot, "_write_v3_shared_lane_decision", slow_v3)
    assert bot._xvl_paper_attempt_inner(xvl_policy.LANE, trigger) == "ORDER_ELIGIBLE"
    assert order == ["SUBMIT"]
    sample = list(bot._xvl_lane_runtime[xvl_policy.LANE]["latency_samples"])[0]
    assert sample["submit_ts"] - sample["attempt_ts"] < 0.25
    bot._preentry_evidence_barrier({"shared_ai_call_id": trigger["trigger_id"]})
    assert order == ["SUBMIT", "V3"]


def test_attempt_keeps_the_synchronous_fail_closed_path_when_not_deferrable(queue, monkeypatch):
    order = []
    trigger = _attempt_env(monkeypatch, order)
    monkeypatch.setattr(bot, "PREENTRY_EVIDENCE_DEFERRAL_ENABLED", False)
    monkeypatch.setattr(bot, "_write_v3_shared_lane_decision", lambda *a, **k: order.append("V3") or False)
    assert bot._xvl_paper_attempt_inner(xvl_policy.LANE, trigger) == "PRE_ENTRY_EVIDENCE_UNAVAILABLE"
    assert order == ["V3"]


def test_rejected_verdicts_stay_synchronous(queue, monkeypatch):
    order = []
    trigger = _attempt_env(monkeypatch, order)
    monkeypatch.setattr(bot, "_patient_chase_policy", lambda lane: type("P", (), {
        "decide_entry": staticmethod(lambda **k: {"action": "STAND_ASIDE", "reason": "TEST"}),
    })())
    monkeypatch.setattr(bot, "_write_v3_shared_lane_decision", lambda *a, **k: order.append("V3") or True)
    assert bot._xvl_paper_attempt_inner(xvl_policy.LANE, trigger) == "ADAPTIVE_TEST"
    assert order == ["V3"] and not bot._preentry_evidence_pending


def test_process_signal_defers_only_the_provisional_multiverse_write():
    source = inspect.getsource(bot.process_signal)
    assert '"MULTIVERSE_PROVISIONAL"' in source
    assert "_sync_order_multiverse(signal, path_complete=False)" in source
    audit = inspect.getsource(bot._record_duplicate_intent_audit)
    assert 'decision == "ALLOW_DISTINCT"' in audit and '"DUPLICATE_INTENT_AUDIT"' in audit


def test_tick_hands_off_the_trigger_before_writing_shadow_rows(monkeypatch):
    order = []

    class FakeEvaluator:
        SHADOW_FILE = "unused.jsonl"

        def step(self, **kwargs):
            return {"status": xvl.STATUS_TRIGGER}, {"trigger_id": "xvl-1"}, [{"trigger_id": "xvl-0"}]

    monkeypatch.setattr(bot, "_XVL_EVALUATORS", {xvl_policy.LANE: FakeEvaluator()})
    monkeypatch.setattr(bot, "_xvl_maybe_attempt_paper", lambda lane, e, t, now: order.append(("ATTEMPT", t["trigger_id"])))
    monkeypatch.setattr(bot, "_xvl_append", lambda row, path: order.append(("APPEND", row["trigger_id"])))
    bot._xvl_tick(time.time(), live={})
    assert order == [("ATTEMPT", "xvl-1"), ("APPEND", "xvl-1"), ("APPEND", "xvl-0")]


def _ring_with(buckets):
    ring = ai_shadow.TapeRing()
    for ts in buckets:
        ring.append_bucket({"bucket_ts": ts, "fresh": True, "valid_bbo": True, "bid": 65000.0, "ask": 65001.0})
    return ring


def test_anchor_readiness_needs_both_the_tape_and_the_collector_close(monkeypatch):
    anchor = 1_790_000_000
    monkeypatch.setattr(bot, "_AI_SHADOW_TAPE", _ring_with([anchor - 1]))
    assert not bot._xvl_anchor_ready({"history_end_ts": anchor}, anchor)
    monkeypatch.setattr(bot, "_AI_SHADOW_TAPE", _ring_with([anchor - 1, anchor]))
    assert not bot._xvl_anchor_ready({"history_end_ts": anchor - 1}, anchor)
    assert not bot._xvl_anchor_ready(None, anchor)
    assert bot._xvl_anchor_ready({"history_end_ts": anchor}, anchor)


def test_wait_returns_on_readiness_and_falls_back_at_the_deadline(monkeypatch):
    now = time.time()
    anchor = int(math.floor(now)) - 1
    monkeypatch.setattr(bot, "_AI_SHADOW_TAPE", _ring_with([anchor - 1, anchor, anchor + 1]))
    live = {"history_end_ts": anchor + 1}
    monkeypatch.setattr(bot, "_cross_venue_live", lambda max_age_sec=1.0: live)
    _, got, ready = bot._xvl_wait_anchor_ready(time.time() + 0.5)
    assert ready and got is live
    live["history_end_ts"] = anchor - 5
    deadline = time.time() + 0.15
    at, got, ready = bot._xvl_wait_anchor_ready(deadline)
    assert not ready and got is None and at >= deadline - 0.005


def test_earlier_tick_sees_the_same_policy_inputs():
    """Readiness changes when the anchor is evaluated, not the 10 s window or thresholds."""
    rule = xvl_policy.RULE
    anchor = 1_790_000_000
    w = int(rule.lookback_sec)
    history = [100.0] * 40
    history[-1] = 100.0 * (1 + 12e-4)
    live = {
        "schema": xvl.cvt.LIVE_SCHEMA, "written_ts": anchor + 1.31, "history_start_ts": anchor - 39,
        "history_end_ts": anchor, "mids": {v: list(history) for v in rule.venues},
        "venues": {v: {"last_bbo_ts": anchor + 1.2} for v in rule.venues},
    }
    quotes = {anchor - w: (64999.0, 65001.0), anchor: (64999.0, 65001.0)}
    early = xvl.evaluate_second(rule, now=anchor + 1.33, live=live, bfx_quotes=quotes, bfx_bbo_ts=anchor + 1.0)
    late = xvl.evaluate_second(rule, now=anchor + 1.6, live=live, bfx_quotes=quotes, bfx_bbo_ts=anchor + 1.0)
    for key in ("anchor_bucket_ts", "status", "lead_bp", "side", "venue_ret_bp", "bfx_ret_bp", "bid", "ask"):
        assert early[key] == late[key], key
    assert early["status"] == xvl.STATUS_TRIGGER
    assert bot.XVL_READY_MIN_OFFSET_SEC >= 0.3 and bot.XVL_TICK_OFFSET_SEC == 0.6
