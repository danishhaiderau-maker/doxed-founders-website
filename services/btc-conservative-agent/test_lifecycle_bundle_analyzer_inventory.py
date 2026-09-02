import json
import hashlib
from pathlib import Path

from lifecycle_bundles import LifecycleKey, materialize_bundle, materialize_transfer_bundle
from research_v3_contract import canonical_json
from research.lifecycle_bundle_inventory import build_lifecycle_bundle_inventory


NOW = 20_000.0


def _completion():
    return {
        "schema": "lifecycle_bundle_completion_v1",
        "terminal": True,
        "entry_outcome": "NO_FILL",
        "entry_schedule_terminal": True,
        "position_closed_or_never_opened": True,
        "post_observation_complete": True,
        "terminal_ts": 10_000.0,
        "horizon_complete_ts": 18_000.0,
        "source_revision": "src",
        "deployed_revision": "dep",
        "tile_config_signature": "tile",
    }


def _row(key, record_id, *, completion=True):
    row = {
        **key.as_dict(), "ledger": "lifecycle", "record_id": record_id,
        "event_id": "trade-1", "observed_ts": 10_000.0,
        "source_revision": "src", "deployed_revision": "dep",
        "tile_config_signature": "tile",
        "bundle_completion": _completion() if completion else None,
    }
    if completion:
        completion_receipt = row["bundle_completion"]
        completion_receipt["completion_receipt_sha256"] = hashlib.sha256(
            canonical_json(completion_receipt).encode("utf-8")
        ).hexdigest()
        collected = {
            "schema": "lifecycle_evidence_collected_v1",
            "identity": key.as_dict(), "event_id": "trade-1",
            "provenance": {
                "source_revision": "src", "deployed_revision": "dep",
                "tile_config_signature": "tile",
            },
            "completion_receipt_sha256": completion_receipt["completion_receipt_sha256"],
            "qualification_eligible_at": 18_000.0,
            "evidence_collected_at": 20_000.0,
        }
        collected["evidence_collected_receipt_sha256"] = hashlib.sha256(
            canonical_json(collected).encode("utf-8")
        ).hexdigest()
        row["evidence_collection_receipt"] = collected
    return row


def _transfer_assessment():
    return {
        "ready": True,
        "qualification_blockers": ["POST_OBSERVATION_MISSING"],
        "receipt": {
            "schema": "lifecycle_bundle_transfer_ready_v1",
            "transfer_ready": True,
            "entry_outcome": "NO_FILL",
            "profitability_supported": False,
            "source_cleanup_authorized": False,
        },
    }


def _forbidden_keys(value):
    forbidden = {"pnl", "net_pnl", "ev", "expectancy", "win_rate", "winner"}
    found = set()
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(token in lowered for token in forbidden):
                found.add(lowered)
            found.update(_forbidden_keys(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_forbidden_keys(child))
    return found


def test_matching_qualification_and_transfer_are_counted_with_audit_isolation(tmp_path):
    key = LifecycleKey("epoch-1", "episode-1", "policy-1", "FIXED")
    materialize_bundle(tmp_path, key, [_row(key, "qualified")], now=NOW)
    materialize_transfer_bundle(
        tmp_path, key, [_row(key, "terminal", completion=False)],
        _transfer_assessment(),
    )
    report = build_lifecycle_bundle_inventory(tmp_path)
    assert report["complete"] is True
    assert report["inventory_scope"] == "MANIFEST_ONLY"
    assert report["complete_scope"] == "MANIFEST_INVENTORY"
    assert report["payload_verification_status"] == "UNKNOWN_NOT_SCANNED"
    assert report["payload_files_read"] == 0
    assert report["scan"]["payload_files_read"] == 0
    assert report["qualification"]["label"] == "manifest-verified qualification bundles"
    assert report["qualification"]["unique_lifecycle_count"] == 1
    assert report["transfer"]["unique_lifecycle_count"] == 1
    assert report["parity"]["intersection_count"] == 1
    assert report["parity"]["scope"] == "MANIFEST_INVENTORY"
    assert report["transfer"]["audit_only"] is True
    assert report["transfer"]["profitability_supported"] is False
    assert report["transfer"]["ranking_eligible"] is False
    assert report["transfer"]["source_cleanup_authorized"] is False
    assert not _forbidden_keys(report["transfer"])


def test_transfer_only_is_visible_but_never_promoted(tmp_path):
    key = LifecycleKey("epoch-1", "episode-1", "policy-1", "FIXED")
    materialize_transfer_bundle(
        tmp_path, key, [_row(key, "terminal", completion=False)],
        _transfer_assessment(),
    )
    report = build_lifecycle_bundle_inventory(tmp_path)
    assert report["qualification"]["unique_lifecycle_count"] == 0
    assert report["transfer"]["unique_lifecycle_count"] == 1
    assert report["parity"]["transfer_only_count"] == 1


def test_corrupt_transfer_manifest_is_rejected(tmp_path):
    key = LifecycleKey("epoch-1", "episode-1", "policy-1", "FIXED")
    result = materialize_transfer_bundle(
        tmp_path, key, [_row(key, "terminal", completion=False)],
        _transfer_assessment(),
    )
    manifest_path = Path(result["path"]) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["ranking_eligible"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report = build_lifecycle_bundle_inventory(tmp_path)
    assert report["transfer"]["unique_lifecycle_count"] == 0
    assert report["invalid_manifest_count"] >= 1


def test_scan_limit_fails_closed_and_atomic_destination_stays_contained(tmp_path):
    key = LifecycleKey("epoch-1", "episode-1", "policy-1", "FIXED")
    materialize_transfer_bundle(
        tmp_path, key, [_row(key, "terminal", completion=False)],
        _transfer_assessment(),
    )
    destination = tmp_path / "analyzer" / "lifecycle_bundle_inventory.json"
    report = build_lifecycle_bundle_inventory(
        tmp_path, destination, max_manifests=0,
    )
    assert report["complete"] is False
    assert report["scan"]["truncated"] is True
    assert destination.is_file()


def test_inventory_never_reads_huge_event_or_segment_payloads(tmp_path):
    key = LifecycleKey("epoch-1", "episode-1", "policy-1", "FIXED")
    result = materialize_transfer_bundle(
        tmp_path, key, [_row(key, "terminal", completion=False)],
        _transfer_assessment(),
    )
    bundle = Path(result["path"])
    # Sparse payloads make an accidental payload scan obvious without consuming
    # equivalent disk space. Manifest-only inventory must ignore both files.
    with (bundle / "events.jsonl").open("r+b") as handle:
        handle.truncate(512 * 1024 * 1024)
    segment = bundle / "market_segments" / "aa" / "huge.json"
    segment.parent.mkdir(parents=True)
    with segment.open("wb") as handle:
        handle.truncate(512 * 1024 * 1024)

    report = build_lifecycle_bundle_inventory(
        tmp_path, max_manifest_bytes=1024 * 1024, max_runtime_sec=1.0,
    )
    assert report["complete"] is True
    assert report["transfer"]["unique_lifecycle_count"] == 1
    assert report["scan"]["payload_files_read"] == 0
    assert report["payload_verification_status"] == "UNKNOWN_NOT_SCANNED"
    assert report["payload_files_read"] == 0
    assert report["scan"]["manifest_bytes_read"] < 1024 * 1024


def test_directory_enumeration_limit_fails_closed_without_eager_walk(tmp_path):
    base = tmp_path / "v3" / "lifecycle_transfer_bundles"
    for shard in ("aa", "bb", "cc"):
        (base / shard).mkdir(parents=True)
    report = build_lifecycle_bundle_inventory(tmp_path, max_directories=1)
    assert report["complete"] is False
    assert report["scan"]["blocker_counts"] == {"DIRECTORY_LIMIT_EXCEEDED": 1}
