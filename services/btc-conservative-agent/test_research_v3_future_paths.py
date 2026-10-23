import json
from pathlib import Path

import pytest

from research_v3_future_paths import (
    _bounded_tape_tail, _bounded_tape_window, _contained,
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


def test_path_containment_rejects_escape(tmp_path):
    outside = tmp_path.parent / "outside-tape.jsonl"
    with pytest.raises(ValueError, match="OUTSIDE_CANONICAL_ROOT"):
        _contained(tmp_path, outside)


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


def test_rotated_tape_window_obeys_aggregate_byte_ceiling(tmp_path):
    active = tmp_path / "market_microstructure_1s.jsonl"
    for index, path in enumerate((active, Path(str(active) + ".3"), Path(str(active) + ".2"))):
        path.write_text("".join(json.dumps({
            "bucket_ts": 300 - index * 100 + offset, "last": 10,
            "bid": 9, "ask": 11, "bid_qty": 1, "ask_qty": 1,
        }) + "\n" for offset in range(20)), encoding="utf-8")
    _rows, receipt = _bounded_tape_window(
        active, required_start_ts=1, required_end_ts=20, max_bytes=900,
    )
    assert receipt["bytes_read"] <= 900
    assert receipt["requested_start_boundary_reached"] is False


