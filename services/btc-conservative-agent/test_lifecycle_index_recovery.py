import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

import lifecycle_index_recovery as recovery
import lifecycle_pipeline


def _source(root: Path, *, rows=1):
    path = root / "v3" / "ledgers" / "opportunity.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    material = b"".join(
        json.dumps({
            "record_id": f"row-{index}", "epoch_id": "epoch",
                    "episode_id": f"episode-{index}", "policy_signature": "policy",
            "research_lane": "FIXED", "observed_ts": index,
        }, separators=(",", ":"), sort_keys=True).encode() + b"\n"
        for index in range(rows)
    )
    path.write_bytes(material)
    return path


def _rotate(path: Path, *, append=b""):
    replacement = path.with_suffix(".replacement")
    replacement.write_bytes(path.read_bytes() + append)
    os.replace(replacement, path)


def _finish(root: Path, maximum=40):
    receipts = []
    for _attempt in range(maximum):
        report = lifecycle_pipeline.process_incremental_lifecycle_pipeline(root)
        receipt = report.get("index_recovery")
        if receipt:
            receipts.append(receipt)
            if receipt["complete"]:
                return receipts
        state_path = root / "v3" / "lifecycle_bundle_index" / "recovery-state.json"
        if state_path.is_file():
            state = json.loads(state_path.read_text())
            if state.get("phase") == "COMPLETE":
                receipts.append({
                    "status": "COMPLETE", "complete": True,
                    "recovery_id": state["recovery_id"],
                })
                return receipts
    raise AssertionError("recovery did not finish within bounded invocations")


def test_identical_rotation_quarantines_rebuilds_and_replays(tmp_path):
    source = _source(tmp_path)
    lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path)
    old_db = tmp_path / "v3" / "lifecycle_bundle_index" / "lifecycle_index.sqlite3"
    old_sha = hashlib.sha256(old_db.read_bytes()).hexdigest()
    _rotate(source)

    receipts = _finish(tmp_path)
    recovery_id = receipts[-1]["recovery_id"]
    quarantine = old_db.parent / "recovery-quarantine" / recovery_id[:16]
    receipt = json.loads((quarantine / "receipt.json").read_text())
    assert receipt["components"][0]["sha256"] == old_sha
    assert receipt["sources"][0]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert (quarantine / "lifecycle_index.sqlite3").is_file()
    assert receipts[-1]["status"] == "COMPLETE"

    replay = recovery.recover_rotated_index(
        tmp_path, "SOURCE_LEDGER_ROTATED:opportunity.jsonl"
    )
    assert replay == receipts[-1]
    with sqlite3.connect(old_db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM lifecycle_event").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM dirty_lifecycle").fetchone()[0] == 0


def test_rotation_with_append_rebuilds_every_row_from_zero(tmp_path):
    source = _source(tmp_path)
    lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path)
    appended = json.dumps({
        "record_id": "row-1", "epoch_id": "epoch", "episode_id": "episode",
        "policy_signature": "policy", "research_lane": "FIXED", "observed_ts": 2,
    }, separators=(",", ":"), sort_keys=True).encode() + b"\n"
    _rotate(source, append=appended)

    _finish(tmp_path)
    database = tmp_path / "v3" / "lifecycle_bundle_index" / "lifecycle_index.sqlite3"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM lifecycle_event").fetchone()[0] == 2
        assert connection.execute("SELECT byte_offset FROM ledger_cursor").fetchone()[0] == source.stat().st_size
        assert connection.execute("SELECT COUNT(*) FROM dirty_lifecycle").fetchone()[0] == 0


def test_source_instability_and_staged_tamper_leave_active_index_untouched(
    tmp_path, monkeypatch,
):
    source = _source(tmp_path)
    lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path)
    database = tmp_path / "v3" / "lifecycle_bundle_index" / "lifecycle_index.sqlite3"
    old_sha = hashlib.sha256(database.read_bytes()).hexdigest()
    _rotate(source)
    original_same = recovery._same_append_source
    calls = 0
    def unstable(path, expected):
        nonlocal calls
        calls += 1
        return original_same(path, expected) if calls == 1 else False
    monkeypatch.setattr(recovery, "_same_append_source", unstable)
    with pytest.raises(ValueError, match="LIFECYCLE_RECOVERY_SOURCE_UNSTABLE"):
        lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == old_sha
    monkeypatch.setattr(recovery, "_same_append_source", original_same)
    lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path)
    state = json.loads((database.parent / "recovery-state.json").read_text())
    staged = database.parent / "recovery-staging" / state["recovery_id"][:16] / "sources" / source.name
    staged.write_bytes(staged.read_bytes() + b"tamper")
    lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path)
    with pytest.raises(ValueError, match="STAGED_SOURCE_TAMPERED"):
        lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == old_sha


def test_quarantine_wal_tamper_and_interrupted_swap_are_restart_safe(tmp_path):
    source = _source(tmp_path)
    lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path)
    database = tmp_path / "v3" / "lifecycle_bundle_index" / "lifecycle_index.sqlite3"
    _rotate(source)
    Path(f"{database}-wal").write_bytes(b"immutable-wal-evidence")
    trigger = "SOURCE_LEDGER_ROTATED:opportunity.jsonl"
    # Resume directly so SQLite never interprets the synthetic sidecar.
    while True:
        result = recovery.recover_rotated_index(tmp_path, trigger)
        if result["status"] == "SWAP": break
    state = json.loads((database.parent / "recovery-state.json").read_text())
    quarantine = database.parent / "recovery-quarantine" / state["recovery_id"][:16]
    assert (quarantine / "lifecycle_index.sqlite3-wal").read_bytes() == b"immutable-wal-evidence"
    staged_db = database.parent / "recovery-staging" / state["recovery_id"][:16] / "lifecycle_index.rebuilt.sqlite3"
    retired = quarantine / "retired-active.sqlite3"
    os.replace(database, retired)
    os.replace(staged_db, database)
    completed = recovery.recover_rotated_index(tmp_path, trigger)
    assert completed["status"] == "COMPLETE"
    (quarantine / "lifecycle_index.sqlite3-wal").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="QUARANTINE_TAMPERED"):
        recovery.recover_rotated_index(tmp_path, trigger)


def test_recovery_state_tamper_refuses_without_touching_active_index(tmp_path):
    source = _source(tmp_path)
    lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path)
    database = tmp_path / "v3" / "lifecycle_bundle_index" / "lifecycle_index.sqlite3"
    old_sha = hashlib.sha256(database.read_bytes()).hexdigest()
    _rotate(source)
    lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path)
    state_path = database.parent / "recovery-state.json"
    state = json.loads(state_path.read_text())
    state["phase"] = "SWAP"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(ValueError, match="LIFECYCLE_RECOVERY_STATE_TAMPERED"):
        lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == old_sha


def test_interrupted_large_source_copy_resumes_at_valid_line_boundary(tmp_path):
    source = _source(tmp_path)
    lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path)
    database = tmp_path / "v3" / "lifecycle_bundle_index" / "lifecycle_index.sqlite3"
    old_sha = hashlib.sha256(database.read_bytes()).hexdigest()
    replacement = source.with_suffix(".replacement")
    with replacement.open("wb") as handle:
        for index in range(11):
            handle.write(json.dumps({
                "record_id": f"large-{index}", "epoch_id": "epoch",
                "episode_id": f"episode-{index}", "policy_signature": "policy",
                "research_lane": "FIXED", "observed_ts": index,
                "padding": "x" * 1_000_000,
            }, separators=(",", ":"), sort_keys=True).encode() + b"\n")
    os.replace(replacement, source)

    lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path)
    state_path = database.parent / "recovery-state.json"
    first = json.loads(state_path.read_text())
    offset = first["sources"][0]["copy_offset"]
    assert 0 < offset < source.stat().st_size
    assert hashlib.sha256(database.read_bytes()).hexdigest() == old_sha
    _finish(tmp_path)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM lifecycle_event").fetchone()[0] == 11


def test_recovery_uses_captured_prefix_while_live_source_keeps_appending(tmp_path):
    source = _source(tmp_path)
    lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path)
    _rotate(source)

    first = lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path)
    assert first["index_recovery"]["status"] == "COPY"
    appended = json.dumps({
        "record_id": "row-after-snapshot", "epoch_id": "epoch",
        "episode_id": "episode-after-snapshot", "policy_signature": "policy",
        "research_lane": "FIXED", "observed_ts": 99,
    }, separators=(",", ":"), sort_keys=True).encode() + b"\n"
    with source.open("ab") as handle:
        handle.write(appended)
        handle.flush()
        os.fsync(handle.fileno())

    trigger = "SOURCE_LEDGER_ROTATED:opportunity.jsonl"
    for _attempt in range(40):
        result = recovery.recover_rotated_index(tmp_path, trigger)
        if result["complete"]:
            break
    else:
        raise AssertionError("recovery did not finish within bounded invocations")
    database = tmp_path / "v3" / "lifecycle_bundle_index" / "lifecycle_index.sqlite3"
    with sqlite3.connect(database) as connection:
        snapshot_offset = connection.execute(
            "SELECT byte_offset FROM ledger_cursor WHERE ledger='opportunity'"
        ).fetchone()[0]
        assert snapshot_offset == source.stat().st_size - len(appended)

    catch_up = lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path)
    assert catch_up["scan"]["bytes_indexed"] == len(appended)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM lifecycle_event").fetchone()[0] == 2
        assert connection.execute(
            "SELECT byte_offset FROM ledger_cursor WHERE ledger='opportunity'"
        ).fetchone()[0] == source.stat().st_size


def test_final_swap_refuses_live_prefix_mutation(tmp_path):
    source = _source(tmp_path)
    lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path)
    database = tmp_path / "v3" / "lifecycle_bundle_index" / "lifecycle_index.sqlite3"
    old_sha = hashlib.sha256(database.read_bytes()).hexdigest()
    _rotate(source)
    trigger = "SOURCE_LEDGER_ROTATED:opportunity.jsonl"
    for _attempt in range(40):
        result = recovery.recover_rotated_index(tmp_path, trigger)
        if result["status"] == "SWAP":
            break
    else:
        raise AssertionError("recovery did not reach SWAP")

    material = bytearray(source.read_bytes())
    marker = material.index(b"row-0")
    material[marker:marker + 5] = b"row-X"
    with source.open("r+b") as handle:
        handle.seek(0)
        handle.write(material)
        handle.flush()
        os.fsync(handle.fileno())

    with pytest.raises(ValueError, match="LIFECYCLE_RECOVERY_SOURCE_PREFIX_CHANGED"):
        recovery.recover_rotated_index(tmp_path, trigger)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == old_sha
