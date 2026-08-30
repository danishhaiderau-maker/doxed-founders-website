import json
from pathlib import Path

import pytest

from research_v3_future_paths import (
    _bounded_tape_tail, _bounded_tape_window, _contained,
    mature_future_market_paths,
)
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


def test_rotated_tape_is_joined_before_truthful_complete_classification(tmp_path):
    _seed(tmp_path, outcomes=("APPROVED",))
    _write_tape(tmp_path)
    active = tmp_path / "market_microstructure_1s.jsonl"
    active.replace(tmp_path / "market_microstructure_1s.jsonl.7")
    active.write_text(json.dumps({
        "bucket_ts": SIGNAL_TS + 7210, "last": 80_100,
        "bid": 80_099, "ask": 80_101, "bid_qty": 1, "ask_qty": 1,
    }) + "\n", encoding="utf-8")
    result = mature_future_market_paths(
        data_dir=tmp_path, epoch_id=EPOCH, now_ts=SIGNAL_TS + 7230,
        max_batch=8, max_tape_read_bytes=8 * 1024 * 1024,
    )
    assert result["complete_count"] == 1
    assert result["bounded_tape_read"]["source_files_read"] == 2
    terminal = [row for row in _market_rows(tmp_path) if row["future_path_status"] == "COMPLETE"]
    assert terminal[0]["coverage"]["requested_bounds_complete"] is True


def test_rotated_tape_window_obeys_aggregate_byte_ceiling(tmp_path):
    active = tmp_path / "market_microstructure_1s.jsonl"
    for index, path in enumerate((active, Path(str(active) + ".3"), Path(str(active) + ".2"))):
        path.write_text("".join(json.dumps({
            "bucket_ts": 300 - index * 100 + offset, "last": 10,
            "bid": 9, "ask": 11, "bid_qty": 1, "ask_qty": 1,
        }) + "\n" for offset in range(20)), encoding="utf-8")
    _rows, receipt = _bounded_tape_window(
        active, required_start_ts=1, max_bytes=900,
    )
    assert receipt["bytes_read"] <= 900
    assert receipt["requested_start_boundary_reached"] is False


def test_oldest_mature_candidates_are_drained_despite_stale_cursor(tmp_path):
    store = V3EvidenceStore(tmp_path, epoch_id=EPOCH)
    for index in range(12):
        episode = f"episode-{index:02d}"
        store.append("opportunity", {
            "record_id": f"opportunity:{episode}", "episode_id": episode,
            "signal_ts": SIGNAL_TS + index, "symbol": "BTCUSD",
        })
    (tmp_path / "v3" / "receipts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "v3" / "receipts" / "future-path-cursor.json").write_text(
        json.dumps({"epoch_id": EPOCH, "cursor": 160}), encoding="utf-8",
    )
    result = mature_future_market_paths(
        data_dir=tmp_path, epoch_id=EPOCH, now_ts=SIGNAL_TS + 9000, max_batch=3,
    )
    unknown = [row for row in _market_rows(tmp_path) if row["future_path_status"] == "UNKNOWN"]
    assert [row["episode_id"] for row in unknown] == [
        "episode-00", "episode-01", "episode-02",
    ]
    assert result["cursor"] == 163


def test_pending_request_backlog_advances_instead_of_rewriting_first_batch(tmp_path):
    store = V3EvidenceStore(tmp_path, epoch_id=EPOCH)
    for index in range(7):
        episode = f"episode-{index}"
        store.append("opportunity", {
            "record_id": f"opportunity:{episode}", "episode_id": episode,
            "signal_ts": SIGNAL_TS + index, "symbol": "BTCUSD",
        })
    first = mature_future_market_paths(
        data_dir=tmp_path, epoch_id=EPOCH, now_ts=SIGNAL_TS + 100, max_batch=3,
    )
    second = mature_future_market_paths(
        data_dir=tmp_path, epoch_id=EPOCH, now_ts=SIGNAL_TS + 100, max_batch=3,
    )
    pending = [row for row in _market_rows(tmp_path) if row["future_path_status"] == "PENDING"]
    assert len(first["request_writes"]) == 3
    assert len(second["request_writes"]) == 3
    assert [row["episode_id"] for row in pending] == [
        "episode-0", "episode-1", "episode-2",
        "episode-3", "episode-4", "episode-5",
    ]


def test_legacy_active_tail_unknown_gets_one_bounded_rotated_recovery(tmp_path):
    _seed(tmp_path, outcomes=("APPROVED",))
    store = V3EvidenceStore(tmp_path, epoch_id=EPOCH)
    owner = __import__("research_v3_future_paths")._owner_key(
        EPOCH, "opportunity:episode-shared", "decision:event-0",
    )
    store.append("market_segment", {
        "record_id": "legacy-unknown", "episode_id": "episode-shared",
        "opportunity_id": "opportunity:episode-shared",
        "decision_id": "decision:event-0", "future_path_owner_key": owner,
        "future_path_status": "UNKNOWN",
        "unknown_reason": "SOURCE_INTERVAL_OUTSIDE_BOUNDED_TAIL",
        "bounded_tape_read": {"schema": "bounded_tape_tail_read_v1"},
    })
    _write_tape(tmp_path)
    active = tmp_path / "market_microstructure_1s.jsonl"
    active.replace(tmp_path / "market_microstructure_1s.jsonl.4")
    active.write_text(json.dumps({
        "bucket_ts": SIGNAL_TS + 7210, "last": 80_100,
        "bid": 80_099, "ask": 80_101, "bid_qty": 1, "ask_qty": 1,
    }) + "\n", encoding="utf-8")
    first = mature_future_market_paths(
        data_dir=tmp_path, epoch_id=EPOCH, now_ts=SIGNAL_TS + 7230,
    )
    second = mature_future_market_paths(
        data_dir=tmp_path, epoch_id=EPOCH, now_ts=SIGNAL_TS + 8000,
    )
    assert first["complete_count"] == 1
    assert second["candidate_count"] == 0
