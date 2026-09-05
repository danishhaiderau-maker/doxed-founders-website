"""Actual bot reset functions with real temporary V3/deletion/session modules."""
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import research_v3_store
from research_v3_store import V3EvidenceStore


@pytest.fixture
def runtime(tmp_path_factory, monkeypatch):
    # Keep nested SHA-bound retirement receipts below Windows MAX_PATH.
    root = tmp_path_factory.mktemp("br") / "runtime"
    root.mkdir()
    old = {"epoch_id": "epoch-old", "source_revision": "a" * 40,
           "deployed_revision": "a" * 40, "tile_config_signature": "c" * 64}
    monkeypatch.setattr(research_v3_store, "_collection_provenance", lambda: {k: v for k, v in old.items() if k != "epoch_id"})
    monkeypatch.setattr(V3EvidenceStore, "_emergency_wal_identity_available", lambda _: False)
    monkeypatch.setenv("SOURCE_GIT_REV", "b" * 40)
    store = V3EvidenceStore(root, epoch_id=old["epoch_id"])
    store.initialize_ledger_generation_authority("opportunity")
    payload = store.ledger_path("opportunity")
    payload.write_text('{"old":true}\n')
    session = root / "research_session.json"
    session.write_text(json.dumps({"collector_v22_epoch_id": "epoch-old"}))
    accounting = root / "trades_3factor.csv"
    accounting.write_text("accounting-must-survive\n")
    settings_history = root / "execution_settings_history.jsonl"
    settings_history.write_text('{"reason":"PRIOR_SETTINGS","signature":"prior"}\n')
    settings_observed_epochs = []
    def append_settings(path, row, **kwargs):
        settings_observed_epochs.append(json.loads(session.read_text())["collector_v22_epoch_id"])
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
        return True
    locks = {name: threading.RLock() for name in (
        "state_lock", "trade_lock", "_research_write_gate", "_collector_epoch_lock",
        "replay_lock", "_cycle_3m_bucket_lock", "_compressed_shadow_lock")}
    locks.update({name: threading.Condition() for name in (
        "_data_sync_inventory_cache_condition", "_data_sync_sqlite_snapshot_condition")})
    locks["_fresh_collection_lock"] = threading.Lock()
    guard_names = ("_fresh_collection_lock", "_collector_epoch_lock", "_research_write_gate",
                   "_data_sync_inventory_cache_condition", "_data_sync_sqlite_snapshot_condition")
    for name in guard_names: locks[name].acquire()
    env = {"Path": Path, "os": os, "re": re, "hashlib": hashlib, "json": json,
           "datetime": datetime, "timezone": timezone, "time": SimpleNamespace(time=lambda: 1788580800.125),
           "logger": Mock(), **locks, "_LIFECYCLE_PIPELINE_RUNTIME": None,
           "_RAW_GENERATION_GATE_LOCAL": SimpleNamespace(mirror=object()),
           "state": {"execution_paused": True, "execution_reason": "ADMIN_MANUAL", "live_armed": False,
                     "account_balance": 502, "daily_pnl_usd": 2},
           "pending_orders": [], "open_positions": [], "trades": [{"id": "old", "pnl": 2}],
           "_force_paper_mode_active": lambda: True, "_data_sync_runtime_root": lambda: root,
           "_load_research_session_meta": lambda: json.loads(session.read_text()),
           "RESEARCH_SESSION_FILE": str(session), "_timestamp_to_melbourne_display": lambda ts: str(ts),
           "EXECUTION_FIX_VERSION": "test", "ANALYZER_SYNC_ID": "test", "COLLECTOR_V31_VERSION": "test",
           "COLLECTOR_V22_VERSION": "test", "_pause_agent_debug_writes": Mock(),
           "_resume_agent_debug_writes": Mock(), "get_genome_bridge": lambda: None,
           "active_tile_registry_signature": lambda: "d" * 64,
           "_enabled_execution_settings": lambda: {"gap_buckets": ["small"], "chase_buckets": ["2", "3", "4"]},
           "_execution_settings_history_lock": threading.RLock(), "_settings_breakdown_cache": {"key": "old"},
           "EXECUTION_SETTINGS_HISTORY_FILE": str(settings_history), "utc_iso": lambda: "2026-09-05T00:00:00Z",
           "_safe_append_jsonl": append_settings,
           "reset_provisional_events": Mock(), "save_persistent_config": Mock(),
           "_update_data_sync_identity_epoch_cache": Mock(), "bot_start_time": 100,
           "replay_buffers": {}, "_cycle_3m_written_buckets": {}, "_cycle_3m_inflight_buckets": set(),
           "_touch_grid_book": {}, "_compressed_shadow_chase_book": {}, "_compressed_shadow_seen_call_ids": set(),
           "_discovery_touch_grid_seen_call_ids": set(), "_order_multiverse_state": {},
           "_order_multiverse_pending_src": {}, "_order_multiverse_path_complete": {},
           "_order_multiverse_post_ttl_done": {}, "_order_multiverse_written": set()}
    path = Path(__file__).with_name("bot.py")
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    names = {"_fresh_research_reset_assert_quiesced", "_fresh_research_reset_resume",
             "_fresh_research_reset_boundary", "_perform_fresh_collection_reset_quiesced",
             "_record_execution_settings_epoch", "_execution_settings_signature",
             "_write_research_session", "_reset_collector_epoch_state", "_collector_v22_epoch_id", "_utc_isoformat_ns"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(nodes) == len(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), env)
    env.update(root=root, store=store, old_identity=old, payload=payload, accounting=accounting, session=session,
               settings_history=settings_history, settings_observed_epochs=settings_observed_epochs)
    yield env
    for name in reversed(guard_names): locks[name].release()


def run(env, **kwargs):
    return env["_perform_fresh_collection_reset_quiesced"](**kwargs)


@pytest.mark.parametrize("signal", [False, True])
def test_actual_reset_deletes_research_retires_authority_and_writes_real_epoch(runtime, signal):
    state_before = dict(runtime["state"])
    result = run(runtime, send_local_signal=signal)
    assert result["ok"], result
    assert not runtime["payload"].exists()
    assert not (runtime["store"]._generation_root("opportunity") / "ACTIVE.json").exists()
    written = json.loads(runtime["session"].read_text())
    assert written["collector_v22_epoch_id"] == result["new_epoch_id"]
    assert result["new_epoch_id"] != "epoch-old"
    assert runtime["accounting"].read_text() == "accounting-must-survive\n"
    assert runtime["state"]["account_balance"] == state_before["account_balance"]
    assert runtime["state"]["daily_pnl_usd"] == state_before["daily_pnl_usd"]
    assert runtime["trades"] == [{"id": "old", "pnl": 2}]
    assert runtime["state"]["execution_paused"] is True
    assert runtime["state"]["execution_reason"] == "ADMIN_MANUAL"
    assert not (runtime["root"] / "research_archive").exists()
    assert json.loads(Path(result["operation_receipt"]).read_text())["stage"] == "COMPLETE"
    assert bool(result["fresh_collection_signal_ts"]) is signal
    history = [json.loads(line) for line in runtime["settings_history"].read_text().splitlines()]
    assert history[0] == {"reason": "PRIOR_SETTINGS", "signature": "prior"}
    assert history[1]["reason"] == "FRESH_COLLECTION_STARTED"
    assert history[1]["signature"] == "gap=small|chase=2,3,4"
    assert runtime["settings_observed_epochs"] == [result["new_epoch_id"]]
    assert runtime["_settings_breakdown_cache"]["key"] is None


def test_existing_real_empty_wal_is_rebound_only_after_deletion(runtime):
    from emergency_evidence_wal import EmergencyEvidenceWal
    wal_root = runtime["root"] / "v3/emergency_evidence_wal_v2"
    EmergencyEvidenceWal(wal_root, identity=runtime["old_identity"])
    result = run(runtime, send_local_signal=False)
    assert result["ok"], result
    new_identity = {"epoch_id": result["new_epoch_id"], "source_revision": "b" * 40,
                    "deployed_revision": "b" * 40, "tile_config_signature": "d" * 64}
    status = EmergencyEvidenceWal.inspect_existing(wal_root, identity=new_identity)
    assert status["records"] == []


def test_payload_rejection_persists_exact_code_and_retains_paused_old_state(runtime, monkeypatch):
    import research_reset_execution
    from research_exact_deletion import ResearchDeletionRejected

    calls = []
    payload_before = runtime["payload"].read_bytes()
    session_before = runtime["session"].read_bytes()
    accounting_before = runtime["accounting"].read_bytes()

    def reject_exact_deletion(**kwargs):
        calls.append(kwargs["receipt_path"])
        raise ResearchDeletionRejected("PROTECTED_SOURCE_OR_RECOVERY")

    monkeypatch.setattr(research_reset_execution, "delete_exact_research_files", reject_exact_deletion)
    result = run(runtime, send_local_signal=False)
    assert len(calls) == 1  # Real preflight passed; failure is at actual deletion admission.
    assert result["ok"] is False
    assert result["failed_stage"] == "PAYLOAD_DELETION"
    assert result["payload_deletion_completed"] is False
    operation = json.loads(Path(result["operation_receipt"]).read_text())
    assert operation["stage"] == "FAILED"
    assert operation["failed_stage"] == "PAYLOAD_DELETION"
    assert operation["error"] == "ResearchDeletionRejected"
    assert operation["rejection_code"] == "PROTECTED_SOURCE_OR_RECOVERY"
    assert not Path(calls[0]).exists()
    assert runtime["payload"].read_bytes() == payload_before
    assert runtime["session"].read_bytes() == session_before
    assert runtime["accounting"].read_bytes() == accounting_before
    assert runtime["state"]["execution_paused"] is True
    assert runtime["state"]["live_armed"] is False


def test_actual_genome_database_resets_and_rebinds_without_copying_old_payload(runtime):
    from research_genome.bridge import GenomeBridge
    bridge = GenomeBridge(str(runtime["root"]), dataset_epoch="epoch-old",
                          deployed_revision="a" * 40, tile_config_signature="c" * 64)
    runtime["get_genome_bridge"] = lambda: bridge
    try:
        result = run(runtime, send_local_signal=False)
        assert result["ok"], result
        assert bridge._generation_identity["dataset_epoch"] == result["new_epoch_id"]
        operation = json.loads(Path(result["operation_receipt"]).read_text())
        assert operation["genome_reset"]["raw_payloads_retained"] is False
        assert operation["genome_reset"]["deletion_receipt"]["status"] == "COMPLETE"
    finally:
        bridge.store.close()


@pytest.mark.parametrize("after_database_reset", [False, True])
def test_interrupted_genome_stage_cannot_be_skipped_or_publish_epoch(runtime, after_database_reset):
    from research_genome.bridge import GenomeBridge
    bridge = GenomeBridge(str(runtime["root"]), dataset_epoch="epoch-old",
                          deployed_revision="a" * 40, tile_config_signature="c" * 64)
    original = bridge.reset_research_store
    def crash(**kwargs):
        if after_database_reset:
            original(**kwargs)
        raise KeyboardInterrupt("abrupt Genome stage interruption")
    bridge.reset_research_store = crash
    runtime["get_genome_bridge"] = lambda: bridge
    try:
        with pytest.raises(KeyboardInterrupt):
            run(runtime, send_local_signal=False)
        bridge.reset_research_store = original
        runtime["time"] = SimpleNamespace(time=lambda: 1788589999.0)
        result = run(runtime, send_local_signal=False)
        assert result["error"] == "RESET_GENOME_STAGE_REQUIRES_RECONCILIATION"
        assert json.loads(runtime["session"].read_text())["collector_v22_epoch_id"] == "epoch-old"
        assert runtime["state"]["execution_paused"] is True
    finally:
        bridge.store.close()


def fly_layout(env):
    scopes = {}
    for name in ("research", "research_accumulator", "research_archive"):
        physical = env["root"].parent / name
        physical.mkdir()
        try:
            (env["root"] / name).symlink_to(physical, target_is_directory=True)
        except OSError:
            pytest.skip("Host cannot create real Fly-layout symlinks")
        scopes[name] = physical
    mirror = scopes["research"] / "genome/decision_genome.jsonl"
    mirror.parent.mkdir()
    mirror.write_text('{"old":true}\n')
    archived = scopes["research_archive"] / "session_001/payload/000000_lifecycle.jsonl"
    archived.parent.mkdir(parents=True)
    archived.write_bytes(b"old archive\n")
    metadata = archived.parent.parent / "archive_meta.json"
    metadata.write_text(json.dumps({"schema": "research_archive_receipt_v2", "source_inventory": [{
        "path": "v3/ledgers/lifecycle.jsonl", "preserved_path": "payload/000000_lifecycle.jsonl",
        "preserved_bytes": archived.stat().st_size, "preserved_sha256": hashlib.sha256(archived.read_bytes()).hexdigest()}]}))
    return scopes, mirror, archived, metadata


def test_actual_fly_layout_deletes_physical_payloads_but_retains_aliases(runtime):
    scopes, mirror, archived, metadata = fly_layout(runtime)
    result = run(runtime, send_local_signal=False)
    assert result["ok"], result
    assert set(result["scope_deletions"]) == set(scopes)
    assert not mirror.exists() and not archived.exists()
    assert metadata.exists()
    assert all((runtime["root"] / name).is_symlink() for name in scopes)
    assert str(mirror) in result["deleted"] and str(archived) in result["deleted"]


def test_bad_sibling_archive_hash_rejects_before_runtime_payload_deletion(runtime):
    _, _, archived, _ = fly_layout(runtime)
    archived.write_bytes(b"bad archive\n")
    result = run(runtime)
    assert result["ok"] is False
    assert runtime["payload"].exists()


def test_pending_real_wal_blocks_before_deletion(runtime):
    from emergency_evidence_wal import EmergencyEvidenceWal
    wal = EmergencyEvidenceWal(runtime["root"] / "v3/emergency_evidence_wal_v2", identity=runtime["old_identity"])
    wal.defer(ledger="lifecycle", record_id="pending", payload=b'{"pending":true}\n')
    result = run(runtime)
    assert result["ok"] is False and result["failed_stage"] == "BOUNDARY"
    assert runtime["payload"].exists()
    assert json.loads(runtime["session"].read_text())["collector_v22_epoch_id"] == "epoch-old"


def test_unknown_recovery_file_blocks_before_deletion(runtime):
    path = runtime["root"] / "v3/receipts/ledger_generations_v1/opportunity/unknown.json"
    path.write_text("{}")
    result = run(runtime)
    assert result["error"] == "RESET_RECOVERY_NOT_PROVEN_CLEAR"
    assert runtime["payload"].exists()


def test_pending_volume_cleanup_blocks_before_any_payload_deletion(runtime):
    folder = runtime["root"].parent / "raw_generation_cleanup_transactions/tx"
    folder.mkdir(parents=True)
    (folder / "PREPARED.json").write_text(json.dumps({"schema": "raw_generation_cleanup_transaction_v1",
                                                   "state": "PREPARED", "generation_id": "tx"}))
    result = run(runtime)
    assert result["error"] == "RESET_AUXILIARY_RECOVERY_NOT_PROVEN_CLEAR"
    assert runtime["payload"].exists()


def test_legacy_genesis_uses_real_session_boundary_without_claiming_old_provenance(runtime):
    (runtime["store"]._generation_root("opportunity") / "ACTIVE.json").unlink()
    result = run(runtime)
    assert result["ok"], result
    operation = json.loads(Path(result["operation_receipt"]).read_text())
    assert operation["boundary_evidence"]["identity_basis"] == "LEGACY_SESSION_RESET_BOUNDARY_NOT_HISTORICAL_PROVENANCE"


def test_legacy_genesis_conflicting_wal_never_uses_current_revision_to_override(runtime):
    from emergency_evidence_wal import EmergencyEvidenceWal
    EmergencyEvidenceWal(runtime["root"] / "v3/emergency_evidence_wal_v2", identity=runtime["old_identity"])
    (runtime["store"]._generation_root("opportunity") / "ACTIVE.json").unlink()
    result = run(runtime)
    assert result["ok"] is False
    assert result["failed_stage"] == "BOUNDARY"
    assert runtime["payload"].exists()


def test_missing_persisted_legacy_session_epoch_refuses_reset(runtime):
    (runtime["store"]._generation_root("opportunity") / "ACTIVE.json").unlink()
    runtime["session"].write_text("{}")
    result = run(runtime)
    assert result["ok"] is False
    assert runtime["payload"].exists()


def test_missing_exclusive_barrier_rejected(runtime):
    runtime["_RAW_GENERATION_GATE_LOCAL"].mirror = None
    result = run(runtime)
    assert result["error"] == "RESET_EXCLUSIVE_BARRIERS_NOT_PROVEN"
    assert runtime["payload"].exists()


def test_authority_failure_does_not_publish_new_epoch(runtime, monkeypatch):
    monkeypatch.setattr(V3EvidenceStore, "retire_empty_epoch_authority", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("retirement failed")))
    result = run(runtime)
    assert result["ok"] is False and result["payload_deletion_completed"] is True
    assert result["failed_stage"] == "AUTHORITY_RETIREMENT"
    assert json.loads(runtime["session"].read_text())["collector_v22_epoch_id"] == "epoch-old"
    assert runtime["state"]["execution_paused"] is True
    assert runtime["settings_observed_epochs"] == []
    assert len(runtime["settings_history"].read_text().splitlines()) == 1


@pytest.mark.parametrize("after_publish", [False, True])
def test_crash_after_authority_retirement_resumes_exact_epoch_without_active_pointer(runtime, after_publish):
    original = runtime["_reset_collector_epoch_state"]
    def crash(anchor):
        if after_publish:
            original(anchor)
        raise KeyboardInterrupt("simulated abrupt process interruption")
    runtime["_reset_collector_epoch_state"] = crash
    with pytest.raises(KeyboardInterrupt):
        run(runtime, send_local_signal=False)
    assert not (runtime["store"]._generation_root("opportunity") / "ACTIVE.json").exists()
    pointer = json.loads((runtime["root"] / "research_reset_receipts/ACTIVE_RESET.json").read_text())
    receipt_dir = runtime["root"] / "research_reset_receipts" / pointer["reset_id"]
    binding = json.loads((receipt_dir / "binding.json").read_text())
    runtime["_reset_collector_epoch_state"] = original
    runtime["time"] = SimpleNamespace(time=lambda: 1788589999.0)
    result = run(runtime, send_local_signal=True)
    assert result["ok"], result
    assert result["new_epoch_id"] == binding["proof"]["new_epoch_id"]
    assert json.loads(runtime["session"].read_text())["collector_v22_epoch_ts"] == binding["reset_anchor"]
    assert result["fresh_collection_signal_ts"] == 0
    assert result["operation_receipt"] == str(receipt_dir / "operation.json")


def test_resume_rejects_payload_reappearing_after_confirmed_deletion(runtime):
    original = runtime["_reset_collector_epoch_state"]
    runtime["_reset_collector_epoch_state"] = lambda _: (_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        run(runtime, send_local_signal=False)
    runtime["payload"].write_text("new unexpected payload\n")
    runtime["_reset_collector_epoch_state"] = original
    result = run(runtime)
    assert result["error"] == "RESET_PARTIAL_DELETION_REQUIRES_RECONCILIATION"
    assert runtime["payload"].read_text() == "new unexpected payload\n"
    assert runtime["state"]["execution_paused"] is True


def test_no_old_archival_or_accounting_reset_hooks_remain_in_actual_body():
    tree = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8-sig"))
    body = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_perform_fresh_collection_reset_quiesced")
    calls = {n.func.id for n in ast.walk(body) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not calls.intersection({"_seal_past_analysis_with_fallback", "create_research_archive_receipt",
                                   "reset_all_research_files", "_reset_runtime_log_handlers", "reset_runtime_state",
                                   "reset_session_risk_state", "load_session_trades_from_csv"})
