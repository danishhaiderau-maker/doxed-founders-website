"""Ledger/funnel failure alarms, relay stale-owner alarm, /api/state pause truth."""
import ast
import sys
import threading
import time
from pathlib import Path

import thread_health as th
from relay_delivery_guard import RelayDeliveryGuard, STALE_OWNER_ALARM_SEC

BOT = Path(__file__).with_name("bot.py")
SOURCE = BOT.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def load(names, ns):
    nodes = [n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "bot.py", "exec"), ns)
    return ns


def collection_ns(now, *, ledger=None, funnel=None, guard=None):
    clock = lambda: now
    return load(["research_collection_health"], {
        "time": time, "COLLECTION_HEALTH_WINDOW_SEC": 3600.0,
        "_collection_stats_lock": threading.Lock(), "_collection_counters": {},
        "_collection_multiverse_recent": [], "_collection_touch_grid_recent": [],
        "_collector_tape_store_obj": None, "_order_multiverse_pending_src": {},
        "COLLECTION_EMPTY_PATH_ALARM_MIN_ROWS": 10, "COLLECTION_EMPTY_PATH_ALARM_RATE": 0.5,
        "COLLECTOR_TAPE_REFRESH_FRESH_SEC": 60, "_collector_maturation_worker_status": {},
        "_collector_v3_reconcile_status": {}, "COLLECTOR_WORKER_RESTART_ALARM_SEC": 600,
        "COLLECTION_TOUCH_GRID_ALARM_MIN_CALLS": 10, "COLLECTION_TOUCH_GRID_ALARM_COVERAGE": 0.5,
        "COLLECTOR_LATE_MATURATION_SEC": 60, "COLLECTOR_FINALIZABLE_BACKLOG_ALARM_SEC": 1800.0,
        "_LEDGER_WRITES": ledger or th.FailureCounters(clock=clock),
        "_FUNNEL_HOOK_FAILURES": funnel or th.FailureCounters(clock=clock),
        "_relay_delivery_guard": guard or RelayDeliveryGuard(Path("unused.jsonl"), clock=clock),
    })["research_collection_health"]


def test_clean_runtime_has_no_new_alarms():
    health = collection_ns(10_000.0)(10_000.0)
    assert health["alarms"] == [] and health["status"] == "OK"
    assert health["runtime_failures"]["ledger_write_failures_total"] == 0


def test_recent_ledger_failure_alarms_then_clears_after_window():
    clock = {"now": 10_000.0}
    ledger = th.FailureCounters(clock=lambda: clock["now"])
    ledger.success("lane_pnl")
    ledger.failure("lane_pnl", OSError("disk full"))
    health = collection_ns(clock["now"], ledger=ledger)(clock["now"])
    assert "LEDGER_WRITE_FAILURES" in health["alarms"]
    assert health["runtime_failures"]["ledger_write_failures_recent"] == ["lane_pnl"]
    later = clock["now"] + 3601
    health = collection_ns(later, ledger=ledger)(later)
    assert "LEDGER_WRITE_FAILURES" not in health["alarms"]
    assert health["runtime_failures"]["ledger_write_failures_total"] == 1


def test_funnel_hook_failure_alarms():
    funnel = th.FailureCounters(clock=lambda: 10_000.0)
    funnel.failure("fill", KeyError("lane"))
    health = collection_ns(10_000.0, funnel=funnel)(10_000.0)
    assert "EXECUTION_FUNNEL_HOOK_FAILURES" in health["alarms"]


def test_never_implemented_funnel_hook_is_reported_not_alarmed():
    funnel = th.FailureCounters(clock=lambda: 10_000.0)
    try:
        # funnel_on_limit_chase now exists; probe a hook that never did.
        from execution_funnel import funnel_on_never_implemented_hook  # noqa: F401
    except ImportError as exc:
        funnel.failure("limit_chase", exc)
    health = collection_ns(10_000.0, funnel=funnel)(10_000.0)
    assert "EXECUTION_FUNNEL_HOOK_FAILURES" not in health["alarms"]
    assert health["runtime_failures"]["execution_funnel_hooks_unavailable"] == ["limit_chase"]


def test_relay_stale_owner_alarm_after_thirty_minutes(tmp_path):
    clock = {"now": 10_000.0}
    guard = RelayDeliveryGuard(tmp_path / "q.jsonl", clock=lambda: clock["now"])
    stale = {"event_id": "e", "bot_instance_id": "old", "created_at_unix": 1.0}
    guard.observe([stale], owner_id="me", armed=False, armed_at_ts=None)
    assert "RELAY_OUTBOX_STALE_OWNER_PENDING" not in collection_ns(clock["now"], guard=guard)(clock["now"])["alarms"]
    clock["now"] += STALE_OWNER_ALARM_SEC + 1
    later = clock["now"]
    assert "RELAY_OUTBOX_STALE_OWNER_PENDING" in collection_ns(later, guard=guard)(later)["alarms"]


def test_monitor_rules_alert_on_new_alarm_codes():
    sys.path.insert(0, str(BOT.resolve().parents[2] / "scripts"))
    import fly_monitor_rules as rules

    health = {
        "research_collection": {
            "alarms": ["RELAY_OUTBOX_STALE_OWNER_PENDING", "LEDGER_WRITE_FAILURES",
                       "EXECUTION_FUNNEL_HOOK_FAILURES"],
            "runtime_failures": {"ledger_write_failures_recent": ["lane_pnl"],
                                 "execution_funnel_hook_failures_recent": ["fill"]},
        },
        "relay_outbox": {"stale_owner_pending": 22, "stale_owner_age_sec": 2000.0},
    }
    findings = rules.collection_findings(health)
    assert set(findings) == {"relay_outbox_stale_owner", "ledger_write_failures",
                             "execution_funnel_hook_failures"}
    assert "22" in findings["relay_outbox_stale_owner"]
    assert rules.collection_findings({"research_collection": {"alarms": []}}) == {}


def overlay_ns(state):
    return load(["_api_state_live_overlay", "_operating_pause_truth", "_pause_owner_locked"], {
        "time": time, "state": state, "_PAUSE_INTENTS": {"OPERATOR"},
        "PAUSE_OWNER_UNATTRIBUTED": "UNATTRIBUTED", "PAUSE_OWNER_SAFETY": "SAFETY",
    })


def test_api_state_serves_live_pause_not_cached_pause():
    cached = {
        "execution_paused": False, "manual_admin_pause": False, "pause_owner": None,
        "dashboard_truth": {"operating": {"pause": {"paused": False}, "disk": {"x": 1}}},
        "trades": [1, 2],
    }
    state = {"execution_paused": True, "manual_admin_pause": True, "pause_intent": "OPERATOR",
             "execution_reason": "ADMIN_MANUAL"}
    out = overlay_ns(state)["_api_state_live_overlay"](cached, 990.0, now=1000.0)
    assert out["execution_paused"] is True and out["pause_owner"] == "OPERATOR"
    assert out["dashboard_truth"]["operating"]["pause"]["paused"] is True
    assert out["dashboard_truth"]["operating"]["pause"]["owner"] == "OPERATOR"
    assert out["dashboard_truth"]["operating"]["disk"] == {"x": 1}
    assert out["api_state_built_at"] == 990.0 and out["api_state_age_sec"] == 10.0
    assert out["api_state_pause_source"] == "LIVE"
    assert cached["execution_paused"] is False
    assert cached["dashboard_truth"]["operating"]["pause"]["paused"] is False


def test_api_state_route_applies_overlay_to_both_views():
    route = ast.get_source_segment(SOURCE, next(
        n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == "api_state"
    ))
    assert "_api_state_live_overlay(cached, built_at)" in route
    assert 'payload["api_state_age_sec"]' in route


def test_ledger_writes_and_funnel_hooks_are_counted_in_bot():
    assert SOURCE.count('_LEDGER_WRITES.failure("lane_pnl", exc)') == 1
    assert SOURCE.count('_LEDGER_WRITES.failure("lane_lab_pnl", exc)') == 1
    assert SOURCE.count("_FUNNEL_HOOK_FAILURES.failure(") >= 10
    assert '"execution_funnel": {"hook_failures": _FUNNEL_HOOK_FAILURES.snapshot(now)}' in SOURCE


def test_status_exposes_shipper_rate_limits_and_registry_toggles():
    for key in ('"shipper":', '"rate_limits":', '"ledgers":', '"relay_outbox":'):
        assert key in SOURCE
    assert "tile_registry = _tile_rows_with_toggles(active_tile_lifecycle_manifest())" in SOURCE
    ns = load(["_tile_rows_with_toggles"], {"is_research_lane_enabled": lambda lane: lane == "A"})
    rows = ns["_tile_rows_with_toggles"]([{"lane": "A"}, {"lane": "B"}])
    assert [row["toggle_on"] for row in rows] == [True, False]
