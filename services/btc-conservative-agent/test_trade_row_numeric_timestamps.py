"""Closed paper trade rows must carry numeric entry_ts/exit_ts, or row_before_epoch fails closed on every row."""
import ast
import pathlib

import paper_fill

SRC = pathlib.Path(__file__).with_name("bot.py")


def _close_row_keys():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "trade_row" for t in node.targets) \
                and isinstance(node.value, ast.Dict):
            keys = {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
            if "event_timestamps" in keys:
                return keys
    raise AssertionError("closed-trade trade_row literal not found")


def test_closed_paper_trade_row_writes_numeric_entry_and_exit_ts():
    keys = _close_row_keys()
    assert {"entry_ts", "exit_ts"} <= keys


def test_row_with_numeric_stamps_is_classified_by_time_not_failed_closed():
    old = paper_fill.FRESH_EPOCH_TS
    try:
        paper_fill.FRESH_EPOCH_TS = 1000.0
        assert paper_fill.row_before_epoch({"ts": "2026-10-18T00:00:00Z", "close_ts": "2026-10-18T00:00:01Z"}) is True
        assert paper_fill.row_before_epoch({"entry_ts": 1500.0, "exit_ts": 1600.0}) is False
        assert paper_fill.row_before_epoch({"entry_ts": 900.0, "exit_ts": 1600.0}) is True
    finally:
        paper_fill.FRESH_EPOCH_TS = old
