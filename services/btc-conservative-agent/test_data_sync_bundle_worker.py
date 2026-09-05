import hashlib
import json
import os
from pathlib import Path

import pytest

import data_sync_bundle_worker as worker

GEN = "a" * 64


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _row(root: Path, relative: str, payload: bytes, *, mode="strict_generation_v1"):
    path = root / Path(*relative.split("/")); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload); stat = path.stat()
    return {"path": relative, "size": len(payload), "mtime_ns": stat.st_mtime_ns,
            "inode": getattr(stat, "st_ino", 0) or 0, "consistency_mode": mode}


def _fixture(tmp_path, rows):
    generation_dir = tmp_path / "inventory" / GEN; generation_dir.mkdir(parents=True)
    payload = {"schema": "fly_runtime_inventory_page_v1", "page_index": 0,
               "file_count": len(rows), "total_bytes": sum(row["size"] for row in rows),
               "rows_sha256": hashlib.sha256(_canonical(rows)).hexdigest(), "rows": rows}
    raw = _canonical(payload); page_sha = hashlib.sha256(raw).hexdigest()
    name = f"p00000000-{page_sha[:24]}.json"; (generation_dir / name).write_bytes(raw)
    descriptor = {"page_index": 0, "file_count": len(rows),
                  "total_bytes": payload["total_bytes"], "page_sha256": page_sha, "file_name": name}
    index = _canonical(descriptor) + b"\n"; index_path = generation_dir / "page-index.jsonl"
    index_path.write_bytes(index)
    meta = {"storage": "disk_pages_v2", "generation_id": GEN, "inventory_sha256": GEN,
            "ack_eligible": True, "source_git_rev": "src", "collection_epoch_id": "epoch",
            "tile_registry_signature": "tile", "page_count": 1, "file_count": len(rows),
            "total_bytes": payload["total_bytes"], "generation_dir": str(generation_dir),
            "page_index_path": str(index_path),
            "page_index_sha256": hashlib.sha256(index).hexdigest()}
    return meta


def test_builds_one_bound_bundle_and_resumes_without_source_scan(tmp_path, monkeypatch):
    source = tmp_path / "source"
    rows = [_row(source, f"v3/market_segments/{char*2}/{char*64}.json", char.encode())
            for char in ("1", "2")]
    meta = _fixture(tmp_path, rows); output = tmp_path / "out"
    first = worker.run_bundle_worker(meta, source, output, max_members=1)
    assert first["status"] == "BUILDING"
    assert first["package"]["member_count"] == 1
    member = first["package"]["members"][0]
    assert {key: member[key] for key in ("path", "size", "mtime_ns", "inode", "consistency_mode")} == {
        key: rows[0][key] for key in ("path", "size", "mtime_ns", "inode", "consistency_mode")}
    state = json.loads((output / f"g-{GEN[:16]}" / "bundle-worker-state.json").read_text())
    assert state["cursor"]["page_row_index"] == 1
    assert len(state["package_index"]) == 1
    second = worker.run_bundle_worker(meta, source, output, max_members=1)
    assert second["status"] == "COMPLETE"
    assert second["package_index_count"] == 2
    assert second["active_integration"] is False


def test_verifies_index_page_rows_and_page_hash(tmp_path):
    source = tmp_path / "source"
    rows = [_row(source, "v3/market_segments/11/" + "1" * 64 + ".json", b"x")]
    meta = _fixture(tmp_path, rows)
    page = next(Path(meta["generation_dir"]).glob("p*.json")); page.write_bytes(b"{}")
    with pytest.raises(worker.BundleWorkerError, match="INVENTORY_PAGE_SHA256_MISMATCH"):
        worker.run_bundle_worker(meta, source, tmp_path / "out")


def test_resume_uses_fenced_index_identity_not_full_rehash(tmp_path, monkeypatch):
    source = tmp_path / "source"
    rows = [_row(source, f"v3/market_segments/{char*2}/{char*64}.json", char.encode())
            for char in ("1", "2")]
    meta = _fixture(tmp_path, rows); output = tmp_path / "out"
    worker.run_bundle_worker(meta, source, output, max_members=1)
    original = worker._bounded_read
    index_path = Path(meta["page_index_path"])
    def guarded(path, limit, code):
        if path == index_path:
            raise AssertionError("resumed worker reread whole page index")
        return original(path, limit, code)
    monkeypatch.setattr(worker, "_bounded_read", guarded)
    assert worker.run_bundle_worker(meta, source, output, max_members=1)["status"] == "COMPLETE"


def test_hot_noneligible_and_oversized_rows_are_explicit_fallback(tmp_path):
    source = tmp_path / "source"
    hot = _row(source, "v3/market_segments/11/" + "1" * 64 + ".json", b"h", mode="best_effort_hot_v1")
    other = _row(source, "v3/ledgers/live.jsonl", b"x")
    large = _row(source, "v3/market_segments/22/" + "2" * 64 + ".json", b"12345")
    meta = _fixture(tmp_path, [hot, other, large])
    result = worker.run_bundle_worker(meta, source, tmp_path / "out", max_payload_bytes=4)
    assert result["status"] == "COMPLETE" and result["package"] is None
    assert result["skipped_counts"] == {
        "INELIGIBLE_HOT_ROW_PER_FILE_FALLBACK": 1,
        "INELIGIBLE_PATH_PER_FILE_FALLBACK": 1,
        "OVERSIZED_ROW_PER_FILE_FALLBACK": 1,
    }


def test_changed_source_identity_fails_without_advancing_cursor(tmp_path):
    source = tmp_path / "source"
    row = _row(source, "v3/market_segments/11/" + "1" * 64 + ".json", b"x")
    meta = _fixture(tmp_path, [row]); (source / row["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="source generation differs from inventory"):
        worker.run_bundle_worker(meta, source, tmp_path / "out")
    assert not (tmp_path / "out" / f"g-{GEN[:16]}" / "bundle-worker-state.json").exists()


def test_page_and_read_budgets_fail_closed(tmp_path):
    source = tmp_path / "source"
    row = _row(source, "v3/market_segments/11/" + "1" * 64 + ".json", b"x")
    meta = _fixture(tmp_path, [row])
    with pytest.raises(worker.BundleWorkerError, match="PAGE_INDEX_READ_BUDGET_EXCEEDED"):
        worker.run_bundle_worker(meta, source, tmp_path / "out", max_read_bytes=1)


def test_time_budget_parks_without_consuming_inventory_row(tmp_path, monkeypatch):
    source = tmp_path / "source"
    row = _row(source, "v3/market_segments/11/" + "1" * 64 + ".json", b"x")
    meta = _fixture(tmp_path, [row])
    clock = iter((0.0, 2.0))
    monkeypatch.setattr(worker.time, "monotonic", lambda: next(clock))
    result = worker.run_bundle_worker(meta, source, tmp_path / "out", max_elapsed_sec=1)
    assert result["status"] == "BUILDING" and result["package"] is None
    assert result["cursor"] == {"page_index": 0, "page_row_index": 0, "index_offset": 0}


def test_generation_totals_must_match_all_completed_pages(tmp_path):
    source = tmp_path / "source"
    row = _row(source, "v3/market_segments/11/" + "1" * 64 + ".json", b"x")
    meta = _fixture(tmp_path, [row]); meta["file_count"] = 2
    with pytest.raises(worker.BundleWorkerError, match="INVENTORY_GENERATION_PAGE_TOTAL_MISMATCH"):
        worker.run_bundle_worker(meta, source, tmp_path / "out")


def test_derivative_output_cannot_pollute_source_inventory(tmp_path):
    source = tmp_path / "source"
    row = _row(source, "v3/market_segments/11/" + "1" * 64 + ".json", b"x")
    meta = _fixture(tmp_path, [row])
    with pytest.raises(worker.BundleWorkerError, match="DERIVATIVE_OUTPUT_MUST_BE_OUTSIDE_SOURCE_ROOT"):
        worker.run_bundle_worker(meta, source, source / "derived")


def test_singleton_lease_rejects_second_owner(tmp_path):
    lease = tmp_path / ".bundle-worker.lease"
    with worker._singleton_lease(lease):
        with pytest.raises(worker.BundleWorkerError, match="BUNDLE_WORKER_LEASE_HELD"):
            with worker._singleton_lease(lease):
                pass


def test_worker_never_deletes_source_and_reports_no_active_integration(tmp_path):
    source = tmp_path / "source"
    row = _row(source, "v3/market_segments/11/" + "1" * 64 + ".json", b"x")
    meta = _fixture(tmp_path, [row])
    result = worker.run_bundle_worker(meta, source, tmp_path / "out")
    assert (source / row["path"]).read_bytes() == b"x"
    assert result["active_integration"] is False


def test_empty_generation_has_zero_totals_not_missing(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    meta = _fixture(tmp_path, [])
    result = worker.run_bundle_worker(meta, source, tmp_path / "out")
    assert result["status"] == "COMPLETE"
    assert result["package_index_count"] == 0


def test_package_descriptor_hash_is_saved_for_bounded_api_verification(tmp_path):
    source = tmp_path / "source"
    row = _row(source, "v3/market_segments/11/" + "1" * 64 + ".json", b"x")
    meta = _fixture(tmp_path, [row])
    output = tmp_path / "out"
    result = worker.run_bundle_worker(meta, source, output)
    state = json.loads((output / f"g-{GEN[:16]}" / "bundle-worker-state.json").read_text())
    assert state["package_index"][0]["descriptor_sha256"] == hashlib.sha256(_canonical(result["package"])).hexdigest()


def test_bounded_read_rejects_oversized_file_before_open(tmp_path, monkeypatch):
    target = tmp_path / "large"
    target.write_bytes(b"1234")
    monkeypatch.setattr(Path, "open", lambda *a, **k: pytest.fail("oversized read opened"))
    with pytest.raises(worker.BundleWorkerError, match="LIMIT"):
        worker._bounded_read(target, 3, "LIMIT")


def test_fractional_generation_counts_are_rejected(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    meta = _fixture(tmp_path, [])
    meta["page_count"] = 1.5
    with pytest.raises(worker.BundleWorkerError, match="COUNTS_INVALID"):
        worker.run_bundle_worker(meta, source, tmp_path / "out")
