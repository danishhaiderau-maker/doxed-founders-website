import hashlib
import json
from pathlib import Path

import pytest

from recover_archived_future_paths import recover_archived_future_paths
from research.policy_evidence_bindings import (
    authoritative_future_path_segments, build_v3_binding_index,
)
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


def test_dry_run_is_default_and_writes_nothing(tmp_path):
    root, tape, size, digest = _fixture(tmp_path)
    ledger = root / "v3" / "ledgers" / "market_segment.jsonl"
    before = ledger.read_bytes()
    result = _recover(root, tape, size, digest)
    assert result["mode"] == "DRY_RUN"
    assert result["complete_recovered_count"] == 1
    assert ledger.read_bytes() == before
    assert not list((root / "v3" / "receipts").glob("archive-future-path-recovery-*.json"))
    assert not list((root / "v3" / "market_segments").glob("*/*.json"))


def test_receipt_hash_size_and_containment_fail_closed(tmp_path):
    root, tape, size, digest = _fixture(tmp_path)
    with pytest.raises(ValueError, match="SIZE_MISMATCH"):
        _recover(root, tape, size + 1, digest)
    with pytest.raises(ValueError, match="SHA256_MISMATCH"):
        _recover(root, tape, size, "0" * 64)
    receipt = Path(str(tape) + ".receipt.json")
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["recoverable"] = False
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="NOT_RECOVERABLE"):
        _recover(root, tape, size, digest)
    payload["recoverable"] = True
    payload["archive_relative"] = "archive/sync-retired/wrong/tape.jsonl.1"
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="ARCHIVE_PATH_MISMATCH"):
        _recover(root, tape, size, digest)
    outside = tmp_path / "outside.jsonl"
    outside.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="OUTSIDE_CANONICAL_ROOT"):
        recover_archived_future_paths(
            canonical_root=root, archive_tape=outside, epoch_id=EPOCH,
            expected_size=outside.stat().st_size,
            expected_sha256=hashlib.sha256(outside.read_bytes()).hexdigest(),
        )


def test_apply_is_append_only_idempotent_and_complete(tmp_path):
    root, tape, size, digest = _fixture(tmp_path)
    first = _recover(root, tape, size, digest, apply=True)
    ledger = root / "v3" / "ledgers" / "market_segment.jsonl"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert first["complete_recovered_count"] == 1
    assert first["conservative_eligible_complete_count"] == 1
    assert len(rows) == 2
    recovered = rows[-1]
    assert recovered["future_path_status"] == "COMPLETE"
    assert recovered["evidence_provenance"] == "ARCHIVED_FLY_MIRROR_RECOVERY"
    assert recovered["supersedes_record_ids"] == ["old-unknown"]
    assert recovered["segment_ref"]["sha256"]
    second = _recover(root, tape, size, digest, apply=True)
    assert second["reapplication_noop"] is True
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 2


def test_complete_with_gap_is_separately_conservative_ineligible(tmp_path):
    root, tape, size, digest = _fixture(tmp_path, gap=True)
    result = _recover(root, tape, size, digest)
    assert result["complete_recovered_count"] == 1
    assert result["conservative_ineligible_complete_count"] == 1
    assert result["conservative_eligible_complete_count"] == 0


def test_missing_requested_bound_remains_unknown(tmp_path):
    root, tape, size, digest = _fixture(tmp_path, incomplete=True)
    result = _recover(root, tape, size, digest, apply=True)
    assert result["complete_recovered_count"] == 0
    assert result["incomplete_unknown_count"] == 1
    ledger = root / "v3" / "ledgers" / "market_segment.jsonl"
    recovered = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert recovered["future_path_status"] == "UNKNOWN"
    assert recovered["segment_ref"] is None


def test_report_selection_prefers_latest_verified_superseding_record(tmp_path):
    root, tape, size, digest = _fixture(tmp_path)
    _recover(root, tape, size, digest, apply=True)
    ledger = root / "v3" / "ledgers" / "market_segment.jsonl"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    selected, audit = authoritative_future_path_segments(root / "v3", rows)
    future = [row for row in selected if row.get("future_path_owner_key") == "owner-1"]
    assert len(future) == 1
    assert future[0]["future_path_status"] == "COMPLETE"
    assert audit["future_path_history_count"] == 2
    assert audit["selected_future_path_record_ids"] == [future[0]["record_id"]]
    report = build_v3_binding_index(root / "v3")
    assert report["decision_binding_count"] == 1
    binding = report["bindings"][0]
    assert binding["future_path_history_count"] == 2
    assert binding["selected_future_path_record_ids"] == [future[0]["record_id"]]
    assert binding["required_entry_horizons_complete"] is True
