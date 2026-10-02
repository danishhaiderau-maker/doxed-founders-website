"""Direct contract checks for the low-space inventory admission fence.

This intentionally extracts only pure/stdlib portions of ``bot.py`` so it can
run without the production Flask/AI dependency graph.  It is not a Fly or
exchange test.
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import threading as real_threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")


def _functions(*names: str) -> list[ast.FunctionDef]:
    tree = ast.parse(SOURCE, filename="bot.py")
    found = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    }
    missing = set(names) - set(found)
    assert not missing, f"missing functions: {sorted(missing)}"
    return [found[name] for name in names]


def _run_low_space_gate() -> tuple[dict, dict]:
    no_thread = SimpleNamespace(
        Thread=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("low-space fence must not start an inventory worker")
        )
    )
    state = {
        "status": "EMPTY",
        "rows": None,
        "generation": None,
        "refreshing": False,
        "active_refresh_nonce": None,
        "generated_at": None,
        "generation_id": None,
    }
    namespace = {
        "Path": Path,
        "hashlib": hashlib,
        "hmac": hmac,
        "json": json,
        "time": time,
        "uuid": uuid,
        "threading": no_thread,
        "shutil": SimpleNamespace(
            disk_usage=lambda _path: SimpleNamespace(free=200 * 1024 * 1024)
        ),
        "_DATA_SYNC_INVENTORY_MIN_FREE_BYTES": 512 * 1024 * 1024,
        "_DATA_SYNC_INVENTORY_WORKER_FAILURE_CODES": frozenset({
            "INVENTORY_CAPACITY_DEFERRED", "INVENTORY_WORKER_FAILED",
        }),
        "_DATA_SYNC_BUNDLE_REGISTRY": SimpleNamespace(ready=True),
        "_data_sync_inventory_cache_condition": real_threading.Condition(),
        "_data_sync_async_inventory": state,
        "_data_sync_volume_root": lambda: Path("/synthetic-volume"),
        "_start_data_sync_bundle_reservation_hydration": lambda: None,
        "_data_sync_load_persisted_inventory_snapshot": lambda: None,
        "_data_sync_inventory_rows_sha256": lambda _rows: "unused",
        "_data_sync_bundle_retention_allowed_locked": lambda _id: True,
        "_data_sync_retain_disk_inventory_generation": lambda *_args, **_kwargs: "unused",
        "_data_sync_retain_inventory_generation": lambda *_args, **_kwargs: "unused",
        "_data_sync_memory_identity_payload": lambda: {},
        "_data_sync_inventory_generation": lambda _id: None,
        "utc_iso": lambda: "2026-09-14T00:00:00Z",
    }
    functions = _functions(
        "_data_sync_inventory_volume_free_bytes",
        "_data_sync_inventory_failure_fingerprint",
        "_data_sync_inventory_public_failure_code",
        "_data_sync_request_async_inventory",
    )
    exec(compile(ast.Module(body=functions, type_ignores=[]), "bot.py", "exec"), namespace)
    result = namespace["_data_sync_request_async_inventory"]()
    return result, state


def _run_parent_exception() -> tuple[dict, list[str]]:
    tree = ast.parse(SOURCE, filename="bot.py")
    worker = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_data_sync_inventory_refresh_worker"
    )
    with tempfile.TemporaryDirectory(prefix="inventory-contract-") as temporary:
        root = Path(temporary)
        state = {
            "status": "EMPTY", "rows": None, "generation": None,
            "refreshing": True,
        }
        logged: list[str] = []
        namespace = {
        "__file__": str(ROOT / "bot.py"),
        "Path": Path,
        "hashlib": hashlib,
        "hmac": hmac,
        "json": json,
        "os": os,
        "re": __import__("re"),
        "sys": sys,
        "time": time,
        "uuid": uuid,
        "threading": real_threading,
        "shutil": SimpleNamespace(
            disk_usage=lambda _path: SimpleNamespace(free=123)
        ),
        "logger": SimpleNamespace(
            error=lambda *args: logged.append(" ".join(str(arg) for arg in args))
        ),
        "_data_sync_inventory_cache_condition": real_threading.Condition(),
        "_data_sync_async_inventory": state,
        "_data_sync_inventory_work_root": lambda: root,
        "_data_sync_cleanup_inventory_worker_orphans": lambda *_args: 0,
        "_data_sync_volume_root": lambda: root,
        "_data_sync_runtime_root": lambda: root,
        "_data_sync_allowed_roots": lambda: [root],
        "_collector_v22_epoch_id": lambda: "epoch-test",
        "_runtime_git_rev": lambda: "a" * 40,
        "active_tile_registry_signature": lambda: "b" * 64,
        "utc_iso": lambda: "2026-09-14T00:00:00Z",
        "_DATA_SYNC_INVENTORY_WORKER_REQUEST_SCHEMA": "request-v1",
        "_DATA_SYNC_INVENTORY_WORKER_RESULT_SCHEMA": "result-v2",
        "_DATA_SYNC_INVENTORY_WORKER_NAME": "data_sync_inventory_worker.py",
        "_DATA_SYNC_INVENTORY_WORKER_TIMEOUT_SECONDS": 300,
        "_DATA_SYNC_INVENTORY_WORKER_SLICE_SECONDS": 15.0,
        "_DATA_SYNC_MANIFEST_PAGE_DEFAULT": 250,
        "_DATA_SYNC_TOP_LEVEL_RECEIPT_NAMES": frozenset(),
        "_DATA_SYNC_EXTENSIONS": frozenset({".json"}),
        "_DATA_SYNC_EXCLUDED_NAMES": frozenset(),
        "_DATA_SYNC_EXCLUDED_DIR_NAMES": frozenset(),
        "_DATA_SYNC_APPEND_PREFIX_NAMES": frozenset(),
        "_DATA_SYNC_INVENTORY_WORKER_FAILURE_CODES": frozenset({
            "INVENTORY_CAPACITY_DEFERRED", "INVENTORY_WORKER_FAILED",
        }),
        "_DATA_SYNC_INVENTORY_FAILURE_STAGES": frozenset({
            "CAPACITY_GATE", "PARENT_REFRESH",
        }),
        "_run_admitted_inventory_child": lambda *_args: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("secret-inventory-command", 300)
        ),
        "_data_sync_inventory_volume_free_bytes": lambda: 123,
        "_data_sync_inventory_failure_fingerprint": lambda **_kwargs: "a" * 64,
        "_data_sync_validate_disk_inventory_generation": lambda *_args: {},
        "_data_sync_receipt_bootstrap_gate": lambda: {"complete": True},
        "_data_sync_persist_disk_inventory_snapshot": lambda *_args: None,
        "_data_sync_retain_disk_inventory_generation": lambda *_args, **_kwargs: "unused",
        "_data_sync_inventory_generation_gc_worker": lambda *_args: None,
        "_start_data_sync_bundle_generation": lambda *_args: None,
        }
        exec(
            compile(ast.Module(body=[worker], type_ignores=[]), "bot.py", "exec"),
            namespace,
        )
        namespace["_data_sync_inventory_refresh_worker"]("c" * 32)
        return state, logged


def _run_capacity_projection(observed_free_bytes: int | None) -> dict:
    namespace = {
        "_DATA_SYNC_INVENTORY_MIN_FREE_BYTES": 512 * 1024 * 1024,
        # The extracted projection must use only the supplied O(1) fact.
        "shutil": SimpleNamespace(
            disk_usage=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("capacity projection must not inspect the filesystem")
            )
        ),
        "_data_sync_request_async_inventory": lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(
                AssertionError("capacity projection must not start an inventory scan")
            )
        ),
    }
    functions = _functions("_data_sync_inventory_capacity_projection")
    exec(compile(ast.Module(body=functions, type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace["_data_sync_inventory_capacity_projection"](
        observed_free_bytes
    )


def _assert_o1_capacity_projection_wiring() -> None:
    tree = ast.parse(SOURCE, filename="bot.py")
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }

    def disk_usage_calls(node: ast.AST) -> list[ast.Call]:
        return [
            child for child in ast.walk(node)
            if isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and isinstance(child.func.value, ast.Name)
            and child.func.value.id == "shutil"
            and child.func.attr == "disk_usage"
        ]

    def has_guarded_disk_usage(node: ast.AST) -> bool:
        for child in ast.walk(node):
            if not isinstance(child, ast.Try):
                continue
            if not disk_usage_calls(ast.Module(body=child.body, type_ignores=[])):
                continue
            if any(
                isinstance(handler.type, ast.Name)
                and handler.type.id == "OSError"
                for handler in child.handlers
            ):
                return True
        return False

    data_size = functions["api_data_size"]
    manifest = functions["api_data_sync_manifest"]
    assert len(disk_usage_calls(data_size)) == 1
    assert len(disk_usage_calls(manifest)) == 1
    assert has_guarded_disk_usage(data_size)
    assert has_guarded_disk_usage(manifest)
    assert not any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and isinstance(child.func.value, ast.Name)
        and child.func.value.id == "os"
        and child.func.attr == "walk"
        for function in (data_size, manifest)
        for child in ast.walk(function)
    )


def main() -> None:
    minimum = 512 * 1024 * 1024
    assert _run_capacity_projection(200 * 1024 * 1024) == {
        "minimum_free_bytes": minimum,
        "observed_free_bytes": 200 * 1024 * 1024,
        "sufficient": False,
        "additional_required_bytes": 312 * 1024 * 1024,
    }
    assert _run_capacity_projection(700 * 1024 * 1024) == {
        "minimum_free_bytes": minimum,
        "observed_free_bytes": 700 * 1024 * 1024,
        "sufficient": True,
        "additional_required_bytes": 0,
    }
    assert _run_capacity_projection(None) == {
        "minimum_free_bytes": minimum,
        "observed_free_bytes": None,
        "sufficient": None,
        "additional_required_bytes": None,
    }
    assert _run_capacity_projection(True) == {
        "minimum_free_bytes": minimum,
        "observed_free_bytes": None,
        "sufficient": None,
        "additional_required_bytes": None,
    }
    _assert_o1_capacity_projection_wiring()
    result, state = _run_low_space_gate()
    assert result["status"] == "EMPTY"
    assert result["error"] == "INVENTORY_CAPACITY_DEFERRED"
    assert result["capacity_deferred"] is True
    assert result["retry_after_seconds"] == 30
    assert state["refreshing"] is False
    assert state["last_worker_failure_code"] == "INVENTORY_CAPACITY_DEFERRED"
    assert state["last_worker_failure_stage"] == "CAPACITY_GATE"
    assert state["last_worker_failure_volume_free_bytes"] == 200 * 1024 * 1024
    assert len(state["last_worker_failure_fingerprint"]) == 64
    assert "logger.error(f\"data-sync inventory background refresh failed: {exc}\")" not in SOURCE
    assert '"error": type(exc).__name__' not in SOURCE
    assert "_data_sync_inventory_public_failure_code" in SOURCE
    assert '"inventory_capacity": inventory_capacity' in SOURCE
    assert '"capacity": inventory_capacity' in SOURCE
    parent_state, logged = _run_parent_exception()
    assert parent_state["error"] == "INVENTORY_WORKER_FAILED"
    assert parent_state["last_worker_failure_stage"] == "PARENT_REFRESH"
    assert parent_state["last_worker_failure_fingerprint"] == "a" * 64
    assert parent_state["last_worker_failure_volume_free_bytes"] == 123
    assert all("secret-inventory-command" not in line for line in logged)
    print("INVENTORY_CAPACITY_GUARD_CONTRACT_OK")


if __name__ == "__main__":
    main()
