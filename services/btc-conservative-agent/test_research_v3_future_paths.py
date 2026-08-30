import json
from pathlib import Path

import pytest

from research_v3_future_paths import _bounded_tape_tail, _contained, mature_future_market_paths
from research_v3_store import V3EvidenceStore


EPOCH = "epoch-test"
SIGNAL_TS = 1_700_000_000.0


def _seed(root: Path, outcomes=("APPROVED", "REJECTED")):
    store = V3EvidenceStore(root, epoch_id=EPOCH)
    episode = "episode-shared"
    store.append("opportunity", {
        "record_id": "opportunity:episode-shared",
        "episode_id": episode,
        "shared_ai_call_id": "call-1",
        "signal_ts": SIGNAL_TS,
        "symbol": "BTCUSD",
    })
    for index, outcome in enumerate(outcomes):
        store.append("decision", {
            "record_id": f"decision:event-{index}",
            "episode_id": episode,
            "event_id": f"event-{index}",
            "primary_outcome": outcome,
        })


def _write_tape(root: Path):
    path = root / "market_microstructure_1s.jsonl"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for offset in range(0, 7201, 2):
            handle.write(json.dumps({
                "bucket_ts": SIGNAL_TS + offset,
                "last": 80_000 + offset / 100,
                "bid": 79_999 + offset / 100,
                "ask": 80_001 + offset / 100,
                "bid_qty": 1.0,
                "ask_qty": 1.0,
            }, sort_keys=True) + "\n")


def _market_rows(root: Path):
    path = root / "v3" / "ledgers" / "market_segment.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_approved_and_rejected_decisions_receive_exact_separate_bindings(tmp_path):
    _seed(tmp_path)
    _write_tape(tmp_path)
    result = mature_future_market_paths(
        data_dir=tmp_path, epoch_id=EPOCH, now_ts=SIGNAL_TS + 7230, max_batch=8,
    )
    assert result["complete_count"] == 2
    completed = [row for row in _market_rows(tmp_path) if row["future_path_status"] == "COMPLETE"]
    assert {row["decision_id"] for row in completed} == {
        "decision:event-0", "decision:event-1",
    }
    assert {row["decision_outcome"] for row in completed} == {"APPROVED", "REJECTED"}
    assert all(row["opportunity_id"] == "opportunity:episode-shared" for row in completed)
    # Identical shared windows deduplicate at the content-addressed object layer.
    assert len({row["segment_ref"]["sha256"] for row in completed}) == 1


def test_immature_horizon_is_persisted_pending_without_segment(tmp_path):
    _seed(tmp_path, outcomes=("REJECTED",))
    result = mature_future_market_paths(
        data_dir=tmp_path, epoch_id=EPOCH, now_ts=SIGNAL_TS + 7229, max_batch=8,
    )
    assert result["pending_count"] == 1
    assert result["mature_selected"] == 0
    rows = _market_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["future_path_status"] == "PENDING"
    assert rows[0]["requested_horizons_sec"] == [60, 300, 900, 1800, 3600, 7200]
    assert rows[0].get("segment_ref") is None


def test_missing_mature_source_is_unknown_never_no_fill(tmp_path):
    _seed(tmp_path, outcomes=("REJECTED",))
    result = mature_future_market_paths(
        data_dir=tmp_path, epoch_id=EPOCH, now_ts=SIGNAL_TS + 8000, max_batch=8,
    )
    assert result["unknown_count"] == 1
    terminal = [row for row in _market_rows(tmp_path) if row["future_path_status"] == "UNKNOWN"]
    assert terminal[0]["unknown_reason"] == "SOURCE_TAPE_MISSING"
    assert terminal[0].get("segment_ref") is None
    assert "NO_FILL" not in json.dumps(terminal[0])


def test_restart_is_idempotent_and_does_not_duplicate_segments(tmp_path):
    _seed(tmp_path, outcomes=("APPROVED",))
    _write_tape(tmp_path)
    first = mature_future_market_paths(
        data_dir=tmp_path, epoch_id=EPOCH, now_ts=SIGNAL_TS + 7230, max_batch=8,
    )
    before = (tmp_path / "v3" / "ledgers" / "market_segment.jsonl").read_bytes()
    second = mature_future_market_paths(
        data_dir=tmp_path, epoch_id=EPOCH, now_ts=SIGNAL_TS + 9000, max_batch=8,
    )
    after = (tmp_path / "v3" / "ledgers" / "market_segment.jsonl").read_bytes()
    assert first["complete_count"] == 1
    assert second["candidate_count"] == 0
    assert before == after
    assert len(list((tmp_path / "v3" / "market_segments").glob("*/*.json"))) == 1


def test_path_containment_rejects_escape(tmp_path):
    outside = tmp_path.parent / "outside-tape.jsonl"
    with pytest.raises(ValueError, match="OUTSIDE_CANONICAL_ROOT"):
        _contained(tmp_path, outside)


def test_orphan_decision_is_explicit_unknown_not_silently_dropped(tmp_path):
    store = V3EvidenceStore(tmp_path, epoch_id=EPOCH)
    store.append("decision", {
        "record_id": "decision:orphan",
        "episode_id": "episode-missing",
        "event_id": "event-orphan",
        "primary_outcome": "REJECTED",
    })
    result = mature_future_market_paths(
        data_dir=tmp_path, epoch_id=EPOCH, now_ts=SIGNAL_TS + 9000, max_batch=8,
    )
    assert result["unknown_count"] == 1
    terminal = [row for row in _market_rows(tmp_path) if row["future_path_status"] == "UNKNOWN"]
    assert terminal[0]["decision_id"] == "decision:orphan"
    assert terminal[0]["unknown_reason"] == "OPPORTUNITY_IDENTITY_MISSING"


def test_runtime_tape_read_is_strictly_byte_bounded_and_aligned(tmp_path):
    tape = tmp_path / "market_microstructure_1s.jsonl"
    old = json.dumps({"bucket_ts": 1, "last": 10, "bid": 9, "ask": 11}) + "\n"
    recent = [
        json.dumps({"bucket_ts": 100 + value, "last": 20, "bid": 19, "ask": 21}) + "\n"
        for value in range(3)
    ]
    tape.write_text(old * 1000 + "".join(recent), encoding="utf-8")
    budget = sum(len(row.encode("utf-8")) for row in recent) + 8
    rows, receipt = _bounded_tape_tail(tape, max_bytes=budget)
    assert receipt["truncated_to_recent_tail"] is True
    assert receipt["bytes_read"] <= budget
    assert [row["ts"] for row in rows] == [100.0, 101.0, 102.0]
    assert all(row["ts"] != 1 for row in rows)


def test_old_interval_outside_runtime_tail_is_unknown_with_exact_reason(tmp_path):
    _seed(tmp_path, outcomes=("REJECTED",))
    _write_tape(tmp_path)
    tape = tmp_path / "market_microstructure_1s.jsonl"
    with tape.open("a", encoding="utf-8") as handle:
        for offset in range(8000, 8010):
            handle.write(json.dumps({
                "bucket_ts": SIGNAL_TS + offset, "last": 80_000,
                "bid": 79_999, "ask": 80_001, "bid_qty": 1, "ask_qty": 1,
            }) + "\n")
    result = mature_future_market_paths(
        data_dir=tmp_path, epoch_id=EPOCH, now_ts=SIGNAL_TS + 9000,
        max_batch=8, max_tape_read_bytes=1600,
    )
    assert result["bounded_tape_read"]["bytes_read"] <= 1600
    terminal = [row for row in _market_rows(tmp_path) if row["future_path_status"] == "UNKNOWN"]
    assert terminal[0]["unknown_reason"] == "SOURCE_INTERVAL_OUTSIDE_BOUNDED_TAIL"
