"""Execute reset invalidation against disposable fixtures, never real data."""
import ast
import json
import os
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
    }
    names = {"_data_sync_invalidate_reset_inventory"}
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
    assert not env["_data_sync_inventory_snapshot_path"]().exists()


def test_epoch_mismatch_refused(tmp_path):
    env = environment(tmp_path)
    with pytest.raises(RuntimeError, match="RESET_INVENTORY_EPOCH_MISMATCH"):
        env["_data_sync_invalidate_reset_inventory"]("epoch-wrong")


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


def test_authority_revoked_before_epoch_publication_then_failure(tmp_path):
    env = environment(tmp_path)
    env["_collector_v22_epoch_id"] = lambda: "epoch-old"
    env["_data_sync_invalidate_reset_inventory"]("epoch-new", expected_current_epoch="epoch-old")
    env["_collector_v22_epoch_id"] = lambda: "epoch-new"
    # An exception in the epoch publisher after it changes identity cannot
    # restore the old memory generation or the old restart pointer.


