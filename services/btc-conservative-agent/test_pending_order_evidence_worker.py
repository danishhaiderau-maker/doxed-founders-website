import ast
import copy
import threading
import time
from collections import defaultdict
from pathlib import Path

from bounded_evidence_worker import BoundedEvidenceWorker


BOT_PATH = Path(__file__).with_name("bot.py")
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8")


class _Log:
    def warning(self, _message):
        pass


def _load_lane_registration(*, worker, trade_lock, pending, lane_pending, observations):
    tree = ast.parse(BOT_SOURCE)
    node = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == "lane_register_pending_order"
    )

    def initialize(order, signal, **_kwargs):
        order["schedule_ready"] = True
        observations.append(("schedule", signal))

    def hydrate(store, order, **_kwargs):
        store[order["trade_id"]] = {"schedule_ready": order.get("schedule_ready")}
        observations.append(("hydrate", order["trade_id"]))

    namespace = {
        "threading": threading,
        "time": time,
        "logger": _Log(),
        "trade_lock": trade_lock,
        "pending_orders": pending,
        "lane_pending_orders": lane_pending,
        "trades_map": {"trade-1": {"signal_ref": {"signal": 1}}},
        "_canonical_source_order_market_evidence": {},
        "_ensure_lane_bucket": lambda order: order.get("research_lane", "CONTINUOUS"),
        "executable_live_copy_entries_blocked": lambda: (False, "", ""),
        "initialize_research_order_schedule": initialize,
        "_sync_canonical_source_pending_order": hydrate,
        "_get_pending_order_evidence_worker": lambda: worker,
        "paper_policy_identity_for_sources": lambda *_args, **_kwargs: {},
        "_collector_v22_epoch_id": lambda: "test-epoch",
        "copy": copy,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(BOT_PATH), "exec"), namespace)
    return namespace["lane_register_pending_order"], namespace


def test_slow_disk_tail_does_not_hold_trade_lock_and_registration_is_visible():
    """Characterization A/B: mutation and hydration precede asynchronous tail."""
    tree = ast.parse(BOT_SOURCE)
    node = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == "lane_register_pending_order"
    )
    body = ast.get_source_segment(BOT_SOURCE, node)
    lock_end = body.index("trades_store =")
    schedule_at = body.index("schedule_initializer(")
    hydrate_at = body.index("hydrate(")
    enqueue_at = body.index(".submit(")
    assert "collector_bridge(" not in body
    assert "dual_write_paper_order_intent(" not in body
    assert "_emit_genome_execution_event(" not in body
    assert lock_end < schedule_at < hydrate_at < enqueue_at


def test_blocked_evidence_enqueue_releases_trade_lock_after_authoritative_hydration():
    entered_tail = threading.Event()
    release_tail = threading.Event()

    class BlockingWorker:
        def submit(self, *_args, **_kwargs):
            entered_tail.set()
            assert release_tail.wait(1)
            return True

    lock = threading.RLock()
    pending = []
    lane_pending = defaultdict(list)
    observations = []
    register, namespace = _load_lane_registration(
        worker=BlockingWorker(), trade_lock=lock, pending=pending,
        lane_pending=lane_pending, observations=observations,
    )
    order = {
        "trade_id": "trade-1", "status": "PENDING", "research_lane": "PATIENT",
        "created_ts": 100.0, "entry_type": "SIM_LIMIT",
    }
    thread = threading.Thread(target=register, args=(order,))
    thread.start()
    assert entered_tail.wait(1)
    # Registration and its authoritative schedule/hydration are already visible.
    assert pending == [order]
    assert lane_pending["PATIENT"] == [order]
    assert order["schedule_ready"] is True
    assert namespace["_canonical_source_order_market_evidence"]["trade-1"] == {
        "schedule_ready": True,
    }
    acquired = lock.acquire(timeout=0.1)
    try:
        assert acquired is True
    finally:
        if acquired:
            lock.release()
        release_tail.set()
        thread.join(timeout=1)
    assert not thread.is_alive()


def test_worker_is_bounded_idempotent_and_preserves_source_timestamp():
    release = threading.Event()
    started = threading.Event()
    calls = []

    def slow_handler(job):
        calls.append(job)
        started.set()
        assert release.wait(1)

    worker = BoundedEvidenceWorker(slow_handler, max_queue=1, max_retries=0)
    assert worker.submit("trade-1", {"value": [1]}, source_ts=123.5) is True
    assert started.wait(1)
    # Same causal order is suppressed while active, without consuming capacity.
    assert worker.submit("trade-1", {"value": [2]}, source_ts=999) is False
    assert worker.submit("trade-2", {"value": [2]}, source_ts=124.5) is True
    assert worker.submit("trade-3", {"value": [3]}, source_ts=125.5) is False
    release.set()
    assert worker.shutdown(drain_timeout=1) is True
    assert [job["key"] for job in calls] == ["trade-1", "trade-2"]
    assert calls[0]["source_ts"] == 123.5
    assert worker.snapshot()["dead_letters"][0]["reason"] == "queue_full"


def test_worker_retries_then_dead_letters_and_shutdown_drains_once():
    attempts = []

    def always_fails(job):
        attempts.append((job["key"], job["attempt"]))
        raise RuntimeError("disk unavailable")

    worker = BoundedEvidenceWorker(always_fails, max_queue=4, max_retries=2)
    assert worker.submit("trade-fail", {"order": {}}, source_ts=10) is True
    assert worker.shutdown(drain_timeout=1) is True
    assert attempts == [("trade-fail", 0), ("trade-fail", 1), ("trade-fail", 2)]
    dead = worker.snapshot()["dead_letters"]
    assert len(dead) == 1
    assert dead[0]["reason"] == "retries_exhausted"
    assert dead[0]["attempt"] == 3


def test_two_lane_fanout_is_not_serialized_by_four_second_evidence_tail():
    """Cadence F: two registrations enqueue promptly while one writer is blocked."""
    release = threading.Event()
    first_started = threading.Event()
    completed = []

    def disk_tail(job):
        first_started.set()
        assert release.wait(1)
        completed.append(job["key"])

    worker = BoundedEvidenceWorker(disk_tail, max_queue=4, max_retries=0)
    start = time.monotonic()
    assert worker.submit("continuous", {"lane": "CONTINUOUS"}, source_ts=1)
    assert first_started.wait(1)
    assert worker.submit("patient", {"lane": "PATIENT"}, source_ts=2)
    elapsed = time.monotonic() - start
    assert elapsed < 0.25
    release.set()
    assert worker.shutdown(drain_timeout=1)
    assert completed == ["continuous", "patient"]
