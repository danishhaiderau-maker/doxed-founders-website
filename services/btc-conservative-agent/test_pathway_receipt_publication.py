import ast
import json
import os
import threading
import time
from pathlib import Path

import pathway_lab_validation as validation


IDENTITY = {
    "source_git_rev": "abc123",
    "bot_version": "test-version",
    "fresh_epoch_id": "epoch-test",
    "tile_registry_signature": "registry-test",
    "analyzer_sync_id": "sync-test",
}


def test_startup_receipts_are_current_registry_owned_and_identity_bound():
    receipts = validation.build_startup_contract_receipts(IDENTITY)

    assert set(receipts) == {
        "tile_independence_report.json",
        "ai_scan_independence_report.json",
        "ai_scan_role_validation.json",
    }
    for payload in receipts.values():
        assert payload["verdict"] == "PASS"
        assert payload["source_git_rev"] == "abc123"
        assert payload["fresh_epoch_id"] == "epoch-test"
        assert payload["tile_registry_signature"] == "registry-test"
        assert "generated_at" in payload

    encoded = json.dumps(receipts)
    assert "OFFSET_029" not in encoded
    assert "v15-typeb-opportunity-v2" not in encoded


def test_startup_receipts_fail_closed_if_ai_scan_becomes_order_capable():
    receipts = validation.build_startup_contract_receipts(
        IDENTITY, ai_scan_orders_allowed=True
    )
    assert receipts["ai_scan_independence_report.json"]["verdict"] == "FAIL"
    assert receipts["ai_scan_role_validation.json"]["verdict"] == "FAIL"


def test_startup_receipts_fail_closed_if_legacy_spawn_targets_return():
    receipts = validation.build_startup_contract_receipts(
        IDENTITY, research_spawn_lanes=("LEGACY_LANE",)
    )
    assert receipts["ai_scan_role_validation.json"]["verdict"] == "FAIL"


def test_periodic_lane_memory_receipt_preserves_identity_and_real_verdict():
    payload = validation.validate_lane_memory_runtime(
        {"UNREGISTERED": 1}, {}, (), identity=IDENTITY
    )
    assert payload["verdict"] == "CRITICAL"
    assert payload["source_git_rev"] == "abc123"
    assert payload["fresh_epoch_id"] == "epoch-test"
    assert payload["critical_issues"] == ["UNREGISTERED_LANE_EXPOSURE:UNREGISTERED:1"]


def test_periodic_runtime_receipt_preserves_identity_and_real_verdict():
    payload = validation.validate_runtime_pathway_integrity(
        startup_snapshot={},
        current_pathway_lane_status={},
        current_combo_execution_lanes=tuple(validation.COMBO_EXECUTION_LANES),
        ai_direct_research_lanes=frozenset(),
        research_spawn_lanes=(),
        ai_scan_orders_allowed=False,
        identity=IDENTITY,
    )
    assert payload["verdict"] == "PASS"
    assert payload["tile_registry_signature"] == "registry-test"
    assert payload["analyzer_sync_id"] == "sync-test"


def test_bot_publication_uses_runtime_directory_and_atomic_replace():
    source = (Path(__file__).with_name("bot.py")).read_text(encoding="utf-8")
    body = source.split("def _publish_pathway_receipt", 1)[1].split(
        "def publish_startup_pathway_receipts", 1
    )[0]
    assert "_data_sync_runtime_root()" in body
    assert "_data_sync_volume_root()" not in body
    assert "_atomic_file_replace(" in body
    assert "Path(name).name" in body
    assert "_AGENT_ROOT" not in body

    monitor = source.split("def ttl_monitor", 1)[1].split(
        "def system_health_check", 1
    )[0]
    assert "validate_lane_memory()" in monitor
    assert "validate_runtime_pathway_integrity()" in monitor
    assert "publish_startup_pathway_receipts()" in monitor

    lane_validation = source.split("def validate_lane_memory()", 1)[1].split(
        "def validate_runtime_pathway_integrity", 1
    )[0]
    assert '"lane_memory_validation.json"' in lane_validation
    assert '"lane_memory_violation.json"' in lane_validation
    assert '"active": False' in lane_validation


def test_newly_written_independence_report_is_under_runtime_not_volume_root(tmp_path, monkeypatch):
    source = (Path(__file__).with_name("bot.py")).read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted = {
        "_data_sync_runtime_root",
        "_data_sync_volume_root",
        "_publish_pathway_receipt",
        "_atomic_file_replace",
    }
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace = {
        "Path": Path,
        "os": os,
        "json": json,
        "threading": threading,
        "time": time,
        "logger": type("Logger", (), {"warning": staticmethod(lambda *args, **kwargs: None)})(),
        "_pathway_receipt_lock": threading.RLock(),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))

    written = namespace["_publish_pathway_receipt"](
        "ai_scan_independence_report.json",
        {"schema": "ai_scan_independence_v2", "verdict": "PASS"},
    )

    runtime_path = tmp_path / "runtime" / "ai_scan_independence_report.json"
    volume_path = tmp_path / "ai_scan_independence_report.json"
    assert written is True
    assert runtime_path.is_file()
    assert runtime_path.parent == (tmp_path / "runtime").resolve()
    assert not volume_path.exists()
    assert runtime_path.resolve() != volume_path.resolve()
    assert json.loads(runtime_path.read_text(encoding="utf-8"))["verdict"] == "PASS"
