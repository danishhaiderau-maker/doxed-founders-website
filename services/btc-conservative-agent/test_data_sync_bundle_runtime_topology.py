"""Production volume/runtime topology, without importing or starting a bot."""
import ast
import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

import data_sync_bundle_resumption as runtime_module
from data_sync_bundle_transport import build_bundle, extract_verified_bundle, is_bundle_eligible_path, MAX_PACKAGE_BYTES
from data_sync_bundle_storage import check_derivative_admission
from data_sync_bundle_worker import _validate_output_root
from data_sync_inventory_worker import _relpath

BOT = Path(__file__).with_name("bot.py")
GEN = "a" * 64


class InlineThread:
    """Execute the exact owner closure synchronously; never create a thread."""
    def __init__(self, *, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


def load_adapter(extra):
    names = {
        "_data_sync_bundle_admission_identity",
        "_data_sync_bundle_admission_store",
        "_data_sync_bundle_admission_publish",
        "_admit_data_sync_bundle_generation",
        "_start_data_sync_bundle_generation",
        "_data_sync_runtime_root", "_data_sync_volume_root", "_data_sync_relpath",
    }
    tree = ast.parse(BOT.read_text(encoding="utf-8"))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in selected} == names
    namespace = {
        "os": os, "Path": Path, "re": re, "uuid": uuid,
        "threading": SimpleNamespace(Thread=InlineThread),
        "_DATA_SYNC_TOP_LEVEL_RECEIPT_NAMES": set(),
        "_DATA_SYNC_BUNDLE_PROCESS_INCARNATION": "1" * 32,
        "_DATA_SYNC_BUNDLE_ADMISSION_STATUS": {"outcome": "NOT_OBSERVED"},
        "_DATA_SYNC_BUNDLE_LAST_STATUS": {},
        "_DATA_SYNC_BUNDLE_REGISTRY": SimpleNamespace(ready=True),
        "_data_sync_receipt_bootstrap_gate": lambda: {"complete": True},
        "_data_sync_inventory_generation": lambda generation_id: extra["_data_sync_bundle_generation"](generation_id),
        "_start_data_sync_bundle_reservation_hydration": lambda: None,
        **extra,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(BOT), "exec"), namespace)
    return namespace


@pytest.mark.parametrize("kind", ["market_segment"])
def test_real_coordinator_uses_inventory_runtime_root_not_parent_volume(tmp_path, monkeypatch, kind):
    volume = tmp_path / "volume"
    runtime = volume / "runtime"
    runtime.mkdir(parents=True)
    work = volume / ".data-sync-snapshots"
    work.mkdir()
    monkeypatch.setenv("BOT_DATA_DIR", str(volume))
    monkeypatch.setenv("DATA_SYNC_TRANSPORT_BUNDLES_ENABLED", "1")
    # signal_snapshots_v1 is inventory/package-excluded (ACK poison); market
    # segments remain the content-addressed transport proof surface here.
    payload = json.dumps({"schema": "market_segment_v3", "source": "CANONICAL_1M",
                          "symbol": "BTCUSD", "timeframe": "1m", "start_ts": 960,
                          "end_ts": 1020, "rows": [{"t": 960, "o": 100, "h": 101, "l": 99, "c": 100}]}).encode()
    digest = hashlib.sha256(payload).hexdigest()
    path = runtime / "v3" / "market_segments" / digest[:2] / (digest + ".json")
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    metadata = {"generation_id": GEN, "inventory_generation_id": GEN, "inventory_sha256": GEN,
                "page_index_sha256": "b" * 64, "source_git_rev": "source", "collection_epoch_id": "epoch",
                "tile_registry_signature": "tile", "ack_eligible": True}
    lock = threading.Lock()
    admissions = []
    def maintain_capacity(generation):
        # Empty derivative topology needs no retirement. Exercise real bounded
        # admission; full reservation/retirement wiring has its own integration suite.
        assert lock.locked()
        assert generation == metadata
        result = check_derivative_admission(work / "transport-bundles", GEN, MAX_PACKAGE_BYTES)
        admissions.append(result)
        return result
    namespace = load_adapter({
        "_DATA_SYNC_BUNDLE_COORDINATOR_LOCK": lock,
        "_data_sync_bundle_generation": lambda generation_id: metadata if generation_id == GEN else None,
        "_data_sync_inventory_work_root": lambda: work,
        "_data_sync_bundle_maintain_capacity": maintain_capacity,
        "_lifecycle_pipeline_pressure_probe": lambda: {"pressure": False, "emergency": False},
        "_lifecycle_pipeline_overlap_probe": lambda: False,
        "utc_iso": lambda: "2026-09-05T00:00:00Z",
    })
    relative = namespace["_data_sync_relpath"](path)
    assert relative == _relpath(path, {"_volume": volume, "_runtime": runtime})
    assert relative.startswith("v3/") and not relative.startswith("runtime/")
    assert is_bundle_eligible_path(relative)
    assert not (volume / relative).exists()  # No hidden v3 symlink or duplicate alias.
    stat = path.stat()
    inventory_row = {"path": relative, "size": stat.st_size, "physical_size": stat.st_size,
                     "mtime_ns": stat.st_mtime_ns, "inode": stat.st_ino,
                     "consistency_mode": "strict_generation_v1"}
    observed = {}

    def managed(generation, source_root, output_root, *, pressure_probe, generation_available, publish):
        observed.update(source_root=Path(source_root), output_root=Path(output_root))
        assert generation_available(generation) is True
        assert pressure_probe() == {"pressure": False, "emergency": False, "overlap": False}
        assert _validate_output_root(Path(source_root), output_root) == output_root.resolve()
        # Real bounded transport: mock only coordinator scheduling/network boundaries.
        observed["descriptor"] = build_bundle(generation, [inventory_row], source_root, output_root)
        return {"status": "COMPLETE", "package_index_count": 1}

    monkeypatch.setattr(runtime_module, "run_resumable_generation", managed)
    assert namespace["_start_data_sync_bundle_generation"](GEN) is True
    assert len(admissions) == 1 and admissions[0]["status"] == "ADMITTED"
    assert observed["source_root"] == runtime, "Inventory is runtime-relative, not volume-relative"
    assert observed["output_root"] == work / "transport-bundles"
    assert namespace["_DATA_SYNC_BUNDLE_LAST_STATUS"]["status"] == "COMPLETE"
    assert not lock.locked()
    descriptor = observed["descriptor"]
    result = extract_verified_bundle(descriptor["package_path"], descriptor, GEN, tmp_path / "stage")
    assert result["members"][0]["path"] == relative
    assert Path(result["members"][0]["staged_path"]).read_bytes() == payload
    assert path.read_bytes() == payload  # No source deletion, ACK, or mutation.
