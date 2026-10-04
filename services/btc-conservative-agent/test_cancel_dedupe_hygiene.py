"""PR-B: duplicate evidence submits are accepted, circuit-breaker sweeps are serialized,
and each cancellation is journaled once to cancellation_evidence_handoffs.jsonl."""
from __future__ import annotations

import ast
import collections
import copy
import hashlib
import json
import threading
import time
from pathlib import Path

from bounded_evidence_worker import (
    SUBMIT_DUPLICATE_ACTIVE, SUBMIT_DUPLICATE_COMPLETED, SUBMIT_ENQUEUED, SUBMIT_QUEUE_FULL,
    BoundedEvidenceWorker,
)

BOT = Path(__file__).with_name("bot.py")
TREE = ast.parse(BOT.read_text(encoding="utf-8"))


def _load(names, ns):
    nodes = [n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(BOT), "exec"), ns)
    return ns


class _Log:
    def __init__(self):
        self.lines = []

    def _add(self, msg, *_a, **_k):
        self.lines.append(str(msg))

    info = warning = error = critical = debug = _add


# ------------------------------------------------------------------ (1) worker duplicates

def test_duplicate_submit_of_active_or_completed_key_is_accepted_not_a_gap():
    release, started, calls = threading.Event(), threading.Event(), []

    def handler(job):
        calls.append(job["key"])
        started.set()
        assert release.wait(2)

    worker = BoundedEvidenceWorker(handler, max_queue=1, max_retries=0)
    assert worker.submit_status("k1", {}) == SUBMIT_ENQUEUED
    assert started.wait(2)
    assert worker.submit_status("k1", {}) == SUBMIT_DUPLICATE_ACTIVE
    assert worker.submit("k1", {}) is True
    assert worker.submit_status("k2", {}) == SUBMIT_ENQUEUED
    assert worker.submit_status("k3", {}) == SUBMIT_QUEUE_FULL
    assert worker.submit("k4", {}) is False  # a real loss stays False and is dead-lettered
    release.set()
    assert worker.shutdown(drain_timeout=2)
    assert worker.submit_status("k1", {}) == SUBMIT_DUPLICATE_COMPLETED
    assert calls == ["k1", "k2"]
    assert worker.snapshot()["duplicates"] == 3


# ------------------------------------------------------------------ (2) circuit breaker

def _breaker_ns(cancel_impl):
    log = _Log()
    ns = {
        "CIRCUIT_BREAKER_CANCEL_REASONS": frozenset({"WS_STALE"}),
        "CIRCUIT_BREAKER_SKIP_REASONS": frozenset({"CANCEL_IN_PROGRESS", "ALREADY_FINALIZED",
                                                   "ALREADY_FILLED_OR_CLOSED", "FILL_CLAIMED"}),
        "CIRCUIT_BREAKER_SWEEP_WAIT_SEC": 5.0,
        "_circuit_breaker_sweep_lock": threading.Lock(),
        "trade_lock": threading.RLock(),
        "pending_orders": [{"trade_id": f"T{i}", "status": "PENDING"} for i in range(3)],
        "logger": log,
    }
    ns["_cancel_pending_order_confirmed"] = lambda o, *a, **k: cancel_impl(ns, o)
    return _load(("circuit_breaker_cancel_pending",), ns), log


def test_concurrent_pause_paths_cancel_each_order_once():
    cancels = collections.Counter()

    def cancel(ns, order):
        cancels[order["trade_id"]] += 1
        time.sleep(0.05)
        with ns["trade_lock"]:
            order["status"] = "EXPIRED"
            ns["pending_orders"].remove(order)
        return {"finalized": True}

    ns, log = _breaker_ns(cancel)
    results = []
    threads = [threading.Thread(target=lambda: results.append(ns["circuit_breaker_cancel_pending"]("WS_STALE")))
               for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [0, 0, 3]
    assert set(cancels.values()) == {1}
    assert not any("retained_unconfirmed=1" in line or "retained_unconfirmed=2" in line for line in log.lines)


def test_orders_owned_by_another_path_are_not_reported_as_retained_unconfirmed():
    ns, log = _breaker_ns(lambda ns, o: {"finalized": False, "failure_reason": "CANCEL_IN_PROGRESS"})
    assert ns["circuit_breaker_cancel_pending"]("WS_STALE") == 0
    assert not any("retained_unconfirmed" in line for line in log.lines)
    ns, log = _breaker_ns(lambda ns, o: {"finalized": False, "retained": True, "failure_reason": "CANCEL_HTTP_ERROR"})
    ns["circuit_breaker_cancel_pending"]("WS_STALE")
    assert any("retained_unconfirmed=3" in line for line in log.lines)


def test_breaker_does_not_wait_for_sweep_lock_while_holding_trade_lock():
    ns, log = _breaker_ns(lambda ns, o: {"finalized": True})
    ns["_circuit_breaker_sweep_lock"].acquire()
    started = time.monotonic()
    with ns["trade_lock"]:
        assert ns["circuit_breaker_cancel_pending"]("WS_STALE") == 0
    assert time.monotonic() - started < 1.0


def test_cancel_claim_in_progress_blocks_a_second_canceller_but_stale_claim_is_taken_over():
    src = ast.get_source_segment(BOT.read_text(encoding="utf-8"),
                                 next(n for n in TREE.body if isinstance(n, ast.FunctionDef)
                                      and n.name == "_cancel_pending_order_confirmed"))
    assert 'result["failure_reason"] = "CANCEL_IN_PROGRESS"' in src
    assert 'order["cancel_claim_ts"] = time.time()' in src
    assert 'order.pop("cancel_claim_ts", None)' in src
    ns = {"trade_lock": threading.RLock(), "time": time, "pending_orders": [], "lane_pending_orders": {},
          "logger": _Log()}
    _load(("_cancel_pending_order_confirmed",), ns)
    order = {"trade_id": "T1", "status": "PENDING", "cancel_claim_in_progress": "WS_STALE",
             "cancel_claim_ts": time.time()}
    out = ns["_cancel_pending_order_confirmed"](order, "ADMIN_MANUAL", record_expired=False, expire_signal=False)
    assert out["failure_reason"] == "CANCEL_IN_PROGRESS" and not out["finalized"]
    assert order["status"] == "PENDING" and order["cancel_claim_in_progress"] == "WS_STALE"


# ------------------------------------------------------------------ (3) write-once handoff journal

def _handoff_ns(append_ok=True):
    rows, dispatched = [], []
    ns = {
        "hashlib": hashlib, "json": json, "copy": copy, "collections": collections,
        "utc_iso": lambda: "2026-10-04T00:00:00Z", "_collector_v22_epoch_id": lambda: "epoch-x",
        "_cancellation_evidence_handoff_lock": threading.Lock(),
        "_cancellation_handoff_seen": collections.OrderedDict(),
        "_cancellation_handoff_seen_stats": {"duplicates_skipped": 0},
        "CANCELLATION_HANDOFF_SEEN_MAX": 1000,
        "_append_cancellation_evidence_handoff": lambda row: (rows.append(row) or True) if append_ok else False,
        "_dispatch_cancellation_evidence_handoff": lambda r: (dispatched.append(r["receipt_id"]) or True),
    }
    _load(("_enqueue_cancellation_evidence_handoff", "_remember_cancellation_handoff"), ns)
    return ns, rows, dispatched


def test_each_cancellation_is_journaled_once():
    ns, rows, dispatched = _handoff_ns()
    enqueue = ns["_enqueue_cancellation_evidence_handoff"]
    order = {"trade_id": "T1", "cancel_confirmed_ts": 1.0, "cancel_confirmed_reason": "WS_STALE"}
    assert enqueue(order) is True
    assert enqueue(order) is True  # exact repeat
    assert enqueue({**order, "cancel_confirmed_ts": 2.0}) is True  # second final cancel of the same trade
    assert len(rows) == 1 and len(dispatched) == 1
    assert ns["_cancellation_handoff_seen_stats"]["duplicates_skipped"] == 2
    # Non-final (virtual chase hide) cancels are distinct events and each is kept.
    enqueue({"trade_id": "T2", "cancel_confirmed_ts": 1.0}, lifecycle_final=False)
    enqueue({"trade_id": "T2", "cancel_confirmed_ts": 2.0}, lifecycle_final=False)
    assert len(rows) == 3


def test_failed_append_does_not_reserve_the_receipt():
    ns, rows, _ = _handoff_ns(append_ok=False)
    order = {"trade_id": "T1", "cancel_confirmed_ts": 1.0}
    assert ns["_enqueue_cancellation_evidence_handoff"](order) is False
    assert not ns["_cancellation_handoff_seen"]


def test_startup_replay_seeds_the_write_once_set():
    src = BOT.read_text(encoding="utf-8")
    replay = src[src.index("def _replay_cancellation_evidence_handoffs"):]
    replay = replay[:replay.index("\ndef ")]
    assert 'globals().get("_remember_cancellation_handoff")' in replay
