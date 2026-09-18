"""Focused contracts for append-first asynchronous cancellation evidence."""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import tempfile
from types import SimpleNamespace

from bounded_evidence_worker import BoundedEvidenceWorker


BOT_PATH = Path(__file__).with_name("bot.py")
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8")
ENGINE_PATH = BOT_PATH.parents[1] / "btc-signal-engine" / "engine.py"
TREE = ast.parse(BOT_SOURCE)


def _function(name):
    return next(
        node for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _load(names, namespace):
    nodes = [_function(name) for name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(BOT_PATH), "exec"), namespace)
    return namespace


def test_cancellation_uses_append_first_async_handoff_and_keeps_authority_ordering():
    cancel = ast.unparse(_function("_cancel_pending_order_confirmed"))
    assert "_enqueue_cancellation_evidence_handoff" in cancel
    assert "_refresh_collector_v22_registered_order_evidence" not in cancel
    assert cancel.index("order['cancel_confirmed'] = True") < cancel.index(
        "_record_expired_order"
    ) < cancel.index("_enqueue_cancellation_evidence_handoff")
    assert "_maybe_bitfinex_cancel(order)" in cancel
    assert "order['bitfinex_order_id'] = None" in cancel


def test_pending_receipt_is_epoch_bound_immutable_and_precedes_dispatch():
    appended = []
    dispatched = []
    ns = {
        "copy": copy,
        "hashlib": hashlib,
        "json": json,
        "_collector_v22_epoch_id": lambda: "epoch-v22-fixed",
        "utc_iso": lambda: "2026-09-19T00:00:00Z",
        "_append_cancellation_evidence_handoff": lambda row: appended.append(copy.deepcopy(row)) or True,
        "_dispatch_cancellation_evidence_handoff": lambda row: dispatched.append(copy.deepcopy(row)) or True,
    }
    _load(["_enqueue_cancellation_evidence_handoff"], ns)
    order = {
        "trade_id": "paper-1", "status": "CANCELLED",
        "cancel_confirmed_ts": 123.5, "cancel_confirmed_reason": "TTL",
    }
    signal = {"research_chase_schedule": {"terminal_reason": "TTL"}}
    assert ns["_enqueue_cancellation_evidence_handoff"](order, signal) is True
    order["status"] = "MUTATED"
    signal["research_chase_schedule"]["terminal_reason"] = "MUTATED"

    assert len(appended) == len(dispatched) == 1
    receipt = appended[0]
    assert receipt == dispatched[0]
    assert receipt["schema"] == "cancellation_evidence_handoff_pending_v1"
    assert receipt["collector_epoch_id"] == "epoch-v22-fixed"
    assert receipt["order_snapshot"]["status"] == "CANCELLED"
    assert receipt["signal_snapshot"]["research_chase_schedule"]["terminal_reason"] == "TTL"
    assert len(receipt["receipt_id"]) == 64


def test_dispatch_returns_while_serialized_evidence_waits_then_applies_once():
    entered = threading.Event()
    release = threading.Event()
    applied = []
    results = []
    def refresh(order, signal, **kwargs):
        entered.set()
        assert release.wait(2)
        applied.append((copy.deepcopy(order), copy.deepcopy(signal), dict(kwargs)))
        return True

    def build_ns():
        ns = {
        "copy": copy,
        "threading": threading,
        "time": time,
        "_collector_v22_epoch_id": lambda: "epoch-v22-fixed",
        "_refresh_collector_v22_registered_order_evidence": refresh,
        "_append_cancellation_evidence_handoff": lambda row: results.append(copy.deepcopy(row)) or True,
        "utc_iso": lambda: "2026-09-19T00:00:01Z",
        "logger": SimpleNamespace(warning=lambda *args, **kwargs: None),
        }
        return ns
    ns = build_ns()
    class Worker:
        def submit(self, key, payload, **kwargs):
            threading.Thread(
                target=ns["_write_cancellation_evidence_handoff"],
                args=({"key": key, "payload": payload},), daemon=True,
            ).start()
            return True
    ns["_get_cancellation_evidence_worker"] = lambda: Worker()
    _load(["_write_cancellation_evidence_handoff", "_dispatch_cancellation_evidence_handoff"], ns)
    receipt = {
        "receipt_id": "a" * 64, "collector_epoch_id": "epoch-v22-fixed",
        "trade_id": "paper-1", "order_snapshot": {"trade_id": "paper-1"},
        "signal_snapshot": {},
    }
    started = time.monotonic()
    assert ns["_dispatch_cancellation_evidence_handoff"](receipt) is True
    assert time.monotonic() - started < 0.25
    assert entered.wait(1)
    # The fake worker does not deduplicate; the production BoundedEvidenceWorker
    # does. The handler itself remains immutable and exactly-once per job.
    release.set()
    deadline = time.monotonic() + 2
    while not results and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(applied) == 1
    assert results[0]["status"] == "APPLIED"
    assert results[0]["receipt_id"] == receipt["receipt_id"]


def test_mismatched_epoch_is_preserved_without_refresh_or_forced_unlock():
    refreshed = []
    results = []
    ns = {
        "copy": copy,
        "threading": threading,
        "time": time,
        "_collector_v22_epoch_id": lambda: "epoch-v22-new",
        "_refresh_collector_v22_registered_order_evidence": lambda *a, **k: refreshed.append(True),
        "_append_cancellation_evidence_handoff": lambda row: results.append(copy.deepcopy(row)) or True,
        "utc_iso": lambda: "2026-09-19T00:00:01Z",
        "logger": SimpleNamespace(warning=lambda *args, **kwargs: None),
    }
    class Worker:
        def submit(self, key, payload, **kwargs):
            threading.Thread(
                target=ns["_write_cancellation_evidence_handoff"],
                args=({"key": key, "payload": payload},), daemon=True,
            ).start()
            return True
    ns["_get_cancellation_evidence_worker"] = lambda: Worker()
    _load(["_write_cancellation_evidence_handoff", "_dispatch_cancellation_evidence_handoff"], ns)
    assert ns["_dispatch_cancellation_evidence_handoff"]({
        "receipt_id": "b" * 64, "collector_epoch_id": "epoch-v22-old",
        "trade_id": "paper-old", "order_snapshot": {}, "signal_snapshot": {},
    }) is True
    deadline = time.monotonic() + 2
    while not results and time.monotonic() < deadline:
        time.sleep(0.01)
    assert refreshed == []
    assert results[0]["status"] == "EPOCH_MISMATCH_PRESERVED"
    assert "release" not in BOT_SOURCE[BOT_SOURCE.index("def _dispatch_cancellation_evidence_handoff"):BOT_SOURCE.index("def _replay_cancellation_evidence_handoffs")]


def test_handoff_append_completes_short_writes_before_fsync():
    writes = bytearray()
    calls = []
    class ShortWriteOS:
        O_RDONLY = 0
        O_CREAT = 1
        O_APPEND = 2
        O_WRONLY = 4
        def open(self, *args):
            if len(args) > 1 and args[1] == self.O_RDONLY:
                raise FileNotFoundError(args[0])
            calls.append(("open", args))
            return 7
        def lseek(self, *args):
            return 0
        def read(self, *args):
            return b""
        def write(self, fd, data):
            chunk = bytes(data[:1])
            writes.extend(chunk)
            return len(chunk)
        def fsync(self, fd):
            calls.append(("fsync", fd))
        def close(self, fd):
            calls.append(("close", fd))

    with tempfile.TemporaryDirectory() as tmp:
        ns = {
            "json": json,
            "os": ShortWriteOS(),
            "Path": Path,
            "threading": threading,
            "_cancellation_evidence_handoff_path": lambda: str(Path(tmp) / "handoffs.jsonl"),
            "_cancellation_evidence_handoff_lock": threading.Lock(),
            "logger": SimpleNamespace(error=lambda *args, **kwargs: None),
        }
        _load(["_append_cancellation_evidence_handoff"], ns)
        assert ns["_append_cancellation_evidence_handoff"]({"receipt_id": "r"}) is True
    assert bytes(writes).endswith(b"\n")
    assert any(kind == "fsync" for kind, _ in calls)


def test_append_after_torn_tail_fences_new_receipt_without_rewriting_tail(tmp_path):
    path = tmp_path / "cancellation_evidence_handoffs.jsonl"
    torn = b'{"schema":"cancellation_evidence_handoff_pending_v1"'
    path.write_bytes(torn)
    ns = {
        "json": json,
        "os": os,
        "Path": Path,
        "threading": threading,
        "_cancellation_evidence_handoff_path": lambda: str(path),
        "_cancellation_evidence_handoff_lock": threading.Lock(),
        "logger": SimpleNamespace(error=lambda *_args, **_kwargs: None),
    }
    _load(["_append_cancellation_evidence_handoff"], ns)
    row = {
        "schema": "cancellation_evidence_handoff_pending_v1",
        "receipt_id": "c" * 64,
        "collector_epoch_id": "epoch-1",
    }
    assert ns["_append_cancellation_evidence_handoff"](row) is True
    payload = path.read_bytes()
    assert payload.startswith(torn + b"\n") or payload.startswith(torn + b"\r\n")
    assert json.loads(payload.splitlines()[1]) == row


def test_queue_full_pending_row_is_replayed_after_dispatch_recovers(tmp_path):
    pending_id = "d" * 64
    path = tmp_path / "cancellation_evidence_handoffs.jsonl"
    path.write_text(
        json.dumps({
            "schema": "cancellation_evidence_handoff_pending_v1",
            "receipt_id": pending_id,
            "collector_epoch_id": "epoch-1",
        }) + "\n",
        encoding="utf-8",
    )
    calls = []
    ns = {
        "os": os,
        "json": json,
        "_cancellation_evidence_handoff_path": lambda: str(path),
        "_dispatch_cancellation_evidence_handoff": lambda row: (
            calls.append(row) or len(calls) > 1
        ),
        "logger": SimpleNamespace(
            error=lambda *_args, **_kwargs: None,
            warning=lambda *_args, **_kwargs: None,
        ),
    }
    _load(["_replay_cancellation_evidence_handoffs"], ns)
    assert ns["_replay_cancellation_evidence_handoffs"]() == 0
    assert ns["_replay_cancellation_evidence_handoffs"]() == 1
    assert [row["receipt_id"] for row in calls] == [pending_id, pending_id]


def test_result_append_failure_does_not_report_worker_success():
    ns = {
        "copy": copy,
        "_collector_v22_epoch_id": lambda: "epoch-v22-fixed",
        "_refresh_collector_v22_registered_order_evidence": lambda *a, **k: True,
        "_append_cancellation_evidence_handoff": lambda row: False,
        "utc_iso": lambda: "2026-09-19T00:00:01Z",
    }
    _load(["_write_cancellation_evidence_handoff"], ns)
    receipt = {
        "receipt_id": "c" * 64,
        "collector_epoch_id": "epoch-v22-fixed",
        "trade_id": "paper-1",
        "order_snapshot": {},
        "signal_snapshot": {},
    }
    try:
        ns["_write_cancellation_evidence_handoff"]({"payload": {"receipt": receipt}})
    except OSError as exc:
        assert "durably append" in str(exc)
    else:
        raise AssertionError("result append failure was reported as success")


def test_terminal_receipt_requires_canonical_written_or_exact_duplicate():
    ns = {}
    _load(["_terminal_schedule_receipt_durable"], ns)
    durable = ns["_terminal_schedule_receipt_durable"]
    assert durable({"write": {"written": True}}) is True
    assert durable({"write": {"duplicate": True, "written": False}}) is True
    assert durable({"write": {"duplicate": True, "deferred": True}}) is False
    assert durable({"write": {"deferred": True, "written": False}}) is False
    assert durable({"write": {"blocked": True}}) is False
    assert durable({"write": None}) is False
    assert durable({}) is False


def test_real_worker_shutdown_exposes_live_timed_out_handler():
    entered = threading.Event()
    release = threading.Event()

    def blocked(_job):
        entered.set()
        release.wait(2)

    worker = BoundedEvidenceWorker(
        blocked, max_retries=0, handler_timeout_sec=0.02,
        name="test-cancellation-evidence",
    )
    try:
        assert worker.submit("timeout-key", {"receipt": {}})
        assert entered.wait(1)
        deadline = time.monotonic() + 1
        while not worker.snapshot()["timed_out_handler_alive"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert worker.shutdown(drain_timeout=0.2) is True
        assert worker.snapshot()["timed_out_handler_alive"] is True
    finally:
        release.set()
        deadline = time.monotonic() + 1
        while worker.snapshot()["timed_out_handler_alive"] and time.monotonic() < deadline:
            time.sleep(0.01)


def test_cancel_finalizes_authority_when_evidence_append_is_unavailable():
    """A cancellation must not roll back execution state on an evidence gap."""
    order = {
        "trade_id": "paper-cancel-1",
        "status": "PENDING",
        "research_lane": "FAMILY_ATR_TRAIL",
    }
    pending = [order]
    lane_pending = {"FAMILY_ATR_TRAIL": [order]}
    ns = {
        "time": time,
        "trade_lock": threading.RLock(),
        "pending_orders": pending,
        "lane_pending_orders": lane_pending,
        "trades_map": {},
        "_ensure_lane_bucket": lambda _row: "FAMILY_ATR_TRAIL",
        "_normalize_lane_key": lambda _row: "FAMILY_ATR_TRAIL",
        "_record_expired_order": lambda row, reason: {
            "trade_id": row["trade_id"], "reason": reason,
        },
        "expire_signal_for_order": lambda *_args: False,
        "_append_paper_action_receipt": lambda *_args, **_kwargs: None,
        "_enqueue_cancellation_evidence_handoff": lambda *_args, **_kwargs: False,
        "logger": SimpleNamespace(
            critical=lambda *_args, **_kwargs: None,
            error=lambda *_args, **_kwargs: None,
        ),
    }
    _load(["_cancel_pending_order_confirmed"], ns)

    result = ns["_cancel_pending_order_confirmed"](
        order, "TTL_EXPIRED", record_expired=True, expire_signal=False,
    )

    assert result["confirmed"] is True
    assert result["finalized"] is True
    assert result["evidence_handoff"] == "PENDING"
    assert order["cancel_confirmed"] is True
    assert order not in pending
    assert lane_pending["FAMILY_ATR_TRAIL"] == []


def test_bounded_worker_queue_saturation_dead_letters_without_stuck_active_key():
    entered = threading.Event()
    release = threading.Event()
    completed = []

    def handler(job):
        if job["key"] == "first":
            entered.set()
            assert release.wait(2)
        completed.append(job["key"])

    worker = BoundedEvidenceWorker(handler, max_queue=1, max_retries=0)
    try:
        assert worker.submit("first", {"n": 1}) is True
        assert entered.wait(1)
        assert worker.submit("second", {"n": 2}) is True
        assert worker.submit("third", {"n": 3}) is False
        assert any(
            row.get("key") == "third" and row.get("reason") == "queue_full"
            for row in worker.snapshot()["dead_letters"]
        )
        release.set()
        deadline = time.monotonic() + 2
        while len(completed) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert completed == ["first", "second"]
        assert worker.snapshot()["active"] == 0
    finally:
        release.set()
        worker.shutdown(drain_timeout=1)


def test_bounded_worker_retries_once_then_marks_completed():
    attempts = []
    completed = threading.Event()

    def flaky(job):
        attempts.append(job["attempt"])
        if len(attempts) == 1:
            raise RuntimeError("transient")
        completed.set()

    worker = BoundedEvidenceWorker(flaky, max_queue=2, max_retries=1)
    try:
        assert worker.submit("retry-key", {"n": 1}) is True
        assert completed.wait(2)
        assert attempts == [0, 1]
        snapshot = worker.snapshot()
        assert snapshot["completed"] == 1
        assert snapshot["dead_letters"] == []
    finally:
        worker.shutdown(drain_timeout=1)


def test_replay_ignores_torn_tail_and_dispatches_only_pending_rows(tmp_path):
    pending_id = "a" * 64
    terminal_id = "b" * 64
    path = tmp_path / "cancellation_evidence_handoffs.jsonl"
    rows = [
        {
            "schema": "cancellation_evidence_handoff_pending_v1",
            "receipt_id": pending_id,
            "collector_epoch_id": "epoch-1",
        },
        {
            "schema": "cancellation_evidence_handoff_result_v1",
            "receipt_id": terminal_id,
            "status": "APPLIED",
        },
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
        + '{"schema":"cancellation_evidence_handoff_pending_v1","receipt_id":"',
        encoding="utf-8",
    )
    dispatched = []
    ns = {
        "os": os,
        "json": json,
        "_cancellation_evidence_handoff_path": lambda: str(path),
        "_dispatch_cancellation_evidence_handoff": lambda row: dispatched.append(row) or True,
        "logger": SimpleNamespace(
            error=lambda *_args, **_kwargs: None,
            warning=lambda *_args, **_kwargs: None,
        ),
    }
    _load(["_replay_cancellation_evidence_handoffs"], ns)

    assert ns["_replay_cancellation_evidence_handoffs"]() == 1
    assert [row["receipt_id"] for row in dispatched] == [pending_id]


def test_mirrored_runtime_sources_are_byte_identical():
    assert BOT_PATH.read_bytes() == ENGINE_PATH.read_bytes()
