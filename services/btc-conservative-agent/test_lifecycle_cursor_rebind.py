import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

import lifecycle_cursor_rebind as rebind
import lifecycle_cursor_rebind_verify as readback


def _write_json(path, value):
    path.write_text(json.dumps(value, separators=(",", ":"), sort_keys=True), encoding="utf-8")


def _fixture(tmp_path, monkeypatch, *, appended=b''):
    prefix = b'{"record_id":"one"}\n{"record_id":"two"}\n'
    anchor = hashlib.sha256(prefix).hexdigest()
    monkeypatch.setattr(rebind, "PREFIX_SIZE", len(prefix))
    monkeypatch.setattr(rebind, "PREFIX_SHA256", hashlib.sha256(prefix).hexdigest())
    monkeypatch.setattr(rebind, "CURSOR_OFFSET", len(prefix))
    monkeypatch.setattr(rebind, "CURSOR_ANCHOR_SHA256", anchor)
    tail = b'{"record_id":"incomplete"'
    original = prefix + tail
    monkeypatch.setattr(rebind, "SOURCE_SIZE", len(original))
    monkeypatch.setattr(rebind, "SOURCE_SHA256", hashlib.sha256(original).hexdigest())
    monkeypatch.setattr(rebind, "TAIL_SIZE", len(tail))
    monkeypatch.setattr(rebind, "TAIL_SHA256", hashlib.sha256(tail).hexdigest())
    ledger = tmp_path / "v3" / "ledgers" / "lifecycle.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(prefix + appended)
    stat = ledger.stat()
    monkeypatch.setattr(rebind, "NEW_DEV", stat.st_dev)
    monkeypatch.setattr(rebind, "NEW_INO", stat.st_ino)
    monkeypatch.setattr(rebind, "OLD_DEV", stat.st_dev)
    monkeypatch.setattr(rebind, "OLD_INO", stat.st_ino + 99)
    quarantine = ledger.parent / "corrupt_evidence_quarantine" / rebind.REPAIR_ID
    quarantine.mkdir(parents=True)
    excluded = {
        "classification": "UNKNOWN", "ranking_eligible": False,
        "profitability_supported": False, "tail_size": len(tail),
        "tail_sha256": hashlib.sha256(tail).hexdigest(),
    }
    validation = {
        "status": "VALIDATED", "active_size": len(prefix),
        "active_sha256": hashlib.sha256(prefix).hexdigest(),
        "invalid_jsonl_lines": 0, "source_cleanup_authorized": False,
    }
    _write_json(quarantine / "excluded_unknown.json", excluded)
    _write_json(quarantine / "validation.json", validation)
    (quarantine / "lifecycle.jsonl.original").write_bytes(original)
    (quarantine / "lifecycle.jsonl.incomplete-tail").write_bytes(tail)
    manifest = {
        "schema": "lifecycle_incomplete_tail_repair_v1",
        "repair_id": rebind.REPAIR_ID,
        "target": "v3/ledgers/lifecycle.jsonl",
        "source": {"size": len(original), "sha256": hashlib.sha256(original).hexdigest()},
        "complete_prefix": {"size": len(prefix), "sha256": hashlib.sha256(prefix).hexdigest()},
        "excluded_tail": {"size": len(tail), "sha256": hashlib.sha256(tail).hexdigest()},
        "source_stat": {"inode": rebind.OLD_INO, "mtime_ns": 1},
        "artifacts": {
            "lifecycle.jsonl.original": hashlib.sha256(original).hexdigest(),
            "lifecycle.jsonl.incomplete-tail": hashlib.sha256(tail).hexdigest(),
            "excluded_unknown.json": hashlib.sha256((quarantine / "excluded_unknown.json").read_bytes()).hexdigest(),
        },
    }
    _write_json(quarantine / "manifest.json", manifest)
    manifest_sha = hashlib.sha256((quarantine / "manifest.json").read_bytes()).hexdigest()
    receipt = {
        "repair_id": rebind.REPAIR_ID, "status": "REPAIRED",
        "source_sha256": rebind.SOURCE_SHA256,
        "prefix_sha256": hashlib.sha256(prefix).hexdigest(),
        "tail_sha256": rebind.TAIL_SHA256,
        "excluded_classification": "UNKNOWN", "ranking_eligible": False,
        "source_cleanup_authorized": False,
        "manifest_sha256": manifest_sha,
        "excluded_unknown_sha256": hashlib.sha256((quarantine / "excluded_unknown.json").read_bytes()).hexdigest(),
        "validation_sha256": hashlib.sha256((quarantine / "validation.json").read_bytes()).hexdigest(),
    }
    receipt["receipt_sha256"] = hashlib.sha256(json.dumps(
        receipt, separators=(",", ":"), sort_keys=True,
    ).encode()).hexdigest()
    _write_json(quarantine / "repair_receipt.json", receipt)
    db = tmp_path / "v3" / "lifecycle_bundle_index" / "lifecycle_index.sqlite3"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE ledger_cursor(ledger TEXT PRIMARY KEY,source_dev INTEGER,source_ino INTEGER,byte_offset INTEGER,source_anchor_sha256 TEXT,source_mtime_ns INTEGER)")
        c.execute("INSERT INTO ledger_cursor VALUES(?,?,?,?,?,?)", ("lifecycle", rebind.OLD_DEV, rebind.OLD_INO, len(prefix), anchor, 123))
        c.execute("CREATE TABLE lifecycle_event(ledger TEXT, byte_offset INTEGER)")
        c.execute("INSERT INTO lifecycle_event VALUES('lifecycle',0)")
        c.execute("CREATE TABLE dirty_lifecycle(x INTEGER)")
        c.execute("INSERT INTO dirty_lifecycle VALUES(1)")
    return ledger, db, quarantine, prefix


def _call(tmp_path):
    return rebind.rebind_lifecycle_cursor(
        tmp_path, expected_old_dev=rebind.OLD_DEV, expected_old_ino=rebind.OLD_INO,
        expected_new_dev=rebind.NEW_DEV, expected_new_ino=rebind.NEW_INO,
        expected_offset=rebind.CURSOR_OFFSET,
        expected_anchor_sha256=rebind.CURSOR_ANCHOR_SHA256,
    )


def test_exact_rebind_preserves_cursor_and_index_rows(tmp_path, monkeypatch):
    _ledger, db, quarantine, _prefix = _fixture(tmp_path, monkeypatch)
    result = _call(tmp_path)
    assert result["status"] == "REBOUND"
    assert result["byte_offset_preserved"] is True
    with sqlite3.connect(db) as c:
        row = c.execute("SELECT source_dev,source_ino,byte_offset,source_anchor_sha256 FROM ledger_cursor").fetchone()
        assert row == (rebind.NEW_DEV, rebind.NEW_INO, rebind.CURSOR_OFFSET, rebind.CURSOR_ANCHOR_SHA256)
        assert c.execute("SELECT count(*) FROM lifecycle_event").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM dirty_lifecycle").fetchone()[0] == 1
    assert json.loads((quarantine / "cursor_rebind_receipt.json").read_text())["receipt_sha256"] == result["receipt_sha256"]


def test_complete_appends_after_prefix_are_accepted(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch, appended=b'{"record_id":"three"}\n')
    assert _call(tmp_path)["active"]["size"] > rebind.CURSOR_OFFSET


def test_idempotent_replay_recovers_missing_file_receipt(tmp_path, monkeypatch):
    _ledger, _db, quarantine, _prefix = _fixture(tmp_path, monkeypatch)
    first = _call(tmp_path)
    (quarantine / "cursor_rebind_receipt.json").unlink()
    second = _call(tmp_path)
    assert second == first
    assert (quarantine / "cursor_rebind_receipt.json").exists()


def test_crash_after_database_commit_recovers_receipt(tmp_path, monkeypatch):
    _ledger, _db, quarantine, _prefix = _fixture(tmp_path, monkeypatch)
    original = rebind._write_once_json
    def crash(path, payload, error):
        if path.name == "cursor_rebind_receipt.json":
            raise RuntimeError("simulated-crash-after-commit")
        return original(path, payload, error)
    monkeypatch.setattr(rebind, "_write_once_json", crash)
    with pytest.raises(RuntimeError, match="after-commit"): _call(tmp_path)
    monkeypatch.setattr(rebind, "_write_once_json", original)
    recovered = _call(tmp_path)
    assert recovered["status"] == "REBOUND"
    assert (quarantine / "cursor_rebind_receipt.json").exists()


@pytest.mark.parametrize("failure", ["old_identity", "prefix", "anchor", "new_identity"])
def test_mismatch_refuses_without_cursor_mutation(tmp_path, monkeypatch, failure):
    ledger, db, _quarantine, prefix = _fixture(tmp_path, monkeypatch)
    if failure == "old_identity":
        with sqlite3.connect(db) as c: c.execute("UPDATE ledger_cursor SET source_ino=source_ino+1")
    elif failure == "prefix":
        ledger.write_bytes(b"X" + prefix[1:])
    elif failure == "anchor":
        monkeypatch.setattr(rebind, "CURSOR_ANCHOR_SHA256", "f" * 64)
    else:
        monkeypatch.setattr(rebind, "NEW_INO", rebind.NEW_INO + 1)
    with pytest.raises(ValueError): _call(tmp_path)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT source_ino FROM ledger_cursor").fetchone()[0] != rebind.NEW_INO


def test_tail_receipt_tamper_refuses(tmp_path, monkeypatch):
    _ledger, _db, quarantine, _prefix = _fixture(tmp_path, monkeypatch)
    (quarantine / "repair_receipt.json").write_text("{}")
    with pytest.raises(ValueError, match="TAIL_REPAIR_RECEIPT_MISMATCH"): _call(tmp_path)


@pytest.mark.parametrize("artifact", [
    "lifecycle.jsonl.original", "lifecycle.jsonl.incomplete-tail",
    "excluded_unknown.json", "validation.json",
])
def test_strict_quarantine_artifact_tamper_refuses(tmp_path, monkeypatch, artifact):
    _ledger, db, quarantine, _prefix = _fixture(tmp_path, monkeypatch)
    (quarantine / artifact).write_bytes(b"tampered")
    with pytest.raises((UnicodeDecodeError, json.JSONDecodeError, ValueError)):
        _call(tmp_path)
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT source_ino FROM ledger_cursor").fetchone()[0] == rebind.OLD_INO


def test_compiled_expectation_mismatch_refuses(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="EXPECTATION_MISMATCH"):
        rebind.rebind_lifecycle_cursor(tmp_path, expected_old_ino=rebind.OLD_INO + 1)


def test_uses_index_then_ledger_lock(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    order = []
    @contextmanager
    def index_lock(_root):
        order.append("index-enter"); yield; order.append("index-exit")
    @contextmanager
    def ledger_lock(_self, _path):
        order.append("ledger-enter"); yield; order.append("ledger-exit")
    monkeypatch.setattr(rebind, "_exclusive_index_lock", index_lock)
    monkeypatch.setattr(rebind.V3EvidenceStore, "_exclusive", ledger_lock)
    _call(tmp_path)
    assert order == ["index-enter", "ledger-enter", "ledger-exit", "index-exit"]


def test_conditional_conflict_rolls_back(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    original = rebind._cursor
    def lie(row):
        value = original(row)
        with sqlite3.connect(tmp_path / "v3/lifecycle_bundle_index/lifecycle_index.sqlite3") as other:
            other.execute("UPDATE ledger_cursor SET source_ino=source_ino+2")
        return value
    monkeypatch.setattr(rebind, "_cursor", lie)
    with pytest.raises((sqlite3.OperationalError, ValueError)): _call(tmp_path)


def test_independent_readback_binds_database_file_and_revision(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch, appended=b'{"record_id":"three"}\n')
    result = _call(tmp_path)
    for name in ("PREFIX_SIZE", "PREFIX_SHA256", "CURSOR_OFFSET", "CURSOR_ANCHOR_SHA256",
                 "NEW_DEV", "NEW_INO", "OLD_DEV", "OLD_INO"):
        monkeypatch.setattr(readback, name, getattr(rebind, name))
    monkeypatch.setenv("SOURCE_GIT_REV", "abcdef1234567890")
    verified = readback.verify(tmp_path, expected_revision="abcdef123456")
    assert verified["ok"] is True
    assert verified["receipt_sha256"] == result["receipt_sha256"]


def test_independent_readback_refuses_intent_tamper(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    _call(tmp_path)
    quarantine = tmp_path / "v3/ledgers/corrupt_evidence_quarantine" / rebind.REPAIR_ID
    (quarantine / "cursor_rebind_intent.json").write_text("{}")
    for name in ("PREFIX_SIZE", "PREFIX_SHA256", "CURSOR_OFFSET", "CURSOR_ANCHOR_SHA256",
                 "NEW_DEV", "NEW_INO", "OLD_DEV", "OLD_INO"):
        monkeypatch.setattr(readback, name, getattr(rebind, name))
    monkeypatch.setenv("SOURCE_GIT_REV", "abcdef1234567890")
    with pytest.raises(ValueError, match="RECEIPT_MISMATCH"):
        readback.verify(tmp_path, expected_revision="abcdef123456")
