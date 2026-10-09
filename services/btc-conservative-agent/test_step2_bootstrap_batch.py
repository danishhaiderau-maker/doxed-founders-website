"""Step 2: batched bootstrap durability and write-set verification on hot paths."""
import inspect
import json
import time

import collector_storage
import research_v3_bridge as bridge
import research_v3_store
from research_v3_store import V3EvidenceStore


def _store(tmp_path, monkeypatch, rows):
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(collector_storage, "disk_usage_fraction", lambda _path=None: 0.5)
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-1")
    path = store.ledger_path("decision")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({"record_id": f"decision:h:{i}", "episode_id": "a"}) + "\n"
                            for i in range(rows)), "utf-8")
    return store


def test_batch_uses_one_directory_fsync_per_step(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch, 200)
    dir_fsyncs = []
    real = research_v3_store._fsync_directory
    monkeypatch.setattr(research_v3_store, "_fsync_directory", lambda d: dir_fsyncs.append(d) or real(d))
    file_fsyncs = []
    real_fsync = research_v3_store.os.fsync
    monkeypatch.setattr(research_v3_store.os, "fsync", lambda fd: file_fsyncs.append(fd) or real_fsync(fd))
    result = store.advance_emergency_idempotency_bootstrap("decision", max_records=512)
    assert result["complete"] is True and result["records_indexed"] == 200
    # every receipt file is still fsynced before its rename...
    assert len(file_fsyncs) >= 200
    # ...but the receipt directory barrier is paid once per batch, not per row
    # (+ cursor and completeness receipts).
    assert len(dir_fsyncs) <= 8
    for i in range(200):
        assert store._record_receipt_path("decision", f"decision:h:{i}").exists()


def test_deadline_stops_the_step_and_cursor_resumes(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch, 50)
    first = store.advance_emergency_idempotency_bootstrap(
        "decision", max_records=512, deadline_monotonic=time.monotonic() - 1.0,
    )
    assert first["complete"] is False and first["records_indexed"] == 1  # always progresses
    state = json.loads(store._bootstrap_path("decision").read_text("utf-8"))
    assert state["cursor"] == first["cursor"] > 0
    second = store.advance_emergency_idempotency_bootstrap("decision", max_records=512)
    assert second["complete"] is True and second["records_indexed"] == 49


def test_batch_failure_never_moves_the_cursor(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch, 10)
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 5:
            raise OSError("disk hiccup")
        return original(*args, **kwargs)

    original = store._publish_record_receipt
    monkeypatch.setattr(store, "_publish_record_receipt", flaky)
    result = store.advance_emergency_idempotency_bootstrap("decision", max_records=512)
    assert result == {"complete": False, "blocked": True, "reason": "disk hiccup", "cursor": 0}
    assert not store._bootstrap_path("decision").exists()
    monkeypatch.setattr(store, "_publish_record_receipt", original)
    assert store.advance_emergency_idempotency_bootstrap("decision", max_records=512)["complete"]


class _FakeStore:
    def __init__(self):
        self.calls = []

    def verify(self):
        raise AssertionError("full store verify on a hot path")

    def verify_write_set(self, *, ledgers, segment_refs=()):
        self.calls.append((tuple(ledgers), list(segment_refs)))
        return {"ok": True, "full_store_verified": False}


def test_write_set_verification_only_touches_written_objects():
    store = _FakeStore()
    ref = {"sha256": "a" * 64, "path": "x"}
    out = bridge._write_set_verification(
        store, [{"ledger": "execution"}, {"ledger": "lifecycle"}, {"ledger": "execution"}, {}],
        segment_refs=[ref, {"no": "sha"}],
    )
    assert out["full_store_verified"] is False
    assert store.calls == [(("execution", "lifecycle"), [ref])]


def test_hot_path_writers_never_run_a_full_store_verify():
    for name in ("dual_write_paper_order_intent", "dual_write_paper_fill", "dual_write_paper_close",
                 "dual_write_lifecycle_qualification_horizon", "dual_write_v22_record"):
        source = inspect.getsource(getattr(bridge, name))
        assert "store.verify()" not in source, name
        assert "_write_set_verification(" in source, name
