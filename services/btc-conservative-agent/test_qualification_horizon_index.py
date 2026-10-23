import json
from pathlib import Path

import pytest

from microstructure_tape import build_bucket
from qualification_horizon_index import TapeIndexError, TapeOffsetIndex, produce_post_exit_path


def _append(path: Path, start: int, end: int, *, bad_depth: int | None = None):
    with path.open("ab") as handle:
        for ts in range(start, end):
            row = build_bucket(
                bucket_ts=ts, bid=99, ask=101,
                bid_qty=None if ts == bad_depth else 2,
                ask_qty=3, last=100, source_ts=ts + 0.9,
            )
            handle.write((json.dumps(row, separators=(",", ":")) + "\n").encode())


def _candidate():
    return {
        "terminal": True, "terminal_ts": 1000, "event_id": "evt-1",
        "episode_id": "episode-1", "opportunity_id": "opportunity:episode-1",
        "shared_ai_call_id": "scan-1", "symbol": "tBTCF0:USTF0",
        "policy_signature": "policy-a", "policy_epoch_id": "pe-a",
    }


def test_index_is_incremental_restart_safe_and_query_reads_only_window(tmp_path):
    tape = tmp_path / "market_microstructure_1s.jsonl"
    _append(tape, 900, 1010)
    index = TapeOffsetIndex(tmp_path)
    first = index.refresh(max_bytes=1_000_000, max_rows=1_000)
    assert first["rows_indexed"] == 110
    restarted = TapeOffsetIndex(tmp_path)
    second = restarted.refresh(max_bytes=1_000_000, max_rows=1_000)
    assert second["rows_indexed"] == 0
    _append(tape, 1010, 1020)
    third = restarted.refresh(max_bytes=1_000_000, max_rows=1_000)
    assert third["rows_indexed"] == 10
    rows, receipt = restarted.query(1000, 1010)
    assert [row["bucket_ts"] for row in rows] == list(range(1000, 1010))
    assert receipt["rows_returned"] == 10
    assert receipt["bytes_read"] < tape.stat().st_size


def test_active_tape_rename_rotation_preserves_indexed_source_identity(tmp_path):
    active = tmp_path / "market_microstructure_1s.jsonl"
    _append(active, 1000, 1005)
    index = TapeOffsetIndex(tmp_path)
    index.refresh(max_bytes=1_000_000)

    rotated = tmp_path / "market_microstructure_1s.jsonl.1"
    active.rename(rotated)
    _append(active, 1005, 1010)
    index.refresh(max_bytes=1_000_000)

    rows, receipt = index.query(1000, 1010)
    assert [row["bucket_ts"] for row in rows] == list(range(1000, 1010))
    assert receipt["hash_errors"] == 0
    assert receipt["read_errors"] == 0


def test_refresh_defers_truncated_last_line_without_advancing_cursor(tmp_path):
    tape = tmp_path / "market_microstructure_1s.jsonl"
    _append(tape, 1000, 1002)
    with tape.open("ab") as handle:
        handle.write(b'{"schema":"market_microstructure_1s_v1","bucket_ts":1002')
    index = TapeOffsetIndex(tmp_path)
    first = index.refresh(max_bytes=100_000)
    assert first["incomplete_line_deferred"] is True
    with tape.open("ab") as handle:
        handle.write(b',"fresh":true}\n')
    second = index.refresh(max_bytes=100_000)
    assert second["rows_indexed"] == 1


def test_index_row_bound_is_restart_safe_and_continues_next_run(tmp_path):
    tape = tmp_path / "market_microstructure_1s.jsonl"
    _append(tape, 1000, 1010)
    index = TapeOffsetIndex(tmp_path)
    first = index.refresh(max_bytes=1_000_000, max_rows=3)
    second = TapeOffsetIndex(tmp_path).refresh(max_bytes=1_000_000, max_rows=20)
    assert first["rows_indexed"] == 3
    assert first["budget_exhausted"] is True
    assert second["rows_indexed"] == 7
    rows, receipt = index.query(1000, 1010)
    assert len(rows) == 10
    assert receipt["bounds_respected"] is True


def test_prefix_mutation_and_index_corruption_fail_closed(tmp_path):
    tape = tmp_path / "market_microstructure_1s.jsonl"
    _append(tape, 1000, 1003)
    index = TapeOffsetIndex(tmp_path)
    index.refresh(max_bytes=100_000)
    with tape.open("r+b") as handle:
        handle.seek(10); handle.write(b"X")
    with pytest.raises(TapeIndexError, match="PREFIX_MUTATED"):
        index.refresh(max_bytes=100_000)
    index.path.write_bytes(b"not sqlite")
    with pytest.raises(TapeIndexError, match="INDEX_CORRUPT"):
        TapeOffsetIndex(tmp_path)


def test_retired_r17_never_queries_or_writes(tmp_path):
    """R17: market_segments writers are retired; the producer returns
    "disabled: R17" without querying the tape or writing a segment, for a
    terminal mature candidate as well as a non-terminal one."""
    _append(tmp_path / "microstructure_tape.jsonl", 900, 1100)
    for terminal in (True, False):
        candidate = _candidate(); candidate["terminal"] = terminal
        receipt = produce_post_exit_path(
            candidate, data_dir=tmp_path, epoch_id="epoch-a", now_ts=5000,
            horizon_sec=10,
        )
        assert receipt["mature"] is False
        assert receipt["status"] == "disabled: R17"
        assert receipt["post_observation"]["blockers"] == ["MARKET_SEGMENTS_RETIRED_R17"]
        assert receipt["query_receipt"]["not_run"] is True
        assert receipt["segment_ref"] is None
        assert receipt["write"]["written"] is False
    assert not (tmp_path / "v3" / "market_segments").exists()
