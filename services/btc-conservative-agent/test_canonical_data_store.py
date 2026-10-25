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


def test_cleanup_symlink_mutation_proof_contained_path_resolves_outside(tmp_path):
    """Mutant: rely on contained_path() alone -> PATH_OUTSIDE, not SYMLINK_FORBIDDEN."""
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
    with pytest.raises(CanonicalStoreError, match="PATH_OUTSIDE"):
        contained_path(root, link)


# --- RK 25 Oct: genesis at the R5/R17 cutoff --------------------------------
from research.canonical_data_store import (  # noqa: E402
    GENESIS_FLOOR_TS,
    build_genesis_manifest,
    current_analyzer_dataset_identity,
)

_PRE = "2026-10-04T09:05:39.963965Z"
_POST = "2026-10-17T19:20:35.6057361Z"  # 7 fractional digits, as PowerShell writes


def _legacy_store(tmp_path):
    src = tmp_path / "src"
    append_manifest(src, _fields(dataset_epoch="epoch-old", collection_started_at=_PRE, dataset_checksum="o" * 64))
    append_manifest(src, _fields(dataset_epoch="epoch-new", collection_started_at=_POST, dataset_checksum="n" * 64))
    append_manifest(src, _fields(dataset_epoch="epoch-new", collection_started_at="2026-10-25T00:55:40Z", dataset_checksum="m" * 64))
    return src


def test_genesis_floor_is_the_cutoff():
    assert GENESIS_FLOOR_TS == 1792263671


def test_genesis_lists_only_post_cutoff_and_validates(tmp_path):
    src = _legacy_store(tmp_path)
    before = (src / "canonical_dataset_manifest.jsonl").read_bytes()
    out = tmp_path / "genesis"
    result = build_genesis_manifest(src, out)
    assert (src / "canonical_dataset_manifest.jsonl").read_bytes() == before
    assert result["datasets"] == 2 and result["excluded_pre_floor_entries"] == 1
    assert {d["dataset_epoch"] for d in result["genesis"]["datasets"]} == {"epoch-new"}
    rows = validate_manifest_chain(out)
    assert rows[0]["genesis"] is True and rows[0]["previous_entry_hash"] is None
    assert rows[1]["previous_entry_hash"] == rows[0]["entry_hash"]
    identity = current_analyzer_dataset_identity(out)
    assert identity["dataset_checksum"] == "m" * 64
    # Further appends chain from the genesis head.
    append_manifest(out, _fields(dataset_epoch="epoch-new", collection_started_at="2026-10-26T00:00:00Z"))
    assert len(validate_manifest_chain(out)) == 3


def test_genesis_validator_rejects_pre_floor_append(tmp_path):
    out = tmp_path / "genesis"
    build_genesis_manifest(_legacy_store(tmp_path), out)
    append_manifest(out, _fields(collection_started_at=_PRE))
    with pytest.raises(CanonicalStoreError, match="MANIFEST_PRE_GENESIS_ROW"):
        validate_manifest_chain(out)


def _rewrite_genesis(out, mutate):
    journal = out / "canonical_dataset_manifest.jsonl"
    lines = journal.read_text().splitlines()
    row = json.loads(lines[0])
    mutate(row)
    row.pop("entry_hash")
    row["entry_hash"] = hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    journal.write_text(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    (out / "canonical_dataset_current.json").write_text(json.dumps(row))


@pytest.mark.parametrize("mutate,code", [
    (lambda r: r.update(genesis_floor_ts=GENESIS_FLOOR_TS - 86400), "MANIFEST_GENESIS_FLOOR_MISMATCH"),
    (lambda r: r["datasets"].append({"collection_started_at": _PRE}), "MANIFEST_GENESIS_PRE_FLOOR_DATASET"),
    (lambda r: r["datasets"].append({"collection_started_at": None}), "MANIFEST_GENESIS_PRE_FLOOR_DATASET"),
])
def test_genesis_validator_rejects_bad_genesis(tmp_path, mutate, code):
    out = tmp_path / "genesis"
    build_genesis_manifest(_legacy_store(tmp_path), out)
    _rewrite_genesis(out, mutate)
    with pytest.raises(CanonicalStoreError, match=code):
        validate_manifest_chain(out)


def test_genesis_refuses_same_or_nonempty_output(tmp_path):
    src = _legacy_store(tmp_path)
    with pytest.raises(CanonicalStoreError, match="GENESIS_OUTPUT_MUST_BE_SEPARATE"):
        build_genesis_manifest(src, src)
    out = tmp_path / "genesis"
    build_genesis_manifest(src, out)
    with pytest.raises(CanonicalStoreError, match="GENESIS_OUTPUT_NOT_EMPTY"):
        build_genesis_manifest(src, out)


def test_genesis_with_no_post_cutoff_datasets_is_empty_but_valid(tmp_path):
    src = tmp_path / "src"
    append_manifest(src, _fields(collection_started_at=_PRE))
    out = tmp_path / "genesis"
    result = build_genesis_manifest(src, out)
    assert result["datasets"] == 0
    assert len(validate_manifest_chain(out)) == 1


# --- HM #886 B1/B2: boundary and guard kills ----------------------------------
_AT_FLOOR = "2026-10-17T19:01:11Z"      # == GENESIS_FLOOR_TS (1792263671): kept
_FLOOR_MINUS_1 = "2026-10-17T19:01:10Z"  # 1792263670: excluded


def test_genesis_floor_boundary_is_inclusive(tmp_path):
    src = tmp_path / "src"
    append_manifest(src, _fields(dataset_epoch="epoch-edge-out", collection_started_at=_FLOOR_MINUS_1,
                                 dataset_checksum="e" * 64))
    append_manifest(src, _fields(dataset_epoch="epoch-edge-in", collection_started_at=_AT_FLOOR,
                                 dataset_checksum="f" * 64))
    result = build_genesis_manifest(src, tmp_path / "genesis")
    assert [d["dataset_epoch"] for d in result["genesis"]["datasets"]] == ["epoch-edge-in"]
    assert result["excluded_pre_floor_entries"] == 1


@pytest.mark.parametrize("floor", [GENESIS_FLOOR_TS - 1, GENESIS_FLOOR_TS + 1])
def test_genesis_build_refuses_any_other_floor(tmp_path, floor):
    with pytest.raises(CanonicalStoreError, match="GENESIS_FLOOR_MUST_EQUAL_CUTOFF"):
        build_genesis_manifest(_legacy_store(tmp_path), tmp_path / "genesis", floor_ts=floor)
    assert not (tmp_path / "genesis").exists()


def test_genesis_row_mid_chain_is_rejected(tmp_path):
    out = tmp_path / "genesis"
    build_genesis_manifest(_legacy_store(tmp_path), out)
    journal = out / "canonical_dataset_manifest.jsonl"
    lines = journal.read_text().splitlines()
    genesis, head = json.loads(lines[0]), json.loads(lines[1])
    # Re-chain as [head-as-first-row, genesis-as-second-row] with valid hashes.
    first = {k: v for k, v in head.items() if k != "entry_hash"}
    first["previous_entry_hash"] = None
    first["entry_hash"] = hashlib.sha256(json.dumps(first, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    second = {k: v for k, v in genesis.items() if k != "entry_hash"}
    second["previous_entry_hash"] = first["entry_hash"]
    second["entry_hash"] = hashlib.sha256(json.dumps(second, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    journal.write_text("\n".join(json.dumps(r, sort_keys=True, separators=(",", ":")) for r in (first, second)) + "\n")
    (out / "canonical_dataset_current.json").write_text(json.dumps(second))
    with pytest.raises(CanonicalStoreError, match="MANIFEST_GENESIS_INVALID:2"):
        validate_manifest_chain(out)


def test_genesis_dedups_datasets_by_epoch_and_checksum(tmp_path):
    # Units: entries (journal rows) vs datasets (distinct (epoch, checksum)); prod was 426 entries / 331 datasets.
    src = tmp_path / "src"
    for checksum in ("a" * 64, "a" * 64, "b" * 64):
        append_manifest(src, _fields(dataset_epoch="epoch-new", collection_started_at=_POST, dataset_checksum=checksum))
    result = build_genesis_manifest(src, tmp_path / "genesis")
    assert result["datasets"] == 2  # 3 post-floor entries -> 2 distinct datasets
    assert [d["dataset_checksum"] for d in result["genesis"]["datasets"]] == ["a" * 64, "b" * 64]

