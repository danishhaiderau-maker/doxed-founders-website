import hashlib
import json
from pathlib import Path


from recover_archived_future_paths import recover_archived_future_paths
from research_v3_store import V3EvidenceStore


EPOCH = "epoch-recovery"
START = 1_700_000_000.0


def _fixture(tmp_path: Path, *, gap=False, incomplete=False):
    root = tmp_path / "canonical-research-data"
    archive = root / "archive" / "sync-retired" / "receipt-1"
    archive.mkdir(parents=True)
    tape = archive / "market_microstructure_1s.jsonl.1"
    end = 7200 if not incomplete else 6000
    offsets = list(range(0, end + 1, 2))
    if gap:
        offsets = [value for value in offsets if not 3000 <= value <= 3010]
    tape.write_text("".join(json.dumps({
        "bucket_ts": START + offset, "last": 80_000,
        "bid": 79_999, "ask": 80_001, "bid_qty": 1, "ask_qty": 1,
    }, sort_keys=True) + "\n" for offset in offsets), encoding="utf-8")
    relative = tape.relative_to(root).as_posix()
    Path(str(tape) + ".receipt.json").write_text(json.dumps({
        "schema": "canonical_research_cleanup_receipt_v1",
        "reason": "ABSENT_FROM_AUTHENTICATED_FLY_MANIFEST",
        "source_relative": "market_microstructure_1s.jsonl.1",
        "archive_relative": relative,
        "recoverable": True,
    }), encoding="utf-8")
    store = V3EvidenceStore(root, epoch_id=EPOCH)
    store.append("opportunity", {
        "record_id": "opportunity-1", "episode_id": "episode-1",
        "opportunity_id": "opportunity-1", "signal_ts": START,
        "symbol": "BTCUSD",
    })
    store.append("decision", {
        "record_id": "decision-1", "episode_id": "episode-1",
        "opportunity_id": "opportunity-1", "event_id": "event-1",
        "policy_signature": "policy-1", "primary_outcome": "REJECTED",
        "outcome_state": "REJECTED", "order_intent_expected": False,
    })
    store.append("market_segment", {
        "record_id": "old-unknown", "episode_id": "episode-1",
        "opportunity_id": "opportunity-1", "decision_id": "decision-1",
        "future_path_owner_key": "owner-1",
        "segment_role": "SIGNAL_TO_120M_FUTURE_PATH",
        "requested_start_ts": START, "requested_end_ts": START + 7200,
        "future_path_status": "UNKNOWN", "segment_ref": None,
    })
    digest = hashlib.sha256(tape.read_bytes()).hexdigest()
    return root, tape, tape.stat().st_size, digest


def _recover(root, tape, size, digest, *, apply=False):
    return recover_archived_future_paths(
        canonical_root=root, archive_tape=tape, epoch_id=EPOCH,
        expected_size=size, expected_sha256=digest, apply=apply,
    )


def test_retired_r17_apply_writes_nothing(tmp_path):
    """R17: market_segments writers are retired; recovery (even apply) is a no-op."""
    root, tape, size, digest = _fixture(tmp_path)
    ledger = root / "v3" / "ledgers" / "market_segment.jsonl"
    before = ledger.read_bytes()
    for apply in (False, True):
        result = _recover(root, tape, size, digest, apply=apply)
        assert result["status"] == "disabled: R17"
        assert result["retired"] == "R17"
        assert result["recovered_count"] == 0
        assert result["status_receipt_written"] is False
    assert ledger.read_bytes() == before
    assert not list((root / "v3" / "receipts").glob("archive-future-path-recovery-*.json"))
    assert not list((root / "v3" / "market_segments").glob("*/*.json"))
