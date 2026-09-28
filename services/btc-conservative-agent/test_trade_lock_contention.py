"""Trade-lock contention: bounded holds, bounded /api/pause, stable /ready."""

import ast
import copy
import json
import math
import os
import pickle
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from relay_event_outbox import RelayEventOutbox, _deep_copy

BOT_PATH = Path(__file__).with_name("bot.py")
SOURCE = BOT_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename=str(BOT_PATH))


def _function(name):
    return next(
        node for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _load(*names, **namespace):
    module = ast.Module(body=[_function(name) for name in names], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    return namespace


def _lifecycle_rows(count):
    return [
        {
            "trade_id": f"fch-{index}",
            "research_lane": "FAMILY_CHANDELIER_3",
            "status": "PENDING",
            "qty": 0.001 + index * 1e-6,
            "limit_price": 64000.0 + index,
            "signal_ref": {"ai": {"confidence": 71, "notes": ["x"] * 8}},
            "history": [{"ts": index + step, "px": 64000.0 + step} for step in range(20)],
            "flags": {"relay_eligible": False, "paper": True, "none": None},
        }
        for index in range(count)
    ]


def test_fast_state_copy_is_equal_and_independent():
    ns = _load("_fast_state_copy", pickle=pickle, copy=copy)
    source = {"rows": _lifecycle_rows(5), "nested": {"a": [1, 2.5, None, True]}}
    copied = ns["_fast_state_copy"](source)
    assert copied == source
    copied["rows"][0]["history"][0]["px"] = -1
    copied["nested"]["a"].append("mutated")
    assert source["rows"][0]["history"][0]["px"] == 64000.0
    assert source["nested"]["a"] == [1, 2.5, None, True]


def test_fast_state_copy_falls_back_to_deepcopy_for_unpicklable_values():
    ns = _load("_fast_state_copy", pickle=pickle, copy=copy)
    local_fn = lambda: 1  # noqa: E731 - lambdas pickle-fail but deepcopy by reference
    copied = ns["_fast_state_copy"]({"fn": local_fn, "rows": [1, 2]})
    assert copied["fn"] is local_fn and copied["rows"] == [1, 2]
    with pytest.raises(TypeError):
        # deepcopy of a lock raises too; the helper must not swallow that.
        ns["_fast_state_copy"]({"lock": threading.Lock()})


def test_outbox_deep_copy_is_independent():
    value = {"pending": _lifecycle_rows(3)}
    copied = _deep_copy(value)
    assert copied == value
    copied["pending"][0]["flags"]["paper"] = False
    assert value["pending"][0]["flags"]["paper"] is True


def test_outbox_atomic_write_is_byte_identical_to_streaming_encoder(tmp_path):
    payload = {"schema": "paper_lifecycle_v1", "z": 1, "a": _lifecycle_rows(10),
               "unicode": "µ✓", "float": 0.1 + 0.2}
    outbox = RelayEventOutbox(tmp_path / "paper.json")
    outbox._atomic_write(payload)
    expected = tmp_path / "expected.json"
    with expected.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
    assert (tmp_path / "paper.json").read_bytes() == expected.read_bytes()


def test_outbox_atomic_write_serialization_failure_leaves_no_temp_file(tmp_path):
    outbox = RelayEventOutbox(tmp_path / "paper.json")
    with pytest.raises(TypeError):
        outbox._atomic_write({"bad": object()})
    assert not any(p.name.endswith(".tmp") for p in tmp_path.iterdir())


def test_lifecycle_decorate_and_write_hold_is_bounded(tmp_path):
    """The work done under trade_lock for one transition stays well under budget."""
    outbox = RelayEventOutbox(tmp_path / "paper.json")
    payload = {"schema": "paper_lifecycle_v1", "paper_only": True, "live_armed": False,
               "pending_orders": _lifecycle_rows(400), "positions": _lifecycle_rows(40),
               "awaiting_signals": []}
    outbox._atomic_write(outbox.decorate_lifecycle(payload))
    samples = []
    for _ in range(5):
        started = time.perf_counter()
        target = _deep_copy(payload)
        target["pending_orders"][0]["limit_price"] += 1
        outbox._atomic_write(outbox.decorate_lifecycle(target))
        samples.append(time.perf_counter() - started)
    assert sorted(samples)[len(samples) // 2] < 0.5, samples


def _tracked_lock_cls():
    namespace = {
        "threading": threading, "time": time, "sys": sys,
        "traceback": __import__("traceback"), "Path": Path,
        "TRADE_LOCK_HOLD_BUDGET_MS": 50.0, "_TRACKED_LOCK_SITE_MAX": 4,
    }
    node = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "_TrackedRLock")
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    cls = namespace["_TrackedRLock"]
    namespace["_TRACKED_LOCK_INTERNAL_CODES"] = frozenset(
        {cls.acquire.__code__, cls.__enter__.__code__}
    )
    return cls


def test_tracked_lock_records_hold_duration_and_over_budget_site():
    lock = _tracked_lock_cls()("trade_lock", hold_budget_ms=50.0)

    def quick_holder():
        with lock:
            pass

    def slow_holder():
        with lock:
            time.sleep(0.08)

    quick_holder()
    slow_holder()
    with lock:
        with lock:  # re-entrant acquire must not count as a second hold
            pass
    stats = lock.hold_stats()
    assert stats["holds"] == 3
    assert stats["over_budget"] == 1
    assert stats["max_ms"] >= 70.0
    assert stats["max_site"].startswith("slow_holder:")
    assert stats["top_sites"][0]["site"].startswith("slow_holder:")
    assert "acquire_site" in lock.diagnostics()


def test_zero_wait_ready_grace_is_bounded_and_watchdog_stays_strict():
    ns = _load("_trade_lock_probe_status", math=math, WATCHDOG_TRADE_LOCK_TIMEOUT_SEC=2.0)
    classify = ns["_trade_lock_probe_status"]
    busy = {"owner_ident": 7, "owner_active": True, "held_seconds": 3.2}
    assert classify(False, busy, 0.0) == (False, False)
    assert classify(False, busy, 0.0, busy_grace_sec=10.0) == (True, True)
    assert classify(False, {**busy, "held_seconds": 10.5}, 0.0, busy_grace_sec=10.0) == (False, False)
    assert classify(False, {**busy, "owner_active": False}, 0.0, busy_grace_sec=10.0) == (False, False)
    # The watchdog's bounded acquire (timeout=None/positive) never gets grace.
    assert classify(False, busy, None, busy_grace_sec=10.0) == (False, False)
    assert classify(False, busy, 2.0, busy_grace_sec=10.0) == (False, False)


def test_ready_grace_constant_is_capped():
    assert "READY_TRADE_LOCK_BUSY_GRACE_SEC = min(\n    60.0," in SOURCE
    body = ast.get_source_segment(SOURCE, _function("_strategy_progress_health_snapshot"))
    assert "busy_grace_sec=lock_busy_grace_sec" in body
    assert '"trade_lock_hold_stats"' in body


def test_api_pause_commits_intent_before_any_trade_lock_wait():
    body = ast.get_source_segment(SOURCE, _function("api_pause"))
    assert "with trade_lock" not in body
    assert "_disarm_live_control(" not in body
    assert "set_execution_paused(" not in body
    intent = body.index('state["manual_admin_pause"] = True')
    disarm = body.index('state["live_armed"] = False')
    persist = body.index("save_persistent_config()")
    join = body.index("worker.join(timeout=PAUSE_FINALIZE_WAIT_SEC)")
    assert intent < disarm < persist < join
    assert "response.status_code = 202" in body


@pytest.fixture(scope="module")
def bot_module():
    os.environ.setdefault("FORCE_PAPER_MODE", "1")
    os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
    os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")
    import bot
    return bot


def test_api_pause_returns_fast_while_trade_lock_is_held(bot_module, monkeypatch):
    bot = bot_module
    config_dir = tempfile.mkdtemp(prefix="pause-contention-")
    monkeypatch.setattr(bot, "get_config_file", lambda: os.path.join(config_dir, "config.json"))
    monkeypatch.setattr(
        bot, "_disarm_live_control",
        lambda reason="TEST": {"cancel": {"failed": [], "cancelled": []}, "exit_only": {}},
    )
    monkeypatch.setattr(bot, "PAUSE_FINALIZE_WAIT_SEC", 0.5)
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot, "pipeline_state_sync", lambda: None)
    with bot.state_lock:
        for key in bot._persistent_config_keys() + ["_threshold_locked", "bootstrap_done"]:
            bot.state.setdefault(key, None)
        bot.state.update({
            "manual_admin_pause": False, "pause_intent": None,
            "execution_paused": False, "execution_reason": "", "_pause_priority": 0,
        })

    holding = threading.Event()
    release = threading.Event()

    def hold_trade_lock():
        with bot.trade_lock:
            holding.set()
            release.wait(10)

    holder = threading.Thread(target=hold_trade_lock, daemon=True)
    holder.start()
    assert holding.wait(5)
    try:
        started = time.monotonic()
        with bot.app.test_client() as client:
            response = client.post(
                "/api/pause", json={"owner": "DEPLOY_MAINTENANCE"},
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )
        elapsed = time.monotonic() - started
        body = response.get_json() or {}
        assert elapsed < 3.0, elapsed
        assert response.status_code == 202, body
        assert body["finalization"] == "IN_PROGRESS"
        assert body["pause_intent_durable"] is True
        with bot.state_lock:
            assert bot.state["manual_admin_pause"] is True
            assert bot.state["execution_paused"] is True
            assert bot.state["live_armed"] is False
            assert bot.state["bitfinex_live_enabled"] is False
        saved = json.loads(Path(bot.get_config_file()).read_text(encoding="utf-8"))
        assert saved["manual_admin_pause"] is True
    finally:
        release.set()
        holder.join(5)
    worker = bot._pause_finalization_current["thread"]
    worker.join(10)
    assert not worker.is_alive()
    assert bot._pause_finalization_current["result"]["done"] is True
