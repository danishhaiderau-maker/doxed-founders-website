import json
from datetime import datetime, timezone

import pytest

from test_data_sync_bundle_api import setup_api, GEN


@pytest.mark.parametrize("mode", ["valid", "missing", "malformed", "mismatch", "stale", "cursor", "complete", "secret"])
def test_index_preserves_package_compatibility_and_exposes_only_bound_diagnostic(tmp_path, mode):
    client, _, _, root = setup_api(tmp_path)
    directory = root / f"g-{GEN[:16]}"
    statepath = directory / "bundle-worker-state.json"
    state = json.loads(statepath.read_text())
    state["completed"] = mode == "complete"
    if mode == "cursor":
        state["cursor"] = {"page_index":21, "page_row_index":128, "index_offset":4006}
    statepath.write_text(json.dumps(state))
    generation = state["generation"]
    value = {"schema":"fly_transport_bundle_coordinator_status_v1",
        "identity":{"generation_id":GEN, **{k:generation[k] for k in (
            "source_git_rev","collection_epoch_id","tile_registry_signature","page_index_sha256")}},
        "status":"FAILED", "terminal":True, "cursor":state["cursor"],
        "authority":"DIAGNOSTIC_ONLY_NO_ACK_OR_LIVENESS_AUTHORITY",
        "updated_at":datetime.now(timezone.utc).isoformat(),
        "error":"BUNDLE_CIRCUIT_OPEN", "last_error":"BUNDLE_SLICE_TIMEOUT"}
    if mode == "mismatch": value["identity"]["source_git_rev"] = "wrong"
    if mode == "stale": value["updated_at"] = "2000-01-01T00:00:00+00:00"
    if mode == "cursor": value["cursor"] = {"page_index":21, "page_row_index":0, "index_offset":4006}
    if mode == "secret": value["last_error"] = "secret /private/path"
    if mode != "missing":
        (directory / "bundle-coordinator-status.json").write_text("invalid" if mode == "malformed" else json.dumps(value))
    response = client.get(f"/api/data-sync/bundles?generation_id={GEN}", headers={"X-Test-Auth":"yes"})
    assert response.status_code == 200
    body = response.json
    assert body["status"] == ("COMPLETE" if mode == "complete" else "BUILDING")
    assert body["ack_authority"] == "ORIGINAL_MANIFEST_ROWS_ONLY" and len(body["packages"]) == 1
    expected = "FAILED" if mode in ("valid", "secret", "stale", "cursor") else "NOT_REQUIRED_PACKAGE_COMPLETE" if mode == "complete" else "UNAVAILABLE"
    assert body["producer"]["status"] == expected
    if mode in ("stale", "cursor"):
        assert body["producer"]["checkpoint_relation"] == "CHECKPOINT_CHANGED_OR_NEWER"
        assert body["producer"]["scope"] == "LAST_ATTEMPT_NOT_CURRENT_LIVENESS"
        assert body["producer"]["last_error"] == "BUNDLE_SLICE_TIMEOUT"
    assert "secret" not in response.get_data(as_text=True)
