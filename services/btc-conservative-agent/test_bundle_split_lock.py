"""OS-process barriers prove producer progress is independent of reads."""
from contextlib import contextmanager
import hashlib
import multiprocessing
from pathlib import Path

import pytest
from flask import Flask, request

import data_sync_bundle_worker as worker
from data_sync_bundle_api import register_bundle_routes
from data_sync_bundle_download_pins import DownloadProtection
from test_data_sync_bundle_api import setup_api
from test_data_sync_bundle_worker import _fixture, _row, GEN
from test_data_sync_bundle_retirement import args as retirement_args
from data_sync_bundle_retirement import retire_derivative_generation

AUTH = {"X-Test-Auth": "yes"}


def _hold_lease(path, ready, release):
    with worker._singleton_lease(Path(path)):
        ready.set()
        assert release.wait(20), "parent did not release lease"


def _build_paused(meta, source, output, ready, release):
    original = worker.build_bundle
    def paused(*args, **kwargs):
        ready.set()
        assert release.wait(20)
        return original(*args, **kwargs)
    worker.build_bundle = paused
    worker.run_bundle_worker(meta, source, output, max_members=1, max_elapsed_sec=30)


@contextmanager
def child(target, *args):
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    process = ctx.Process(target=target, args=(*args, ready, release))
    process.start()
    try:
        assert ready.wait(10), "child did not reach barrier"
        assert process.is_alive()
        yield
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
        assert process.exitcode == 0


def test_existing_package_reads_while_producer_process_holds_lease(tmp_path):
    client, result, _, output = setup_api(tmp_path)
    digest = result["package"]["package_sha256"]
    url = f"/api/data-sync/bundle?generation_id={GEN}&package_id={digest}"
    expected = Path(result["package"]["package_path"]).read_bytes()
    with child(_hold_lease, str(output / ".bundle-worker.lease")):
        assert client.get(f"/api/data-sync/bundles?generation_id={GEN}", headers=AUTH).status_code == 200
        assert client.get(url + "&descriptor=1", headers=AUTH).status_code == 200
        response = client.get(url + "&limit=127", headers=AUTH)
        assert response.status_code == 200 and response.data == expected[:127]
        assert response.headers["X-Chunk-Sha256"] == hashlib.sha256(expected[:127]).hexdigest()


def test_actual_paused_build_keeps_previous_atomic_index_readable(tmp_path):
    source, output = tmp_path / "source", tmp_path / "out"
    rows = [_row(source, f"v3/market_segments/{c*2}/{c*64}.json", c.encode()) for c in "12"]
    meta = _fixture(tmp_path, rows)
    first = worker.run_bundle_worker(meta, source, output, max_members=1)
    app = Flask(__name__)
    register_bundle_routes(app, authenticated=lambda: request.headers.get("X-Test-Auth") == "yes",
                           generation_lookup=lambda _: meta, output_root=output)
    client = app.test_client()
    with child(_build_paused, meta, source, output):
        index = client.get(f"/api/data-sync/bundles?generation_id={GEN}", headers=AUTH)
        assert index.status_code == 200 and len(index.json["packages"]) == 1
        digest = first["package"]["package_sha256"]
        assert client.get(f"/api/data-sync/bundle?generation_id={GEN}&package_id={digest}&limit=17", headers=AUTH).status_code == 200
        assert client.get(f"/api/data-sync/bundle?generation_id={GEN}&package_id={'e'*64}&limit=17", headers=AUTH).status_code == 404
    index = client.get(f"/api/data-sync/bundles?generation_id={GEN}", headers=AUTH)
    assert index.status_code == 200 and len(index.json["packages"]) == 2
    assert index.json["status"] == "COMPLETE"


@pytest.mark.parametrize("lease", [".bundle-worker.lease", ".bundle-readers.lease"])
def test_retirement_excludes_both_process_owners_and_releases_other_lock(tmp_path, lease):
    meta, source, output, directory, options = retirement_args(tmp_path)
    with child(_hold_lease, str(output / lease)):
        with pytest.raises(worker.BundleWorkerError, match="LEASE_HELD"):
            retire_derivative_generation(source, output, meta["generation_id"], **options)
        assert directory.exists() and not options["receipt_path"].exists()
        other = ".bundle-readers.lease" if lease == ".bundle-worker.lease" else ".bundle-worker.lease"
        with worker._singleton_lease(output / other):
            pass


def test_inflight_read_blocks_fencing_even_after_ttl(tmp_path):
    output, pins = tmp_path / "out", tmp_path / "pins"
    output.mkdir(); pins.mkdir()
    clock = [100.0]
    owner = DownloadProtection(pins, output / ".bundle-worker.lease", clock=lambda: clock[0])
    owner.pin(GEN, "b"*64, ttl_seconds=1)
    with owner.read_chunk(GEN, "b"*64):
        clock[0] = 1000
        with pytest.raises(worker.BundleWorkerError, match="LEASE_HELD"):
            owner.retirement(GEN, fence_token="f"*64)
    assert owner.retirement(GEN, fence_token="f"*64)["ready"]
    with pytest.raises(ValueError, match="RETIRING"):
        DownloadProtection(pins, output / ".bundle-worker.lease", clock=lambda: 1001).pin(GEN, "b"*64)
