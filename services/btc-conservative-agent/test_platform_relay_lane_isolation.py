"""Focused fail-closed checks for the Fly paper-source -> live relay boundary."""

from __future__ import annotations

import ast
import copy
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit


BOT_SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
BOT_TREE = ast.parse(BOT_SOURCE)


def _function(name: str) -> ast.FunctionDef:
    return next(
        node
        for node in BOT_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _compile_function(name: str, namespace: dict) -> object:
    node = _function(name)
    exec(
        compile(ast.Module(body=[node], type_ignores=[]), f"<{name}-test>", "exec"),
        namespace,
    )
    return namespace[name]


def test_relay_lane_resolution_requires_matching_allowlisted_prefix() -> None:
    namespace = {
        "PLATFORM_RELAY_TRADE_PREFIX_LANES": {"cont": "CONTINUOUS"},
    }
    resolve = _compile_function("_platform_relay_lane_for_event", namespace)

    assert resolve("cont-abc123", "CONTINUOUS") == "CONTINUOUS"
    assert resolve("cont-abc123", None) == "CONTINUOUS"
    assert resolve("cont-abc123", "TYPE_B_HUNTER_V1") == ""
    assert resolve("tbhv1-paper", "TYPE_B_HUNTER_V1") == ""
    assert resolve("tbhv1-paper", "CONTINUOUS") == ""
    assert resolve("bareuuid", "CONTINUOUS") == ""


def test_type_b_chase_and_close_events_stop_before_network_post() -> None:
    warnings: list[str] = []

    class QuietLogger:
        def warning(self, message: str) -> None:
            warnings.append(message)

    class FakeEnvironment:
        @staticmethod
        def getenv(name: str) -> str:
            return {
                "SHOWCASE_RELAY_WEBHOOK_URL": "https://example.invalid/relay",
                "BOT_CONTROL_SECRET": "control",
                "SHOWCASE_WEBHOOK_SECRET": "webhook",
            }.get(name, "")

    namespace = {
        "PLATFORM_RELAY_TRADE_PREFIX_LANES": {"cont": "CONTINUOUS"},
        "PLATFORM_RELAY_ELIGIBLE_LANES": frozenset({"CONTINUOUS"}),
        "is_active_dashboard_owner": lambda: True,
        "_dashboard_owner_metadata": lambda: {},
        "utc_iso": lambda: "2026-07-31T00:00:00Z",
        "logger": QuietLogger(),
        "os": FakeEnvironment(),
    }
    _compile_function("_platform_relay_lane_for_event", namespace)
    push = _compile_function("_push_showcase_relay_event", namespace)

    push(
        "LIMIT_UPDATED",
        "tbhv1-paper",
        {
            "research_lane": "TYPE_B_HUNTER_V1",
            "direction": "LONG",
            "limit_price": 63_900,
            "entry_limit_policy": "deterministic_0.1pct_offset_v1",
            "executable": True,
        },
    )
    push(
        "POSITION_CLOSED",
        "tbhv1-paper",
        {
            "research_lane": "TYPE_B_HUNTER_V1",
            "direction": "LONG",
            "exit_price": 64_100,
        },
    )

    assert len(warnings) == 2
    assert all("blocked non-relay lifecycle" in message for message in warnings)


def test_every_relay_lifecycle_path_is_wired_to_lane_metadata() -> None:
    push_source = ast.get_source_segment(
        BOT_SOURCE,
        _function("_push_showcase_relay_event"),
    )
    emit_source = ast.get_source_segment(BOT_SOURCE, _function("emit_signal_webhook"))
    close_source = ast.get_source_segment(BOT_SOURCE, _function("close_position"))

    assert (
        'event in ("LIMIT_UPDATED", "POSITION_OPENED", "POSITION_CLOSED", "ORDER_EXPIRED")'
        in push_source
    )
    assert "_platform_relay_lane_for_event" in push_source
    assert 'payload["research_lane"] = relay_lane' in push_source
    assert 'isinstance(payload.get("qty"), (int, float))' in push_source
    assert "_platform_relay_lane_for_event" in emit_source
    assert '"research_lane": (' in close_source
    assert 'pos.get("research_lane")' in close_source
    fill_source = ast.get_source_segment(BOT_SOURCE, _function("fill_order"))
    finalize_source = ast.get_source_segment(
        BOT_SOURCE,
        _function("_finalize_position_open_lifecycle"),
    )
    commit_source = ast.get_source_segment(
        BOT_SOURCE,
        _function("_commit_position_open_lifecycle"),
    )
    assert "_finalize_position_open_lifecycle(" in fill_source
    assert '_push_showcase_relay_event(' in finalize_source
    assert '"POSITION_OPENED"' in finalize_source
    assert '"fill_price": fill_px' in finalize_source
    assert '"ts": opened_ts' in finalize_source
    assert "with position_close_lock:" in commit_source
    assert "_position_open_relay_allowed(pos, master)" in commit_source

    chase_source = ast.get_source_segment(BOT_SOURCE, _function("_apply_limit_chase"))
    chase_commit_source = ast.get_source_segment(
        BOT_SOURCE,
        _function("_commit_relay_limit_chase"),
    )
    assert "_commit_relay_limit_chase(" in chase_source
    assert '"qty": float(order.get("qty") or 0)' in chase_commit_source
    assert '"ts": utc_iso()' in chase_commit_source


def test_terminal_close_dominates_a_late_fill_thread() -> None:
    allowed = _compile_function(
        "_position_open_relay_allowed",
        {
            "is_terminal_signal": lambda row: row.get("status") in {"CLOSED", "EXPIRED"},
        },
    )

    assert allowed({"status": "OPEN"}, {"status": "FILLED"}) is True
    assert allowed({"status": "CLOSED"}, {"status": "FILLED"}) is False
    assert allowed({"status": "OPEN", "_close_in_progress": True}, {"status": "FILLED"}) is False
    assert allowed({"status": "OPEN"}, {"status": "CLOSED"}) is False
    assert allowed({"status": "OPEN", "exit_reason": "PROFIT_LOCK_LADDER"}, None) is False


def test_terminal_close_wins_the_actual_open_commit_barrier() -> None:
    lock = threading.Lock()
    calls: list[tuple] = []
    namespace = {
        "copy": copy,
        "time": time,
        "position_close_lock": lock,
        "is_terminal_signal": lambda row: row.get("status") in {"CLOSED", "EXPIRED"},
        "_emit_genome_execution_event": lambda *args: calls.append(("genome", *args)),
        "_push_showcase_relay_event": lambda *args: calls.append(("push", *args)),
        "_relay_mirror": lambda *args, **kwargs: calls.append(("mirror", *args)),
    }
    _compile_function("_position_open_relay_allowed", namespace)
    _compile_function("_commit_position_open_lifecycle", namespace)
    finalize = _compile_function("_finalize_position_open_lifecycle", namespace)
    pos = {"trade_id": "cont-race", "status": "OPEN", "dir": "SHORT", "qty": 0.01}
    master = {"trade_id": "cont-race", "status": "PENDING_ENTRY"}
    order = {"trade_id": "cont-race", "signal_dir": "SHORT", "qty": 0.01}
    started = threading.Event()
    result: list[object] = []

    def late_fill() -> None:
        started.set()
        result.append(finalize(pos, master, master, order, 63_500, "2026-08-11T00:00:00Z"))

    lock.acquire()
    thread = threading.Thread(target=late_fill)
    thread.start()
    assert started.wait(timeout=1)
    pos.update({"status": "CLOSED", "exit_reason": "PROFIT_LOCK_LADDER"})
    master.update({"status": "CLOSED", "outcome": "WIN"})
    lock.release()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert result == [None]
    assert calls == []
    assert pos["status"] == "CLOSED"
    assert master["status"] == "CLOSED"

    pos2 = {"trade_id": "cont-valid", "status": "OPEN", "dir": "SHORT", "qty": 0.01}
    master2 = {"trade_id": "cont-valid", "status": "PENDING_ENTRY"}
    order2 = {"trade_id": "cont-valid", "signal_dir": "SHORT", "qty": 0.01}
    snapshot = finalize(
        pos2,
        master2,
        master2,
        order2,
        63_500,
        "2026-08-11T00:00:01Z",
    )
    assert snapshot["status"] == "FILLED"
    assert snapshot["_persist_ts"] == "2026-08-11T00:00:01Z"
    assert any(call[0] == "push" and call[3]["ts"] == "2026-08-11T00:00:01Z" for call in calls)
    assert any(call[0] == "mirror" for call in calls)


def test_limit_chase_never_emits_after_the_same_trade_is_open() -> None:
    lock = threading.RLock()
    order = {
        "trade_id": "cont-chase-open-race", "status": "PENDING",
        "limit_price": 64_100.0, "limit_chase_count": 1, "qty": 0.01,
    }
    signal = {"research_lane": "CONTINUOUS"}
    pending = [order]
    positions: list[dict] = []
    namespace = {
        "trade_lock": lock,
        "pending_orders": pending,
        "open_positions": positions,
        "_resolve_fill_model": lambda _signal, _order: "SIM_LIMIT",
        "utc_iso": lambda: "2026-08-11T19:50:32.000Z",
    }
    commit = _compile_function("_commit_relay_limit_chase", namespace)

    event = commit(
        order, signal, direction="SHORT", old_limit=64_100.0,
        new_limit=64_090.0, chase_count=2, now=123.0,
    )
    assert event["ts"] == "2026-08-11T19:50:32.000Z"
    assert event["event_seq"] == 2
    assert order["limit_price"] == 64_090.0

    positions.append({"trade_id": "cont-chase-open-race", "status": "OPEN"})
    rejected = commit(
        order, signal, direction="SHORT", old_limit=64_090.0,
        new_limit=64_080.0, chase_count=3, now=124.0,
    )
    assert rejected is None
    assert order["limit_price"] == 64_090.0
    assert order["limit_chase_count"] == 2


def test_relay_keepalive_is_health_only_on_the_exact_webhook_origin() -> None:
    class FakeEnvironment:
        @staticmethod
        def getenv(name: str) -> str:
            if name == "SHOWCASE_RELAY_WEBHOOK_URL":
                return "https://platform.example/api/showcase/relay?ignored=yes"
            return ""

    keepalive_url = _compile_function(
        "_platform_relay_keepalive_url",
        {"os": FakeEnvironment(), "urlsplit": urlsplit},
    )
    assert keepalive_url() == "https://platform.example/api/health"

    loop_source = ast.get_source_segment(
        BOT_SOURCE,
        _function("_platform_relay_connection_keepalive_loop"),
    )
    assert "_relay_http_session.get(" in loop_source
    assert "_relay_http_session.post(" not in loop_source


if __name__ == "__main__":
    test_relay_lane_resolution_requires_matching_allowlisted_prefix()
    test_type_b_chase_and_close_events_stop_before_network_post()
    test_every_relay_lifecycle_path_is_wired_to_lane_metadata()
    test_terminal_close_dominates_a_late_fill_thread()
    test_terminal_close_wins_the_actual_open_commit_barrier()
    test_relay_keepalive_is_health_only_on_the_exact_webhook_origin()
    print("Platform relay lane isolation checks passed")
