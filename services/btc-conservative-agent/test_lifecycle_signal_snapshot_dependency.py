"""Snapshot dependencies survive transfer without becoming market segments."""
import copy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

import lifecycle_bundles as bundles
from collector_signal_snapshot import freeze_signal_snapshot
from test_lifecycle_bundle_materialization import row, transfer_assessment

NOW = 20_000.0
KEY = bundles.LifecycleKey("epoch-1", "episode-1", "policy-a", "FIXED")


def source_row(root):
    record = {"event_id": "signal-event", "epoch_id": KEY.collection_epoch_id,
              "envelope": {"signal_ts": 900.0}, "pre_signal_context": {"sample": "frozen"}}
    ref = freeze_signal_snapshot(record, data_dir=root, captured_at=1000.0)
    event = row(KEY, "life-1", NOW, event_id=record["event_id"], signal_ts=900.0,
                research_signal_snapshot_ref=ref)
    return event, ref


def publish(root, rows, mode):
    if mode == "qualification":
        return bundles.materialize_bundle(root, KEY, rows, now=NOW)
    return bundles.materialize_transfer_bundle(root, KEY, rows, transfer_assessment(NOW))


def rewrite_manifest(bundle, mutate):
    path = Path(bundle) / "manifest.json"
    manifest = json.loads(path.read_text())
    mutate(manifest)
    manifest.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = hashlib.sha256(bundles.canonical_json(manifest).encode()).hexdigest()
    path.write_text(bundles.canonical_json(manifest) + "\n", encoding="utf-8")


@pytest.mark.parametrize("mode", ["qualification", "transfer"])
def test_snapshot_copied_as_distinct_pinned_role_and_verifies_without_source(tmp_path, mode):
    event, ref = source_row(tmp_path)
    result = publish(tmp_path, [event], mode)
    assert result["written"] is True
    manifest = result["manifest"]
    snapshots = [r for r in manifest["files"] if "SIGNAL_SNAPSHOT" in r["role"]]
    assert len(snapshots) == 1
    receipt = snapshots[0]
    assert receipt["role"] == ("SIGNAL_SNAPSHOT" if mode == "qualification" else "TRANSFER_SIGNAL_SNAPSHOT")
    assert receipt["source_relative_path"] == ref["relative_path"]
    assert receipt["sha256"] == ref["sha256"] and receipt["size"] == ref["bytes"]
    assert receipt["row_count"] == 1 and receipt["snapshot_identity"] == ref["identity"]
    assert not any("MARKET_SEGMENT" in r["role"] for r in manifest["files"])
    assert Path(bundles._io_path(Path(result["path"]) / receipt["path"])).read_bytes() == (tmp_path / ref["relative_path"]).read_bytes()
    (tmp_path / ref["relative_path"]).unlink()  # Temporary fixture only: bundle is independently readable.
    verified = bundles.verify_bundle(result["path"])
    assert verified["passed"] is True
    assert verified["manifest"]["source_cleanup_authorized"] is False
    if mode == "transfer":
        assert manifest["qualification_ready"] is False
        assert manifest["profitability_supported"] is False
        assert manifest["ranking_eligible"] is False


@pytest.mark.parametrize("mode", ["qualification", "transfer"])
def test_legacy_no_reference_keeps_original_content_id(tmp_path, mode):
    event = row(KEY, "life-1", NOW)
    result = publish(tmp_path, [event], mode)
    manifest = result["manifest"]
    evidence = manifest["completion"] if mode == "qualification" else manifest["transfer_receipt"]
    expected = bundles._bundle_content_id(KEY, [event], evidence, [], prefix="lifecycle-" if mode == "qualification" else "transfer-")
    assert result["bundle_id"] == expected
    assert len(manifest["files"]) == 1
    assert bundles.verify_bundle(result["path"])["passed"]


@pytest.mark.parametrize("mode", ["qualification", "transfer"])
@pytest.mark.parametrize("fault", ["null", "empty", "schema", "bool_bytes", "size", "sha", "path", "event", "epoch", "signal", "missing_row_signal"])
def test_malformed_snapshot_reference_cannot_be_silently_skipped(tmp_path, mode, fault):
    event, ref = source_row(tmp_path)
    if fault == "null": event["research_signal_snapshot_ref"] = None
    if fault == "empty": event["research_signal_snapshot_ref"] = {}
    if fault == "schema": ref["schema"] = "market_segment_ref_v3"
    if fault == "bool_bytes": ref["bytes"] = True
    if fault == "size": ref["bytes"] += 1
    if fault == "sha": ref["sha256"] = ref["sha256"].upper()
    if fault == "path": ref["relative_path"] = "../outside.json"
    if fault == "event": ref["identity"]["event_id"] = "other-event"
    if fault == "epoch": ref["identity"]["epoch_id"] = "other-epoch"
    if fault == "signal": ref["identity"]["signal_ts"] += 1
    if fault == "missing_row_signal": event.pop("signal_ts")
    with pytest.raises(ValueError, match="SIGNAL_SNAPSHOT"):
        publish(tmp_path, [event], mode)
    assert not list(tmp_path.glob("v3/lifecycle*/*/*/manifest.json"))


@pytest.mark.parametrize("mode", ["qualification", "transfer"])
@pytest.mark.parametrize("fault", ["missing", "tamper", "wrong_schema", "wrong_identity", "wrong_fields", "capture_bool", "availability"])
def test_missing_or_invalid_snapshot_content_blocks_publication(tmp_path, mode, fault):
    event, ref = source_row(tmp_path)
    path = tmp_path / ref["relative_path"]
    if fault == "missing": path.unlink()
    elif fault == "tamper": path.write_bytes(path.read_bytes() + b" ")
    else:
        payload = json.loads(path.read_bytes())
        if fault == "wrong_schema": payload["schema"] = "market_segment_v3"
        if fault == "wrong_identity": payload["identity"]["event_id"] = "other"
        if fault == "wrong_fields": payload["evidence"].pop("pre_signal_context")
        if fault == "capture_bool": payload["captured_at"] = True
        if fault == "availability": payload["availability_at_signal_verified"] = True
        raw = json.dumps(payload, sort_keys=True).encode()
        ref["sha256"] = hashlib.sha256(raw).hexdigest()
        ref["bytes"] = len(raw)
        ref["relative_path"] = f"v3/signal_snapshots_v1/{ref['sha256']}.json"
        (tmp_path / ref["relative_path"]).write_bytes(raw)
    with pytest.raises((ValueError, FileNotFoundError)):
        publish(tmp_path, [event], mode)


@pytest.mark.parametrize("mode", ["qualification", "transfer"])
@pytest.mark.parametrize("fault", ["delete_member", "remove_receipt", "source_path", "role", "size_bool", "snapshot_identity", "duplicate_receipt", "extra_file", "bytes", "timestamp"])
def test_bundle_verifier_rejects_dependency_tampering_even_with_rehashed_manifest(tmp_path, mode, fault):
    event, ref = source_row(tmp_path)
    result = publish(tmp_path, [event], mode)
    bundle = Path(result["path"])
    snapshot = next(r for r in result["manifest"]["files"] if "SIGNAL_SNAPSHOT" in r["role"])
    member = Path(bundles._io_path(bundle / snapshot["path"]))
    if fault == "delete_member": member.unlink()
    elif fault == "extra_file": (member.parent / "extra.json").write_text("{}")
    elif fault == "bytes": member.write_bytes(member.read_bytes() + b" ")
    else:
        def mutate(manifest):
            target = next(r for r in manifest["files"] if "SIGNAL_SNAPSHOT" in r["role"])
            if fault == "remove_receipt": manifest["files"].remove(target)
            if fault == "source_path": target["source_relative_path"] = "v3/other.json"
            if fault == "role": target["role"] = "MARKET_SEGMENT"
            if fault == "size_bool": target["size"] = True
            if fault == "snapshot_identity": target["snapshot_identity"]["event_id"] = "other"
            if fault == "duplicate_receipt": manifest["files"].append(copy.deepcopy(target))
            if fault == "timestamp": target["first_timestamp"] = target["last_timestamp"]
        rewrite_manifest(bundle, mutate)
    assert bundles.verify_bundle(bundle)["passed"] is False


@pytest.mark.parametrize("mode", ["qualification", "transfer"])
def test_repeated_reference_is_one_dependency(tmp_path, mode):
    event, ref = source_row(tmp_path)
    second = copy.deepcopy(event)
    second["record_id"] = "later"
    second.pop("bundle_completion")
    second.pop("evidence_collection_receipt")
    result = publish(tmp_path, [event, second], mode)
    assert len([r for r in result["manifest"]["files"] if "SIGNAL_SNAPSHOT" in r["role"]]) == 1


@pytest.mark.parametrize("mode", ["qualification", "transfer"])
def test_source_mutation_at_copy_is_checked_against_original_pin(tmp_path, mode, monkeypatch):
    event, ref = source_row(tmp_path)
    original = shutil.copyfile
    def changing_copy(source, destination, *args, **kwargs):
        if "signal_snapshots_v1" in str(source):
            with open(source, "ab") as handle: handle.write(b" ")
        return original(source, destination, *args, **kwargs)
    monkeypatch.setattr(bundles.shutil, "copyfile", changing_copy)
    with pytest.raises(ValueError, match="SIGNAL_SNAPSHOT_HASH_OR_SIZE_MISMATCH"):
        publish(tmp_path, [event], mode)


def test_snapshot_material_changes_content_id(tmp_path):
    first, _ = source_row(tmp_path)
    first_result = publish(tmp_path, [first], "qualification")
    second_root = tmp_path / "second"
    second, ref = source_row(second_root)
    record = {"event_id": "signal-event", "epoch_id": KEY.collection_epoch_id,
              "envelope": {"signal_ts": 900.0}, "pre_signal_context": {"sample": "different"}}
    second["research_signal_snapshot_ref"] = freeze_signal_snapshot(record, data_dir=second_root, captured_at=1000.0)
    second_result = publish(second_root, [second], "qualification")
    assert first_result["bundle_id"] != second_result["bundle_id"]


@pytest.mark.parametrize("mode", ["qualification", "transfer"])
@pytest.mark.parametrize("fault", ["duplicate_json", "nonfinite_evidence", "exponent_overflow", "huge_timestamp", "capture_before_signal"])
def test_hash_valid_payload_cannot_relax_snapshot_writer_contract(tmp_path, mode, fault):
    event, ref = source_row(tmp_path)
    raw = (tmp_path / ref["relative_path"]).read_bytes()
    if fault == "duplicate_json":
        raw = raw.replace(b'"schema":', b'"schema":"other","schema":', 1)
    elif fault == "nonfinite_evidence":
        raw = raw.replace(b'"atr14_pct":null', b'"atr14_pct":NaN', 1)
    elif fault == "exponent_overflow":
        raw = raw.replace(b'"atr14_pct":null', b'"atr14_pct":1e999', 1)
    else:
        payload = json.loads(raw)
        payload["captured_at"] = 10 ** 400 if fault == "huge_timestamp" else 899.0
        raw = json.dumps(payload).encode()
    ref["sha256"] = hashlib.sha256(raw).hexdigest()
    ref["bytes"] = len(raw)
    ref["relative_path"] = f"v3/signal_snapshots_v1/{ref['sha256']}.json"
    (tmp_path / ref["relative_path"]).write_bytes(raw)
    with pytest.raises(ValueError, match="SIGNAL_SNAPSHOT"):
        publish(tmp_path, [event], mode)


@pytest.mark.parametrize("mode", ["qualification", "transfer"])
def test_two_different_snapshots_for_same_event_are_not_merged(tmp_path, mode):
    event, _ = source_row(tmp_path)
    record = {"event_id": "signal-event", "epoch_id": KEY.collection_epoch_id,
              "envelope": {"signal_ts": 900.0}, "pre_signal_context": {"sample": "different"}}
    other = copy.deepcopy(event)
    other["research_signal_snapshot_ref"] = freeze_signal_snapshot(record, data_dir=tmp_path, captured_at=1000.0)
    other["record_id"] = "later"
    other.pop("bundle_completion")
    other.pop("evidence_collection_receipt")
    with pytest.raises(ValueError, match="EVENT_REFERENCE_CONFLICT"):
        publish(tmp_path, [event, other], mode)


@pytest.mark.parametrize("mode", ["qualification", "transfer"])
def test_linked_snapshot_source_is_refused_before_copy(tmp_path, mode, monkeypatch):
    event, ref = source_row(tmp_path)
    source = (tmp_path / ref["relative_path"]).absolute()
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path.absolute() == source or original(path))
    with pytest.raises(ValueError, match="SIGNAL_SNAPSHOT_LINK_REFUSED"):
        publish(tmp_path, [event], mode)
