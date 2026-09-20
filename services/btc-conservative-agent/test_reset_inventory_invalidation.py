"""Execute reset invalidation against disposable fixtures, never real data."""
import ast
import json
import os
import re
import threading
import uuid
from pathlib import Path
from unittest.mock import Mock

import pytest

SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


def environment(tmp_path):
    env = {
        "json": json, "os": os, "uuid": uuid,
        "utc_iso": lambda: "2026-09-20T16:00:00Z",
        "_collector_v22_epoch_id": lambda: "epoch-new",
        "_data_sync_inventory_snapshot_path": lambda: tmp_path / "sync_inventory_current.json",
        "_data_sync_inventory_cache_condition": threading.Condition(),
        "_data_sync_inventory_cache": {"rows": [{"path": "old"}], "expires_at": 1000},
        "_data_sync_async_inventory": {
            "status": "CURRENT", "generation_id": "a" * 64,
            "generation": {"total_bytes": 2452000000},
            "completed_refresh_nonce": "old-nonce", "worker_files_seen": 26111,
        },
        "_data_sync_inventory_generations": {"a" * 64: {"ack_eligible": True}},
        "_DATA_SYNC_INVENTORY_SNAPSHOT_SCHEMA": "fly_runtime_inventory_snapshot_v1",
        "_DATA_SYNC_INVENTORY_SNAPSHOT_SCHEMA_V2": "fly_runtime_inventory_snapshot_v2",
    }
    names = {"_data_sync_invalidate_reset_inventory", "_data_sync_load_persisted_inventory_snapshot"}
    nodes = [n for n in ast.parse(SOURCE).body if isinstance(n, ast.FunctionDef) and n.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "reset-invalidation", "exec"), env)
    return env


def test_reset_retires_cache_nonce_pointer_and_authority_without_deleting_evidence(tmp_path):
    env = environment(tmp_path)
    pointer = env["_data_sync_inventory_snapshot_path"]()
    pointer.write_text(json.dumps({"schema": "old-acceleration", "generation": "old"}))
    evidence = tmp_path / "source.jsonl"
    evidence.write_bytes(b"immutable evidence\n")
    ack = tmp_path / "sync_ack.json"
    ack.write_text('{"historical":"receipt"}')
    with env["_data_sync_inventory_cache_condition"]:
        result = env["_data_sync_invalidate_reset_inventory"]("epoch-new")
    assert result["previous_generation_id"] == "a" * 64
    assert result["ack_eligible"] is False
    assert env["_data_sync_inventory_cache"]["rows"] is None
    assert env["_data_sync_async_inventory"]["status"] == "EMPTY"
    assert env["_data_sync_async_inventory"]["completed_refresh_nonce"] is None
    assert "worker_files_seen" not in env["_data_sync_async_inventory"]
    assert not env["_data_sync_inventory_generations"]
    assert env["_data_sync_load_persisted_inventory_snapshot"]() is None
    assert evidence.read_bytes() == b"immutable evidence\n"
    assert json.loads(ack.read_text()) == {"historical": "receipt"}
    assert json.loads(pointer.read_text()) == result


@pytest.mark.parametrize("which,key", [
    ("_data_sync_inventory_cache", "refreshing"),
    ("_data_sync_async_inventory", "refreshing"),
    ("_data_sync_async_inventory", "worker_active"),
])
def test_active_builder_refused_without_mutation(tmp_path, which, key):
    env = environment(tmp_path)
    env[which][key] = True
    with pytest.raises(RuntimeError, match="RESET_INVENTORY_OWNER_ACTIVE"):
        env["_data_sync_invalidate_reset_inventory"]("epoch-new")
    assert env["_data_sync_inventory_generations"]
    assert not env["_data_sync_inventory_snapshot_path"]().exists()


def test_epoch_mismatch_refused(tmp_path):
    env = environment(tmp_path)
    with pytest.raises(RuntimeError, match="RESET_INVENTORY_EPOCH_MISMATCH"):
        env["_data_sync_invalidate_reset_inventory"]("epoch-wrong")
    assert env["_data_sync_inventory_generations"]


def test_invalidation_is_before_deletion_and_epoch_publication_even_if_later_code_throws():
    tree = ast.parse(SOURCE)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == "_perform_fresh_collection_reset_quiesced")
    text = ast.get_source_segment(SOURCE, fn)
    assert text.index('_data_sync_invalidate_reset_inventory') < text.index('stage = "PAYLOAD_DELETION"')
    assert text.index('_data_sync_invalidate_reset_inventory') < text.index('_reset_collector_epoch_state(reset_anchor)')


@pytest.mark.parametrize("failure", ["open", "fsync", "replace"])
def test_persistence_failure_cannot_leave_old_current_authority(tmp_path, monkeypatch, failure):
    env = environment(tmp_path)
    with monkeypatch.context() as patcher:
        if failure == "open":
            patcher.setattr(Path, "open", Mock(side_effect=OSError("injected open")))
        else:
            patcher.setattr(os, failure, Mock(side_effect=OSError("injected " + failure)))
        with pytest.raises(OSError):
            env["_data_sync_invalidate_reset_inventory"]("epoch-new")
    assert env["_data_sync_async_inventory"]["status"] == "EMPTY"
    assert env["_data_sync_inventory_cache"]["rows"] is None
    assert not env["_data_sync_inventory_generations"]


def test_authority_revoked_before_epoch_publication_then_failure(tmp_path):
    env = environment(tmp_path)
    env["_collector_v22_epoch_id"] = lambda: "epoch-old"
    env["_data_sync_invalidate_reset_inventory"]("epoch-new", expected_current_epoch="epoch-old")
    env["_collector_v22_epoch_id"] = lambda: "epoch-new"
    # An exception in the epoch publisher after it changes identity cannot
    # restore the old memory generation or the old restart pointer.
    assert not env["_data_sync_inventory_generations"]
    assert env["_data_sync_load_persisted_inventory_snapshot"]() is None


def compile_function(name, env):
    node = next(n for n in ast.parse(SOURCE).body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), name, "exec"), env)
    return env[name]


def test_historical_ack_is_retained_but_not_advertised_for_new_epoch(tmp_path):
    target = tmp_path / "sync_ack.json"
    identity = {"source_git_rev": "a" * 12, "collection_epoch_id": "epoch-new",
                "tile_registry_signature": "b" * 64}
    env = {"json": json, "_data_sync_ack_path": lambda: target,
           "_load_research_session_meta": lambda: {"collector_v22_epoch_id": "epoch-new"},
           "_runtime_git_rev": lambda: identity["source_git_rev"],
           "active_tile_registry_signature": lambda: identity["tile_registry_signature"]}
    read = compile_function("_read_data_sync_ack", env)
    receipt = {"schema": "fly_runtime_incremental_generation_ack_v3", **identity,
               "file_count": 123, "collection_epoch_id": "epoch-old"}
    raw = json.dumps(receipt)
    target.write_text(raw)
    assert read() == {}
    assert target.read_text() == raw
    receipt["collection_epoch_id"] = "epoch-new"
    target.write_text(json.dumps(receipt))
    assert read() == receipt
    receipt.pop("tile_registry_signature")
    target.write_text(json.dumps(receipt))
    assert read() == {}


@pytest.mark.parametrize("raced", [None, "epoch", "generation", "unbound"])
def test_v3_final_ack_rechecks_identity_under_reset_gate(tmp_path, raced):
    generation_id = "c" * 64
    session_id = "d" * 32
    identity = {"source_git_rev": "a" * 12, "collection_epoch_id": "epoch-new",
                "tile_registry_signature": "b" * 64}
    generation = {"storage": "disk_pages_v2", "ack_eligible": True,
                  "file_count": 1, "page_count": 1, "generated_at": "now",
                  "bundle_identity": identity if raced != "unbound" else None}
    (tmp_path / "page-00000000.json").write_text(json.dumps({
        "schema": "fly_runtime_incremental_ack_page_v3", "inventory_generation_id": generation_id,
        "ack_session_id": session_id, "page_index": 0, "page_sha256": "hash", "accepted": 1}))
    gate = threading.Condition()
    calls = {"generation": 0, "identity": 0}
    def get_generation(_):
        calls["generation"] += 1
        if calls["generation"] == 2:
            assert gate._is_owned()
            if raced == "generation":
                return None
        return generation
    def match_identity(_):
        calls["identity"] += 1
        return (False, "collection_epoch_id") if raced == "epoch" and calls["identity"] == 2 else (True, None)
    writer = Mock(side_effect=lambda _receipt: gate._is_owned() or pytest.fail("write outside reset gate"))
    env = {"json": json, "re": re, "Path": Path, "jsonify": lambda value: value,
           "utc_iso": lambda: "now", "_data_sync_ack_lock": threading.RLock(),
           "_data_sync_inventory_cache_condition": gate,
           "_data_sync_inventory_generation": get_generation,
           "_data_sync_ack_v3_identity_matches": match_identity,
           "_data_sync_ack_v3_stage_root": lambda *_: tmp_path,
           "_data_sync_disk_page_descriptor": lambda *_: {"page_sha256": "hash", "file_count": 1},
           "_data_sync_receipt_bootstrap_gate": lambda: {"complete": True},
           "_write_data_sync_ack": writer}
    fn = compile_function("_data_sync_ack_v3", env)
    result = fn({"operation": "FINALIZE", "inventory_generation_id": generation_id,
                 "inventory_sha256": generation_id, "ack_session_id": session_id,
                 "inventory_generated_at": "now", "inventory_file_count": 1,
                 "manifest_page_count": 1, "manifest_pages_complete": True, **identity})
    if raced:
        assert result[1] == 409
        writer.assert_not_called()
    else:
        assert result["ok"] is True
        writer.assert_called_once()
        assert all(writer.call_args.args[0][key] == value for key, value in identity.items())
