import ast
import copy
from pathlib import Path

import pytest


BOT = Path(__file__).with_name("bot.py")
SOURCE = BOT.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def load_function(name, namespace):
    node = next(
        item for item in TREE.body
        if isinstance(item, ast.FunctionDef) and item.name == name
    )
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(BOT), "exec"), namespace)
    return namespace[name]


class QuietLogger:
    def info(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass


@pytest.mark.parametrize(
    "process_result,expected",
    [
        ({"entry_resolution": "NO_ORDER", "exact_reason": "READINESS_STABILIZING"},
         ("NO_ORDER", "READINESS_STABILIZING")),
        ({"entry_resolution": "NO_ORDER", "exact_reason": "DUPLICATE_LIMIT_PRICE"},
         ("NO_ORDER", "DUPLICATE_LIMIT_PRICE")),
        ({"entry_resolution": "AWAITING", "exact_reason": "AWAITING_DASHBOARD_CHASE"},
         ("AWAITING", "AWAITING_DASHBOARD_CHASE")),
        (None, ("NO_ORDER", "PIPELINE_RETURNED_WITHOUT_ENTRY_RESOLUTION")),
        ({}, ("NO_ORDER", "PIPELINE_RETURNED_WITHOUT_ENTRY_RESOLUTION")),
    ],
)
def test_spawn_resolves_readiness_duplicate_and_chase_wait(process_result, expected):
    writes = []
    namespace = {
        "copy": copy,
        "RESEARCH_LANE_CONTINUOUS": "CONTINUOUS",
        "is_research_data_collection": lambda: True,
        "guard_retired_lane_execution": lambda *_args: True,
        "_enrich_combo_lane_features": lambda features, _ctx: features,
        "is_research_lane_enabled": lambda _lane: True,
        "RESEARCH_LANE_OFFSET_029_ATR_TP_25": "PATIENT",
        "_shared_ai_call_id": lambda ai_result=None, ctx=None: (ai_result or ctx)["shared_ai_call_id"],
        "allocate_lane_trade_id": lambda _lane: "child-1",
        "get_exit_config_for_lane": lambda _lane: {"policy": "TEST"},
        "logger": QuietLogger(),
        "process_signal": lambda _event: process_result,
        "state": {"price": 100},
        "nz": lambda value: value,
        "utc_iso": lambda: "2026-08-22T00:00:00+00:00",
        "_append_v3_lane_entry_resolution": (
            lambda source, lane, resolution, reason:
            writes.append((source, lane, resolution, reason))
        ),
        "log_lane_opportunity_event": lambda *_args, **_kwargs: None,
        "_spawn_lab_combo_shadow": lambda *_args, **_kwargs: None,
    }
    spawn = load_function("_spawn_combo_lane", namespace)
    spawn(
        {"shared_ai_call_id": "scan-1"},
        {"decision": "APPROVE", "direction": "LONG", "shared_ai_call_id": "scan-1"},
        1.0, {}, "CONTINUOUS", "TEST",
    )
    assert len(writes) == 1
    assert writes[0][2:] == expected
    assert writes[0][0]["shared_ai_call_id"] == "scan-1"


def test_preorder_ttl_is_no_order_but_submitted_expiry_is_not():
    writes = []
    namespace = {
        "_append_v3_lane_entry_resolution": lambda *args: writes.append(args),
    }
    resolve = load_function("_v3_record_preorder_terminal_if_needed", namespace)
    row = {"research_lane": "CONTINUOUS", "shared_ai_call_id": "scan-1"}
    assert resolve({}, {}, row, "SIGNAL_TTL_EXPIRED") is True
    assert writes[-1][2:] == ("NO_ORDER", "SIGNAL_TTL_EXPIRED")
    assert resolve({"order_placed": True}, {}, row, "SIGNAL_TTL_EXPIRED") is False
    assert len(writes) == 1


@pytest.mark.parametrize(
    "reason",
    ["VIRTUAL_TOUCH_BEFORE_SELECTED_ENTRY", "STALE_NO_EXPOSURE"],
)
def test_preorder_retirement_reasons_close_expected_order_lifecycle(reason):
    writes = []
    namespace = {
        "_append_v3_lane_entry_resolution": lambda *args: writes.append(args),
    }
    resolve = load_function("_v3_record_preorder_terminal_if_needed", namespace)
    row = {"research_lane": "FAMILY_ATR_TRAIL", "shared_ai_call_id": "scan-1"}

    assert resolve({}, {}, row, reason) is True
    assert writes == [(row, "FAMILY_ATR_TRAIL", "NO_ORDER", reason)]

    # A submitted-order expiry belongs to the order lifecycle and must never be
    # rewritten as a pre-order NO_ORDER terminal.
    assert resolve({"submitted_order_trade_id": "paper-1"}, {}, row, reason) is False
    assert len(writes) == 1


def test_combo_execution_enqueue_freezes_shared_call_snapshot():
    submitted = []

    class Worker:
        def submit(self, key, payload, source_ts=None):
            submitted.append((key, payload, source_ts))
            return True

    namespace = {
        "copy": copy,
        "time": type("Clock", (), {"time": staticmethod(lambda: 123.0)}),
        "_shared_ai_call_id": lambda ai_result=None, ctx=None: (ai_result or ctx)["shared_ai_call_id"],
        "_get_combo_lane_execution_worker": lambda _lane: Worker(),
        "logger": QuietLogger(),
    }
    enqueue = load_function("_enqueue_combo_lane_execution", namespace)
    ctx = {"shared_ai_call_id": "scan-old", "nested": {"value": 1}}
    ai = {"shared_ai_call_id": "scan-old", "direction": "LONG"}
    assert enqueue(ctx, ai, 2.5, {"adx": 30}, "OFFSET_029_ATR_TP_25", "TEST")
    ctx["shared_ai_call_id"] = "scan-new"
    ctx["nested"]["value"] = 2
    ai["shared_ai_call_id"] = "scan-new"

    key, payload, source_ts = submitted[0]
    assert key == "OFFSET_029_ATR_TP_25:scan-old"
    assert payload["ctx"]["shared_ai_call_id"] == "scan-old"
    assert payload["ctx"]["nested"]["value"] == 1
    assert payload["ai"]["shared_ai_call_id"] == "scan-old"
    assert source_ts == 123.0


def test_ai_fanout_queues_lane_execution_instead_of_blocking_scheduler():
    fanout = ast.get_source_segment(
        SOURCE, next(item for item in TREE.body if isinstance(item, ast.FunctionDef)
                     and item.name == "spawn_combo_lanes_from_ai_scan"),
    )
    assert "_enqueue_combo_lane_execution(" in fanout
    assert "_spawn_combo_lane(" not in fanout


def test_family_decision_stamps_dashboard_history_before_v3_ledger_write():
    fanout = ast.get_source_segment(
        SOURCE, next(item for item in TREE.body if isinstance(item, ast.FunctionDef)
                     and item.name == "spawn_combo_lanes_from_ai_scan"),
    )
    stamp = fanout.index("_stamp_shared_ai_lane_verdict(")
    ledger = fanout.index("_write_v3_shared_lane_decision(")
    assert stamp < ledger
    assert "policy_accepted" in fanout[stamp:ledger]
    assert "decision_reason" in fanout[stamp:ledger]


def test_verdict_and_resolution_share_one_policy_material_builder():
    decision = ast.get_source_segment(
        SOURCE, next(item for item in TREE.body if isinstance(item, ast.FunctionDef)
                     and item.name == "_write_v3_shared_lane_decision"),
    )
    resolution = ast.get_source_segment(
        SOURCE, next(item for item in TREE.body if isinstance(item, ast.FunctionDef)
                     and item.name == "_append_v3_lane_entry_resolution"),
    )
    assert "lane_policy = _v3_lane_policy_material(lane)" in decision
    assert "lane_policy=_v3_lane_policy_material(lane)" in resolution
    assert '"entry_ttl_sec": float(SIGNAL_TTL_SEC)' in SOURCE
    assert "dual_write_paper_order_intent(" in SOURCE


def test_pending_registration_freezes_policy_identity_before_async_evidence_and_fill():
    register = ast.get_source_segment(
        SOURCE, next(item for item in TREE.body if isinstance(item, ast.FunctionDef)
                     and item.name == "lane_register_pending_order"),
    )
    fill = ast.get_source_segment(
        SOURCE, next(item for item in TREE.body if isinstance(item, ast.FunctionDef)
                     and item.name == "fill_order"),
    )
    assert "frozen_identity = paper_policy_identity_for_sources(" in register
    assert "order.update(copy.deepcopy(frozen_identity))" in register
    assert "master_signal.update(copy.deepcopy(frozen_identity))" not in register
    assert register.index("order,", register.index("paper_policy_identity_for_sources(")) < register.index(
        "master_signal if isinstance(master_signal, dict) else {}",
        register.index("paper_policy_identity_for_sources("),
    )
    assert '"order": copy.deepcopy(order)' in register
    assert "paper_policy_identity_for_sources(" in fill

