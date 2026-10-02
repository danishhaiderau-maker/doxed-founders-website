"""Prove pre-append ENOSPC preserves incoming evidence without replacing head."""
import errno
import json
from pathlib import Path

import pytest

from test_emergency_wal_runtime_integration import _production_identity, _storage_fraction, _store


def row(name):
    return {"record_id": f"lifecycle:{name}:terminal", "episode_id": name,
            "terminal": True, "outcome_state": "NO_FILL"}


@pytest.mark.parametrize("code", [errno.ENOSPC, errno.EACCES])
def test_bootstrap_receipt_failure_preserves_enospc_incoming_row(tmp_path, monkeypatch, code):
    _production_identity(monkeypatch); _storage_fraction(monkeypatch, .50)
    store = _store(tmp_path, monkeypatch)
    original = store._atomic_json_receipt
    injected = []
    def fail(path, payload):
        injected.append(str(path))
        raise OSError(code, "synthetic bootstrap receipt failure")
    monkeypatch.setattr(store, "_atomic_json_receipt", fail)
    if code == errno.ENOSPC:
        assert store.append("lifecycle", row("incoming"))["deferred"]
    else:
        with pytest.raises(OSError) as exc:
            store.append("lifecycle", row("incoming"))
        assert exc.value.errno == code
    assert injected
    assert store._emergency_wal().status()["deferred_count"] == (1 if code == errno.ENOSPC else 0)
    assert not store._append_head_path("lifecycle").exists()
    assert not store.ledger_path("lifecycle").exists()
    monkeypatch.setattr(store, "_atomic_json_receipt", original)
    assert store.append("lifecycle", row("incoming"))["written"]
    assert store.append("lifecycle", row("incoming"))["duplicate"]
    assert len(store.ledger_path("lifecycle").read_text().splitlines()) == 1
    if code == errno.ENOSPC:
        assert store.replay_one_emergency_wal_record()["canonical_duplicate"]
        assert len(store.ledger_path("lifecycle").read_text().splitlines()) == 1


@pytest.mark.parametrize("code", [errno.ENOSPC, errno.EACCES])
@pytest.mark.parametrize("incoming", ["original", "different"])
def test_head_recovery_fault_preserves_old_head_and_enospc_incoming(tmp_path, monkeypatch, code, incoming):
    _production_identity(monkeypatch); _storage_fraction(monkeypatch, .50)
    store = _store(tmp_path, monkeypatch)
    path = store.ledger_path("lifecycle")
    original_open = Path.open
    injected = []
    error = [errno.EACCES]
    def fail(p, mode="r", *args, **kwargs):
        if p == path and mode == "ab":
            injected.append(error[0])
            raise OSError(error[0], "synthetic append/recovery failure")
        return original_open(p, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", fail)
    with pytest.raises(OSError):
        store.append("lifecycle", row("original"))
    head = store._append_head_path("lifecycle")
    previous = head.read_bytes()
    assert json.loads(previous)["record_id"] == row("original")["record_id"]
    # Exercise append's explicit head recovery, independently of bootstrap's
    # own recovery traversal (already covered by the bootstrap fault test).
    monkeypatch.setattr(store, "_bootstrap_empty_ledgers", lambda: None)
    error[0] = code
    if code == errno.ENOSPC:
        assert store.append("lifecycle", row(incoming))["deferred"]
    else:
        with pytest.raises(OSError) as exc:
            store.append("lifecycle", row(incoming))
        assert exc.value.errno == code
    assert injected == [errno.EACCES, code]
    assert head.read_bytes() == previous
    assert not path.exists()
    assert store._emergency_wal().status()["deferred_count"] == (1 if code == errno.ENOSPC else 0)
    # Different incoming row is not in canonical receipts; ENOSPC retains it in WAL.
    if incoming == "different":
        assert not store._record_receipt_path("lifecycle", row(incoming)["record_id"]).exists()
    monkeypatch.setattr(Path, "open", original_open)
    result = store.append("lifecycle", row(incoming))
    assert result["written"]
    ids = [json.loads(line)["record_id"] for line in path.read_text().splitlines()]
    assert ids == [row("original")["record_id"]] + ([row("different")["record_id"]] if incoming == "different" else [])
    assert store.append("lifecycle", row(incoming))["duplicate"]
    assert len(path.read_text().splitlines()) == len(ids)
    if code == errno.ENOSPC:
        assert store.replay_one_emergency_wal_record()["canonical_duplicate"]
        assert len(path.read_text().splitlines()) == len(ids)


@pytest.mark.parametrize("stage", ["bootstrap", "head"])
@pytest.mark.parametrize("ineligible", ["optional", "identity", "capacity"])
def test_precatch_fallback_remains_fail_closed(tmp_path, monkeypatch, stage, ineligible):
    _production_identity(monkeypatch); _storage_fraction(monkeypatch, .50)
    store = _store(tmp_path, monkeypatch)
    incoming = row("incoming")
    if ineligible == "optional":
        incoming = {"record_id": "optional", "episode_id": "incoming"}
    if ineligible == "identity":
        monkeypatch.setattr(store, "_emergency_wal_identity_available", lambda: False)
    if ineligible == "capacity":
        wal = store._emergency_wal()
        for i in range(4):
            wal.defer(ledger="lifecycle", record_id=f"full:{i}", payload=b"reserved")
    injected = []
    def fail(*args):
        injected.append(stage)
        raise OSError(errno.ENOSPC, "synthetic preappend failure")
    if stage == "bootstrap":
        monkeypatch.setattr(store, "_bootstrap_empty_ledgers", fail)
    else:
        monkeypatch.setattr(store, "_bootstrap_empty_ledgers", lambda: None)
        monkeypatch.setattr(store, "_recover_append_head", fail)
    with pytest.raises((OSError, RuntimeError)):
        store.append("lifecycle", incoming)
    assert injected == [stage]
    assert store._emergency_wal().status()["deferred_count"] == (4 if ineligible == "capacity" else 0)
    assert not store.ledger_path("lifecycle").exists()
