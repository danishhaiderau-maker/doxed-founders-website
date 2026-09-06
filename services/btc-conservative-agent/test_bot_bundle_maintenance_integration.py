"""Execute canonical bot helpers without importing or starting trading runtime."""
import ast
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
from types import SimpleNamespace

import pytest

from data_sync_bundle_download_pins import DownloadProtection
from test_data_sync_bundle_maintenance import setup, IDS

BOT = Path(__file__).with_name("bot.py")
NAMES = {
    "_data_sync_bundle_retention_allowed_locked", "_data_sync_bundle_current_identity_locked",
    "_data_sync_bundle_protected_locked", "_start_data_sync_bundle_reservation_hydration",
    "_data_sync_bundle_maintain_capacity", "_data_sync_retain_inventory_generation",
    "_data_sync_retain_disk_inventory_generation", "_data_sync_inventory_rows_sha256",
    "_data_sync_inventory_generation", "_data_sync_bundle_generation", "_start_data_sync_bundle_generation",
    "_data_sync_request_async_inventory", "_data_sync_validated_inventory_index",
    "_data_sync_register_served_ack_generation",
}


def harness(tmp_path, *, queued=False):
    tree = ast.parse(BOT.read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in NAMES]
    assert {node.name for node in nodes} == NAMES
    work, source = tmp_path / "work", tmp_path / "runtime"
    work.mkdir(exist_ok=True)
    source.mkdir(exist_ok=True)
    tasks = []
    class Thread:
        def __init__(self, *, target, **kwargs): self.target = target
        def start(self):
            if queued: tasks.append(self.target)
            else: self.target()
    namespace = dict(Path=Path, os=os, re=re, hashlib=hashlib, hmac=hmac, json=json,
        datetime=datetime, time=time, threading=SimpleNamespace(Thread=Thread),
        logger=SimpleNamespace(warning=lambda *a: None), utc_iso=lambda: datetime.now(timezone.utc).isoformat(),
        _DATA_SYNC_BUNDLE_REGISTRY=None, _DATA_SYNC_BUNDLE_COORDINATOR_LOCK=threading.Lock(),
        _DATA_SYNC_BUNDLE_REGISTRY_INIT_LOCK=threading.Lock(), _DATA_SYNC_BUNDLE_REGISTRY_HYDRATING=False,
        _DATA_SYNC_BUNDLE_REGISTRY_RETRY_AT=0, _DATA_SYNC_BUNDLE_EXTERNAL_PROTECTED=frozenset(),
        _DATA_SYNC_BUNDLE_LAST_STATUS={}, _data_sync_inventory_cache_condition=threading.Condition(),
        _data_sync_inventory_generations={}, _data_sync_async_inventory={"status": "EMPTY"},
        _DATA_SYNC_INVENTORY_GENERATION_TTL_SECONDS=7200, _DATA_SYNC_INVENTORY_GENERATION_MAX=8,
        _DATA_SYNC_INVENTORY_CACHE_TTL_SECONDS=7200,
        _data_sync_inventory_work_root=lambda: work, _data_sync_runtime_root=lambda: source,
        _data_sync_inventory_snapshot_path=lambda: tmp_path / "snapshot.json",
        _runtime_git_rev=lambda: "same-source", _data_sync_receipt_bootstrap_gate=lambda: {"complete": True})
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(BOT), "exec"), namespace)
    return namespace, tasks, work, source


def generation(identity):
    return {"generation_id": identity, "storage": "disk_pages_v2", "page_index_sha256": "f" * 64,
            "file_count": 1, "total_bytes": 1,
            "bundle_identity": {"source_git_rev": "same-source", "collection_epoch_id": "epoch",
                                "tile_registry_signature": "tile"}}


def publish(namespace, identity):
    return namespace["_data_sync_retain_disk_inventory_generation"](generation(identity), "now", status="CURRENT")


def test_hydration_is_async_and_does_not_require_current_identity(tmp_path):
    ns, tasks, _, _ = harness(tmp_path, queued=True)
    ns["_data_sync_load_persisted_inventory_snapshot"] = lambda: pytest.fail("restore before hydration")
    response = ns["_data_sync_request_async_inventory"]()
    assert response["status"] == "BUILDING" and response["error"] == "BUNDLE_RESERVATIONS_NOT_READY"
    assert len(tasks) == 1 and ns["_DATA_SYNC_BUNDLE_REGISTRY"] is None
    with pytest.raises(RuntimeError, match="PUBLICATION_RESERVED"): publish(ns, IDS[0])
    tasks.pop()()
    assert ns["_DATA_SYNC_BUNDLE_REGISTRY"].ready
    assert ns["_data_sync_async_inventory"]["status"] == "EMPTY"
    assert publish(ns, IDS[0]) == IDS[0]  # No identity-provider startup deadlock.


def test_restart_hydration_blocks_fenced_publication_and_lookup(tmp_path):
    ns, _, work, _ = harness(tmp_path)
    for name in ("transport-bundles", "transport-download-pins"):
        (work / name).mkdir()
    owner = DownloadProtection(work / "transport-download-pins", work / "transport-bundles/.bundle-worker.lease")
    owner.retirement(IDS[0], fence_token="f" * 64)
    ns["_start_data_sync_bundle_reservation_hydration"]()
    with pytest.raises(RuntimeError, match="PUBLICATION_RESERVED"): publish(ns, IDS[0])
    ns["_data_sync_inventory_generations"][IDS[0]] = {**generation(IDS[0]), "ack_eligible": True,
                                                    "retained_at": time.monotonic()}
    assert ns["_data_sync_inventory_generation"](IDS[0]) is None
    assert ns["_data_sync_bundle_generation"](IDS[0]) is None
    assert ns["_data_sync_validated_inventory_index"](IDS[0], "now")[0] == {}


def test_unreserved_persisted_restores_only_after_hydration(tmp_path):
    ns, _, _, _ = harness(tmp_path)
    ns["_data_sync_load_persisted_inventory_snapshot"] = lambda: {
        "generation": generation(IDS[0]), "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_git_rev": "same-source"}
    class RestoreObserved(Exception): pass
    original = ns["_data_sync_retain_disk_inventory_generation"]
    def observe(*args, **kwargs):
        assert ns["_DATA_SYNC_BUNDLE_REGISTRY"].ready
        assert original(*args, **kwargs) == IDS[0]
        raise RestoreObserved()
    ns["_data_sync_retain_disk_inventory_generation"] = observe
    with pytest.raises(RestoreObserved):
        ns["_data_sync_request_async_inventory"]()


def test_publication_and_accessor_consult_same_reservation_under_condition(tmp_path):
    ns, _, _, _ = harness(tmp_path)
    ns["_start_data_sync_bundle_reservation_hydration"]()
    publish(ns, IDS[3])
    ns["_data_sync_async_inventory"].update(status="CURRENT", generation_id=IDS[3])
    registry = ns["_DATA_SYNC_BUNDLE_REGISTRY"]
    done, failures = threading.Event(), []
    def writer():
        try: publish(ns, IDS[0])
        except RuntimeError: failures.append(True)
        finally: done.set()
    with registry.protection_boundary(IDS[0]):
        thread = threading.Thread(target=writer)
        thread.start()
        assert done.wait(2)
        thread.join(2)
        assert failures and ns["_data_sync_inventory_generation"](IDS[0]) is None
    assert publish(ns, IDS[0]) == IDS[0]


def test_coordinator_maintains_capacity_before_starting_slice_owner(tmp_path, setup, monkeypatch):
    import data_sync_bundle_runtime
    args, _, _ = setup
    ns, _, work, source = harness(tmp_path)
    shutil.copytree(args["output_root"], work / "transport-bundles")
    (source / "raw-record").write_bytes(b"preserved")
    ns["_start_data_sync_bundle_reservation_hydration"]()
    for identity in (IDS[3], IDS[4]): publish(ns, identity)
    ns["_data_sync_async_inventory"].update(status="CURRENT", generation_id=IDS[3])
    events = []
    def run(*a, **kw):
        assert ns["_DATA_SYNC_BUNDLE_COORDINATOR_LOCK"].locked()
        assert len(list((work / "transport-bundles").glob("g-*"))) == 3
        assert (source / "raw-record").read_bytes() == b"preserved"
        events.append("run")
        return {"status": "COMPLETE"}
    monkeypatch.setattr(data_sync_bundle_runtime, "run_managed_generation", run)
    monkeypatch.setenv("DATA_SYNC_TRANSPORT_BUNDLES_ENABLED", "1")
    assert ns["_start_data_sync_bundle_generation"](IDS[4]) is True
    assert events == ["run"] and not ns["_DATA_SYNC_BUNDLE_COORDINATOR_LOCK"].locked()


def test_canonical_callsites_guard_every_retention_writer_and_direct_accessor():
    tree = ast.parse(BOT.read_text(encoding="utf-8"))
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    for name in ("_data_sync_retain_inventory_generation", "_data_sync_retain_disk_inventory_generation",
                 "_data_sync_inventory_generation", "_data_sync_register_served_ack_generation",
                 "_data_sync_validated_inventory_index"):
        assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id == "_data_sync_bundle_retention_allowed_locked" for n in ast.walk(functions[name]))
    main = functions["main"]
    calls = [(n.func.id, n.lineno) for n in ast.walk(main) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    hydration = next(line for name, line in calls if name == "_start_data_sync_bundle_reservation_hydration")
    lifecycle = next(line for name, line in calls if name == "_start_lifecycle_pipeline_runtime")
    assert hydration < lifecycle
    request = functions["_data_sync_request_async_inventory"]
    request_calls = [(n.func.id, n.lineno) for n in ast.walk(request) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert next(line for name, line in request_calls if name == "_start_data_sync_bundle_reservation_hydration") < next(
        line for name, line in request_calls if name == "_data_sync_load_persisted_inventory_snapshot")


def test_expired_cache_is_not_permanent_protection(tmp_path):
    ns, _, _, _ = harness(tmp_path)
    now = time.monotonic()
    ns["_data_sync_inventory_generations"].update({
        IDS[0]: {"retained_at": now - 7201}, IDS[1]: {"retained_at": now},
        IDS[2]: {}, IDS[3]: {"retained_at": now + 100}})
    with ns["_data_sync_inventory_cache_condition"]:
        protected = ns["_data_sync_bundle_protected_locked"]()
    assert IDS[0] not in protected
    assert {IDS[1], IDS[2], IDS[3]} <= protected


def test_nested_ack_session_protects_old_parent_directory(tmp_path, monkeypatch):
    import data_sync_bundle_maintenance
    ns, _, work, _ = harness(tmp_path)
    ns["_start_data_sync_bundle_reservation_hydration"]()
    publish(ns, IDS[3])
    ns["_data_sync_async_inventory"].update(status="CURRENT", generation_id=IDS[3])
    parent = work / "inventory-acks" / IDS[0]
    session = parent / ("c" * 32)
    session.mkdir(parents=True)
    old = time.time() - 8000
    os.utime(parent, (old, old))
    def inspect(**kwargs):
        with kwargs["protection_boundary"](IDS[0]) as snapshot:
            assert IDS[0] in snapshot["protected_generations"]
        return {"status": "ADMITTED"}
    monkeypatch.setattr(data_sync_bundle_maintenance, "maintain_capacity", inspect)
    assert ns["_data_sync_bundle_maintain_capacity"](generation(IDS[3]))["status"] == "ADMITTED"
