import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

import lifecycle_tail_repair as repair
import lifecycle_tail_repair_verify as readback


def _fixture(tmp_path, monkeypatch):
    prefix = b'{"record_id":"one"}\n{"record_id":"two"}\n'
    tail = b'{"record_id":"incomplete"'
    source = prefix + tail
    monkeypatch.setattr(repair, "SOURCE_SHA256", hashlib.sha256(source).hexdigest())
    monkeypatch.setattr(repair, "SOURCE_SIZE", len(source))
    monkeypatch.setattr(repair, "PREFIX_SIZE", len(prefix))
    monkeypatch.setattr(repair, "PREFIX_SHA256", hashlib.sha256(prefix).hexdigest())
    monkeypatch.setattr(repair, "TAIL_SIZE", len(tail))
    monkeypatch.setattr(repair, "TAIL_SHA256", hashlib.sha256(tail).hexdigest())
    target = tmp_path / "v3" / "ledgers" / "lifecycle.jsonl"
    target.parent.mkdir(parents=True); target.write_bytes(source)
    return target, prefix, tail, source


def test_exact_tail_repair_preserves_original_and_unknown_exclusion(tmp_path, monkeypatch):
    target, prefix, tail, source = _fixture(tmp_path, monkeypatch)
    receipt = repair.repair_lifecycle_incomplete_tail(tmp_path)
    quarantine = target.parent / "corrupt_evidence_quarantine" / receipt["repair_id"]
    assert target.read_bytes() == prefix
    assert (quarantine / "lifecycle.jsonl.original").read_bytes() == source
    assert (quarantine / "lifecycle.jsonl.incomplete-tail").read_bytes() == tail
    excluded = json.loads((quarantine / "excluded_unknown.json").read_text())
    assert excluded["classification"] == "UNKNOWN"
    assert excluded["ranking_eligible"] is False
    assert receipt["source_cleanup_authorized"] is False
    assert json.loads((quarantine / "validation.json").read_text())["invalid_jsonl_lines"] == 0


def test_repair_is_idempotent_and_receipt_is_stable(tmp_path, monkeypatch):
    target, prefix, _tail, _source = _fixture(tmp_path, monkeypatch)
    first = repair.repair_lifecycle_incomplete_tail(tmp_path)
    second = repair.repair_lifecycle_incomplete_tail(tmp_path)
    assert second == first
    assert target.read_bytes() == prefix
    quarantine_root = target.parent / "corrupt_evidence_quarantine"
    assert len(list(quarantine_root.iterdir())) == 1


@pytest.mark.parametrize("mutation", ["source", "prefix", "tail"])
def test_exact_precondition_mismatch_refuses_without_quarantine(tmp_path, monkeypatch, mutation):
    target, _prefix, _tail, _source = _fixture(tmp_path, monkeypatch)
    if mutation == "source": monkeypatch.setattr(repair, "SOURCE_SHA256", "f" * 64)
    elif mutation == "prefix": monkeypatch.setattr(repair, "PREFIX_SHA256", "e" * 64)
    else: monkeypatch.setattr(repair, "TAIL_SHA256", "d" * 64)
    before = target.read_bytes()
    with pytest.raises(ValueError): repair.repair_lifecycle_incomplete_tail(tmp_path)
    assert target.read_bytes() == before
    assert not (target.parent / "corrupt_evidence_quarantine").exists()


def test_invalid_complete_prefix_refuses_before_mutation(tmp_path, monkeypatch):
    target, _prefix, tail, _source = _fixture(tmp_path, monkeypatch)
    invalid = b'{"valid":true}\n{broken}\n'
    source = invalid + tail
    target.write_bytes(source)
    monkeypatch.setattr(repair, "SOURCE_SHA256", hashlib.sha256(source).hexdigest())
    monkeypatch.setattr(repair, "SOURCE_SIZE", len(source))
    monkeypatch.setattr(repair, "PREFIX_SIZE", len(invalid))
    monkeypatch.setattr(repair, "PREFIX_SHA256", hashlib.sha256(invalid).hexdigest())
    with pytest.raises(ValueError, match="PREFIX_INVALID_JSON"):
        repair.repair_lifecycle_incomplete_tail(tmp_path)
    assert target.read_bytes() == source


def test_quarantine_tamper_refuses_idempotent_replay(tmp_path, monkeypatch):
    target, _prefix, _tail, _source = _fixture(tmp_path, monkeypatch)
    receipt = repair.repair_lifecycle_incomplete_tail(tmp_path)
    quarantine = target.parent / "corrupt_evidence_quarantine" / receipt["repair_id"]
    (quarantine / "lifecycle.jsonl.incomplete-tail").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="QUARANTINE_TAMPERED"):
        repair.repair_lifecycle_incomplete_tail(tmp_path)


def test_repair_uses_v3_store_exclusive_ledger_lock(tmp_path, monkeypatch):
    target, _prefix, _tail, _source = _fixture(tmp_path, monkeypatch)
    observed = []
    @contextmanager
    def locked(_store, path):
        observed.append(path.resolve()); yield
    index_observed = []
    @contextmanager
    def index_locked(root):
        index_observed.append(Path(root).resolve()); yield
    monkeypatch.setattr(repair.V3EvidenceStore, "_exclusive", locked)
    monkeypatch.setattr(repair, "_exclusive_index_lock", index_locked)
    repair.repair_lifecycle_incomplete_tail(tmp_path)
    assert observed == [target.resolve()]
    assert index_observed == [tmp_path.resolve()]


def test_cli_rejects_expectation_mismatch_without_mutation(tmp_path, monkeypatch, capsys):
    target, _prefix, _tail, _source = _fixture(tmp_path, monkeypatch)
    before = target.read_bytes()
    result = repair.main([
        "--root", str(tmp_path),
        "--expected-source-size", str(repair.SOURCE_SIZE),
        "--expected-source-sha256", "f" * 64,
        "--expected-prefix-size", str(repair.PREFIX_SIZE),
        "--expected-prefix-sha256", repair.PREFIX_SHA256,
        "--expected-tail-size", str(repair.TAIL_SIZE),
        "--expected-tail-sha256", repair.TAIL_SHA256,
    ])
    assert result == 2
    assert json.loads(capsys.readouterr().out)["error_code"] == "LIFECYCLE_REPAIR_EXPECTATION_MISMATCH"
    assert target.read_bytes() == before


def test_cli_accepts_exact_compiled_expectations_and_fences(tmp_path, monkeypatch, capsys):
    target, prefix, _tail, _source = _fixture(tmp_path, monkeypatch)
    stat = target.stat()
    result = repair.main([
        "--root", str(tmp_path),
        "--expected-source-size", str(repair.SOURCE_SIZE),
        "--expected-source-sha256", repair.SOURCE_SHA256,
        "--expected-prefix-size", str(repair.PREFIX_SIZE),
        "--expected-prefix-sha256", repair.PREFIX_SHA256,
        "--expected-tail-size", str(repair.TAIL_SIZE),
        "--expected-tail-sha256", repair.TAIL_SHA256,
        "--expected-inode", str(stat.st_ino),
        "--expected-mtime-ns", str(stat.st_mtime_ns),
    ])
    payload = json.loads(capsys.readouterr().out)
    assert result == 0 and payload["ok"] is True
    assert target.read_bytes() == prefix


@pytest.mark.parametrize("fence", ["inode", "mtime"])
def test_optional_inode_and_mtime_fences_refuse(tmp_path, monkeypatch, fence):
    target, _prefix, _tail, _source = _fixture(tmp_path, monkeypatch)
    kwargs = {"expected_inode": target.stat().st_ino + 1} if fence == "inode" else {
        "expected_mtime_ns": target.stat().st_mtime_ns + 1,
    }
    with pytest.raises(ValueError, match=f"LIFECYCLE_REPAIR_{fence.upper()}_MISMATCH"):
        repair.repair_lifecycle_incomplete_tail(tmp_path, **kwargs)
    assert target.read_bytes() != b""


def test_crash_after_active_replace_replays_to_same_receipt(tmp_path, monkeypatch):
    target, prefix, _tail, _source = _fixture(tmp_path, monkeypatch)
    original = repair._write_once_json
    def crash(path, payload, error):
        if path.name == "validation.json":
            raise RuntimeError("simulated-crash")
        return original(path, payload, error)
    monkeypatch.setattr(repair, "_write_once_json", crash)
    with pytest.raises(RuntimeError, match="simulated-crash"):
        repair.repair_lifecycle_incomplete_tail(tmp_path)
    assert target.read_bytes() == prefix
    monkeypatch.setattr(repair, "_write_once_json", original)
    receipt = repair.repair_lifecycle_incomplete_tail(tmp_path)
    assert receipt["status"] == "REPAIRED"


def test_crash_replay_accepts_original_inode_and_mtime_fences(tmp_path, monkeypatch):
    target, prefix, _tail, _source = _fixture(tmp_path, monkeypatch)
    original_stat = target.stat()
    original = repair._write_once_json
    def crash(path, payload, error):
        if path.name == "validation.json":
            raise RuntimeError("simulated-crash")
        return original(path, payload, error)
    monkeypatch.setattr(repair, "_write_once_json", crash)
    with pytest.raises(RuntimeError, match="simulated-crash"):
        repair.repair_lifecycle_incomplete_tail(
            tmp_path,
            expected_inode=original_stat.st_ino,
            expected_mtime_ns=original_stat.st_mtime_ns,
        )
    assert target.read_bytes() == prefix
    monkeypatch.setattr(repair, "_write_once_json", original)
    receipt = repair.repair_lifecycle_incomplete_tail(
        tmp_path,
        expected_inode=original_stat.st_ino,
        expected_mtime_ns=original_stat.st_mtime_ns,
    )
    assert receipt["status"] == "REPAIRED"


def test_directory_fsync_boundaries_are_invoked(tmp_path, monkeypatch):
    target, _prefix, _tail, _source = _fixture(tmp_path, monkeypatch)
    synced = []
    monkeypatch.setattr(repair, "_fsync_directory", lambda path: synced.append(Path(path)))
    receipt = repair.repair_lifecycle_incomplete_tail(tmp_path)
    quarantine = target.parent / "corrupt_evidence_quarantine" / receipt["repair_id"]
    assert target.parent in synced
    assert quarantine.parent in synced
    assert quarantine in synced


def test_independent_readback_binds_revision_stat_and_all_artifacts(tmp_path, monkeypatch):
    target, _prefix, _tail, _source = _fixture(tmp_path, monkeypatch)
    stat = target.stat()
    receipt = repair.repair_lifecycle_incomplete_tail(
        tmp_path,
        expected_inode=stat.st_ino,
        expected_mtime_ns=stat.st_mtime_ns,
    )
    monkeypatch.setattr(readback, "SOURCE_SHA256", repair.SOURCE_SHA256)
    monkeypatch.setattr(readback, "SOURCE_SIZE", repair.SOURCE_SIZE)
    monkeypatch.setattr(readback, "PREFIX_SHA256", repair.PREFIX_SHA256)
    monkeypatch.setattr(readback, "PREFIX_SIZE", repair.PREFIX_SIZE)
    monkeypatch.setattr(readback, "TAIL_SHA256", repair.TAIL_SHA256)
    monkeypatch.setattr(readback, "TAIL_SIZE", repair.TAIL_SIZE)
    monkeypatch.setattr(readback, "REPAIR_ID", receipt["repair_id"])
    monkeypatch.setenv("SOURCE_GIT_REV", "abcdef1234567890")
    result = readback.verify(
        tmp_path,
        expected_revision="abcdef123456",
        expected_inode=stat.st_ino,
        expected_mtime_ns=stat.st_mtime_ns,
    )
    assert result["ok"] is True
    assert result["original_preserved"] is True
    assert result["tail_preserved"] is True
    assert result["excluded_classification"] == "UNKNOWN"
    assert result["source_cleanup_authorized"] is False
