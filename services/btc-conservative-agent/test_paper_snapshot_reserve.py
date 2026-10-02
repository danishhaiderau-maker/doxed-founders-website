import json
import threading
import pytest
import paper_snapshot_reserve as module
from relay_event_outbox import RelayEventOutbox


def test_alternates_preallocated_slots_with_compatible_json(tmp_path, monkeypatch):
    path = tmp_path / "paper.json"
    reserve = module.PaperSnapshotReserve(path, 4096)
    reserve.provision()
    identities = {slot.stat().st_ino for slot in reserve.slots}
    monkeypatch.setattr(module, "_allocate_file", lambda *a: pytest.fail("unexpected payload allocation"))
    observed = []
    for index in range(4):
        reserve.write({"generation": index})
        assert json.loads(path.read_text()) == {"generation": index}
        observed.append(path.stat().st_ino)
        assert path.stat().st_size == 4096
    assert set(observed) == identities
    assert observed[0] == observed[2] and observed[1] == observed[3]


def test_failed_publication_keeps_old_snapshot_and_retry_recovers(tmp_path, monkeypatch):
    path = tmp_path / "paper.json"
    reserve = module.PaperSnapshotReserve(path, 4096)
    reserve.write({"generation": 1})
    original = module.os.replace
    monkeypatch.setattr(module.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("metadata full")))
    with pytest.raises(OSError):
        reserve.write({"generation": 2})
    assert json.loads(path.read_text())["generation"] == 1
    monkeypatch.setattr(module.os, "replace", original)
    module.PaperSnapshotReserve(path, 4096).write({"generation": 2})
    assert json.loads(path.read_text())["generation"] == 2


def test_oversize_does_not_touch_existing_snapshot(tmp_path):
    path = tmp_path / "paper.json"
    reserve = module.PaperSnapshotReserve(path, 4096)
    reserve.write({"generation": 1})
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="CAPACITY_EXCEEDED"):
        reserve.write({"payload": "x" * 4096})
    assert path.read_bytes() == before


def test_outbox_opt_in_requires_reader_lease_and_preserves_format(tmp_path):
    path = tmp_path / "paper.json"
    with pytest.raises(ValueError, match="READER_LEASE"):
        RelayEventOutbox(path, snapshot_reserve_bytes=4096)
    outbox = RelayEventOutbox(path, shared_lock=threading.RLock(), snapshot_reserve_bytes=4096,
                             readers_share_generation_lease=True)
    outbox._atomic_write({"schema": "paper_lifecycle_v1", "generation": 1})
    assert RelayEventOutbox(path).healthy
    assert json.loads(path.read_text())["generation"] == 1


def test_leased_copy_preserves_old_generation_across_slot_reuse(tmp_path):
    outbox = RelayEventOutbox(tmp_path / "paper.json", shared_lock=threading.RLock(),
        snapshot_reserve_bytes=4096, readers_share_generation_lease=True)
    outbox._atomic_write({"generation": 1})
    first = outbox.read_snapshot()
    outbox._atomic_write({"generation": 2})
    outbox._atomic_write({"generation": 3})
    assert first == {"generation": 1}
    assert outbox.read_snapshot() == {"generation": 3}


def test_characterizes_unleased_handle_incompatible_with_slot_reuse(tmp_path):
    # This is the concrete reason production opt-in is not yet authorized.
    reserve = module.PaperSnapshotReserve(tmp_path / "paper.json", 4096)
    reserve.write({"generation": 1})
    with reserve.path.open("rb", buffering=0) as old_reader:
        if module.os.name == "nt":
            with pytest.raises(PermissionError):
                reserve.write({"generation": 2})
            assert json.loads(old_reader.read())["generation"] == 1
            return
        reserve.write({"generation": 2})
        reserve.write({"generation": 3})
        assert json.loads(old_reader.read())["generation"] == 3
