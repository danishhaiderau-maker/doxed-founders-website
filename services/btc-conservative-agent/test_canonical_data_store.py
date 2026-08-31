import hashlib
import importlib.util
import json
from pathlib import Path

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
    migration_path = Path(__file__).resolve().parents[2] / "scripts" / "migrate_canonical_research_store.py"
    spec = importlib.util.spec_from_file_location("canonical_migration_test", migration_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.REPO_ROOT = project
    destination = default_store_root(project)
    destination.mkdir(parents=True)
    evidence = destination / "evidence.jsonl"
    original = b'{"event":"kept"}\n'
    evidence.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()
    (destination / ".fly-sync-state.json").write_text(
        json.dumps({"evidence.jsonl": {"size": len(original), "sha256": digest}}),
        encoding="utf-8",
    )
    (destination / "research_session.json").write_text(
        json.dumps({"collector_v22_epoch_id": "epoch-current", "started_at": "2026-08-29T00:00:00Z"}),
        encoding="utf-8",
    )
    heartbeat = project / "heartbeat.json"
    heartbeat.write_text(
        json.dumps(
            {
                "ok": True,
                "inProgress": False,
                "revisionParity": "MATCH",
                "sourceRevision": "a" * 40,
                "deployedRevision": "d" * 40,
                "mirroredSourceRevision": "a" * 40,
                "tileRegistrySignature": "b" * 64,
                "syncedAt": "2026-08-29T00:03:00Z",
            }
        ),
        encoding="utf-8",
    )
    first = module.record_existing_store(destination, heartbeat)
    second = module.record_existing_store(destination, heartbeat)
    assert evidence.read_bytes() == original
    assert first["source_deleted"] is False and second["source_deleted"] is False
    rows = validate_manifest_chain(destination)
    assert len(rows) == 2
    assert rows[0]["dataset_checksum"] == rows[1]["dataset_checksum"]
    assert rows[1]["previous_entry_hash"] == rows[0]["entry_hash"]


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
