"""Fill-guard v2: per-order fill/expiry claims are atomic, post-fill evidence
leaves the fill thread, and that evidence is applied exactly once.

Executes the real bot.py functions (no trading owner is imported).
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
from types import ModuleType, SimpleNamespace

import pytest

from bounded_evidence_worker import BoundedEvidenceWorker
from position_registry import promote_pending_to_open


BOT_PATH = Path(__file__).with_name("bot.py")
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8")
TREE = ast.parse(BOT_SOURCE)
TILE_LANES = {
    "ftf": "FAMILY_TREND_FADE_60",
    "tsa": "SYNTHETIC_TILE_A",
    "tsb": "SYNTHETIC_TILE_B",
}


def _load(names, namespace):
    nodes = []
    for node in TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in names for target in node.targets
        ):
            nodes.append(node)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(BOT_PATH), "exec"), namespace)
    return namespace


def _function_source(name):
    node = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(BOT_SOURCE, node)


@pytest.fixture(autouse=True)
def _quiet_funnel(monkeypatch):
    funnel = ModuleType("execution_funnel")
    funnel.funnel_on_fill = lambda *a, **k: None
    funnel.funnel_on_expire = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "execution_funnel", funnel)


class Logger:
    def __init__(self):
        self.lines = []

    def _log(self, level):
        return lambda message, *args, **kwargs: self.lines.append((level, str(message)))

    def __getattr__(self, level):
        return self._log(level)


class FillRuntime:
    """Real fill_order / TTL sweep / cancel helper over in-memory books."""

    def __init__(self, *, fill_thread_work_sec=0.0):
        self.trade_lock = threading.RLock()
        self.pending = []
        self.lane_pending = {}
        self.open_positions = []
        self.lane_open = {}
        self.handoffs = set()
        self.trades_map = {}
        self.expired = []
        self.evidence = []
        self.logger = Logger()
        work = fill_thread_work_sec

        def commit_transition(event, trade_id, extra, *, target_mutator, live_mutator, canonical_lock=None):
            with canonical_lock, self.trade_lock:
                target = {
                    "pending_orders": copy.deepcopy(self.pending),
                    "positions": copy.deepcopy(self.open_positions),
                }
                target_mutator(target)
                live_mutator()
            return True

        def enqueue(order, signal, pos, fill_snapshot, **kwargs):
            self.evidence.append(pos["trade_id"])
            # Stand-in for any slow work left on the fill thread; the claim
            # must protect queued siblings regardless of its duration.
            time.sleep(work)
            return True

        def record_expired(order, reason):
            self.expired.append((order["trade_id"], reason))
            return {"recorded": True}

        ns = {
            "copy": copy, "time": time, "os": os, "threading": threading,
            "logger": self.logger,
            "trade_lock": self.trade_lock, "position_close_lock": threading.RLock(),
            "state_lock": threading.RLock(), "state": {},
            "pending_orders": self.pending, "lane_pending_orders": self.lane_pending,
            "open_positions": self.open_positions, "lane_open_positions": self.lane_open,
            "fill_handoff_trade_ids": self.handoffs, "trades_map": self.trades_map,
            "LIMIT_ORDER_MAX_AGE_SEC": 600, "RETIRED_TILE_BOUNDARY_REASON": "TILE_RETIRED",
            "manual_admin_pause_active": lambda: False,
            "_retired_tile_boundary_lanes": lambda: set(),
            "_row_has_exchange_identity": lambda row: False,
            "_normalize_lane_key": lambda row: str(row.get("research_lane") or "").upper(),
            "_ensure_lane_bucket": lambda row: str(row.get("research_lane") or "").upper(),
            "_normalize_order_side_to_dir": lambda value: str(value or "").upper(),
            "fmt": str,
            "_register_fill_markout": lambda *a, **k: None,
            "_build_open_position": lambda order, signal, ai: {
                "trade_id": order["trade_id"], "status": "OPEN", "dir": order["signal_dir"],
                "qty": order["qty"], "entry": order["fill_price"],
                "research_lane": order["research_lane"],
            },
            "paper_policy_identity_for_sources": lambda epoch, order, pos, signal: {
                "paper_policy_spec": {"research_lane": order.get("research_lane")},
            },
            "_collector_v22_epoch_id": lambda: "epoch-fill-guard-v2",
            "utc_iso": lambda: "2026-10-02T00:00:00Z",
            "promote_pending_to_open": promote_pending_to_open,
            "_commit_paper_lifecycle_transition": commit_transition,
            "_canonicalize_paper_position_snapshot": lambda row: row,
            "log_lane_opportunity_event": lambda *a, **k: None,
            "_emit_genome_execution_event": lambda *a, **k: None,
            "_relay_mirror": lambda *a, **k: None,
            "_mark_fill_replay_buffer_executed": lambda trade_id, px: None,
            "_enqueue_fill_evidence_handoff": enqueue,
            "pipeline_state_sync": lambda: None,
            "_record_expired_order": record_expired,
            "_append_paper_action_receipt": lambda *a, **k: None,
            "_agent_dbg": lambda *a, **k: None,
        }
        _load({
            "TERMINAL_SIGNAL_STATUSES", "TERMINAL_SIGNAL_OUTCOMES",
            "is_terminal_signal", "_position_open_relay_allowed", "_fill_commit_refusal",
            "_commit_position_open_lifecycle", "_finalize_position_open_lifecycle",
            "fill_order", "_release_unfilled_fill_handoff", "_cancel_pending_order_confirmed",
            "cleanup_expired_orders", "expire_signal_for_order",
        }, ns)
        assert "FILLED" in ns["TERMINAL_SIGNAL_STATUSES"]
        self.ns = ns

    def add_order(self, prefix, *, expires_in):
        now = time.time()
        tid = f"{prefix}-{len(self.pending) + len(self.open_positions)}"
        lane = TILE_LANES[prefix]
        order = {
            "trade_id": tid, "status": "PENDING", "research_lane": lane,
            "signal_dir": "LONG", "qty": 0.0003, "fill_price": 84000.0,
            "limit_price": 84000.0, "created_ts": now - 60, "entry_expires_ts": now + expires_in,
        }
        self.pending.append(order)
        self.lane_pending.setdefault(lane, []).append(order)
        self.trades_map[tid] = {"signal_ref": {"trade_id": tid, "status": "ORDERED", "research_lane": lane}}
        return order

    def claim_touch(self, order):
        """The process_pending_orders touch claim (same lock, same fields)."""
        with self.trade_lock:
            assert order in self.pending and order["status"] == "PENDING"
            assert order["trade_id"] not in self.handoffs
            assert not order.get("cancel_claim_in_progress")
            order["fill_handoff_in_progress"] = True
            self.handoffs.add(order["trade_id"])

    def fill(self, order):
        try:
            return self.ns["fill_order"](order)
        finally:
            self.ns["_release_unfilled_fill_handoff"](order)

    def open_ids(self):
        return {row["trade_id"] for row in self.open_positions}

    def expired_ids(self):
        return {tid for tid, _ in self.expired} | {
            tid for tid, meta in self.trades_map.items()
            if meta["signal_ref"].get("status") == "EXPIRED"
        }

    def warnings(self, tag):
        return [line for level, line in self.logger.lines if tag in line]


def test_same_tick_sibling_fills_on_three_tiles_never_lose_to_ttl_expiry():
    # Fill-thread work per fill (0.25 s) far exceeds the 0.05 s TTL deadline,
    # reproducing the flb-6a32a8bad760 timing: touched together, filled late.
    rt = FillRuntime(fill_thread_work_sec=0.25)
    siblings = [rt.add_order(prefix, expires_in=0.05) for prefix in ("ftf", "tsa", "tsb")]
    unclaimed = rt.add_order("ftf", expires_in=0.05)
    for order in siblings:
        rt.claim_touch(order)

    stop = threading.Event()
    sweeps = []

    def ttl_thread():
        while not stop.is_set():
            sweeps.append(rt.ns["cleanup_expired_orders"]())
            time.sleep(0.005)

    sweeper = threading.Thread(target=ttl_thread, daemon=True)
    sweeper.start()
    try:
        for order in siblings:
            rt.fill(order)
        # The unclaimed order expired while the fill thread was busy; a late
        # fill attempt on it must not open a position.
        assert rt.fill(unclaimed) is None
    finally:
        stop.set()
        sweeper.join(2)

    assert rt.open_ids() == {o["trade_id"] for o in siblings}
    assert rt.expired_ids() == {unclaimed["trade_id"]}
    assert rt.open_ids().isdisjoint(rt.expired_ids()), "expired+filled contradiction"
    assert sum(sweeps) == 1
    assert not rt.warnings("[FILL GUARD]")
    assert len(rt.warnings("[FILL CLAIM]")) == 1
    assert not rt.handoffs and not rt.pending
    for order in siblings:
        assert rt.trades_map[order["trade_id"]]["signal_ref"]["status"] == "FILLED"


def test_claimed_touch_survives_overdue_ttl_sweep_then_fills():
    rt = FillRuntime()
    order = rt.add_order("tsa", expires_in=-1)
    rt.claim_touch(order)
    assert rt.ns["cleanup_expired_orders"]() == 0
    outcome = rt.ns["_cancel_pending_order_confirmed"](order, "TTL_EXPIRED")
    assert outcome["finalized"] is False and outcome["failure_reason"] == "FILL_CLAIMED"
    assert order in rt.pending and order["status"] == "PENDING"
    assert rt.trades_map[order["trade_id"]]["signal_ref"]["status"] == "ORDERED"
    rt.fill(order)
    assert rt.open_ids() == {order["trade_id"]} and not rt.expired


def test_already_expired_order_never_opens_a_position():
    rt = FillRuntime()
    order = rt.add_order("tsb", expires_in=-1)
    assert rt.ns["cleanup_expired_orders"]() == 1
    assert rt.fill(order) is None
    assert not rt.open_positions and not rt.evidence
    assert rt.warnings("reason=ORDER_NOT_PENDING")
    assert not rt.handoffs
    # Same verdict if a recorder left the status untouched but removed the row.
    ghost = rt.add_order("tsb", expires_in=60)
    rt.pending.remove(ghost)
    assert rt.fill(ghost) is None
    assert rt.warnings("reason=ORDER_NOT_IN_BOOK") and not rt.open_positions


def test_terminal_signal_or_in_flight_cancel_blocks_the_fill():
    rt = FillRuntime()
    expired_signal = rt.add_order("ftf", expires_in=60)
    rt.trades_map[expired_signal["trade_id"]]["signal_ref"].update(
        status="EXPIRED", outcome="SIGNAL_TTL_EXPIRED", exit_reason="SIGNAL_TTL_EXPIRED",
    )
    assert rt.fill(expired_signal) is None
    cancelling = rt.add_order("tsa", expires_in=60)
    cancelling["cancel_claim_in_progress"] = "TTL_EXPIRED"
    assert rt.fill(cancelling) is None
    assert not rt.open_positions
    assert rt.warnings("reason=SIGNAL_TERMINAL") and rt.warnings("reason=CANCEL_CLAIMED")
    assert expired_signal in rt.pending and cancelling in rt.pending
    assert not rt.handoffs


def test_cancel_claim_is_released_when_the_durable_recorder_fails():
    rt = FillRuntime()
    order = rt.add_order("ftf", expires_in=-1)

    def failing_recorder(row, reason):
        raise RuntimeError("DURABLE_PREPARE_FAILED")

    rt.ns["_record_expired_order"] = failing_recorder
    with pytest.raises(RuntimeError):
        rt.ns["_cancel_pending_order_confirmed"](order, "TTL_EXPIRED")
    assert "cancel_claim_in_progress" not in order
    assert order in rt.pending


def test_filled_own_commit_is_not_terminal_for_post_fill_steps():
    rt = FillRuntime()
    orders = [rt.add_order(prefix, expires_in=60) for prefix in ("ftf", "tsa", "tsb")]
    for order in orders:
        rt.claim_touch(order)
        rt.fill(order)
    # Every fill reached the post-fill evidence handoff exactly once.
    assert rt.evidence == [o["trade_id"] for o in orders]
    assert not rt.warnings("[FILL GUARD]")

    allowed = rt.ns["_position_open_relay_allowed"]
    pos = {"trade_id": "ftf-own", "status": "OPEN"}
    assert allowed(pos, {"status": "FILLED", "outcome": "OPEN"}) is True
    assert allowed(pos, {"status": "EXPIRED", "outcome": "TTL_EXPIRED"}) is False
    assert allowed(pos, {"status": "FILLED", "outcome": "OPEN", "exit_reason": "TTL_EXPIRED"}) is False
    assert allowed(pos, {"status": "CLOSED", "outcome": "WIN"}) is False
    assert allowed({**pos, "_close_in_progress": True}, {"status": "FILLED", "outcome": "OPEN"}) is False


class EvidenceRuntime:
    """Real append-first journal + BoundedEvidenceWorker + handler."""

    def __init__(self, root: Path, *, v3_delay=0.0, v3_failures=0):
        self.calls = []
        self.open_positions = [{"trade_id": "tsa-9", "status": "OPEN"}]
        failures = {"left": v3_failures}

        def record(name):
            return lambda *args, **kwargs: self.calls.append(name)

        def dual_write(order, signal, position, *, epoch_id, data_dir):
            time.sleep(v3_delay)
            self.calls.append("v3_fill")
            if failures["left"] > 0:
                failures["left"] -= 1
                raise OSError("ledger busy")
            return {"fill_id": "fill-tsa-9", "policy_signature": "sig-1"}

        self.ns = {
            "copy": copy, "hashlib": hashlib, "json": json, "os": os, "time": time,
            "threading": threading, "Path": Path,
            "logger": Logger(),
            "BoundedEvidenceWorker": BoundedEvidenceWorker,
            "trade_lock": threading.RLock(), "open_positions": self.open_positions,
            "_collector_v22_epoch_id": lambda: "epoch-fill-guard-v2",
            "utc_iso": lambda: "2026-10-02T00:00:00Z",
            "_data_sync_runtime_root": lambda: str(root),
            "_fill_evidence_handoff_lock": threading.Lock(),
            "_fill_evidence_worker": None,
            "_fill_evidence_worker_lock": threading.Lock(),
            "_fill_evidence_steps_done": {},
            "_cancellation_evidence_reset_fence": False,
            "_fresh_collection_lock": threading.Lock(),
            "_append_paper_action_receipt": record("action_receipt"),
            "dual_write_paper_fill": dual_write,
            "_refresh_collector_v22_registered_order_evidence": record("collector_refresh"),
            "patch_signal_snapshot_outcome": record("signal_snapshot"),
            "persist_signal": record("persist_signal"),
            "_sync_order_multiverse": record("multiverse"),
            "save_positions": record("save_positions"),
        }
        _load({
            "FILL_EVIDENCE_IDENTITY_KEYS", "_stable_pending_signal_copy",
            "_append_durable_handoff_row", "_fill_evidence_handoff_path",
            "_append_fill_evidence_handoff", "_write_fill_evidence_handoff",
            "_fill_evidence_dead_letter", "_get_fill_evidence_worker",
            "_shutdown_fill_evidence_worker", "_dispatch_fill_evidence_handoff",
            "_replay_fill_evidence_handoffs", "_enqueue_fill_evidence_handoff",
        }, self.ns)
        self.journal = root / "fill_evidence_handoffs.jsonl"

    def enqueue(self):
        return self.ns["_enqueue_fill_evidence_handoff"](
            {"trade_id": "tsa-9", "fill_price": 84000.0, "qty": 0.0003},
            {"trade_id": "tsa-9", "status": "FILLED"},
            self.open_positions[0],
            {"trade_id": "tsa-9", "status": "FILLED"},
            fill_commit_ts=1_790_000_000.25, fill_px=84000.0, fill_dynamics={"fill_delay_sec": 1.0},
        )

    def drain(self, timeout=5.0):
        worker = self.ns["_fill_evidence_worker"]
        deadline = time.monotonic() + timeout
        while worker is not None and worker.snapshot()["unfinished"] and time.monotonic() < deadline:
            time.sleep(0.01)
        return self.ns["_shutdown_fill_evidence_worker"](timeout=timeout)

    def rows(self, status=None):
        rows = [json.loads(line) for line in self.journal.read_text(encoding="utf-8").splitlines() if line.strip()]
        if status is None:
            return rows
        return [row for row in rows if row.get("status") == status]


STEPS = [
    "action_receipt", "v3_fill", "collector_refresh", "signal_snapshot",
    "persist_signal", "multiverse", "save_positions",
]


def test_post_fill_evidence_is_written_exactly_once(tmp_path):
    rt = EvidenceRuntime(tmp_path)
    assert rt.enqueue() is True
    assert rt.enqueue() is False  # same fill identity: deduplicated
    assert rt.drain()
    assert rt.calls == STEPS
    assert len(rt.rows("APPLIED")) == 1
    assert rt.open_positions[0]["fill_id"] == "fill-tsa-9"
    assert rt.ns["_replay_fill_evidence_handoffs"]() == 0
    assert rt.calls == STEPS


def test_crash_before_apply_is_replayed_once_after_restart(tmp_path):
    rt = EvidenceRuntime(tmp_path)
    real_dispatch = rt.ns["_dispatch_fill_evidence_handoff"]
    rt.ns["_dispatch_fill_evidence_handoff"] = lambda receipt: False  # process died
    assert rt.enqueue() is False
    assert rt.calls == [] and len(rt.rows()) == 1
    rt.ns["_dispatch_fill_evidence_handoff"] = real_dispatch
    assert rt.ns["_replay_fill_evidence_handoffs"]() == 1
    assert rt.drain()
    assert rt.ns["_replay_fill_evidence_handoffs"]() == 0
    assert rt.calls == STEPS and len(rt.rows("APPLIED")) == 1


def test_v3_retry_never_duplicates_completed_steps(tmp_path):
    rt = EvidenceRuntime(tmp_path, v3_failures=1)
    assert rt.enqueue() is True
    assert rt.drain()
    assert rt.calls.count("v3_fill") == 2
    for name in STEPS:
        if name != "v3_fill":
            assert rt.calls.count(name) == 1, name
    assert len(rt.rows("APPLIED")) == 1


def test_fill_thread_pays_only_the_journal_append(tmp_path):
    rt = EvidenceRuntime(tmp_path, v3_delay=0.6)
    started = time.monotonic()
    assert rt.enqueue() is True
    assert time.monotonic() - started < 0.3
    assert rt.drain()
    assert len(rt.rows("APPLIED")) == 1


def test_fill_order_keeps_evidence_io_off_the_fill_thread():
    fill = _function_source("fill_order")
    for name in (
        "dual_write_paper_fill(", "_refresh_collector_v22_registered_order_evidence",
        "patch_signal_snapshot_outcome(", "mark_approve_research_executed(",
        "persist_signal(", "_sync_order_multiverse(", "save_positions(",
        "_append_paper_action_receipt(",
    ):
        assert name not in fill, name
    assert fill.index("_fill_commit_refusal(") < fill.index("_commit_paper_lifecycle_transition(")
    assert fill.index("_finalize_position_open_lifecycle(") < fill.index("_enqueue_fill_evidence_handoff(")


def test_signal_ttl_reconcile_and_touch_claim_respect_the_fill_claim():
    reconcile = _function_source("reconcile_stale_signals")
    assert reconcile.count("tid in fill_handoff_trade_ids") == 2
    touch = _function_source("process_pending_orders")
    claim = touch.index('fill_handoff_trade_ids.add(order["trade_id"])')
    assert touch.index('order.get("cancel_claim_in_progress")') < claim
    assert touch.index("is_terminal_signal(fill_signal)") < claim
    chase6 = _function_source("process_virtual_chase_chase6_market_conversions")
    assert chase6.index('order.get("cancel_claim_in_progress")') < chase6.index("fill_handoff_trade_ids.add(tid)")
