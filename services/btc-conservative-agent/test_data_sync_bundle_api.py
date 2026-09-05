import hashlib
import json
import os
from pathlib import Path

import pytest
from flask import Flask, request

import data_sync_bundle_api as api
from data_sync_bundle_worker import run_bundle_worker
from test_data_sync_bundle_worker import _fixture, _row, GEN


def setup_api(tmp_path):
    source = tmp_path / "source"
    row = _row(source, "v3/market_segments/11/" + "1" * 64 + ".json", b"data")
    meta = _fixture(tmp_path, [row])
    output = tmp_path / "out"
    result = run_bundle_worker(meta, source, output)
    app = Flask(__name__)
    retained = dict(meta)
    api.register_bundle_routes(
        app, authenticated=lambda: request.headers.get("X-Test-Auth") == "yes",
        generation_lookup=lambda g: retained if g == GEN else None, output_root=output)
    return app.test_client(), result, retained, output


def test_index_and_chunks_bind_generation_and_do_not_expose_paths(tmp_path):
    client, result, _, _ = setup_api(tmp_path)
    headers = {"X-Test-Auth": "yes"}
    index = client.get(f"/api/data-sync/bundles?generation_id={GEN}", headers=headers)
    assert index.status_code == 200
    assert index.json["status"] == "COMPLETE"
    assert index.json["ack_authority"] == "ORIGINAL_MANIFEST_ROWS_ONLY"
    assert "descriptor_path" not in index.json["packages"][0]
    digest = result["package"]["package_sha256"]
    url = f"/api/data-sync/bundle?generation_id={GEN}&package_id={digest}"
    descriptor = client.get(url + "&descriptor=1", headers=headers)
    assert descriptor.status_code == 200
    assert "package_path" not in descriptor.json
    from data_sync_bundle_worker import _canonical
    assert hashlib.sha256(_canonical(descriptor.json)).hexdigest() == index.json["packages"][0]["descriptor_sha256"]
    response = client.get(url + "&limit=123", headers=headers)
    assert response.status_code == 200 and len(response.data) == 123
    assert response.headers["X-Chunk-Sha256"] == hashlib.sha256(response.data).hexdigest()
    assert response.headers["X-Inventory-Generation"] == GEN
    assert response.headers["X-Chunk-EOF"] == "false"


def test_strict_auth_precedes_generation_lookup_even_loopback(tmp_path):
    client, _, _, _ = setup_api(tmp_path)
    for route in ("bundles", "bundle"):
        response = client.get(f"/api/data-sync/{route}?generation_id={GEN}")
        assert response.status_code == 401


def test_expired_or_non_ack_generation_is_rejected(tmp_path):
    client, _, retained, _ = setup_api(tmp_path)
    retained["ack_eligible"] = False
    assert client.get(f"/api/data-sync/bundles?generation_id={GEN}", headers={"X-Test-Auth": "yes"}).status_code == 409
    assert client.get(f"/api/data-sync/bundles?generation_id={'b'*64}", headers={"X-Test-Auth": "yes"}).status_code == 409


@pytest.mark.parametrize("suffix", ["offset=-1", "limit=0", "limit=1048577", "offset=9999999"])
def test_invalid_ranges_fail_closed(tmp_path, suffix):
    client, result, _, _ = setup_api(tmp_path)
    digest = result["package"]["package_sha256"]
    response = client.get(f"/api/data-sync/bundle?generation_id={GEN}&package_id={digest}&{suffix}", headers={"X-Test-Auth": "yes"})
    assert response.status_code == 416


def test_tampered_descriptor_or_path_is_rejected(tmp_path):
    client, result, _, output = setup_api(tmp_path)
    digest = result["package"]["package_sha256"]
    descriptor = output / f"g-{GEN[:16]}" / "descriptors" / f"d-{digest[:20]}.json"
    descriptor.write_text('{}')
    response = client.get(f"/api/data-sync/bundle?generation_id={GEN}&package_id={digest}&descriptor=1", headers={"X-Test-Auth": "yes"})
    assert response.status_code == 409
    assert response.json["error"] == "PACKAGE_DESCRIPTOR_HASH_MISMATCH"
    response = client.get(f"/api/data-sync/bundle?generation_id={GEN}&package_id=../../secret", headers={"X-Test-Auth": "yes"})
    assert response.status_code == 400


def test_http_never_builds_or_hashes_whole_archive(tmp_path, monkeypatch):
    client, result, _, _ = setup_api(tmp_path)
    import data_sync_bundle_transport as transport
    monkeypatch.setattr(transport, "build_bundle", lambda *a, **k: pytest.fail("HTTP built package"))
    original = Path.open

    class BoundedFile:
        def __init__(self, path, *a, **k): self.handle = original(path, *a, **k)
        def __enter__(self): return self
        def __exit__(self, *args): self.handle.close()
        def seek(self, *args): return self.handle.seek(*args)
        def fileno(self): return self.handle.fileno()
        def read(self, size=-1):
            assert 0 <= size <= 17
            return self.handle.read(size)

    def guarded(path, *a, **k):
        return BoundedFile(path, *a, **k) if str(path).endswith('.tar') else original(path, *a, **k)
    monkeypatch.setattr(Path, "open", guarded)
    digest = result["package"]["package_sha256"]
    response = client.get(f"/api/data-sync/bundle?generation_id={GEN}&package_id={digest}&limit=17", headers={"X-Test-Auth": "yes"})
    assert response.status_code == 200


def test_changed_epoch_rejects_old_package(tmp_path):
    client, _, retained, _ = setup_api(tmp_path)
    retained["collection_epoch_id"] = "different-epoch"
    response = client.get(f"/api/data-sync/bundles?generation_id={GEN}", headers={"X-Test-Auth": "yes"})
    assert response.status_code == 409
    assert response.json["error"] == "PACKAGE_STATE_GENERATION_MISMATCH"


def test_duplicate_package_index_is_rejected(tmp_path):
    client, _, _, output = setup_api(tmp_path)
    path = output / f"g-{GEN[:16]}" / "bundle-worker-state.json"
    state = json.loads(path.read_text())
    state["package_index"].append(dict(state["package_index"][0]))
    path.write_text(json.dumps(state))
    response = client.get(f"/api/data-sync/bundles?generation_id={GEN}", headers={"X-Test-Auth": "yes"})
    assert response.status_code == 409


@pytest.mark.parametrize("field", ["member_count", "payload_bytes"])
def test_descriptor_counts_must_match_index(tmp_path, field):
    client, result, _, output = setup_api(tmp_path)
    digest = result["package"]["package_sha256"]
    state_path = output / f"g-{GEN[:16]}" / "bundle-worker-state.json"
    state = json.loads(state_path.read_text())
    state["package_index"][0][field] += 1
    state_path.write_text(json.dumps(state))
    response = client.get(
        f"/api/data-sync/bundle?generation_id={GEN}&package_id={digest}&descriptor=1",
        headers={"X-Test-Auth": "yes"},
    )
    assert response.status_code == 409
    assert response.json["error"] == "PACKAGE_DESCRIPTOR_INDEX_MISMATCH"


def test_root_reparse_attribute_rejected_at_registration(tmp_path, monkeypatch):
    output = tmp_path / "out"
    output.mkdir()
    original = Path.lstat

    class ReparseStat:
        st_file_attributes = 0x400

    def marked(path):
        if path == output:
            return ReparseStat()
        return original(path)

    monkeypatch.setattr(Path, "lstat", marked)
    app = Flask(__name__)
    with pytest.raises(api.BundleReadError, match="PACKAGE_ROOT_LINK_REJECTED"):
        api.register_bundle_routes(
            app, authenticated=lambda: True, generation_lookup=lambda _g: None,
            output_root=output,
        )


def test_generation_directory_reparse_attribute_rejected(tmp_path, monkeypatch):
    client, _, _, output = setup_api(tmp_path)
    generation_dir = output / f"g-{GEN[:16]}"
    original = Path.lstat

    class ReparseStat:
        st_file_attributes = 0x400

    def marked(path):
        if path == generation_dir:
            return ReparseStat()
        return original(path)

    monkeypatch.setattr(Path, "lstat", marked)
    response = client.get(
        f"/api/data-sync/bundles?generation_id={GEN}",
        headers={"X-Test-Auth": "yes"},
    )
    assert response.status_code == 409
    assert response.json["error"] == "PACKAGE_PATH_LINK_REJECTED"


def test_chunk_growth_after_open_fails_generation_fence(tmp_path, monkeypatch):
    client, result, _, _ = setup_api(tmp_path)
    digest = result["package"]["package_sha256"]
    package = Path(result["package"]["package_path"])
    original = Path.open

    class GrowingFile:
        def __init__(self, path, *args, **kwargs):
            self.path = path
            self.handle = original(path, *args, **kwargs)
        def __enter__(self): return self
        def __exit__(self, *args): self.handle.close()
        def seek(self, *args): return self.handle.seek(*args)
        def fileno(self): return self.handle.fileno()
        def read(self, size=-1):
            payload = self.handle.read(size)
            with original(self.path, "ab") as writer:
                writer.write(b"growth")
                writer.flush()
                os.fsync(writer.fileno())
            return payload

    def growing_open(path, *args, **kwargs):
        if path == package and args and args[0] == "rb":
            return GrowingFile(path, *args, **kwargs)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", growing_open)
    response = client.get(
        f"/api/data-sync/bundle?generation_id={GEN}&package_id={digest}&limit=17",
        headers={"X-Test-Auth": "yes"},
    )
    assert response.status_code == 409
    assert response.json["error"] == "PACKAGE_GENERATION_CHANGED"


def test_oversized_metadata_fails_before_open(tmp_path, monkeypatch):
    path = tmp_path / "metadata"
    path.write_bytes(b"x" * (api.MAX_METADATA_BYTES + 1))
    monkeypatch.setattr(Path, "open", lambda *a, **k: pytest.fail("oversized metadata opened"))
    with pytest.raises(api.BundleReadError, match="PACKAGE_METADATA_LIMIT"):
        api._metadata(path)
