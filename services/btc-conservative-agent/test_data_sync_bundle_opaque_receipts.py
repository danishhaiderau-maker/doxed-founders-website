import copy
import hashlib
import json
from pathlib import Path

import pytest
from flask import Flask

import data_sync_bundle_transport as transport
import data_sync_bundle_client as client
from data_sync_bundle_worker import run_bundle_worker
from data_sync_bundle_api import register_bundle_routes
from test_data_sync_bundle_worker import _fixture, _row, GEN


def fixture(tmp_path):
    source = tmp_path / "source"
    rows = []
    for i, state in enumerate(("PREPARED", "DEFERRED", "COMMITTED"), start=1):
        path = f"v3/receipts/emergency_record_idempotency_v1/lifecycle/{i:064x}.json"
        payload = json.dumps({"schema": "emergency_record_idempotency_v1", "state": state,
                              "row_payload_utf8": "original recovery evidence"}).encode()
        rows.append(_row(source, path, payload))
    metadata = _fixture(tmp_path, rows)
    output = tmp_path / "output"
    return source, rows, metadata, output


def test_worker_api_client_batches_all_states_and_reuses_without_semantic_promotion(tmp_path):
    source, rows, metadata, output = fixture(tmp_path)
    result = run_bundle_worker(metadata, source, output)
    assert result["status"] == "COMPLETE" and result["package"]["member_count"] == 3
    assert result["skipped_counts"] == {}
    app = Flask(__name__)
    register_bundle_routes(app, authenticated=lambda: True, generation_lookup=lambda _: metadata, output_root=output)
    http = app.test_client()
    index = http.get(f"/api/data-sync/bundles?generation_id={GEN}").json
    calls = []
    def fetch(url, *, timeout):
        calls.append(url)
        response = http.get(url)
        return response.status_code, dict(response.headers), response.data
    generation = {**index["generation"], "ack_eligible": True}
    staged = client.fetch_verified_package(index["packages"][0], generation, rows, tmp_path / "stage", fetch)
    assert len(calls) == 2  # One descriptor + one chunk, not per-receipt requests.
    assert staged["ack_authority"] == "ORIGINAL_MANIFEST_ROWS_ONLY"
    assert {row["path"] for row in staged["members"]} == {row["path"] for row in rows}
    for i, member in enumerate(staged["members"]):
        assert Path(member["staged_path"]).read_bytes() == (source / member["path"]).read_bytes()
        assert {key: member[key] for key in client.ROW_FIELDS} == rows[i]
        if i < 2:
            assert member["evidence_class"] == "OPAQUE_UNCOMMITTED_IDEMPOTENCY_RECEIPT"
            assert member["receipt_state"] == ("PREPARED", "DEFERRED")[i]
            assert member["semantic_completion"] is False
        else:
            assert not transport._RECEIPT_EVIDENCE_FIELDS.intersection(member)
    calls.clear()
    reused = client.fetch_verified_package(index["packages"][0], generation, rows,
        tmp_path / "unused", fetch, verified_local_root=source)
    assert reused["reused_local"] is True and len(calls) == 1
    assert not (tmp_path / "unused").exists()
    assert reused["members"][1]["receipt_state"] == "DEFERRED"


@pytest.mark.parametrize("tamper", ["missing", "committed", "completion", "integer_false"])
def test_forged_or_missing_classification_rejected_on_extraction_and_reuse(tmp_path, tamper):
    source, _, metadata, output = fixture(tmp_path)
    result = run_bundle_worker(metadata, source, output)
    descriptor = copy.deepcopy(result["package"])
    member = descriptor["members"][0]
    if tamper == "missing":
        for key in transport._RECEIPT_EVIDENCE_FIELDS: member.pop(key)
    elif tamper == "committed": member["receipt_state"] = "COMMITTED"
    elif tamper == "completion": member["semantic_completion"] = True
    else: member["semantic_completion"] = 0
    descriptor["member_tree_sha256"] = hashlib.sha256(transport._canonical_json(descriptor["members"])).hexdigest()
    with pytest.raises(transport.BundleTransportError, match="classification mismatch"):
        transport.extract_verified_bundle(descriptor["package_path"], descriptor, GEN, tmp_path / "stage")
    with pytest.raises(transport.BundleTransportError, match="classification mismatch"):
        client._verified_local_members(source, {m["path"]: m for m in descriptor["members"]}, lambda: None)


def test_source_stat_fence_still_rejects_changed_deferred_record(tmp_path):
    source, rows, metadata, output = fixture(tmp_path)
    (source / rows[1]["path"]).write_bytes(b"changed after inventory")
    with pytest.raises(transport.BundleTransportError, match="source generation differs"):
        run_bundle_worker(metadata, source, output)


@pytest.mark.parametrize("payload", [b"not JSON", b'{"schema":"wrong","state":"DEFERRED"}',
                                     b'{"schema":"emergency_record_idempotency_v1","state":"UNKNOWN"}'])
def test_opaque_support_does_not_admit_invalid_schema_or_unknown_state(tmp_path, payload):
    source = tmp_path / "source"
    row = _row(source, "v3/receipts/emergency_record_idempotency_v1/lifecycle/" + "1" * 64 + ".json", payload)
    metadata = _fixture(tmp_path, [row])
    with pytest.raises(transport.BundleTransportError):
        run_bundle_worker(metadata, source, tmp_path / "out")
