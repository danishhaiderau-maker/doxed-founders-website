"""A dead package must not keep STALE_REVALIDATING forever."""
import ast
import hmac
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import uuid
from types import SimpleNamespace


DEAD_PACKAGE = "b66dbd76" + "ab" * 28
DEAD_STEM = "b66dbd76abcdef0123456789abcdef01"
FRESH_GENERATION = "c" * 64
REV = "a" * 40


def test_dead_package_does_not_revalidate_forever_and_uses_current_build(tmp_path):
    assert len(DEAD_PACKAGE) == 64 and DEAD_PACKAGE.startswith("b66dbd76")
    bot = Path(__file__).with_name("bot.py")
    tree = ast.parse(bot.read_text(encoding="utf-8"))
    names = {
        "_data_sync_inventory_refresh_worker",
        "_data_sync_request_async_inventory",
    }
    nodes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    dead_checkpoint = tmp_path / f"inventory-worker-v2-{DEAD_STEM}.checkpoint.json"
    dead_checkpoint.write_text(
        json.dumps({"package_sha256": DEAD_PACKAGE, "status": "DEAD"}),
        encoding="utf-8",
    )
    research = tmp_path / "research_events_v22.jsonl"
    research.write_text('{"event":"keep"}\n', encoding="utf-8")
    decoy = tmp_path / f"{DEAD_PACKAGE}.jsonl"
    decoy.write_text("research-history\n", encoding="utf-8")
    state = {
        "status": "STALE",
        "rows": None,
        "generation": {
            "generation_id": DEAD_PACKAGE,
            "storage": "disk_pages_v2",
            "bundle_identity": {
                "source_git_rev": REV,
                "collection_epoch_id": "epoch",
                "tile_registry_signature": "tile",
            },
        },
        "generation_id": DEAD_PACKAGE,
        "generated_at": "prior",
        "expires_at": 0.0,
        "served_since_refresh": False,
        "refreshing": True,
        "worker_pages_written": 0,
        "worker_scan_units_completed": 0,
    }
    retained = {}
    started = []
    calls = []
    observed = []

    def retain(generation, generated_at, *, status, refresh_nonce=None):
        retained[generation["generation_id"]] = dict(generation)
        return generation["generation_id"]

    def lookup(generation_id):
        value = retained.get(str(generation_id or ""))
        return dict(value) if value else None

    def validate(result, root):
        assert result["status"] == "COMPLETE"
        assert not dead_checkpoint.exists()
        return {
            "generation_id": FRESH_GENERATION,
            "storage": "disk_pages_v2",
            "page_count": 1,
            "file_count": 1,
            "total_bytes": 1,
            "top_files": [],
        }

    def run(command, **kwargs):
        calls.append(list(command))
        if len(calls) > 3:
            raise AssertionError("dead package revalidation looped")
        result_path = Path(command[command.index("--result") + 1])
        nonce = command[command.index("--nonce") + 1]
        base = {
            "schema": "fly_runtime_inventory_worker_result_v2",
            "nonce": nonce,
            "source_revision": REV,
            "generated_at": "2026-09-28T00:00:00Z",
            "generated_unix": time.time(),
        }
        if dead_checkpoint.exists():
            payload = {
                **base,
                "status": "BUILDING",
                "phase": "SCAN",
                "retry_after_seconds": 5,
                "invocation_files_seen": 0,
                "invocation_dirs_seen": 0,
                "pages_written": 0,
                "scan_units_completed": 0,
                "files_seen": 0,
                "dirs_seen": 0,
                "rows_discovered": 0,
                "resume_token": DEAD_STEM,
                "checkpoint_path": str(dead_checkpoint),
                "spool_path": str(tmp_path / f"inventory-worker-v2-{DEAD_STEM}.sqlite3"),
            }
            result_path.write_text(json.dumps(payload), encoding="utf-8")
            return SimpleNamespace(returncode=75)
        payload = {
            **base,
            "status": "COMPLETE",
            "file_count": 1,
            "page_count": 1,
            "worker_receipt": {"invocations": 1, "scan_units_completed": 1},
        }
        result_path.write_text(json.dumps(payload), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    ns = {
        "Path": Path, "os": os, "sys": sys, "uuid": uuid, "json": json, "hmac": hmac,
        "re": re, "threading": threading, "__file__": str(bot),
        "time": SimpleNamespace(time=time.time, monotonic=time.monotonic, sleep=None),
        "subprocess": SimpleNamespace(run=run, DEVNULL=-3),
        "logger": SimpleNamespace(error=lambda *args: None),
        "utc_iso": lambda: "2026-09-28T00:00:00Z",
        "active_tile_registry_signature": lambda: "tile",
        "_collector_v22_epoch_id": lambda: "epoch",
        "_runtime_git_rev": lambda: REV,
        "_data_sync_inventory_work_root": lambda: tmp_path,
        "_data_sync_volume_root": lambda: tmp_path,
        "_data_sync_runtime_root": lambda: tmp_path,
        "_data_sync_allowed_roots": lambda: [tmp_path],
        "_data_sync_cleanup_inventory_worker_orphans": lambda *args: None,
        "_data_sync_inventory_cache_condition": threading.Condition(),
        "_data_sync_async_inventory": state,
        "_DATA_SYNC_TOP_LEVEL_RECEIPT_NAMES": set(),
        "_DATA_SYNC_EXTENSIONS": set(),
        "_DATA_SYNC_EXCLUDED_NAMES": set(),
        "_DATA_SYNC_EXCLUDED_DIR_NAMES": set(),
        "_DATA_SYNC_APPEND_PREFIX_NAMES": set(),
        "_DATA_SYNC_INVENTORY_WORKER_REQUEST_SCHEMA": "request",
        "_DATA_SYNC_INVENTORY_WORKER_RESULT_SCHEMA": "fly_runtime_inventory_worker_result_v2",
        "_DATA_SYNC_INVENTORY_WORKER_NAME": "worker.py",
        "_DATA_SYNC_MANIFEST_PAGE_DEFAULT": 250,
        "_DATA_SYNC_INVENTORY_WORKER_SLICE_SECONDS": 0.1,
        "_DATA_SYNC_INVENTORY_WORKER_TIMEOUT_SECONDS": 10,
        "_DATA_SYNC_INVENTORY_WORKER_FAILURE_CODES": set(),
        "_DATA_SYNC_INVENTORY_CACHE_TTL_SECONDS": 150.0,
        "_DATA_SYNC_INVENTORY_GENERATION_TTL_SECONDS": 7200,
        "_DATA_SYNC_BUNDLE_REGISTRY": SimpleNamespace(ready=True),
        "_start_data_sync_bundle_reservation_hydration": lambda: None,
        "_data_sync_bundle_retention_allowed_locked": lambda generation_id: True,
        "_data_sync_load_persisted_inventory_snapshot": lambda: None,
        "_data_sync_inventory_rows_sha256": lambda rows: "d" * 64,
        "_data_sync_retain_inventory_generation": retain,
        "_data_sync_retain_disk_inventory_generation": retain,
        "_data_sync_inventory_generation": lookup,
        "_data_sync_inventory_generations": retained,
        "_data_sync_memory_identity_payload": lambda: {
            "source_git_rev": REV,
            "collection_epoch_id": "epoch",
            "tile_registry_signature": "tile",
        },
        "_data_sync_validate_disk_inventory_generation": validate,
        "_data_sync_persist_disk_inventory_snapshot": lambda generation, generated_at: None,
        "_data_sync_receipt_bootstrap_gate": lambda: {"complete": True},
        "_data_sync_inventory_generation_gc_worker": lambda root: None,
        "_start_data_sync_bundle_generation": lambda generation_id: started.append(generation_id) or True,
    }

    def sleep(seconds):
        observed.append(ns["_data_sync_request_async_inventory"]())
        if len(observed) > 1:
            raise AssertionError("stale revalidation slept more than once")

    ns["time"].sleep = sleep
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(bot), "exec"), ns)
    ns["_data_sync_inventory_refresh_worker"]("e" * 32)

    assert len(calls) == 3
    assert len(observed) == 1
    assert observed[0]["status"] == "STALE_REVALIDATING"
    assert observed[0]["generation_id"] == DEAD_PACKAGE
    assert observed[0]["refreshing"] is True
    assert not dead_checkpoint.exists()
    assert list(tmp_path.glob(f"inventory-worker-v2-{DEAD_STEM}.checkpoint.json.dead-*"))
    assert research.read_text(encoding="utf-8") == '{"event":"keep"}\n'
    assert decoy.read_text(encoding="utf-8") == "research-history\n"
    assert state["status"] == "CURRENT"
    assert state["generation_id"] == FRESH_GENERATION
    assert state["refreshing"] is False
    assert started == [FRESH_GENERATION]
    current = ns["_data_sync_request_async_inventory"]()
    assert current["status"] == "CURRENT"
    assert current["generation_id"] == FRESH_GENERATION
    assert current["generation_id"] != DEAD_PACKAGE
