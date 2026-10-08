"""Signed replay contract for batched directory-fsync receipt bootstrap.

The bootstrap hot path publishes one idempotency receipt per ledger row. Before
this change, every publication ran a directory fsync (``_fsync_directory``) per
row inside ``_atomic_json_receipt``. The speedup keeps the per-file content
fsync (fsync BEFORE ``os.replace``) and batches the directory barrier into one
fsync per bootstrap step. Receipt OUTPUT must be byte-for-byte identical to the
pre-batching behavior, and a simulated mid-batch crash must resume idempotently.
"""
from __future__ import annotations

import hashlib
import json

import pytest

import research_v3_store as store_module
from research_v3_store import V3EvidenceStore

LEDGER = "decision"
RECEIPT_SUBDIR = "emergency_record_idempotency_v1"


def _make_store(root, monkeypatch) -> V3EvidenceStore:
    monkeypatch.setattr(
        store_module, "storage_blocks_new_nonessential_research", lambda *_: False,
    )
    monkeypatch.setattr(
        V3EvidenceStore, "_emergency_wal_identity_available", lambda _: False,
    )
    return V3EvidenceStore(root, epoch_id="replay-epoch")


def _seed_rows(store: V3EvidenceStore, rows: int) -> None:
    """Seed a small synthetic JSONL ledger with representative rows."""
    lines = [
        json.dumps(
            {
                "record_id": f"record-{number}",
                "episode_id": f"episode-{number % 7}",
                "direction": "long" if number % 2 else "short",
                "confidence": round(0.1 + (number % 10) / 20.0, 3),
                "timestamp_utc": f"2026-10-0{1 + number % 6}T00:00:00Z",
            },
            sort_keys=True,
        )
        + "\n"
        for number in range(rows)
    ]
    store.ledger_path(LEDGER).write_text("".join(lines), encoding="utf-8")


def _record_receipts(root) -> dict[str, bytes]:
    """Return {relative_path: raw_bytes} for every per-row record receipt.

    Checkpoint files (``bootstrap.json`` / ``complete.json``) embed the ledger
    inode/device signature and are deliberately excluded: those fields differ
    across physical roots while the per-row receipts do not.
    """
    base = root / "v3" / "receipts" / RECEIPT_SUBDIR / LEDGER
    if not base.exists():
        return {}
    receipts: dict[str, bytes] = {}
    for path in sorted(base.iterdir()):
        if path.is_file() and path.name not in {"bootstrap.json", "complete.json"}:
            receipts[path.name] = path.read_bytes()
    return receipts


def _record_receipt_fields(root) -> dict[str, dict]:
    """Return parsed record-receipt fields keyed by filename for field checks."""
    result: dict[str, dict] = {}
    for name, raw in _record_receipts(root).items():
        result[name] = json.loads(raw.decode("utf-8"))
    return result


def test_batched_fsync_receipts_byte_identical_to_unbatched(tmp_path, monkeypatch):
    """(a-d) NEW batched output is byte-for-byte identical to OLD per-row fsync."""
    rows = 40

    # OLD reference: force every per-row publish to run its own directory fsync,
    # replicating the pre-batching durability barrier.
    old_root = tmp_path / "old"
    store_old = _make_store(old_root, monkeypatch)
    _seed_rows(store_old, rows)
    original_publish = store_old._publish_record_receipt

    def force_per_row_fsync(*args, **kwargs):
        kwargs["fsync_dir"] = True
        return original_publish(*args, **kwargs)

    monkeypatch.setattr(store_old, "_publish_record_receipt", force_per_row_fsync)
    old_result = store_old.advance_emergency_idempotency_bootstrap(LEDGER)
    assert old_result["complete"] is True
    assert old_result["records_indexed"] == rows

    # NEW batched behavior.
    new_root = tmp_path / "new"
    store_new = _make_store(new_root, monkeypatch)
    _seed_rows(store_new, rows)
    new_result = store_new.advance_emergency_idempotency_bootstrap(LEDGER)
    assert new_result["complete"] is True
    assert new_result["records_indexed"] == rows

    old_receipts = _record_receipts(old_root)
    new_receipts = _record_receipts(new_root)

    # Same file count and byte-for-byte identical receipt bytes.
    assert len(old_receipts) == len(new_receipts) == rows
    assert old_receipts == new_receipts

    # Field-level integrity: same row_sha256 / identity / offset / length.
    old_fields = _record_receipt_fields(old_root)
    new_fields = _record_receipt_fields(new_root)
    assert set(old_fields) == set(new_fields)
    for name in old_fields:
        old = old_fields[name]
        new = new_fields[name]
        assert old == new
        assert old["row_sha256"] == new["row_sha256"]
        assert old["identity"] == new["identity"]
        assert old["offset"] == new["offset"]
        assert old["length"] == new["length"]

    # Independent ground truth: each receipt's row_sha256 matches the sha256 of
    # the exact ledger line at its offset/length.
    ledger_bytes = store_new.ledger_path(LEDGER).read_bytes()
    for name in new_fields:
        receipt = new_fields[name]
        row_bytes = ledger_bytes[receipt["offset"]:receipt["offset"] + receipt["length"]]
        assert hashlib.sha256(row_bytes).hexdigest() == receipt["row_sha256"]


def test_batching_reduces_directory_fsync_barriers(tmp_path, monkeypatch):
    """(e) Directory fsync is batched to O(1) per step, not one per row."""
    rows = 40
    observed: list = []

    monkeypatch.setattr(
        store_module,
        "_fsync_directory",
        lambda path: observed.append(str(path)),
    )

    new_root = tmp_path / "new"
    store_new = _make_store(new_root, monkeypatch)
    _seed_rows(store_new, rows)
    result = store_new.advance_emergency_idempotency_bootstrap(LEDGER)
    assert result["complete"] is True

    # Record batch + bootstrap checkpoint + completeness checkpoint = 3.
    assert len(observed) == 3, observed


def test_mid_batch_crash_resumes_idempotently(tmp_path, monkeypatch):
    """(e) A simulated mid-batch crash resumes to byte-identical receipts."""
    rows = 40
    crash_at = 17

    ref_root = tmp_path / "ref"
    store_ref = _make_store(ref_root, monkeypatch)
    _seed_rows(store_ref, rows)
    assert store_ref.advance_emergency_idempotency_bootstrap(LEDGER)["complete"] is True

    crash_root = tmp_path / "crash"
    store_crash = _make_store(crash_root, monkeypatch)
    _seed_rows(store_crash, rows)

    original_publish = store_crash._publish_record_receipt
    state = {"calls": 0, "armed": True}

    def crash_mid_batch(*args, **kwargs):
        state["calls"] += 1
        if state["armed"] and state["calls"] == crash_at:
            raise OSError("simulated mid-batch crash")
        return original_publish(*args, **kwargs)

    monkeypatch.setattr(store_crash, "_publish_record_receipt", crash_mid_batch)

    crashed = store_crash.advance_emergency_idempotency_bootstrap(LEDGER)
    assert crashed["complete"] is False
    assert crashed["blocked"] is True
    # crash_at - 1 rows were renamed (not dir-fsynced) before the interruption:
    # the blocked result's cursor advanced exactly that far into the ledger.
    ledger_bytes = store_crash.ledger_path(LEDGER).read_bytes()
    expected_cursor = sum(
        len(line) for line in ledger_bytes.splitlines(keepends=True)[: crash_at - 1]
    )
    assert crashed["cursor"] == expected_cursor
    assert 0 < crashed["cursor"] < len(ledger_bytes)

    # Resume: re-arm removed, re-run the same store (process restart equivalent).
    state["armed"] = False
    resumed = store_crash.advance_emergency_idempotency_bootstrap(LEDGER)
    assert resumed["complete"] is True
    assert resumed["records_indexed"] == rows

    assert _record_receipts(crash_root) == _record_receipts(ref_root)
