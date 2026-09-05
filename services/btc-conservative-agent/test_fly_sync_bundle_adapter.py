import importlib.util
import json
from pathlib import Path

import pytest
from flask import Flask

from data_sync_bundle_api import register_bundle_routes
from data_sync_bundle_worker import run_bundle_worker
from test_data_sync_bundle_worker import _fixture, _row, GEN

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("bundle_adapter", ROOT / "scripts" / "fly-sync-bundle-client.py")
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


def build(tmp_path, count=3):
    source = tmp_path / "src"
    rows = []
    for number in range(count):
        digest = f"{number:064x}"
        rows.append(_row(source, f"v3/market_segments/{digest[:2]}/{digest}.json",
                         json.dumps({"sample": number}).encode()))
    metadata = _fixture(tmp_path, rows)
    while True:
        receipt = run_bundle_worker(metadata, source, tmp_path / "out", max_members=128)
        if receipt["status"] == "COMPLETE": break
    app = Flask(__name__)
    register_bundle_routes(app, authenticated=lambda: True, generation_lookup=lambda _: metadata,
                           output_root=tmp_path / "out")
    client = app.test_client()
    calls = []
    def fetch(url, *, timeout):
        calls.append(url)
        response = client.get(url)
        return response.status_code, dict(response.headers), response.data
    # This is the authenticated bot manifest wire shape, NOT the worker's
    # internal metadata dictionary (which has a different ack field name).
    manifest = {
        "schema": "fly_runtime_incremental_sync_v1",
        "inventory_status": "CURRENT", "inventory_authoritative": True,
        "inventory_ack_eligible": True, "inventory_generation_id": GEN,
        "inventory_sha256": GEN, "source_git_rev": metadata["source_git_rev"],
        "collection_epoch_id": metadata["collection_epoch_id"],
        "tile_registry_signature": metadata["tile_registry_signature"],
        "files": rows,
    }
    request = {"source_url": "https://doxed-btc-bot.fly.dev", "manifest": manifest,
               "staging_root": str(tmp_path / "stage")}
    return request, fetch, calls, source


def test_many_small_files_keep_exact_original_ack_rows(tmp_path):
    request, fetch, calls, source = build(tmp_path, 384)
    emitted = []
    sleeps = []
    adapter.run(request, emit=emitted.append, fetch=fetch, sleep=sleeps.append)
    assert emitted[-1]["files"] == 384 and emitted[-1]["packages"] == 3
    assert emitted[-1]["ack_sent"] is False
    assert len(calls) == 7  # one index + three descriptors + three package chunks
    assert sleeps == [0.5, 0.5, 0.5]
    members = [member for receipt in emitted[:-1] for member in receipt["members"]]
    # The original ACK v3 path/size/mtime rows are identical after batching.
    fields = ("path", "size", "mtime_ns")
    before = [{key: row[key] for key in fields} for row in request["manifest"]["files"]]
    after = [{key: row[key] for key in fields} for row in members]
    assert before == after
    for member in members:
        assert Path(member["staged_path"]).read_bytes() == (source / member["path"]).read_bytes()


@pytest.mark.parametrize("defect", ["foreign", "duplicate-package", "oversized", "unavailable"])
def test_bad_index_never_downloads_a_package(tmp_path, defect):
    request, fetch, calls, _ = build(tmp_path)
    def faulty(url, *, timeout):
        status, headers, body = fetch(url, timeout=timeout)
        if "/bundles?" in url:
            index = json.loads(body)
            if defect == "building": index["status"] = "BUILDING"
            if defect == "foreign": index["generation"]["source_git_rev"] = "foreign"
            if defect == "duplicate-package": index["packages"] *= 2
            if defect == "oversized": return 200, headers, b"x" * (adapter.MAX_META + 1)
            if defect == "unavailable": return 409, {}, b""
            return status, headers, json.dumps(index).encode()
        return status, headers, body
    with pytest.raises(ValueError): adapter.run(request, emit=lambda _: None, fetch=faulty)
    # Duplicate package is currently rejected before its second download.
    if defect != "duplicate-package": assert len(calls) == 1


def test_noncanonical_never_sends_credential(tmp_path):
    request, fetch, calls, _ = build(tmp_path)
    request["source_url"] = "https://not-fly.example"
    with pytest.raises(ValueError, match="NON_CANONICAL_SOURCE"):
        adapter.run(request, emit=lambda _: None, fetch=fetch)
    assert not calls


def test_actual_wire_authority_normalized_without_alias(tmp_path):
    request, fetch, _, _ = build(tmp_path)
    assert "ack_eligible" not in request["manifest"]
    emitted = []
    adapter.run(request, emit=emitted.append, fetch=fetch, sleep=lambda _: None)
    assert emitted[0]["generation"]["ack_eligible"] is True
    assert emitted[-1]["status"] == "COMPLETE"


@pytest.mark.parametrize("field,value", [
    ("inventory_status", "BUILDING"), ("inventory_status", "STALE"),
    ("inventory_authoritative", False), ("inventory_authoritative", 1),
    ("inventory_ack_eligible", False), ("inventory_ack_eligible", "true"),
    ("ack_eligible", False), ("ack_eligible", 1),
])
def test_wire_authority_or_alias_conflict_rejected_before_http(tmp_path, field, value):
    request, fetch, calls, _ = build(tmp_path)
    request["manifest"][field] = value
    with pytest.raises(ValueError, match="MANIFEST_NOT_ACK_ELIGIBLE"):
        adapter.run(request, emit=lambda _: None, fetch=fetch)
    assert not calls


@pytest.mark.parametrize("field", ["inventory_status", "inventory_authoritative", "inventory_ack_eligible"])
def test_internal_alias_cannot_replace_missing_public_authority(tmp_path, field):
    request, fetch, calls, _ = build(tmp_path)
    del request["manifest"][field]
    request["manifest"]["ack_eligible"] = True
    with pytest.raises(ValueError, match="MANIFEST_NOT_ACK_ELIGIBLE"):
        adapter.run(request, emit=lambda _: None, fetch=fetch)
    assert not calls


@pytest.mark.parametrize("field,value", [
    ("inventory_generation_id", "bad"), ("inventory_sha256", "b"*64),
    ("generation_id", "b"*64), ("source_git_rev", None),
    ("collection_epoch_id", ""), ("tile_registry_signature", 1),
])
def test_wire_generation_identity_rejected_before_http(tmp_path, field, value):
    request, fetch, calls, _ = build(tmp_path)
    request["manifest"][field] = value
    with pytest.raises(ValueError, match="MANIFEST_GENERATION_INVALID"):
        adapter.run(request, emit=lambda _: None, fetch=fetch)
    assert not calls


class FakeTime:
    def __init__(self): self.now = 0.0; self.sleeps = []
    def clock(self): return self.now
    def sleep(self, seconds): self.sleeps.append(seconds); self.now += seconds


def test_waits_only_same_generation_index_until_complete(tmp_path):
    request, fetch, calls, _ = build(tmp_path)
    timing = FakeTime()
    polls = []
    emitted = []
    def preparing(url, **kwargs):
        status, headers, body = fetch(url, **kwargs)
        if "/bundles?" in url:
            polls.append(url)
            if len(polls) == 1: return 404, {}, b""
            if len(polls) == 2:
                index = json.loads(body); index["status"] = "BUILDING"
                return 200, headers, json.dumps(index).encode()
        return status, headers, body
    adapter.run(request, emit=emitted.append, fetch=preparing,
                clock=timing.clock, sleep=timing.sleep)
    assert len(set(polls)) == 1 and len(polls) == 3
    assert timing.sleeps == [5, 10, 0.5]
    assert [r["status"] for r in emitted] == ["INDEX_WAITING", "INDEX_WAITING", "PACKAGE_VERIFIED", "COMPLETE"]
    assert emitted[0]["packages"] is None and emitted[1]["packages"] == 1
    assert all("manifest" not in url for url in calls)
    assert emitted[0]["ack_sent"] is False


@pytest.mark.parametrize("status", [404, 200])
def test_missing_or_building_index_has_capped_prep_deadline(tmp_path, status):
    request, fetch, calls, _ = build(tmp_path)
    timing = FakeTime()
    emitted = []
    def pending(url, **kwargs):
        if status == 404:
            calls.append(url)
            return 404, {}, b""
        response_status, headers, body = fetch(url, **kwargs)
        index = json.loads(body); index["status"] = "BUILDING"
        return response_status, headers, json.dumps(index).encode()
    with pytest.raises(ValueError, match="PREPARATION_DEADLINE"):
        adapter.run(request, emit=emitted.append, fetch=pending,
                    clock=timing.clock, sleep=timing.sleep)
    assert timing.now == 600 and max(timing.sleeps) == 30
    assert len(calls) < 30 and len(set(calls)) == 1
    assert all(r["status"] == "INDEX_WAITING" for r in emitted)


@pytest.mark.parametrize("status", [503, 429, "timeout"])
def test_index_pressure_opens_after_two_failures(tmp_path, status):
    request, _, _, _ = build(tmp_path)
    timing = FakeTime()
    calls = []
    def failed(url, **kwargs):
        calls.append(url)
        if status == "timeout": raise TimeoutError()
        return status, {}, b""
    with pytest.raises(ValueError, match="PRESSURE_CIRCUIT_OPEN"):
        adapter.run(request, emit=lambda _: None, fetch=failed,
                    clock=timing.clock, sleep=timing.sleep)
    assert len(calls) == 2 and timing.sleeps == [5]


def test_building_foreign_generation_is_not_joined(tmp_path):
    request, fetch, calls, _ = build(tmp_path)
    timing = FakeTime()
    def foreign(url, **kwargs):
        status, headers, body = fetch(url, **kwargs)
        index = json.loads(body)
        index["status"] = "BUILDING"
        index["generation"]["collection_epoch_id"] = "other"
        return status, headers, json.dumps(index).encode()
    with pytest.raises(ValueError, match="NOT_COMPLETE_OR_MATCHED"):
        adapter.run(request, emit=lambda _: None, fetch=foreign,
                    clock=timing.clock, sleep=timing.sleep)
    assert len(calls) == 1 and not timing.sleeps


def test_preparation_time_is_inside_total_transfer_budget(tmp_path, monkeypatch):
    request, fetch, _, _ = build(tmp_path)
    timing = FakeTime()
    def slow_index(url, **kwargs):
        timing.now = 500
        return fetch(url, **kwargs)
    observed = []
    def slow_package(*args, **kwargs):
        observed.append(kwargs["deadline_sec"])
        timing.now = 1801
        return {"members": []}
    monkeypatch.setattr(adapter, "fetch_verified_package", slow_package)
    with pytest.raises(ValueError, match="TRANSFER_DEADLINE"):
        adapter.run(request, emit=lambda _: None, fetch=slow_index,
                    clock=timing.clock, sleep=timing.sleep)
    assert observed == [120]
