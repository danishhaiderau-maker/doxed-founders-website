import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

from research.canonical_data_store import (
    CanonicalStoreError,
    append_manifest,
    archive_before_cleanup,
    assert_store_root,
    contained_path,
    default_store_root,
    initialize_store,
    parity_status,
    publish_parity_status,
    record_analyzer_completion,
    require_analyzer_dataset,
    validate_manifest_chain,
)


def _fields(**updates):
    base = {
        "dataset_epoch": "epoch-1",
        "source_revision": "a" * 40,
        "deployed_revision": "d" * 40,
        "tile_config_signature": "b" * 64,
        "collection_started_at": "2026-08-29T00:00:00Z",
        "collection_observed_at": "2026-08-29T00:03:00Z",
        "row_count": 12,
        "opportunity_count": 2,
        "dataset_checksum": "c" * 64,
        "analyzer_status": "PENDING",
        "analyzer_completed_at": None,
        "analyzer_schema_version": "v62",
    }
    base.update(updates)
    return base


def _load_migration(project):
    migration_path = Path(__file__).resolve().parents[2] / "scripts" / "migrate_canonical_research_store.py"
    spec = importlib.util.spec_from_file_location("canonical_migration_test", migration_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.REPO_ROOT = project
    return module


def _fixture_io_path(path):
    text = str(path)
    if os.name == "nt" and not text.startswith("\\\\?\\"):
        text = "\\\\?\\" + text
    return Path(text)


def _remove_terminal_receipt_fixture(receipt_path):
    _fixture_io_path(receipt_path).unlink(missing_ok=True)
    for directory in (receipt_path.parent, receipt_path.parent.parent):
        try:
            directory.rmdir()
        except OSError:
            pass


def _terminal_membership_fixture(root, *, revision="a" * 40, epoch="epoch-current", registry="b" * 64):
    """Write an independently constructed terminal membership fixture."""
    root.mkdir(parents=True, exist_ok=True)
    evidence = root / "evidence.jsonl"
    evidence.write_bytes(b'{"event":"kept"}\n')
    session = root / "research_session.json"
    session.write_text(
        json.dumps({"collector_v22_epoch_id": epoch, "started_at": "2026-08-29T00:00:00Z"}) + "\n",
        encoding="utf-8",
    )
    members = []
    for relative in ("evidence.jsonl", "research_session.json"):
        payload = (root / relative).read_bytes()
        members.append({
            "relative_path": relative,
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
    assert [row["relative_path"] for row in members] == sorted(row["relative_path"] for row in members)
    total_bytes = sum(row["size_bytes"] for row in members)
    local_rows = "".join(
        f"{len(row['relative_path'].encode('utf-8'))}:{row['relative_path']}:"
        f"{row['size_bytes']}:{row['sha256']}\n"
        for row in members
    )
    local_digest = hashlib.sha256(local_rows.encode("utf-8")).hexdigest()
    page_sha = "f" * 64
    page_rows = f"0:{page_sha}:{len(members)}:{total_bytes}\n"
    page_digest = hashlib.sha256(page_rows.encode("utf-8")).hexdigest()
    generation = "1" * 64
    ack_session = "2" * 32
    receipt = {
        "schema": "fly_terminal_transfer_membership_receipt_v1",
        "receipt_written_at": "2026-08-29T00:03:00Z",
        "inventory_generation_id": generation,
        "inventory_sha256": generation,
        "inventory_generated_at": "2026-08-29T00:02:00Z",
        "source_git_rev": revision,
        "collection_epoch_id": epoch,
        "collection_epoch_field": "collection_epoch_id",
        "tile_registry_signature": registry,
        "manifest_file_count": len(members),
        "manifest_total_bytes": total_bytes,
        "remote_final_ack": {
            "ok": True,
            "outcome": "FINALIZE_VALIDATED",
            "operation": "FINALIZE",
            "inventory_status": "VALIDATED",
            "ack_session_id": ack_session,
            "expected_count": len(members),
            "accepted_count": len(members),
            "rejected_count": 0,
            "manifest_pages_complete": True,
        },
        "post_ack_identity_fence": "PASSED",
        "manifest_pages": {
            "descriptor_schema": "fly_manifest_page_descriptor_v1",
            "canonicalization": "PAGE_INDEX_PAGE_SHA256_FILE_COUNT_TOTAL_BYTES_UTF8_LF_V1",
            "sorted_page_digest_sha256": page_digest,
            "descriptors": [{
                "page_index": 0,
                "page_sha256": page_sha,
                "file_count": len(members),
                "total_bytes": total_bytes,
            }],
        },
        "content_coverage": {
            "remote_manifest_page_descriptors": "COMPLETE_IMMUTABLE_PAGE_METADATA",
            "remote_per_file_content_sha256": "UNAVAILABLE_NOT_DECLARED_BY_MANIFEST",
            "local_content_coverage_complete": True,
            "local_full_file_sha256": {
                "status": "COMPLETE_FRESH_RECOMPUTED",
                "file_count": len(members),
                "total_bytes": total_bytes,
                "canonicalization": "UTF8_PATH_BYTE_LENGTH_RELATIVE_PATH_SIZE_BYTES_SHA256_UTF8_LF_V1",
                "sorted_file_digest_sha256": local_digest,
                "files": members,
            },
            "promotion_content_hash_status": "LOCAL_COMPLETE_FRESH_RECOMPUTED",
            "promotion_consumer_must_verify_local_content_digest": True,
        },
    }
    receipt_path = (
        root / "receipts" / "terminal-transfer-membership"
        / f"terminal-transfer-membership-{generation}-{ack_session}.json"
    )
    _fixture_io_path(receipt_path.parent).mkdir(parents=True)
    _fixture_io_path(receipt_path).write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
    heartbeat = {
        "ok": True,
        "inProgress": False,
        "phase": "complete",
        "revisionParity": "MATCH",
        "completionAuthority": "REMOTE_ACK_FINALIZED",
        "ackPending": False,
        "sourceRevision": revision,
        "mirroredSourceRevision": revision,
        "deployedRevision": "d" * 40,
        "inventoryGenerationId": generation,
        "inventorySha256": generation,
        "inventoryGeneratedAt": receipt["inventory_generated_at"],
        "collectionEpochId": epoch,
        "tileRegistrySignature": registry,
        "fileIndex": len(members),
        "fileCount": len(members),
        "syncedAt": "2026-08-29T00:03:00Z",
        "ackAccepted": True,
        "ackFinalized": True,
        "ackCoverageComplete": True,
        "ackManifestPagesComplete": True,
        "ackAcceptedCount": len(members),
        "ackExpectedCount": len(members),
        "ackRejectedCount": 0,
        "ackInventoryFileCount": len(members),
        "ackOperation": "FINALIZE",
        "ackInventoryStatus": "VALIDATED",
        "ackSessionId": ack_session,
    }
    heartbeat_path = root / ".fly-data-sync-loop.heartbeat.json"
    heartbeat_path.write_text(json.dumps(heartbeat), encoding="utf-8")
    return receipt_path, heartbeat_path, receipt, heartbeat


def test_store_root_and_containment_fail_closed(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    root = default_store_root(project)
    assert assert_store_root(root, project) == root.resolve()
    with pytest.raises(CanonicalStoreError, match="OUTSIDE_PROJECT"):
        assert_store_root(tmp_path / "canonical-research-data", project)
    with pytest.raises(CanonicalStoreError, match="PATH_OUTSIDE"):
        contained_path(root, tmp_path / "escape")
    with pytest.raises(CanonicalStoreError, match="ROOT_OPERATION_FORBIDDEN"):
        contained_path(root, root)


def test_manifest_is_append_first_hash_chained_and_current_is_exact(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    root = initialize_store(default_store_root(project), project)
    first = append_manifest(root, _fields())
    second = append_manifest(root, _fields(row_count=18, opportunity_count=3))
    rows = validate_manifest_chain(root)
    assert [row["entry_hash"] for row in rows] == [first["entry_hash"], second["entry_hash"]]
    assert second["previous_entry_hash"] == first["entry_hash"]
    current = json.loads((root / "canonical_dataset_current.json").read_text())
    assert current == second


def test_manifest_tamper_is_detected(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    root = initialize_store(default_store_root(project), project)
    append_manifest(root, _fields())
    journal = root / "canonical_dataset_manifest.jsonl"
    row = json.loads(journal.read_text())
    row["row_count"] = 999
    journal.write_text(json.dumps(row) + "\n")
    with pytest.raises(CanonicalStoreError, match="MANIFEST_CHAIN_INVALID"):
        validate_manifest_chain(root)


def test_append_refuses_a_tampered_existing_chain(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    root = initialize_store(default_store_root(project), project)
    append_manifest(root, _fields())
    journal = root / "canonical_dataset_manifest.jsonl"
    journal.write_text(journal.read_text().replace('"row_count":12', '"row_count":99'))
    with pytest.raises(CanonicalStoreError, match="MANIFEST_CHAIN_INVALID"):
        append_manifest(root, _fields(row_count=13))


def test_analyzer_completion_appends_report_hash_and_status(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    root = initialize_store(default_store_root(project), project)
    pending = append_manifest(root, _fields())
    report = root / "analyzer" / "report_manifest.json"
    report.parent.mkdir()
    report.write_text('{"generated_at":"2026-08-29T00:10:00Z"}\n')
    completed = record_analyzer_completion(
        root,
        report_manifest_path=report,
        analyzer_schema_version="v62",
        completed_at="2026-08-29T00:10:00Z",
    )
    assert completed["previous_entry_hash"] == pending["entry_hash"]
    assert completed["analyzer_status"] == "COMPLETE"
    assert completed["analyzer_completed_at"] == "2026-08-29T00:10:00Z"
    assert completed["analyzer_report_manifest_relative"] == "analyzer/report_manifest.json"
    assert completed["analyzer_report_manifest_sha256"] == hashlib.sha256(report.read_bytes()).hexdigest()


def test_parity_and_analyzer_selection_require_all_causal_identity(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    root = initialize_store(default_store_root(project), project)
    current = append_manifest(root, _fields())
    expected = {key: current[key] for key in ("dataset_epoch", "source_revision", "deployed_revision", "tile_config_signature")}
    assert parity_status(current, expected)["status"] == "MATCH"
    assert require_analyzer_dataset(root, expected)["entry_hash"] == current["entry_hash"]
    expected["source_revision"] = "d" * 40
    with pytest.raises(CanonicalStoreError, match="PARITY_MISMATCH"):
        require_analyzer_dataset(root, expected)


def test_analyzer_selection_rejects_missing_deployed_revision_expected_key(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    root = initialize_store(default_store_root(project), project)
    current = append_manifest(root, _fields())
    expected = {
        key: current[key]
        for key in ("dataset_epoch", "source_revision", "tile_config_signature")
    }

    with pytest.raises(CanonicalStoreError, match="PARITY_MISMATCH"):
        require_analyzer_dataset(root, expected)


def test_parity_status_is_atomic_and_bound_to_current_manifest(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    root = initialize_store(default_store_root(project), project)
    current = append_manifest(root, _fields())
    expected = {key: current[key] for key in ("dataset_epoch", "source_revision", "deployed_revision", "tile_config_signature")}
    status = publish_parity_status(root, expected)
    assert status["status"] == "MATCH"
    assert status["manifest_entry_hash"] == current["entry_hash"]
    assert json.loads((root / "canonical_dataset_parity.json").read_text()) == status


def test_record_existing_store_is_deterministic_and_preserves_evidence(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    module = _load_migration(project)
    destination = default_store_root(project)
    receipt_path, heartbeat, receipt, _ = _terminal_membership_fixture(destination)
    try:
        original = (destination / "evidence.jsonl").read_bytes()
        # Resumability cache material is neither enumerated nor copied as promotion
        # authority. A forged cached row cannot alter receipt-covered membership.
        (destination / ".fly-sync-state.json").write_text(
            json.dumps({"cache-injected.json": {"size": 0, "sha256": "0" * 64}}),
            encoding="utf-8",
        )
        first = module.record_existing_store(destination, heartbeat, receipt_path)
        second = module.record_existing_store(destination, heartbeat, receipt_path)
        assert (destination / "evidence.jsonl").read_bytes() == original
        assert first["source_deleted"] is False and second["source_deleted"] is False
        assert first["terminal_membership_receipt_sha256"] == hashlib.sha256(
            _fixture_io_path(receipt_path).read_bytes()
        ).hexdigest()
        rows = validate_manifest_chain(destination)
        assert len(rows) == 2
        assert rows[0]["dataset_checksum"] == rows[1]["dataset_checksum"]
        assert rows[1]["previous_entry_hash"] == rows[0]["entry_hash"]
        pointer = json.loads((destination / "canonical_dataset_current.json").read_text())
        assert pointer["terminal_membership_receipt_name"] == receipt_path.name
        assert pointer["terminal_membership_receipt_sha256"] == first["terminal_membership_receipt_sha256"]
        assert pointer["terminal_membership_content_digest_sha256"] == receipt[
            "content_coverage"
        ]["local_full_file_sha256"]["sorted_file_digest_sha256"]
    finally:
        _remove_terminal_receipt_fixture(receipt_path)


def test_record_existing_cli_requires_receipt_before_publishing_completion(
    tmp_path, monkeypatch, capsys
):
    project = tmp_path / "project"
    project.mkdir()
    module = _load_migration(project)
    destination = default_store_root(project)
    receipt_path, heartbeat, _, _ = _terminal_membership_fixture(destination)
    pointer = destination / "canonical_dataset_current.json"
    try:
        base_args = [
            str(Path(module.__file__)),
            "--record-existing",
            "--destination",
            str(destination),
            "--heartbeat",
            str(heartbeat),
        ]
        monkeypatch.setattr(sys, "argv", base_args)
        with pytest.raises(SystemExit) as missing:
            module.main()
        assert missing.value.code == 2
        assert not pointer.exists()

        monkeypatch.setattr(
            sys,
            "argv",
            base_args + ["--terminal-membership-receipt", str(receipt_path)],
        )
        assert module.main() == 0
        output = json.loads(capsys.readouterr().out)
        assert output["terminal_membership_receipt_name"] == receipt_path.name
        assert pointer.is_file()
    finally:
        _remove_terminal_receipt_fixture(receipt_path)


def test_record_existing_refuses_cache_only_authority_when_terminal_receipt_is_missing(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    module = _load_migration(project)
    destination = default_store_root(project)
    receipt_path, heartbeat, _, _ = _terminal_membership_fixture(destination)
    try:
        _fixture_io_path(receipt_path).unlink()
        (destination / ".fly-sync-state.json").write_text(
            json.dumps({"evidence.jsonl": {"size": 1, "sha256": "0" * 64}}), encoding="utf-8"
        )
        with pytest.raises(RuntimeError, match="TERMINAL_MEMBERSHIP_RECEIPT_REQUIRED"):
            module.record_existing_store(destination, heartbeat, receipt_path)
    finally:
        _remove_terminal_receipt_fixture(receipt_path)


def test_record_existing_rejects_tampered_receipt_covered_content(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    module = _load_migration(project)
    destination = default_store_root(project)
    receipt_path, heartbeat, _, _ = _terminal_membership_fixture(destination)
    try:
        (destination / "evidence.jsonl").write_bytes(b'{"event":"tampered"}\n')
        with pytest.raises(RuntimeError, match="TERMINAL_MEMBERSHIP_LOCAL_(SIZE|CHECKSUM)_MISMATCH"):
            module.record_existing_store(destination, heartbeat, receipt_path)
    finally:
        _remove_terminal_receipt_fixture(receipt_path)


def test_record_existing_rejects_heartbeat_mismatched_terminal_receipt(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    module = _load_migration(project)
    destination = default_store_root(project)
    receipt_path, heartbeat_path, _, heartbeat = _terminal_membership_fixture(destination)
    try:
        heartbeat["ackSessionId"] = "3" * 32
        heartbeat_path.write_text(json.dumps(heartbeat), encoding="utf-8")
        with pytest.raises(RuntimeError, match="TERMINAL_MEMBERSHIP_HEARTBEAT_ACK_MISMATCH"):
            module.record_existing_store(destination, heartbeat_path, receipt_path)
    finally:
        _remove_terminal_receipt_fixture(receipt_path)


def test_record_existing_rejects_abbreviated_terminal_source_revision(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    module = _load_migration(project)
    destination = default_store_root(project)
    receipt_path, heartbeat_path, receipt, heartbeat = _terminal_membership_fixture(destination)
    try:
        heartbeat["sourceRevision"] = heartbeat["sourceRevision"][:12]
        heartbeat["mirroredSourceRevision"] = heartbeat["mirroredSourceRevision"][:12]
        heartbeat_path.write_text(json.dumps(heartbeat), encoding="utf-8")
        with pytest.raises(RuntimeError, match="exact full SHA"):
            module.record_existing_store(destination, heartbeat_path, receipt_path)

        heartbeat["sourceRevision"] = "a" * 40
        heartbeat["mirroredSourceRevision"] = "a" * 40
        heartbeat_path.write_text(json.dumps(heartbeat), encoding="utf-8")
        receipt["source_git_rev"] = receipt["source_git_rev"][:12]
        _fixture_io_path(receipt_path).write_text(json.dumps(receipt), encoding="utf-8")
        with pytest.raises(RuntimeError, match="TERMINAL_MEMBERSHIP_IDENTITY_INVALID"):
            module.record_existing_store(destination, heartbeat_path, receipt_path)
    finally:
        _remove_terminal_receipt_fixture(receipt_path)


def test_record_existing_rejects_incomplete_checksum_coverage(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    module = _load_migration(project)
    destination = default_store_root(project)
    receipt_path, heartbeat, receipt, _ = _terminal_membership_fixture(destination)
    try:
        receipt["content_coverage"]["local_content_coverage_complete"] = False
        _fixture_io_path(receipt_path).write_text(json.dumps(receipt), encoding="utf-8")
        with pytest.raises(RuntimeError, match="TERMINAL_MEMBERSHIP_CONTENT_COVERAGE_INVALID"):
            module.record_existing_store(destination, heartbeat, receipt_path)
    finally:
        _remove_terminal_receipt_fixture(receipt_path)


def test_migrate_copies_only_explicit_terminal_membership(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    module = _load_migration(project)
    source = tmp_path / "mirror"
    receipt_path, heartbeat, _, _ = _terminal_membership_fixture(source)
    (source / ".fly-sync-state.json").write_text(
        json.dumps({"cache-injected.json": {"size": 0, "sha256": "0" * 64}}), encoding="utf-8"
    )
    (source / "cache-injected.json").write_text("not receipt-covered", encoding="utf-8")
    destination = default_store_root(project)
    destination_receipt = destination / "receipts" / "terminal-transfer-membership" / receipt_path.name
    try:
        result = module.migrate(source, destination, heartbeat, receipt_path)

        assert result["source_deleted"] is False
        assert (source / "evidence.jsonl").is_file()
        assert (destination / "evidence.jsonl").read_bytes() == (source / "evidence.jsonl").read_bytes()
        assert not (destination / ".fly-sync-state.json").exists()
        assert not (destination / "cache-injected.json").exists()
        assert _fixture_io_path(destination_receipt).is_file()
    finally:
        _remove_terminal_receipt_fixture(receipt_path)
        _remove_terminal_receipt_fixture(destination_receipt)


def test_cleanup_is_archive_first_and_cannot_escape(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    root = initialize_store(default_store_root(project), project)
    target = root / "obsolete.jsonl"
    target.write_text("evidence\n")
    receipt = archive_before_cleanup(root, target, reason="superseded verified generation")
    archived = root / receipt["archive_relative"]
    assert receipt["recoverable"] is True
    assert archived.read_text() == "evidence\n"
    assert receipt["archive_file_count"] == 1
    assert receipt["archive_bytes"] == archived.stat().st_size
    assert len(receipt["archive_manifest_sha256"]) == 64
    assert receipt["verification"] == "COPY_AND_SOURCE_STABILITY_SHA256_VERIFIED_BEFORE_REMOVAL"
    assert not target.exists()
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    with pytest.raises(CanonicalStoreError, match="PATH_OUTSIDE"):
        archive_before_cleanup(root, outside, reason="must refuse")
    assert outside.read_text() == "keep"


def test_cleanup_archives_directory_tree_with_verified_manifest(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    root = initialize_store(default_store_root(project), project)
    target = root / "derived-old"
    (target / "nested").mkdir(parents=True)
    (target / "a.json").write_text("a", encoding="utf-8")
    (target / "nested" / "b.jsonl").write_text("b\n", encoding="utf-8")

    receipt = archive_before_cleanup(root, target, reason="verified derived offload")
    archived = root / receipt["archive_relative"]
    assert receipt["archive_file_count"] == 2
    assert receipt["archive_bytes"] == sum(
        path.stat().st_size for path in archived.rglob("*") if path.is_file()
    )
    assert (archived / "a.json").read_text(encoding="utf-8") == "a"
    assert (archived / "nested" / "b.jsonl").read_text(encoding="utf-8") == "b\n"
    assert not target.exists()


def test_cleanup_refuses_symlink_without_removing_source(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    root = initialize_store(default_store_root(project), project)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    link = root / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    with pytest.raises(CanonicalStoreError, match="SYMLINK_FORBIDDEN"):
        archive_before_cleanup(root, link, reason="must refuse symlink")
    assert link.exists()
    assert outside.read_text(encoding="utf-8") == "keep"
