import hashlib, json, os, subprocess, sys, time
from pathlib import Path
import pytest
import emergency_evidence_wal as module
from emergency_evidence_wal import (CONTROL_SLOT_BYTES, DATA_PREFIX_BYTES, EXTENT_BYTES,
                                    EmergencyEvidenceWal, MAX_ROW_BYTES, _cross_process_lock)

IDENTITY = {"epoch_id":"epoch-1", "source_revision":"a"*40, "deployed_revision":"a"*40, "tile_config_signature":"b"*64}

def test_preallocated_capacity_supports_exact_8mib_payload(tmp_path):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=2)
    assert wal.data_path.stat().st_size == 2 * EXTENT_BYTES
    assert module._allocated_bytes(wal.data_path) >= 2 * EXTENT_BYTES
    row = wal.defer(ledger="execution", record_id="terminal:1", payload=b"x" * MAX_ROW_BYTES)
    assert row["length"] == MAX_ROW_BYTES and wal.data_path.stat().st_size == 2 * EXTENT_BYTES

def test_new_directory_and_provisioned_files_request_parent_fsync(tmp_path, monkeypatch):
    calls=[]
    monkeypatch.setattr(module, "_fsync_parent", lambda path: calls.append(Path(path).resolve()))
    root=tmp_path/"new-reserve"
    EmergencyEvidenceWal(root, identity=IDENTITY, extents=1)
    assert calls[0] == tmp_path.resolve()
    assert root.resolve() in calls

def test_sparse_exact_size_existing_reserve_is_rejected(tmp_path, monkeypatch):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    original = module._allocated_bytes
    monkeypatch.setattr(module, "_allocated_bytes", lambda path: 0 if path.name == wal.data_path.name else original(path))
    with pytest.raises(RuntimeError, match="NOT_PHYSICALLY_ALLOCATED"):
        EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)

def test_prepared_exact_envelope_generation_offset_length_hash_recovers(tmp_path, monkeypatch):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1); original = wal._write_header
    def crash(handle, slot, value):
        if value and value.get("state") == "DEFERRED": raise OSError("crash")
        return original(handle, slot, value)
    monkeypatch.setattr(wal, "_write_header", crash)
    payload = b"durable\n"
    with pytest.raises(OSError): wal.defer(ledger="lifecycle", record_id="terminal:1", payload=payload)
    row = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1).status()["records"][0]
    assert row["state"] == "DEFERRED" and row["offset"] == 0 and row["length"] == len(payload)
    assert row["row_sha256"] == hashlib.sha256(payload).hexdigest()

def test_reused_identical_payload_cannot_alias_stale_extent(tmp_path, monkeypatch):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    first = wal.defer(ledger="execution", record_id="old", payload=b"identical")
    # Simulate a future trusted bridge having released only the header. The old
    # extent remains, exactly as it would after metadata-first reuse.
    with _cross_process_lock(wal.lock_path), wal.header_path.open("r+b") as hf:
        wal._write_header(hf, 0, None); wal._publish_control([None], [])
    original = wal._write_header
    def crash(handle, slot, value):
        original(handle, slot, value)
        if value and value.get("state") == "PREPARED": raise OSError("before-envelope")
    monkeypatch.setattr(wal, "_write_header", crash)
    with pytest.raises(OSError): wal.defer(ledger="execution", record_id="new", payload=b"identical")
    with pytest.raises(RuntimeError, match="PAYLOAD_UNPROVABLE"):
        EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    assert first["generation"]

def test_malformed_deferred_header_fails_closed(tmp_path):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    wal.defer(ledger="execution", record_id="terminal:1", payload=b"row")
    with _cross_process_lock(wal.lock_path), wal.header_path.open("r+b") as hf:
        row = wal._read_headers(hf)[0]; row["ledger"] = ""; wal._write_header(hf, 0, row)
    with pytest.raises(RuntimeError, match="HEADER_INVALID"): wal.status()
    with pytest.raises(RuntimeError, match="HEADER_INVALID"): EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)

def test_crc_invalid_header_publishes_control_only_alarm_persistent_after_repair(tmp_path):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    wal.defer(ledger="execution", record_id="terminal:1", payload=b"row")
    original = wal.header_path.read_bytes()
    damaged = bytearray(original); damaged[20] ^= 1; wal.header_path.write_bytes(damaged)
    with pytest.raises(RuntimeError, match="HEADER_CORRUPT"): wal.status()
    controls, invalid = wal._controls()
    assert invalid == 0 and all("EMERGENCY_WAL_HEADER_CORRUPT" in c["alarms"] for c in controls)
    wal.header_path.write_bytes(original)
    restarted = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    assert "EMERGENCY_WAL_HEADER_CORRUPT" in restarted.status()["alarms"]

def test_structurally_invalid_header_alarm_uses_prior_control_telemetry(tmp_path):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    wal.defer(ledger="execution", record_id="terminal:1", payload=b"row")
    original = wal.header_path.read_bytes(); before = wal.status()
    with _cross_process_lock(wal.lock_path), wal.header_path.open("r+b") as hf:
        header=wal._read_headers(hf)[0]; header["record_id"]=""; wal._write_header(hf,0,header)
    with pytest.raises(RuntimeError, match="HEADER_INVALID"): wal.status()
    controls, invalid = wal._controls()
    assert invalid == 0
    assert all(c["deferred_count"] == before["deferred_count"] and c["deferred_bytes"] == before["deferred_bytes"] for c in controls)
    assert all("EMERGENCY_WAL_HEADER_INVALID" in c["alarms"] for c in controls)
    wal.header_path.write_bytes(original)
    assert "EMERGENCY_WAL_HEADER_INVALID" in EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1).status()["alarms"]

def test_persisted_header_control_character_is_rejected(tmp_path):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    wal.defer(ledger="execution", record_id="terminal:1", payload=b"row")
    with _cross_process_lock(wal.lock_path), wal.header_path.open("r+b") as hf:
        row=wal._read_headers(hf)[0]; row["record_id"]="terminal:\n1"; wal._write_header(hf,0,row)
    with pytest.raises(RuntimeError, match="HEADER_INVALID"): wal.status()

def test_torn_one_control_copy_reconstructs_with_persistent_alarm(tmp_path):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    with wal.control_path.open("r+b") as handle:
        handle.seek(CONTROL_SLOT_BYTES); handle.write(b"torn" + bytes(40)); handle.flush(); os.fsync(handle.fileno())
    with pytest.raises(RuntimeError, match="REDUNDANCY_LOST"): wal.status()
    restarted = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    assert "EMERGENCY_WAL_CONTROL_COPY_CORRUPT" in restarted.status()["alarms"]
    again = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    assert "EMERGENCY_WAL_CONTROL_COPY_CORRUPT" in again.status()["alarms"]
    with pytest.raises(RuntimeError, match="RESET_BRIDGE_UNAVAILABLE"): restarted.reset_alarm({"forged": True})

def test_header_control_crash_is_detected_and_recover_repairs_with_alarm(tmp_path, monkeypatch):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=2); original = wal._publish_control
    monkeypatch.setattr(wal, "_publish_control", lambda *_a, **_k: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError): wal.defer(ledger="execution", record_id="terminal:1", payload=b"row")
    with pytest.raises(RuntimeError, match="HEADER_MISMATCH"): wal.status()
    monkeypatch.setattr(wal, "_publish_control", original); wal.recover()
    assert "EMERGENCY_WAL_CONTROL_TELEMETRY_RECOVERED" in wal.status()["alarms"]

def test_status_and_duplicate_revalidate_every_occupied_extent(tmp_path):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=2)
    wal.defer(ledger="execution", record_id="one", payload=b"one")
    wal.defer(ledger="execution", record_id="two", payload=b"two")
    with wal.data_path.open("r+b") as data:
        data.seek(EXTENT_BYTES + DATA_PREFIX_BYTES); data.write(b"X"); data.flush(); os.fsync(data.fileno())
    with pytest.raises(RuntimeError, match="DEFERRED_PAYLOAD_UNPROVABLE"): wal.status()
    with pytest.raises(RuntimeError, match="DEFERRED_PAYLOAD_UNPROVABLE"):
        wal.defer(ledger="execution", record_id="one", payload=b"one")
    controls, invalid = wal._controls()
    assert invalid == 0 and all(
        "EMERGENCY_WAL_DEFERRED_PAYLOAD_UNPROVABLE" in control["alarms"]
        for control in controls
    )

def test_crc_valid_header_relabel_cannot_reuse_extent_identity(tmp_path):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    wal.defer(ledger="execution", record_id="original", payload=b"same")
    with _cross_process_lock(wal.lock_path), wal.header_path.open("r+b") as hf:
        header=wal._read_headers(hf)[0]; header["record_id"]="relabeled"; wal._write_header(hf,0,header)
        wal._publish_control([header], [])
    with pytest.raises(RuntimeError, match="DEFERRED_PAYLOAD_UNPROVABLE"): wal.status()

def test_both_torn_controls_are_reconstructed_and_fsynced(tmp_path, monkeypatch):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    with wal.control_path.open("r+b") as handle:
        handle.seek(0); handle.write(b"bad" + bytes(CONTROL_SLOT_BYTES - 3))
        handle.seek(CONTROL_SLOT_BYTES); handle.write(b"bad" + bytes(CONTROL_SLOT_BYTES - 3))
        handle.flush(); os.fsync(handle.fileno())
    calls=[]; original=os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append(fd), original(fd))[1])
    repaired = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    controls, invalid = repaired._controls()
    assert invalid == 0 and len(controls) == 2 and controls[1]["version"] > controls[0]["version"]
    assert "EMERGENCY_WAL_CONTROL_RECONSTRUCTED" in repaired.status()["alarms"] and calls

def test_defer_repairs_single_control_before_mutating_header(tmp_path):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=2)
    with wal.control_path.open("r+b") as handle:
        handle.seek(CONTROL_SLOT_BYTES); handle.write(b"bad")
    wal.defer(ledger="execution", record_id="one", payload=b"one")
    controls, invalid = wal._controls()
    assert invalid == 0 and len(controls) == 2
    assert "EMERGENCY_WAL_CONTROL_COPY_CORRUPT" in wal.status()["alarms"]

def test_ack_is_hard_noop_without_concrete_persisted_bridge_receipt(tmp_path):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    generation = wal.defer(ledger="execution", record_id="terminal:1", payload=b"row")["generation"]
    before = wal.header_path.read_bytes()
    with pytest.raises(RuntimeError, match="ACK_MISSING"): wal.acknowledge(generation, {"authenticated":True})
    with pytest.raises(RuntimeError, match="RESET_BRIDGE_UNAVAILABLE"): wal.reset_alarm({"authenticated":True})
    assert wal.header_path.read_bytes() == before and wal.status()["deferred_count"] == 1

def test_oldest_record_is_verified_and_in_durable_admission_order(tmp_path):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=3)
    first = wal.defer(ledger="lifecycle", record_id="terminal:oldest", payload=b"one")
    wal.defer(ledger="execution", record_id="terminal:newer", payload=b"two")
    oldest = wal.oldest_record()
    assert oldest["generation"] == first["generation"]
    assert oldest["payload"] == b"one"
    assert oldest["sequence"] < max(row["sequence"] for row in wal.status()["records"])

def test_mark_replayed_requires_exact_canonical_receipt_and_oldest_generation(tmp_path):
    wal_root = tmp_path / "v3" / "emergency_evidence_wal_v2"
    wal = EmergencyEvidenceWal(wal_root, identity=IDENTITY, extents=2)
    payload = b'{"record_id":"terminal:oldest"}\n'
    first = wal.defer(ledger="lifecycle", record_id="terminal:oldest", payload=payload)
    second = wal.defer(ledger="execution", record_id="terminal:newer", payload=b"newer\n")
    ledgers = tmp_path / "v3" / "ledgers"; ledgers.mkdir(parents=True)
    receipts = tmp_path / "v3" / "receipts"; receipts.mkdir(parents=True)
    ledger = ledgers / "lifecycle.jsonl"; ledger.write_bytes(payload)
    receipt = receipts / "oldest.json"
    receipt.write_text(json.dumps({
        "schema": "emergency_record_idempotency_v1", "state": "COMMITTED",
        "ledger": "lifecycle", "record_id": "terminal:oldest",
        "row_sha256": hashlib.sha256(payload).hexdigest(), "offset": 0,
        "length": len(payload), "identity": IDENTITY,
    }), encoding="utf-8")
    with pytest.raises(RuntimeError, match="OUT_OF_ORDER"):
        wal.mark_replayed(second["generation"], canonical_ledger=ledger, canonical_receipt=receipt)
    replayed = wal.mark_replayed(first["generation"], canonical_ledger=ledger, canonical_receipt=receipt)
    assert replayed["state"] == "REPLAYED" and replayed["duplicate"] is False
    assert wal.mark_replayed(first["generation"], canonical_ledger=ledger, canonical_receipt=receipt)["duplicate"] is True
    assert EmergencyEvidenceWal(wal_root, identity=IDENTITY, extents=2).oldest_record()["state"] == "REPLAYED"

def test_release_requires_exact_persisted_lifecycle_ack_and_two_clear_leases(tmp_path):
    wal_root = tmp_path / "v3" / "emergency_evidence_wal_v2"
    wal = EmergencyEvidenceWal(wal_root, identity=IDENTITY, extents=1)
    payload = b'{"record_id":"terminal:release"}\n'
    deferred = wal.defer(ledger="lifecycle", record_id="terminal:release", payload=payload)
    ledgers = tmp_path / "v3" / "ledgers"; ledgers.mkdir(parents=True)
    receipts = tmp_path / "v3" / "receipts"; receipts.mkdir(parents=True)
    ledger = ledgers / "lifecycle.jsonl"; ledger.write_bytes(payload)
    receipt_path = receipts / "release.json"
    receipt_path.write_text(json.dumps({
        "schema": "emergency_record_idempotency_v1", "state": "COMMITTED",
        "ledger": "lifecycle", "record_id": "terminal:release",
        "row_sha256": hashlib.sha256(payload).hexdigest(), "offset": 0,
        "length": len(payload), "identity": IDENTITY,
    }), encoding="utf-8")
    replayed = wal.mark_replayed(deferred["generation"], canonical_ledger=ledger, canonical_receipt=receipt_path)
    clear = {key: [] for key in ("runtime", "sync", "analyzer", "lifecycle_worker")}
    proof = {
        "schema": "emergency_wal_lifecycle_release_ack_v1",
        "source_cleanup_authorized": True, "generation": deferred["generation"],
        "identity": IDENTITY, "ledger": "lifecycle", "record_id": "terminal:release",
        "row_sha256": deferred["row_sha256"],
        "replay_receipt_sha256": replayed["replay_receipt_sha256"],
        "manifest_sha256": "c" * 64, "cleanup_transaction_sha256": "d" * 64,
        "lifecycle_ack_sha256": "f" * 64, "config_signature": "1" * 64,
        "bundle_id": "lifecycle-" + "e" * 64,
        "lifecycle_id": "episode|policy|lane",
        "lease_snapshot_before": clear, "lease_snapshot_after": clear,
    }
    proof["binding_sha256"] = hashlib.sha256(json.dumps(proof, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    ack_dir = tmp_path / "v3" / "emergency_wal_release_acks"; ack_dir.mkdir()
    ack = ack_dir / f'{deferred["generation"]}.json'; ack.write_text(json.dumps(proof), encoding="utf-8")
    assert wal.acknowledge(deferred["generation"], {})["released"] is True
    assert wal.status()["free_extents"] == 1
    with pytest.raises(RuntimeError, match="STALE_OR_OUT_OF_ORDER"):
        wal.acknowledge(deferred["generation"], {})

def test_release_rejects_active_lease_and_forged_binding(tmp_path):
    ack_dir = tmp_path / "v3" / "emergency_wal_release_acks"; ack_dir.mkdir(parents=True)
    wal = EmergencyEvidenceWal(tmp_path / "v3" / "emergency_evidence_wal_v2", identity=IDENTITY, extents=1)
    generation = wal.defer(ledger="lifecycle", record_id="terminal:blocked", payload=b"row\n")["generation"]
    proof = {"schema": "emergency_wal_lifecycle_release_ack_v1", "binding_sha256": "0" * 64}
    (ack_dir / f"{generation}.json").write_text(json.dumps(proof), encoding="utf-8")
    before = wal.header_path.read_bytes()
    with pytest.raises(RuntimeError, match="ACK_INVALID"):
        wal.acknowledge(generation, {})
    assert wal.header_path.read_bytes() == before

def test_required_identity_and_rollover_fail_closed(tmp_path):
    for key in IDENTITY:
        bad = dict(IDENTITY); bad.pop(key)
        with pytest.raises(ValueError, match="IDENTITY_INVALID"): EmergencyEvidenceWal(tmp_path/key, identity=bad)
    wal = EmergencyEvidenceWal(tmp_path/"roll", identity=IDENTITY, extents=1)
    wal.defer(ledger="execution", record_id="terminal:1", payload=b"row")
    changed = dict(IDENTITY, epoch_id="epoch-2")
    with pytest.raises(RuntimeError, match="HEADER_INVALID"): EmergencyEvidenceWal(tmp_path/"roll", identity=changed, extents=1)

@pytest.mark.parametrize("key,value", [
    ("epoch_id", " epoch-1"), ("epoch_id", "UNKNOWN"),
    ("source_revision", "NOT_DEPLOYED_LOCAL"), ("deployed_revision", "abc"),
    ("tile_config_signature", "z"*64),
])
def test_provenance_rejects_whitespace_control_sentinel_and_bad_format(tmp_path, key, value):
    bad=dict(IDENTITY); bad[key]=value
    with pytest.raises(ValueError, match="IDENTITY_INVALID"): EmergencyEvidenceWal(tmp_path, identity=bad)

def test_record_identity_rejects_controls_whitespace_and_bad_ledger(tmp_path):
    wal=EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    for ledger, record in (("Bad Ledger","ok"),("execution"," bad"),("execution","bad\n")):
        with pytest.raises(ValueError, match="RECORD_INVALID"): wal.defer(ledger=ledger, record_id=record, payload=b"x")

def test_symlink_artifacts_are_refused(tmp_path):
    target = tmp_path/"target"; target.mkdir(); link = tmp_path/"link"
    try: link.symlink_to(target, target_is_directory=True)
    except OSError: pytest.skip("symlinks unavailable")
    with pytest.raises(RuntimeError, match="SYMLINK_REFUSED"): EmergencyEvidenceWal(link, identity=IDENTITY)

def test_symlink_reserve_file_is_refused(tmp_path):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    target = tmp_path/"real-data"; wal.data_path.replace(target)
    try: wal.data_path.symlink_to(target)
    except OSError: pytest.skip("symlinks unavailable")
    with pytest.raises(RuntimeError, match="SYMLINK_REFUSED"):
        EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)

def test_capacity_alarm_persists_and_no_extent_is_released(tmp_path):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1); wal.defer(ledger="execution", record_id="one", payload=b"1")
    with pytest.raises(RuntimeError, match="CAPACITY_EXHAUSTED"): wal.defer(ledger="execution", record_id="two", payload=b"2")
    assert "EMERGENCY_WAL_CAPACITY_EXHAUSTED" in EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1).status()["alarms"]

def test_cross_process_serialization_and_bounded_timeout(tmp_path):
    module_dir = str(Path(__file__).parent); lock = tmp_path/"held.lock"; ready = tmp_path/"ready"
    code = ("import sys,time;sys.path.insert(0,sys.argv[1]);from pathlib import Path;"
            "from emergency_evidence_wal import _cross_process_lock;"
            "exec(\"with _cross_process_lock(Path(sys.argv[2]),timeout=2):\\n Path(sys.argv[3]).write_text('1')\\n time.sleep(.7)\")")
    holder = subprocess.Popen([sys.executable,"-c",code,module_dir,str(lock),str(ready)])
    deadline=time.monotonic()+3
    while not ready.exists() and time.monotonic()<deadline: time.sleep(.01)
    assert ready.exists(); started=time.monotonic()
    with pytest.raises(TimeoutError, match="LOCK_TIMEOUT"):
        with _cross_process_lock(lock, timeout=.1): pass
    assert time.monotonic()-started < .6 and holder.wait(timeout=3)==0

@pytest.mark.parametrize("timeout,poll", [
    (float("nan"), .01), (float("inf"), .01), (float("-inf"), .01),
    (61, .01), (1, float("nan")), (1, float("inf")), (1, 1.1),
])
def test_lock_rejects_nonfinite_or_unbounded_numeric_configuration(tmp_path, timeout, poll):
    with pytest.raises(ValueError, match="LOCK_TIMEOUT_INVALID"):
        with _cross_process_lock(tmp_path/"lock", timeout=timeout, poll_interval=poll): pass

@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), float("-inf"), 61])
def test_constructor_rejects_nonfinite_or_unbounded_lock_timeout(tmp_path, timeout):
    with pytest.raises(ValueError, match="CONFIGURATION_INVALID"):
        EmergencyEvidenceWal(tmp_path, identity=IDENTITY, lock_timeout=timeout)
