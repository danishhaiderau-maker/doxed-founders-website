from __future__ import annotations

import pandas as pd
import pytest

import data_epoch as de
from analyzer_epoch_guard import EpochGuard, PreEpochRowError

EPOCH = "ce-20261004-v31-clean"
START = 1_790_000_000.0


def _guard():
    return EpochGuard(de.new_manifest(EPOCH, started_at_ts=START))


def test_no_epoch_declared_admits_everything():
    guard = EpochGuard(None)
    assert guard.filter_rows("x.jsonl", [{"ts": 1.0}, {"a": 1}]) == [{"ts": 1.0}, {"a": 1}]
    assert guard.receipt_block()["declared"] is False


def test_only_clean_epoch_rows_are_admitted_and_counted():
    guard = _guard()
    rows = [{"ts": START - 10}, {"ts": START + 10, "data_epoch_id": EPOCH},
            {"ts": START + 10, "data_epoch_id": "ce-20200101-old"}, {"ts": START + 20}]
    assert guard.filter_rows("trade_outcome.jsonl", rows) == [rows[1], rows[3]]
    assert guard.filter_rows("trades_3factor.csv", [{"ts": START + 5}, {"ts": START - 5}]) == [{"ts": START + 5}]
    assert guard.filter_rows("x.jsonl", [{"no_ts": 1}]) == []
    block = guard.receipt_block()
    assert block["epoch_id"] == EPOCH and block["rows_admitted"] == 3 and block["pre_epoch_rows_rejected"] == 4
    assert block["pre_epoch_rows_admitted"] == 0
    assert block["rejected_by_stream"]["trade_outcome.jsonl"] == {"PRE_EPOCH": 1, "FOREIGN_EPOCH": 1}
    assert block["rejected_by_stream"]["x.jsonl"] == {"UNDATED": 1}


def test_filter_frame_and_assert_pure():
    guard = _guard()
    frame = pd.DataFrame([{"ts": START - 1, "v": 1}, {"ts": START + 1, "v": 2}])
    out = guard.filter_frame("v3/ledgers/execution.jsonl", frame)
    assert out["v"].tolist() == [2]
    guard.assert_pure("v3/ledgers/execution.jsonl", out.to_dict("records"))
    with pytest.raises(PreEpochRowError):
        guard.assert_pure("v3/ledgers/execution.jsonl", frame.to_dict("records"))
