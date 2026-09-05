"""Execute the production reset wrapper without importing the live bot app."""
import ast
from pathlib import Path
import threading
from unittest.mock import Mock

import pytest


@pytest.fixture
def reset_env():
    path = Path(__file__).with_name("bot.py")
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
              and node.name == "_perform_fresh_collection_reset_locked")
    events = []
    env = {
        "state_lock": threading.RLock(), "trade_lock": threading.RLock(),
        "state": {"execution_paused": True, "live_armed": False},
        "pending_orders": [], "open_positions": [],
        "_LIFECYCLE_PIPELINE_RUNTIME": object(),
        "_FRESH_RESET_LIFECYCLE_RESTART_PENDING": False,
        "_data_sync_inventory_cache_condition": threading.Condition(),
        "_data_sync_sqlite_snapshot_condition": threading.Condition(),
        "_data_sync_inventory_cache": {}, "_data_sync_async_inventory": {},
        "_data_sync_sqlite_snapshot_states": {},
        "_research_write_gate": threading.RLock(),
        "_collector_epoch_lock": threading.RLock(),
    }
    env["_stop_lifecycle_pipeline_runtime"] = Mock(side_effect=lambda **_: events.append("stop") or True)
    env["_raw_generation_cleanup_gate_acquire"] = Mock(side_effect=lambda: events.append("lease") or True)
    env["_raw_generation_cleanup_gate_release"] = Mock(side_effect=lambda: events.append("release"))

    def reset_body(**kwargs):
        assert env["_research_write_gate"]._is_owned()
        assert env["_collector_epoch_lock"]._is_owned()
        assert env["_data_sync_inventory_cache_condition"]._is_owned()
        assert env["_data_sync_sqlite_snapshot_condition"]._is_owned()
        events.append("archive_delete_new_epoch")
        env["epoch"] = "new-epoch"
        return {"ok": True, "local_signal_sent": kwargs["send_local_signal"]}

    def start():
        assert env["epoch"] == "new-epoch"
        assert not env["_research_write_gate"]._is_owned()
        assert not env["_collector_epoch_lock"]._is_owned()
        assert not env["_data_sync_inventory_cache_condition"]._is_owned()
        assert not env["_data_sync_sqlite_snapshot_condition"]._is_owned()
        events.append("restart")
        return True

    env["_perform_fresh_collection_reset_quiesced"] = Mock(side_effect=reset_body)
    env["_start_lifecycle_pipeline_runtime"] = Mock(side_effect=start)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), "exec"), env)
    env["events"] = events
    return env


def invoke(env, **kwargs):
    return env["_perform_fresh_collection_reset_locked"](**kwargs)


@pytest.mark.parametrize("send_local_signal", [True, False])
def test_stop_lease_reset_release_restart_new_identity(reset_env, send_local_signal):
    result = invoke(reset_env, send_local_signal=send_local_signal)
    assert result["ok"] is True
    assert result["local_signal_sent"] is send_local_signal
    assert result["lifecycle_restarted_on_new_epoch"] is True
    assert reset_env["events"] == ["stop", "lease", "archive_delete_new_epoch", "release", "restart"]
    assert reset_env["state"]["execution_paused"] is True


@pytest.mark.parametrize("boundary", ["unpaused", "armed", "pending", "open"])
def test_unsafe_boundary_does_not_touch_owner_or_archive(reset_env, boundary):
    if boundary == "unpaused": reset_env["state"]["execution_paused"] = False
    if boundary == "armed": reset_env["state"]["live_armed"] = True
    if boundary == "pending": reset_env["pending_orders"].append({})
    if boundary == "open": reset_env["open_positions"].append({})
    assert invoke(reset_env)["wipe_aborted"] is True
    assert reset_env["events"] == []


def test_failed_stop_aborts_before_archive(reset_env):
    reset_env["_stop_lifecycle_pipeline_runtime"].side_effect = None
    reset_env["_stop_lifecycle_pipeline_runtime"].return_value = False
    assert invoke(reset_env)["error"] == "fresh_collection_lifecycle_not_quiescent"
    reset_env["_perform_fresh_collection_reset_quiesced"].assert_not_called()
    reset_env["_raw_generation_cleanup_gate_acquire"].assert_not_called()
    reset_env["_start_lifecycle_pipeline_runtime"].assert_not_called()


@pytest.mark.parametrize("kind", ["inventory", "async", "worker", "snapshot", "lease", "scheduler", "writer", "epoch"])
def test_active_owner_aborts_before_archive_and_releases(reset_env, kind):
    if kind == "inventory": reset_env["_data_sync_inventory_cache"]["refreshing"] = True
    if kind == "async": reset_env["_data_sync_async_inventory"]["refreshing"] = True
    if kind == "worker": reset_env["_data_sync_async_inventory"]["worker_active"] = True
    if kind == "snapshot": reset_env["_data_sync_sqlite_snapshot_states"]["one"] = {"status": "BUILDING"}
    if kind == "lease": reset_env["_raw_generation_cleanup_gate_acquire"].side_effect = lambda: False
    if kind == "scheduler":
        reset_env["_data_sync_sqlite_snapshot_condition"] = Mock()
        reset_env["_data_sync_sqlite_snapshot_condition"].acquire.return_value = False
    if kind == "writer":
        reset_env["_research_write_gate"] = Mock()
        reset_env["_research_write_gate"].acquire.return_value = False
    if kind == "epoch":
        reset_env["_collector_epoch_lock"] = Mock()
        reset_env["_collector_epoch_lock"].acquire.return_value = False
    assert invoke(reset_env)["wipe_aborted"] is True
    reset_env["_perform_fresh_collection_reset_quiesced"].assert_not_called()
    reset_env["_start_lifecycle_pipeline_runtime"].assert_not_called()
    assert not reset_env["_data_sync_inventory_cache_condition"]._is_owned()
    if kind in {"writer", "epoch"}: reset_env["_raw_generation_cleanup_gate_release"].assert_called_once()


@pytest.mark.parametrize("failure", ["result", "exception"])
def test_reset_failure_never_restarts_old_epoch(reset_env, failure):
    body = reset_env["_perform_fresh_collection_reset_quiesced"]
    if failure == "exception":
        body.side_effect = RuntimeError("archive failure")
        with pytest.raises(RuntimeError): invoke(reset_env)
    else:
        body.side_effect = lambda **_: {"ok": False, "wipe_aborted": True}
        assert invoke(reset_env)["ok"] is False
    reset_env["_start_lifecycle_pipeline_runtime"].assert_not_called()
    reset_env["_raw_generation_cleanup_gate_release"].assert_called_once()
    assert reset_env["state"]["execution_paused"] is True


def test_restart_failure_does_not_disguise_completed_reset(reset_env):
    reset_env["_start_lifecycle_pipeline_runtime"].side_effect = lambda: False
    result = invoke(reset_env)
    assert result["ok"] is False and result["reset_completed"] is True
    assert result["error"] == "fresh_collection_lifecycle_restart_failed"
    assert result.get("wipe_aborted") is not True
    assert reset_env["state"]["execution_paused"] is True


def test_absent_optional_lifecycle_not_enabled_by_reset(reset_env):
    reset_env["_LIFECYCLE_PIPELINE_RUNTIME"] = None
    assert invoke(reset_env)["ok"] is True
    reset_env["_start_lifecycle_pipeline_runtime"].assert_not_called()


def test_successful_retry_restarts_owner_stopped_by_previous_abort(reset_env):
    reset_env["_data_sync_async_inventory"]["worker_active"] = True
    assert invoke(reset_env)["wipe_aborted"] is True
    assert reset_env["_FRESH_RESET_LIFECYCLE_RESTART_PENDING"] is True
    reset_env["_LIFECYCLE_PIPELINE_RUNTIME"] = None
    reset_env["_data_sync_async_inventory"].clear()
    assert invoke(reset_env)["lifecycle_restarted_on_new_epoch"] is True
    assert reset_env["_FRESH_RESET_LIFECYCLE_RESTART_PENDING"] is False


@pytest.mark.parametrize("preserve", [True, False])
def test_runtime_reset_never_transiently_unpauses_when_requested(preserve):
    path = Path(__file__).with_name("bot.py")
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "reset_runtime_state")
    observations = []

    class ObservedState(dict):
        def update(self, values):
            observations.append(values["execution_paused"])
            super().update(values)

    env = {"logger": Mock(), "time": Mock(), "state_lock": threading.RLock(),
           "state": ObservedState(execution_paused=True, execution_reason="ADMIN_MANUAL", _pause_priority=8),
           "STARTING_BALANCE": 500, "_fresh_debug_state": lambda: {},
           **{name: [] for name in ("trades", "pending_orders", "expired_orders", "open_positions")},
           "trades_map": {}}
    exec(compile(ast.Module(body=[helper], type_ignores=[]), str(path), "exec"), env)
    env["reset_runtime_state"](preserve_execution_pause=preserve)
    assert observations == [preserve]
    assert env["state"]["execution_reason"] == ("ADMIN_MANUAL" if preserve else "")
    assert env["state"]["_pause_priority"] == (8 if preserve else 0)
    # Verify the actual fresh-reset body opts into the tested helper behavior.
    body = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "_perform_fresh_collection_reset_quiesced")
    calls = [n for n in ast.walk(body) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "reset_runtime_state"]
    assert len(calls) == 1
    assert any(k.arg == "preserve_execution_pause" and isinstance(k.value, ast.Constant)
               and k.value.value is True for k in calls[0].keywords)


def test_real_epoch_contention_aborts_without_waiting_or_research_acquire(reset_env):
    acquired = threading.Event()
    release = threading.Event()

    def owner():
        with reset_env["_collector_epoch_lock"]:
            acquired.set()
            release.wait(5)

    thread = threading.Thread(target=owner)
    thread.start()
    assert acquired.wait(2)
    try:
        assert invoke(reset_env)["error"] == "fresh_collection_epoch_writer_busy"
        reset_env["_perform_fresh_collection_reset_quiesced"].assert_not_called()
        assert not reset_env["_research_write_gate"]._is_owned()
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()
