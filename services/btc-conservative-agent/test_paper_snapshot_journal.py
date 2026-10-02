import errno
import json
import pytest
from paper_snapshot_journal import PaperSnapshotJournal
from relay_event_outbox import RelayEventOutbox


def state(generation, positions):
    return {"schema": "paper_lifecycle_v1", "paper_only": True, "live_armed": False,
            "generation": generation, "positions": positions}


def test_enospc_close_has_durable_effective_postimage_and_immutable_old_inode(tmp_path, monkeypatch):
    path = tmp_path / "paper.json"
    outbox = RelayEventOutbox(path)
    outbox._atomic_write(state(1, [{"trade_id": "a"}]))
    outbox.enable_protective_journal(4096, epoch_id="epoch-e1")
    outbox.assert_entry_snapshot_budget(outbox.read_snapshot(), 1024)
    before = path.read_bytes()
    original = outbox._atomic_write_canonical
    monkeypatch.setattr(outbox, "_atomic_write_canonical", lambda v: (_ for _ in ()).throw(OSError(errno.ENOSPC, "full")))
    outbox._atomic_write(state(2, []))
    assert outbox.read_snapshot()["positions"] == []
    assert path.read_bytes() == before and outbox.snapshot_publication_pending
    with pytest.raises(RuntimeError, match="PUBLICATION_PENDING"):
        outbox.assert_entry_snapshot_budget(state(3, [{"trade_id": "b"}]), 1024)
    restarted = RelayEventOutbox(path)
    restarted.enable_protective_journal(4096, epoch_id="epoch-e1")
    assert restarted.read_snapshot()["positions"] == []
    assert json.loads(path.read_text())["generation"] == 2
    monkeypatch.setattr(outbox, "_atomic_write_canonical", original)


def test_partial_inactive_slot_write_keeps_previous_commit(tmp_path, monkeypatch):
    journal = PaperSnapshotJournal(tmp_path / "paper.json", 4096, epoch_id="epoch-e1")
    journal.commit(state(1, []), predecessor_sha256="a" * 64)
    real = journal._write_all
    calls = []
    def fail(handle, data):
        calls.append(1)
        if len(calls) == 2:
            raise OSError(errno.ENOSPC, "injected payload failure")
        return real(handle, data)
    monkeypatch.setattr(journal, "_write_all", fail)
    with pytest.raises(OSError):
        journal.commit(state(2, []), predecessor_sha256="a" * 64)
    assert PaperSnapshotJournal(tmp_path / "paper.json", 4096, epoch_id="epoch-e1").latest()["value"]["generation"] == 1


def test_bounded_entry_budget(tmp_path):
    outbox = RelayEventOutbox(tmp_path / "paper.json")
    outbox._atomic_write(state(1, []))
    outbox.enable_protective_journal(4096, epoch_id="epoch-e1")
    with pytest.raises(RuntimeError, match="BYTE_CAPACITY"):
        outbox.assert_entry_snapshot_budget(state(2, []), 4096)


@pytest.mark.parametrize("change", ["epoch", "predecessor"])
def test_old_journal_never_resurrects_pre_reset_positions(tmp_path, monkeypatch, change):
    path = tmp_path / "paper.json"
    outbox = RelayEventOutbox(path)
    outbox._atomic_write(state(1, []))
    outbox.enable_protective_journal(4096, epoch_id="epoch-old")
    monkeypatch.setattr(outbox, "_atomic_write_canonical", lambda v: (_ for _ in ()).throw(OSError(errno.ENOSPC, "full")))
    outbox._atomic_write(state(999, [{"trade_id": "obsolete"}]))
    # A separate reset owner replaces canonical state. Never replay old999
    # merely because the reset's generation counter is smaller.
    reset = RelayEventOutbox(path)
    reset._atomic_write(dict(state(1, []), reset_receipt="new"))
    before = path.read_bytes()
    restored = RelayEventOutbox(path)
    with pytest.raises(RuntimeError, match="EPOCH_MISMATCH" if change == "epoch" else "PREDECESSOR_MISMATCH"):
        restored.enable_protective_journal(4096, epoch_id="epoch-new" if change == "epoch" else "epoch-old")
    assert path.read_bytes() == before
    assert json.loads(before)["positions"] == []


def test_stale_writer_cannot_replace_pending_protective_postimage(tmp_path, monkeypatch):
    path = tmp_path / "paper.json"
    outbox = RelayEventOutbox(path)
    outbox._atomic_write(state(1, [{"trade_id": "a"}]))
    outbox.enable_protective_journal(4096, epoch_id="epoch-e1")
    monkeypatch.setattr(outbox, "_atomic_write_canonical", lambda v: (_ for _ in ()).throw(OSError(errno.ENOSPC, "full")))
    outbox._atomic_write(state(2, []))
    with pytest.raises(RuntimeError, match="STALE_POSTIMAGE"):
        outbox._atomic_write(state(2, [{"trade_id": "a"}]))
    assert outbox.read_snapshot()["positions"] == []


def test_constructor_loads_effective_pending_and_ack_state(tmp_path, monkeypatch):
    path = tmp_path / "paper.json"
    original = RelayEventOutbox(path)
    original._atomic_write(state(1, [{"trade_id": "a"}]))
    original.enable_protective_journal(8192, epoch_id="epoch-e1")
    monkeypatch.setattr(original, "_atomic_write_canonical", lambda v: (_ for _ in ()).throw(OSError(errno.ENOSPC, "full")))
    target = dict(state(2, []), relay_events={
        "pending": [{"event_id": "close-a", "trade_id": "a", "event_seq": 3}],
        "acks": [{"event_id": "fill-a", "trade_id": "a", "event_seq": 2}],
        "sequence_highwater": {"a": 3}})
    original._atomic_write(target)
    restarted = RelayEventOutbox(path, protective_journal_bytes=8192, protective_epoch_id="epoch-e1")
    assert restarted.read_snapshot() == target
    assert list(restarted._pending) == ["close-a"]
    assert restarted._acks[0]["event_id"] == "fill-a"
    assert restarted._highwater == {"a": 3}
    assert restarted.snapshot_publication_pending


def test_constructor_rejects_reset_before_canonical_wal_recovery(tmp_path, monkeypatch):
    path = tmp_path / "paper.json"
    original = RelayEventOutbox(path)
    original._atomic_write(state(1, []))
    original.enable_protective_journal(8192, epoch_id="epoch-old")
    monkeypatch.setattr(original, "_atomic_write_canonical", lambda v: (_ for _ in ()).throw(OSError(errno.ENOSPC, "full")))
    original._atomic_write(state(2, [{"trade_id": "obsolete"}]))
    # Simulate an independently published reset carrying a canonical WAL.
    reset = RelayEventOutbox(path)
    reset._atomic_write(dict(state(1, []), transition_wal={"reset": True}))
    before = path.read_bytes()
    calls = []
    monkeypatch.setattr(RelayEventOutbox, "_recover_prepared_value", lambda *a: calls.append("recovery"))
    with pytest.raises(RuntimeError, match="EPOCH_MISMATCH"):
        RelayEventOutbox(path, protective_journal_bytes=8192, protective_epoch_id="epoch-new")
    assert calls == []
    assert path.read_bytes() == before
