"""Regression tests for bounded relay presentation snapshots."""
from __future__ import annotations

import ast
import json
import os
import threading
from pathlib import Path


BOT_PATH = Path(__file__).with_name("bot.py")
SOURCE = BOT_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _functions(*names):
    selected = [
        node for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
    namespace = {
        "trades": [], "_DASHBOARD_TRADES_MAX": 5,
        "_RELAY_TRADES_MAP_MAX": 512, "_RELAY_FIDELITY_TRADES_MAX": 512,
        "_trade_row_in_session": lambda row, start: float(row.get("closed_epoch") or 0) >= start,
    }
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    return namespace


def test_relay_execution_builder_never_deepcopies_closed_trade_rows():
    function = next(node for node in TREE.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_build_relay_execution_state_snapshot")
    body = ast.get_source_segment(SOURCE, function)
    assert "_snapshot_relay_trade_projections_locked" in body
    assert "copy.deepcopy(trades[" not in body
    assert "_snapshot_trade_rows_locked(session_start)" not in body


def test_legacy_relay_state_builder_uses_same_bounded_trade_projection():
    function = next(node for node in TREE.body if isinstance(node, ast.FunctionDef)
                    and node.name == "api_relay_state")
    body = ast.get_source_segment(SOURCE, function)
    assert "_snapshot_relay_trade_projections_locked(session_start)" in body
    assert "copy.deepcopy(trades[" not in body
    assert "_snapshot_trade_rows_locked(session_start)" not in body


def test_all_runtime_relay_evidence_consumers_share_file_identity_cache():
    function = next(node for node in TREE.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_platform_relay_evidence_index")
    body = ast.get_source_segment(SOURCE, function)
    assert "_load_dashboard_trade_enrichment()[1]" in body
    assert "os.path.abspath(path)" in body
    assert "_pure_platform_relay_evidence_index(path)" in body


def test_projection_shape_is_bounded_and_ignores_oversized_research_fields():
    namespace = _functions(
        "_relay_trade_row_lite", "_relay_fidelity_trade_row",
        "_relay_trade_enrichment_row_lite", "_snapshot_relay_trade_projections_locked",
    )

    class DeepcopyForbidden:
        def __deepcopy__(self, _memo):
            raise AssertionError("oversized research context was deep-copied")

    bulky_fields = {
        "entry_features": {"ticks": [DeepcopyForbidden()] * 10000},
        "entry_controls": {"grid": [DeepcopyForbidden()] * 10000},
        "entry_indicators": {"candles": [DeepcopyForbidden()] * 10000},
        "entry_context": {"path": [DeepcopyForbidden()] * 10000},
        "exit_context": {"path": [DeepcopyForbidden()] * 10000},
        "research_replay": {"ticks": [DeepcopyForbidden()] * 10000},
        "decision_ai_reason": "x" * 1_000_000,
    }
    rows = []
    for index in range(600):
        rows.append({
            "trade_id": f"trade-{index}", "dir": "LONG", "entry": 100,
            "exit": 101, "net_pnl_usd": 0.01, "research_lane": "FAMILY_ATR_TRAIL",
            "ts": index, "closed_epoch": index, **bulky_fields,
        })
    namespace["trades"][:] = rows
    recent, relay, fidelity = namespace["_snapshot_relay_trade_projections_locked"](0)
    assert len(recent) == 5
    assert len(relay) == 512
    assert len(fidelity) == 512
    forbidden = set(bulky_fields)
    assert all(not forbidden.intersection(row) for row in recent + relay + fidelity)
    # Payload size depends on the fixed presentation schema, not the discarded
    # research arrays/LLM text attached to each in-memory trade.
    assert len(json.dumps({"recent": recent, "relay": relay, "fidelity": fidelity})) < 500_000


def test_projection_preserves_relay_identity_execution_and_fidelity_fields():
    namespace = _functions(
        "_relay_trade_row_lite", "_relay_fidelity_trade_row",
        "_relay_trade_enrichment_row_lite", "_snapshot_relay_trade_projections_locked",
    )
    namespace["trades"].append({
        "trade_id": "trade-1", "shared_ai_call_id": "scan-1", "dir": "SHORT",
        "entry": 101, "exit": 100, "entry_ts": 10, "closed_ts": 20,
        "exit_reason": "TP", "net_pnl_usd": 1.25, "research_lane": "LANE",
        "status": "CLOSED", "executed": True, "epoch_id": "epoch-1",
    })
    recent, relay, fidelity = namespace["_snapshot_relay_trade_projections_locked"](0)
    assert recent[0]["trade_id"] == relay[0]["trade_id"] == fidelity[0]["trade_id"] == "trade-1"
    assert recent[0]["shared_ai_call_id"] == "scan-1"
    assert recent[0]["net_pnl_usd"] == 1.25
    assert recent[0]["status"] == "CLOSED" and recent[0]["executed"] is True
    assert fidelity[0]["closed_ts"] == 20


def test_dashboard_trade_enrichment_is_loaded_once_per_file_revision(tmp_path):
    functions = [
        node for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name in {
            "_dashboard_trade_enrichment_file_key",
            "_load_dashboard_trade_enrichment",
        }
    ]
    module = ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[]))
    evidence = tmp_path / "relay.json"
    evidence.write_text("{}", encoding="utf-8")
    calls = []

    def fake_index():
        calls.append("load")
        return {"trade-1": {"revision": len(calls)}}

    namespace = {
        "threading": threading,
        "os": os,
        "PLATFORM_RELAY_EVIDENCE_FILE": str(evidence),
        "_DASHBOARD_TRADE_ENRICHMENT_CACHE_LOCK": threading.Lock(),
        "_DASHBOARD_TRADE_ENRICHMENT_CACHE_KEY": None,
        "_DASHBOARD_TRADE_ENRICHMENT_CACHE_VALUE": (None, {}),
    }
    import sys
    import types
    dual = types.ModuleType("research.dual_execution_truth")
    dual.split_execution_truth = lambda row: row
    platform = types.ModuleType("research.platform_relay_evidence")
    platform._platform_relay_evidence_index = fake_index
    old_dual = sys.modules.get("research.dual_execution_truth")
    old_platform = sys.modules.get("research.platform_relay_evidence")
    sys.modules["research.dual_execution_truth"] = dual
    sys.modules["research.platform_relay_evidence"] = platform
    try:
        exec(compile(module, str(BOT_PATH), "exec"), namespace)
        first = namespace["_load_dashboard_trade_enrichment"]()
        second = namespace["_load_dashboard_trade_enrichment"]()
        assert first is second
        assert calls == ["load"]
        evidence.write_text('{"changed":true}', encoding="utf-8")
        third = namespace["_load_dashboard_trade_enrichment"]()
        assert calls == ["load", "load"]
        assert third[1]["trade-1"]["revision"] == 2
    finally:
        if old_dual is None:
            sys.modules.pop("research.dual_execution_truth", None)
        else:
            sys.modules["research.dual_execution_truth"] = old_dual
        if old_platform is None:
            sys.modules.pop("research.platform_relay_evidence", None)
        else:
            sys.modules["research.platform_relay_evidence"] = old_platform
