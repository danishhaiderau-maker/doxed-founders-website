"""RK ruling 22:32 AEDT: the exit trigger is stamped with its book row's own WS_FRAME receive_ts (EXIT_LAG basis)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bot  # noqa: E402
import paper_fill  # noqa: E402
import exit_lag_regression as elr  # noqa: E402

S = 1792840000


def _rows(monkeypatch, rows):
    monkeypatch.setattr(paper_fill, "_book10l_rows_between", lambda a, b: [r for r in rows if a <= r["s"] <= b])


def test_book_trigger_stamps_row_receive_ts(monkeypatch):
    _rows(monkeypatch, [{"s": S, "receive_ts": S + 0.437, "receive_ts_source": "WS_FRAME"},
                        {"s": S + 1, "receive_ts": S + 1.2, "receive_ts_source": "WS_FRAME"}])
    assert bot._trigger_receive_stamp({"book_s": S}) == {
        "exit_trigger_row_s": S, "exit_trigger_receive_ts": S + 0.437, "exit_trigger_receive_ts_source": "WS_FRAME"}
    # event trigger row wins over the latest book_s
    assert bot._trigger_receive_stamp({"trigger_event_book_s": S + 1, "book_s": S})["exit_trigger_receive_ts"] == S + 1.2


def test_missing_or_non_ws_receive_ts_and_trade_triggers_are_none(monkeypatch):
    _rows(monkeypatch, [{"s": S, "receive_ts": None, "receive_ts_source": "MISSING"},
                        {"s": S + 2, "receive_ts": S + 2.1, "receive_ts_source": "REST"}])
    for m in ({"book_s": S}, {"book_s": S + 2}, {"book_s": S + 5},
              {"trigger_trade_id": 7, "trigger_trade_recv_s": S, "book_s": S}, {}):
        st = bot._trigger_receive_stamp(m)
        assert st["exit_trigger_receive_ts"] is None, (m, st)
        assert elr.lag_s({"trade_id": "x", "exit_decision_ts": S + 3, **st}) is None


def test_receive_keys_persist_with_exit_scan_stamps():
    assert set(elr.RECEIVE_KEYS) <= set(bot.EXIT_SCAN_STAMP_KEYS)
