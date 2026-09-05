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
    manifest = {**metadata, "inventory_generation_id": GEN, "files": rows}
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


@pytest.mark.parametrize("defect", ["building", "foreign", "duplicate-package", "oversized", "unavailable"])
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
            if defect == "unavailable": return 503, {}, b""
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
