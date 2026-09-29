"""Explicit full-backlog acceptance fixture; no production data or networking.

Run: python test_batch_full_backlog.py --count 34433
Temporary files are exclusively owned by TemporaryDirectory and removed at exit.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import time

from flask import Flask

from data_sync_bundle_api import register_bundle_routes
from data_sync_bundle_runtime import run_slice
from data_sync_bundle_client import fetch_verified_package
from data_sync_bundle_storage import check_derivative_admission
from data_sync_bundle_transport import MAX_PACKAGE_BYTES


def canonical(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=True).encode()


def run(count):
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="btc-b-") as owned:
        root = Path(owned)
        source = root / "src"
        relative_root = "v3/receipts/emergency_record_idempotency_v1/decision"
        (source / relative_root).mkdir(parents=True)
        rows = []
        for number in range(count):
            relative = f"{relative_root}/{number:064x}.json"
            payload = canonical({"schema": "emergency_record_idempotency_v1", "state": "COMMITTED",
                                 "record_id": number, "fixture_padding": "x" * 600})
            path = source / relative
            path.write_bytes(payload)
            stat = path.stat()
            rows.append({"path": relative, "size": len(payload), "mtime_ns": stat.st_mtime_ns,
                         "inode": stat.st_ino, "consistency_mode": "strict_generation_v1"})
        print(json.dumps({"phase": "FIXTURE_CREATED", "files": count}), flush=True)
        work = source / ".data-sync-snapshots"
        building = work / "inventory-generations" / "fixture-building"
        building.mkdir(parents=True)
        descriptors = []
        hasher = hashlib.sha256(b"fly_runtime_inventory_generation_v2\n")
        for page_index, offset in enumerate(range(0, count, 250)):
            selected = rows[offset:offset + 250]
            size = sum(row["size"] for row in selected)
            page = {"schema": "fly_runtime_inventory_page_v1", "page_index": page_index,
                    "file_count": len(selected), "total_bytes": size,
                    "rows_sha256": hashlib.sha256(canonical(selected)).hexdigest(), "rows": selected}
            raw = canonical(page)
            digest = hashlib.sha256(raw).hexdigest()
            name = f"p{page_index:08d}-{digest[:24]}.json"
            (building / name).write_bytes(raw)
            descriptors.append({"page_index": page_index, "file_count": len(selected),
                                "total_bytes": size, "page_sha256": digest, "file_name": name})
            hasher.update(f"{page_index}:{len(selected)}:{size}:{digest}\n".encode())
        total = sum(row["size"] for row in rows)
        top = [{"path": row["path"], "name": Path(row["path"]).name, "size": row["size"]}
               for row in sorted(rows, key=lambda row: (-row["size"], row["path"]))[:5]]
        hasher.update(f"files:{count}\nbytes:{total}\n".encode())
        hasher.update(canonical(top) + b"\n")
        generation = hasher.hexdigest()
        directory = building.with_name(generation)
        building.rename(directory)
        index = b"".join(canonical(item) + b"\n" for item in descriptors)
        (directory / "page-index.jsonl").write_bytes(index)
        meta = {"storage": "disk_pages_v2", "generation_id": generation,
                "inventory_generation_id": generation, "inventory_sha256": generation,
                "ack_eligible": True, "source_git_rev": "fixture-source", "collection_epoch_id": "fixture-epoch",
                "tile_registry_signature": "fixture-tile", "generation_dir": str(directory),
                "page_index_path": str(directory / "page-index.jsonl"),
                "page_index_sha256": hashlib.sha256(index).hexdigest(), "page_count": len(descriptors),
                "page_size": 250, "file_count": count, "total_bytes": total, "top_files": top}
        # Execute the real backend's index/generation validator without importing
        # a trading owner. This is the same production metadata contract.
        from test_data_sync_inventory_worker_contract import _parent_validate_generation
        assert _parent_validate_generation(meta, work)["generation_id"] == generation
        output = work / "transport-bundles"
        slices = 0
        maximum_slice = 0
        while True:
            before = time.monotonic()
            result = run_slice(meta, source, output)
            maximum_slice = max(maximum_slice, time.monotonic() - before)
            slices += 1
            if result["status"] == "FAILED":
                raise AssertionError(result)
            if slices % 25 == 0:
                print(json.dumps({"phase": "BUILDING", "slices": slices, "cursor": result["cursor"]}), flush=True)
            if result["status"] == "COMPLETE": break
            assert slices <= 512
        admission = check_derivative_admission(output, generation, MAX_PACKAGE_BYTES)
        app = Flask(__name__)
        register_bundle_routes(app, authenticated=lambda: True,
                               generation_lookup=lambda _: meta, output_root=output)
        http = app.test_client()
        calls = 1
        index = http.get(f"/api/data-sync/bundles?generation_id={generation}").json
        def fetch(url, *, timeout):
            nonlocal calls
            calls += 1
            response = http.get(url)
            return response.status_code, dict(response.headers), response.data
        lookup = {row["path"]: row for row in rows}
        actual = {}
        for number, entry in enumerate(index["packages"]):
            result = fetch_verified_package(entry, meta, lookup, root / "stage", fetch)
            for member in result["members"]:
                assert member["path"] not in actual
                assert (source / member["path"]).read_bytes() == Path(member["staged_path"]).read_bytes()
                actual[member["path"]] = {key: member[key] for key in ("path", "size", "mtime_ns")}
            if number % 50 == 0:
                print(json.dumps({"phase": "VERIFIED", "packages": number + 1, "files": len(actual)}), flush=True)
        expected = {row["path"]: {key: row[key] for key in ("path", "size", "mtime_ns")} for row in rows}
        assert actual == expected
        receipt = {"status": "PASS", "files": count, "bytes": total, "pages": len(descriptors),
                   "packages": len(index["packages"]), "http_requests": calls,
                   "subprocess_slices": slices, "max_slice_seconds": round(maximum_slice, 3),
                   "fixture_seconds": round(time.monotonic() - started, 3),
                   "original_ack_rows_equal": True, "every_member_bytes_equal": True,
                   "derivative_estimated_bytes": admission["estimated_bytes"],
                   "production_throughput_proven": False, "temporary_fixture_cleanup": "ON_CONTEXT_EXIT"}
    receipt["temporary_fixture_cleanup"] = "COMPLETE"
    print(json.dumps(receipt, sort_keys=True), flush=True)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=34433)
    args = parser.parse_args()
    if not 1 <= args.count <= 34433: parser.error("count must be1..34433")
    run(args.count)
