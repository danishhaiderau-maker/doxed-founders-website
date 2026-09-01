import json
from pathlib import Path

import pytest

from microstructure_tape import build_bucket
from qualification_horizon_index import TapeIndexError, TapeOffsetIndex, produce_post_exit_path
from research_v3_store import V3EvidenceStore


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


def test_indexed_row_mutation_is_detected_at_query_and_cannot_publish(tmp_path):
    tape = tmp_path / "market_microstructure_1s.jsonl"
    _append(tape, 1000, 1010)
    index = TapeOffsetIndex(tmp_path); index.refresh(max_bytes=1_000_000)
    with tape.open("r+b") as handle:
        handle.seek(20); original = handle.read(1); handle.seek(20)
        handle.write(b"0" if original != b"0" else b"1")
    receipt = produce_post_exit_path(
        _candidate(), data_dir=tmp_path, epoch_id="epoch-a", now_ts=1010,
        horizon_sec=10, index=index,
    )
    assert receipt["post_observation"]["complete"] is False
    assert "POST_OBSERVATION_SOURCE_INTEGRITY_FAILED" in receipt["post_observation"]["blockers"]
    assert receipt["segment_ref"] is None


def test_query_bounds_fail_closed_and_never_publish_partial_path(tmp_path):
    tape = tmp_path / "market_microstructure_1s.jsonl"
    _append(tape, 1000, 1010)
    index = TapeOffsetIndex(tmp_path); index.refresh(max_bytes=1_000_000)
    receipt = produce_post_exit_path(
        _candidate(), data_dir=tmp_path, epoch_id="epoch-a", now_ts=1010,
        horizon_sec=10, index=index, max_query_rows=4,
    )
    assert receipt["post_observation"]["complete"] is False
    assert "POST_OBSERVATION_QUERY_BOUNDS_EXHAUSTED" in receipt["post_observation"]["blockers"]
    assert receipt["segment_ref"] is None


def test_missing_depth_remains_unknown(tmp_path):
    tape = tmp_path / "market_microstructure_1s.jsonl"
    _append(tape, 1000, 1010, bad_depth=1005)
    index = TapeOffsetIndex(tmp_path); index.refresh(max_bytes=1_000_000)
    receipt = produce_post_exit_path(
        _candidate(), data_dir=tmp_path, epoch_id="epoch-a", now_ts=1010,
        horizon_sec=10, index=index,
    )
    assert receipt["post_observation"]["complete"] is False
    assert "POST_OBSERVATION_DEPTH_INCOMPLETE" in receipt["post_observation"]["blockers"]


def test_complete_path_is_content_addressed_and_idempotent(tmp_path):
    tape = tmp_path / "market_microstructure_1s.jsonl"
    _append(tape, 1000, 1010)
    index = TapeOffsetIndex(tmp_path); index.refresh(max_bytes=1_000_000)
    first = produce_post_exit_path(
        _candidate(), data_dir=tmp_path, epoch_id="epoch-a", now_ts=1010,
        horizon_sec=10, index=index,
    )
    second = produce_post_exit_path(
        _candidate(), data_dir=tmp_path, epoch_id="epoch-a", now_ts=1010,
        horizon_sec=10, index=index,
    )
    assert first["post_observation"]["complete"] is True
    assert first["write"]["written"] is True
    assert second["write"]["duplicate"] is True
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-a")
    rows = store.ledger_path("market_segment").read_text().splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["context_role"] == "POST_EXIT_PATH"


def test_not_mature_or_nonterminal_does_not_query_or_write(tmp_path):
    candidate = _candidate(); candidate["terminal"] = False
    receipt = produce_post_exit_path(
        candidate, data_dir=tmp_path, epoch_id="epoch-a", now_ts=1005,
        horizon_sec=10,
    )
    assert receipt["mature"] is False
    assert set(receipt["post_observation"]["blockers"]) == {
        "LIFECYCLE_NOT_TERMINAL", "QUALIFICATION_HORIZON_NOT_MATURE",
    }
