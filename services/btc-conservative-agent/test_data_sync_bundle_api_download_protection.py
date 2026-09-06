import hashlib
import json
from pathlib import Path

import pytest

import data_sync_bundle_api as api
from data_sync_bundle_download_pins import DownloadProtection
from data_sync_bundle_worker import _singleton_lease, BundleWorkerError, _canonical
from test_data_sync_bundle_api import setup_api
from test_data_sync_bundle_worker import GEN

AUTH = {"X-Test-Auth": "yes"}


def test_legacy_index_descriptor_chunks_share_durable_session_unchanged_payload(tmp_path):
    client, result, _, output = setup_api(tmp_path)
    index = client.get(f"/api/data-sync/bundles?generation_id={GEN}", headers=AUTH)
    digest = result["package"]["package_sha256"]
    url = f"/api/data-sync/bundle?generation_id={GEN}&package_id={digest}"
    descriptor = client.get(url + "&descriptor=1", headers=AUTH)
    assert hashlib.sha256(_canonical(descriptor.json)).hexdigest() == index.json["packages"][0]["descriptor_sha256"]
    chunks = [client.get(url + f"&offset={offset}&limit=17", headers=AUTH) for offset in (0, 17)]
    expected = (output / f"g-{GEN[:16]}" / "packages" / (digest + ".tar")).read_bytes()
    assert b"".join(row.data for row in chunks) == expected[:34]
    assert all(row.status_code == 200 for row in [index, descriptor, *chunks])
    state = json.loads((output.parent / "transport-download-pins" / (GEN + ".json")).read_text())
    assert len(state["sessions"]) == 1 and state["fence"] is None
    # Reinstantiated owner reads the same pin, rather than forgetting downloads.
    owner = DownloadProtection(output.parent / "transport-download-pins", output / ".bundle-worker.lease")
    assert owner.retirement(GEN, fence_token="f" * 64)["active_sessions"] == 1


@pytest.mark.parametrize("kind", ["index", "descriptor", "chunk"])
def test_every_artifact_read_is_inside_worker_exclusion(tmp_path, monkeypatch, kind):
    client, result, _, output = setup_api(tmp_path)
    original = Path.open
    observed = []
    def guarded(path, *args, **kwargs):
        if output in path.parents and path.name != ".bundle-worker.lease":
            with pytest.raises(BundleWorkerError, match="BUNDLE_WORKER_LEASE_HELD"):
                with _singleton_lease(output / ".bundle-worker.lease"):
                    pytest.fail("artifact read outside exclusion")
            observed.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded)
    url = f"/api/data-sync/bundles?generation_id={GEN}"
    if kind != "index":
        url = f"/api/data-sync/bundle?generation_id={GEN}&package_id={result['package']['package_sha256']}"
        url += "&descriptor=1" if kind == "descriptor" else "&limit=17"
    assert client.get(url, headers=AUTH).status_code == 200
    assert observed


def test_contention_retry_and_durable_fence_deny_reads(tmp_path):
    client, _, _, output = setup_api(tmp_path)
    url = f"/api/data-sync/bundles?generation_id={GEN}"
    with _singleton_lease(output / ".bundle-worker.lease"):
        response = client.get(url, headers=AUTH)
    assert response.status_code == 503 and response.json["error"] == "BUNDLE_DOWNLOAD_BUSY"
    assert response.headers["Retry-After"] == "1"
    assert client.get(url, headers=AUTH).status_code == 200
    owner = DownloadProtection(output.parent / "transport-download-pins", output / ".bundle-worker.lease")
    owner.retirement(GEN, fence_token="f" * 64)
    response = client.get(url, headers=AUTH)
    assert response.status_code == 409 and response.json["error"] == "BUNDLE_DOWNLOAD_RETIRING"


def test_auth_and_authority_precede_all_metadata_mutation(tmp_path):
    client, _, retained, output = setup_api(tmp_path)
    url = f"/api/data-sync/bundles?generation_id={GEN}"
    assert client.get(url).status_code == 401
    retained["ack_eligible"] = False
    assert client.get(url, headers=AUTH).status_code == 409
    assert not (output.parent / "transport-download-pins").exists()


def test_existing_metadata_directory_supports_concurrent_provisioning(tmp_path, monkeypatch):
    client, _, _, output = setup_api(tmp_path)
    root = output.parent / "transport-download-pins"
    original = Path.mkdir
    def racing_mkdir(path, *args, **kwargs):
        if path == root and not path.exists():
            original(path)  # Simulate another authorized request creating first.
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", racing_mkdir)
    assert client.get(f"/api/data-sync/bundles?generation_id={GEN}", headers=AUTH).status_code == 200


def test_metadata_reparse_rejected_before_any_pin_write(tmp_path, monkeypatch):
    client, _, _, output = setup_api(tmp_path)
    root = output.parent / "transport-download-pins"
    root.mkdir()
    original = Path.lstat
    class Marked:
        def __init__(self, value): self.value = value
        st_file_attributes = 0x400
        def __getattr__(self, name): return getattr(self.value, name)
    monkeypatch.setattr(Path, "lstat", lambda path: Marked(original(path)) if path == root else original(path))
    response = client.get(f"/api/data-sync/bundles?generation_id={GEN}", headers=AUTH)
    assert response.status_code == 409
    assert list(root.iterdir()) == []


def test_invalid_explicit_session_does_not_create_metadata(tmp_path):
    client, _, _, output = setup_api(tmp_path)
    response = client.get(f"/api/data-sync/bundles?generation_id={GEN}",
                          headers={**AUTH, "X-Bundle-Download-Session": "private-invalid"})
    assert response.status_code == 400 and response.json["error"] == "INVALID_DOWNLOAD_SESSION"
    assert not (output.parent / "transport-download-pins").exists()


def test_retention_revoked_after_pin_prevents_artifact_read(tmp_path, monkeypatch):
    client, _, retained, _ = setup_api(tmp_path)
    original = DownloadProtection.pin
    def pin_then_revoke(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        retained["ack_eligible"] = False
        return result
    monkeypatch.setattr(DownloadProtection, "pin", pin_then_revoke)
    monkeypatch.setattr(api, "_metadata", lambda *args: pytest.fail("read after authority revoked"))
    response = client.get(f"/api/data-sync/bundles?generation_id={GEN}", headers=AUTH)
    assert response.status_code == 409 and response.json["error"] == "GENERATION_NOT_RETAINED_OR_ACK_ELIGIBLE"


def test_malformed_pin_state_fails_closed_without_artifact_read(tmp_path, monkeypatch):
    client, _, _, output = setup_api(tmp_path)
    root = output.parent / "transport-download-pins"
    root.mkdir()
    (root / (GEN + ".json")).write_text("{}")
    monkeypatch.setattr(api, "_metadata", lambda *args: pytest.fail("read with malformed protection"))
    response = client.get(f"/api/data-sync/bundles?generation_id={GEN}", headers=AUTH)
    assert response.status_code == 409 and response.json["error"] == "PACKAGE_READ_INVALID"


def test_explicit_sessions_are_distinct_and_resume_without_slot_growth(tmp_path):
    client, _, _, output = setup_api(tmp_path)
    url = f"/api/data-sync/bundles?generation_id={GEN}"
    for session in ("c" * 64, "d" * 64, "c" * 64):
        assert client.get(url, headers={**AUTH, "X-Bundle-Download-Session": session}).status_code == 200
    state = json.loads((output.parent / "transport-download-pins" / (GEN + ".json")).read_text())
    assert set(state["sessions"]) == {"c" * 64, "d" * 64}


def test_existing_unmodified_clients_retry_busy_index_descriptor_and_chunk(tmp_path):
    import importlib.util
    script = Path(__file__).resolve().parents[2] / "scripts" / "fly-sync-bundle-client.py"
    spec = importlib.util.spec_from_file_location("existing_bundle_adapter_retry_contract", script)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    client, result, retained, output = setup_api(tmp_path)
    manifest = {**retained, "schema": "fly_runtime_incremental_sync_v1",
                "inventory_status": "CURRENT", "inventory_authoritative": True,
                "inventory_ack_eligible": True, "inventory_generation_id": GEN,
                "files": result["package"]["members"]}
    attempts = {}
    delays, receipts = [], []
    def fetch(url, *, timeout):
        kind = "index" if "/bundles?" in url else "descriptor" if "descriptor=1" in url else "chunk"
        attempts[kind] = attempts.get(kind, 0) + 1
        if attempts[kind] == 1:
            with _singleton_lease(output / ".bundle-worker.lease"):
                response = client.get(url, headers=AUTH)
            assert response.status_code == 503 and response.headers["Retry-After"] == "1"
        else:
            response = client.get(url, headers=AUTH)
        return response.status_code, dict(response.headers), response.data
    adapter.run({"source_url": "https://doxed-btc-bot.fly.dev", "manifest": manifest,
                 "staging_root": str(tmp_path / "staging")}, emit=receipts.append,
                fetch=fetch, sleep=delays.append, clock=lambda: 0)
    assert attempts == {"index": 2, "descriptor": 2, "chunk": 2}
    assert receipts[-1]["status"] == "COMPLETE" and receipts[-1]["ack_sent"] is False
    assert any(row["status"] == "PACKAGE_VERIFIED" for row in receipts)
    assert delays[:3] == [5, 0.25, 0.25]
