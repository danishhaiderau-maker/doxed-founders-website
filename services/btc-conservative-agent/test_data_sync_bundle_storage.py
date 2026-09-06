import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import data_sync_bundle_storage as storage


def generation(root, identifier, *, payload=100, members=1):
    directory = root / ("g-" + identifier[:16])
    directory.mkdir(parents=True)
    package = "a" * 64
    state = {"schema": storage.STATE_SCHEMA,
             "generation": {"inventory_generation_id": identifier, "inventory_sha256": identifier},
             "package_index": [{"package_sha256": package, "descriptor_sha256": "b" * 64,
                 "descriptor_path": "descriptors/d-" + package[:20] + ".json",
                 "member_count": members, "payload_bytes": payload}]}
    (directory / "packages").mkdir()
    (directory / "descriptors").mkdir()
    (directory / "packages" / (package + ".tar")).write_bytes(b"package")
    (directory / "descriptors" / ("d-" + package[:20] + ".json")).write_bytes(b"{}")
    path = directory / "bundle-worker-state.json"
    path.write_text(json.dumps(state))
    return directory, path


def test_absent_root_admitted_without_creating_it(tmp_path):
    root = tmp_path / "missing" / "child"
    result = storage.check_derivative_admission(root, "1" * 64, 1024)
    assert result["status"] == "ADMITTED" and result["projected_generations"] == 1
    assert not root.exists()


def test_counts_all_generations_and_resume_does_not_use_new_slot(tmp_path):
    generation(tmp_path, "1" * 64)
    generation(tmp_path, "2" * 64)
    resumed = storage.check_derivative_admission(tmp_path, "2" * 64, 100, max_generations=2)
    assert resumed["existing_generations"] == resumed["projected_generations"] == 2
    assert resumed["current_generation_present"] is True
    assert resumed["estimated_bytes"] > 2 * (100 + 4096 + 32768)
    with pytest.raises(storage.DerivativeAdmissionError, match="GENERATION_LIMIT"):
        storage.check_derivative_admission(tmp_path, "3" * 64, 100, max_generations=2)


def test_total_budget_across_generations_and_boundary(tmp_path):
    generation(tmp_path, "1" * 64, payload=3 * 1024 * 1024)
    generation(tmp_path, "2" * 64, payload=3 * 1024 * 1024)
    receipt = storage.check_derivative_admission(tmp_path, "2" * 64, 100)
    exact = receipt["estimated_bytes"] + 100
    assert storage.check_derivative_admission(tmp_path, "2" * 64, 100, budget=exact)["status"] == "ADMITTED"
    with pytest.raises(storage.DerivativeAdmissionError, match="TOTAL_BUDGET"):
        storage.check_derivative_admission(tmp_path, "2" * 64, 100, budget=exact - 1)


@pytest.mark.parametrize("location", ["root", "generation", "packages", "descriptors"])
def test_orphan_artifact_fails_without_deleting(tmp_path, location):
    directory, _ = generation(tmp_path, "1" * 64)
    parent = {"root": tmp_path, "generation": directory,
              "packages": directory / "packages", "descriptors": directory / "descriptors"}[location]
    orphan = parent / "unindexed.tmp"
    orphan.write_bytes(b"forensic bytes")
    with pytest.raises(storage.DerivativeAdmissionError):
        storage.check_derivative_admission(tmp_path, "1" * 64, 10)
    assert orphan.read_bytes() == b"forensic bytes"


def test_missing_indexed_file_is_not_zero_bytes(tmp_path):
    directory, _ = generation(tmp_path, "1" * 64)
    (directory / "packages" / ("a" * 64 + ".tar")).unlink()
    with pytest.raises(storage.DerivativeAdmissionError, match="INDEXED_ARTIFACT_MISSING"):
        storage.check_derivative_admission(tmp_path, "1" * 64, 10)


def test_generation_prefix_collision_rejected(tmp_path):
    identifier = "1" * 64
    generation(tmp_path, identifier)
    with pytest.raises(storage.DerivativeAdmissionError, match="GENERATION_INVALID"):
        storage.check_derivative_admission(tmp_path, "1" * 16 + "2" * 48, 10)


@pytest.mark.parametrize("defect", ["large", "duplicate", "index-limit", "negative", "boolean", "invalid"])
def test_malformed_state_fails_closed(tmp_path, defect):
    _, path = generation(tmp_path, "1" * 64)
    value = json.loads(path.read_text())
    if defect == "large": path.write_bytes(b" " * (storage.MAX_STATE_BYTES + 1))
    elif defect == "duplicate": path.write_text('{"schema":"x","schema":"y"}')
    elif defect == "invalid": path.write_text("[]")
    else:
        if defect == "index-limit": value["package_index"] *= storage.MAX_PACKAGES + 1
        if defect == "negative": value["package_index"][0]["payload_bytes"] = -1
        if defect == "boolean": value["package_index"][0]["member_count"] = True
        path.write_text(json.dumps(value))
    before = path.read_bytes()
    with pytest.raises(storage.DerivativeAdmissionError):
        storage.check_derivative_admission(tmp_path, "1" * 64, 10)
    assert path.read_bytes() == before


def test_reparse_mock_on_root_or_package_rejected(tmp_path, monkeypatch):
    directory, _ = generation(tmp_path, "1" * 64)
    target = directory / "packages" / ("a" * 64 + ".tar")
    original = Path.lstat
    def lstat(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if path == target:
            return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400)
        return value
    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(storage.DerivativeAdmissionError, match="LINK_REJECTED"):
        storage.check_derivative_admission(tmp_path, "1" * 64, 10)


def test_lease_is_known_and_bounded(tmp_path):
    (tmp_path / ".bundle-worker.lease").write_bytes(b"0")
    assert storage.check_derivative_admission(tmp_path, "1" * 64, 10)["estimated_bytes"] == 4096
    (tmp_path / ".bundle-worker.lease").write_bytes(b"0" * 4097)
    with pytest.raises(storage.DerivativeAdmissionError, match="LEASE_LIMIT"):
        storage.check_derivative_admission(tmp_path, "1" * 64, 10)


def test_real_worker_output_admitted(tmp_path):
    from data_sync_bundle_worker import run_bundle_worker
    from test_data_sync_bundle_worker import _fixture, _row
    source, root = tmp_path / "source", tmp_path / "output"
    row = _row(source, "v3/market_segments/11/" + "1" * 64 + ".json", b"sample")
    metadata = _fixture(tmp_path, [row])
    assert run_bundle_worker(metadata, source, root)["status"] == "COMPLETE"
    result = storage.check_derivative_admission(root, metadata["generation_id"], storage.MAX_PACKAGE_BYTES)
    assert result["status"] == "ADMITTED" and result["current_generation_present"]


def test_directory_enumeration_is_bounded(tmp_path, monkeypatch):
    class Entries:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def __iter__(self):
            for i in range(100000):
                # Four generations, worker lease, one bounded early diagnostic,
                # and one overflow entry are the complete admission bound.
                if i > 6: raise AssertionError("unbounded enumeration")
                yield SimpleNamespace(path=str(tmp_path / ("g-" + str(i) * 16)))
    monkeypatch.setattr(storage.os, "scandir", lambda _: Entries())
    with pytest.raises(storage.DerivativeAdmissionError, match="ENTRY_LIMIT"):
        storage.check_derivative_admission(tmp_path, "1" * 64, 10)


def test_physical_artifacts_cannot_exceed_index_estimate(tmp_path):
    directory, _ = generation(tmp_path, "1" * 64)
    (directory / "packages" / ("a" * 64 + ".tar")).write_bytes(b"x" * 100000)
    with pytest.raises(storage.DerivativeAdmissionError, match="ESTIMATE_UNDERSTATES_FILES"):
        storage.check_derivative_admission(tmp_path, "1" * 64, 10)


@pytest.mark.parametrize("reserve,budget,limit", [(True, 1024, 4), (-1, 1024, 4),
    (2048, 1024, 4), (1, 1024, 17), (1, 1024, True)])
def test_admission_limits_are_strict(tmp_path, reserve, budget, limit):
    with pytest.raises(storage.DerivativeAdmissionError, match="ADMISSION_ARGUMENTS"):
        storage.check_derivative_admission(tmp_path, "1" * 64, reserve, budget, limit)
